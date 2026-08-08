from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from blea import __version__
from blea.codec import parse_payload
from blea.diff import DEFAULT_RSSI_TOLERANCE_DBM, diff_evidence
from blea.errors import EXIT_CONFIG_ERROR, EXIT_DEVICE_UNAVAILABLE, BleaError
from blea.replay import ReplayBackend, replay_operation
from blea.service import BleService
from blea.workflow import run_workflow

OPERATION_TIMEOUT_HELP = (
    "seconds per scan, connect, and GATT operation; not a total command deadline"
)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def _add_json(parser: argparse.ArgumentParser, *, jsonl: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="print one machine-readable result")
    if jsonl:
        parser.add_argument("--jsonl", action="store_true", help="print event records as JSONL")


def _print_human(payload: dict[str, Any]) -> None:
    operation = payload.get("operation")
    if not payload.get("ok", False):
        error = payload.get("error") or payload
        print(f"[ERROR] {error.get('message') or error.get('reason')}", file=sys.stderr)
        if operation == "capture" and payload.get("output"):
            print(f"Evidence: {payload['output']}", file=sys.stderr)
        return
    replay = payload.get("replay")
    if isinstance(replay, dict):
        print(f"Replay evidence: {replay['evidence']} ({replay['timing']})")
    if operation == "doctor":
        print(f"BLE backend: {payload['backend']}")
        print("Adapter: available")
        print(f"Devices observed: {payload['devices_observed']}")
    elif operation == "scan":
        if not payload["devices"]:
            print("No BLE devices observed.")
        for device in payload["devices"]:
            name = device.get("local_name") or device.get("name") or "(unnamed)"
            rssi = device.get("rssi")
            print(f"{device['identifier']:<38} {str(rssi):>4} dBm  {name}")
    elif operation == "inspect":
        print(f"Device: {payload['device']['identifier']}")
        for service in payload["profile"]["services"]:
            print(f"service {service['uuid']}  {service.get('description') or ''}")
            for char in service["characteristics"]:
                properties = ",".join(char["properties"])
                print(f"  char {char['uuid']}  [{properties}]")
    elif operation == "probe":
        print(f"Device: {payload['device']['identifier']}")
        print(f"Services: {payload['profile_summary']['service_count']}")
        page = payload["read_page"]
        print(
            "Reads: "
            f"{page['success_count']} succeeded, "
            f"{page['failure_count']} failed, "
            f"{page['remaining_count']} remaining"
        )
        for item in payload["reads"]:
            value = item["data"]["hex"] if item.get("ok") else "ERROR"
            print(f"  {item['characteristic']}  {value}")
        if payload["next_read_offset"] is not None:
            print(f"Next read offset: {payload['next_read_offset']}")
    elif operation == "capture":
        print(f"Evidence: {payload['output']}")
        if payload.get("device"):
            print(f"Device: {payload['device']['identifier']}")
        reads = payload["read_summary"]
        print(f"Reads: {reads['success_count']} succeeded, {reads['failure_count']} failed")
        observation = payload.get("observation") or {}
        print(f"Notifications: {observation.get('notification_count', 0)}")
        print(f"Status: {payload['status']}")
    elif operation == "diff":
        summary = payload["summary"]
        print(f"Diff: {payload['status']}")
        print(
            f"Added: {summary['added']}, removed: {summary['removed']}, "
            f"changed: {summary['changed']}, unchanged: {summary['unchanged']}"
        )
        for category, marker in (("added", "+"), ("removed", "-"), ("changed", "~")):
            for item in payload["changes"][category]:
                print(f"  {marker} {item['path']}")
    elif operation in {"read", "session_read"}:
        print(payload["data"]["hex"])
    elif operation in {"subscribe", "session_subscribe"}:
        for notification in payload["notifications"]:
            print(
                f"{notification['timestamp']} {notification['characteristic']} "
                f"{notification['data']['hex']}"
            )
        print(f"Notifications: {payload['notification_count']}")
    elif operation in {"observe", "session_observe"}:
        selection = payload["selection"]
        summary = payload["subscription_summary"]
        print(f"Device: {payload['device']['identifier']}")
        print(
            f"Observed {selection['selected_count']} characteristics for "
            f"{payload['sample_duration_seconds']} seconds"
        )
        print(
            "Subscriptions: "
            f"{summary['success_count']} succeeded, "
            f"{summary['failure_count']} failed; "
            f"Notifications: {payload['notification_count']}"
        )
        for item in payload["notifications"]:
            print(f"{item['timestamp']} {item['characteristic']} {item['data']['hex']}")
    elif operation in {"exchange", "session_exchange"}:
        print(f"Wrote {payload['written']['length']} bytes to {payload['write_characteristic']}")
        if payload.get("read_back"):
            print(f"Read back: {payload['read_back']['hex']}")
        for notification in payload["notifications"]:
            print(
                f"{notification['timestamp']} {notification['characteristic']} "
                f"{notification['data']['hex']}"
            )
        print(f"Notifications: {payload['notification_count']}")
    elif operation in {"write", "session_write"}:
        print(f"Wrote {payload['written']['length']} bytes to {payload['characteristic']}")
        if payload.get("read_back"):
            print(f"Read back: {payload['read_back']['hex']}")
    elif operation == "workflow":
        marker = "PASS" if payload["ok"] else "FAIL"
        print(f"[{marker}] {payload['name']}")
        for step in payload["steps"]:
            step_marker = "PASS" if step.get("ok") else "FAIL"
            print(f"  [{step_marker}] {step['id']} ({step['action']})")


def _emit(payload: dict[str, Any], args: argparse.Namespace) -> int:
    if getattr(args, "jsonl", False):
        if payload.get("operation") == "scan":
            for device in payload.get("devices", []):
                print(_json({"type": "device", **device}))
        elif payload.get("operation") in {
            "subscribe",
            "observe",
            "session_observe",
            "exchange",
            "session_exchange",
        }:
            for notification in payload.get("notifications", []):
                print(_json({"type": "notification", **notification}))
        print(_json({"type": "result", **payload}))
    elif getattr(args, "json", False):
        print(_json(payload))
    else:
        _print_human(payload)
    if payload.get("ok"):
        return int(payload.get("exit_code", 0))
    error = payload.get("error") or payload
    return int(error.get("exit_code", EXIT_DEVICE_UNAVAILABLE))


async def command_doctor(args: argparse.Namespace) -> int:
    return _emit(await BleService().doctor(scan_timeout=args.scan_timeout), args)


async def command_scan(args: argparse.Namespace) -> int:
    result = await BleService().scan(
        timeout=args.timeout,
        name_contains=args.name_contains,
        service_uuid=args.service,
    )
    return _emit(result, args)


async def command_inspect(args: argparse.Namespace) -> int:
    return _emit(await BleService().inspect(args.device, timeout=args.timeout), args)


async def command_probe(args: argparse.Namespace) -> int:
    return _emit(
        await BleService().probe(
            args.device,
            timeout=args.timeout,
            max_reads=args.max_reads,
            read_offset=args.read_offset,
            include_profile=args.include_profile,
        ),
        args,
    )


async def command_capture(args: argparse.Namespace) -> int:
    return _emit(
        await BleService().capture(
            args.device,
            args.output,
            service_uuid=args.service,
            max_reads=args.max_reads,
            read_offset=args.read_offset,
            observe_duration=args.observe_duration,
            timeout=args.timeout,
            redact_identifiers=args.redact_identifiers,
        ),
        args,
    )


def command_diff(args: argparse.Namespace) -> int:
    return _emit(
        diff_evidence(
            args.before,
            args.after,
            rssi_tolerance=args.rssi_tolerance,
            strict_rssi=args.strict_rssi,
            allow_different_devices=args.allow_different_devices,
            fail_on_change=args.fail_on_change,
        ),
        args,
    )


async def command_replay(args: argparse.Namespace) -> int:
    characteristics = getattr(args, "characteristics", None)
    result = await replay_operation(
        args.evidence,
        args.replay_operation,
        speed=args.speed,
        device=getattr(args, "device", None),
        characteristic=getattr(args, "characteristic", None),
        characteristics=tuple(characteristics) if characteristics else None,
        workflow=getattr(args, "workflow", None),
        timeout=getattr(args, "timeout", 10.0),
        duration=getattr(args, "duration", 10.0),
        max_reads=getattr(args, "max_reads", 32),
        read_offset=getattr(args, "read_offset", 0),
        include_profile=getattr(args, "include_profile", True),
        name_contains=getattr(args, "name_contains", None),
        service_uuid=getattr(args, "service", None),
    )
    return _emit(result, args)


def command_replay_mcp(args: argparse.Namespace) -> int:
    from blea.mcp_server import run

    run(ReplayBackend(args.evidence, speed=args.speed))
    return 0


async def command_read(args: argparse.Namespace) -> int:
    return _emit(
        await BleService().read(args.device, args.characteristic, timeout=args.timeout), args
    )


async def command_subscribe(args: argparse.Namespace) -> int:
    return _emit(
        await BleService().subscribe(
            args.device,
            args.characteristic,
            duration=args.duration,
            timeout=args.timeout,
        ),
        args,
    )


async def command_observe(args: argparse.Namespace) -> int:
    return _emit(
        await BleService().observe(
            args.device,
            characteristics=tuple(args.characteristics) if args.characteristics else None,
            duration=args.duration,
            timeout=args.timeout,
        ),
        args,
    )


async def command_exchange(args: argparse.Namespace) -> int:
    data = parse_payload(
        hex_value=args.hex_value,
        text_value=args.text_value,
        base64_value=args.base64_value,
    )
    return _emit(
        await BleService().exchange(
            args.device,
            args.write_characteristic,
            args.notify_characteristic,
            data,
            duration=args.duration,
            response=args.response,
            read_back=args.read_back,
            allow_write=args.allow_write,
            confirm_device=args.confirm_device,
            timeout=args.timeout,
        ),
        args,
    )


async def command_write(args: argparse.Namespace) -> int:
    data = parse_payload(
        hex_value=args.hex_value,
        text_value=args.text_value,
        base64_value=args.base64_value,
    )
    return _emit(
        await BleService().write(
            args.device,
            args.characteristic,
            data,
            response=args.response,
            read_back=args.read_back,
            allow_write=args.allow_write,
            confirm_device=args.confirm_device,
            timeout=args.timeout,
        ),
        args,
    )


async def command_run(args: argparse.Namespace) -> int:
    return _emit(await run_workflow(args.workflow, allow_write=args.allow_write), args)


def command_mcp(args: argparse.Namespace) -> int:
    del args
    from blea.mcp_server import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ble", description="Agent-first Bluetooth Low Energy diagnostics"
    )
    parser.add_argument("--version", action="version", version=f"ble {__version__}")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    doctor = subparsers.add_parser("doctor", help="diagnose adapter and permission availability")
    doctor.add_argument("--scan-timeout", type=float, default=1.0)
    _add_json(doctor)
    doctor.set_defaults(func=command_doctor)

    scan = subparsers.add_parser("scan", help="scan nearby BLE advertisements")
    scan.add_argument("--timeout", type=float, default=5.0)
    scan.add_argument("--name-contains")
    scan.add_argument("--service", help="filter by advertised service UUID")
    _add_json(scan, jsonl=True)
    scan.set_defaults(func=command_scan)

    inspect_parser = subparsers.add_parser("inspect", help="discover a device GATT profile")
    inspect_parser.add_argument("--device", required=True, help="exact identifier or exact name")
    inspect_parser.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(inspect_parser)
    inspect_parser.set_defaults(func=command_inspect)

    probe = subparsers.add_parser(
        "probe", help="discover GATT and read a bounded set of readable characteristics"
    )
    probe.add_argument("--device", required=True, help="exact identifier or exact name")
    probe.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    probe.add_argument("--max-reads", type=int, default=32)
    probe.add_argument("--read-offset", type=int, default=0)
    probe.add_argument(
        "--include-profile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include the full GATT profile in structured output",
    )
    _add_json(probe)
    probe.set_defaults(func=command_probe)

    capture = subparsers.add_parser(
        "capture", help="save read-only BLE advertisements, GATT, reads, and notifications"
    )
    capture.add_argument("--device", required=True, help="exact identifier or exact name")
    capture.add_argument("--output", type=Path, required=True, help="destination .blea.jsonl")
    capture.add_argument("--service", help="filter by advertised service UUID")
    capture.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    capture.add_argument("--max-reads", type=int, default=128)
    capture.add_argument("--read-offset", type=int, default=0)
    capture.add_argument("--observe-duration", type=float, default=10.0)
    capture.add_argument(
        "--redact-identifiers",
        action="store_true",
        help="replace platform identifiers with stable per-capture tokens",
    )
    _add_json(capture)
    capture.set_defaults(func=command_capture)

    diff = subparsers.add_parser("diff", help="compare two BLEA evidence files offline")
    diff.add_argument("before", type=Path, help="baseline .blea.jsonl evidence file")
    diff.add_argument("after", type=Path, help="comparison .blea.jsonl evidence file")
    rssi = diff.add_mutually_exclusive_group()
    rssi.add_argument(
        "--rssi-tolerance",
        type=float,
        default=DEFAULT_RSSI_TOLERANCE_DBM,
        metavar="DBM",
        help="ignore RSSI deltas at or below DBM (default: 5)",
    )
    rssi.add_argument(
        "--strict-rssi",
        action="store_true",
        help="compare RSSI exactly instead of applying a tolerance",
    )
    diff.add_argument(
        "--allow-different-devices",
        action="store_true",
        help="allow and report an intentional device identifier mismatch",
    )
    diff.add_argument(
        "--fail-on-change",
        action="store_true",
        help="return exit code 3 when a valid comparison finds differences",
    )
    _add_json(diff)
    diff.set_defaults(func=command_diff)

    replay = subparsers.add_parser(
        "replay", help="replay read-only BLE operations from evidence without an adapter"
    )
    replay.add_argument("evidence", type=Path, help="complete .blea.jsonl evidence file")
    replay.add_argument(
        "--speed",
        type=float,
        default=0.0,
        metavar="MULTIPLIER",
        help="notification timing multiplier; 0 replays instantly (default: 0)",
    )
    replay_operations = replay.add_subparsers(dest="replay_operation", required=True)

    replay_scan = replay_operations.add_parser("scan", help="replay captured advertisement data")
    replay_scan.add_argument("--timeout", type=float, default=5.0)
    replay_scan.add_argument("--name-contains")
    replay_scan.add_argument("--service", help="filter by advertised service UUID")
    _add_json(replay_scan, jsonl=True)
    replay_scan.set_defaults(func=command_replay)

    replay_inspect = replay_operations.add_parser(
        "inspect", help="replay the captured GATT profile"
    )
    replay_inspect.add_argument("--device", help="exact identifier or exact name")
    replay_inspect.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(replay_inspect)
    replay_inspect.set_defaults(func=command_replay)

    replay_probe = replay_operations.add_parser(
        "probe", help="replay the captured GATT profile and readable values"
    )
    replay_probe.add_argument("--device", help="exact identifier or exact name")
    replay_probe.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    replay_probe.add_argument("--max-reads", type=int, default=32)
    replay_probe.add_argument("--read-offset", type=int, default=0)
    replay_probe.add_argument(
        "--include-profile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include the full GATT profile in structured output",
    )
    _add_json(replay_probe)
    replay_probe.set_defaults(func=command_replay)

    replay_read = replay_operations.add_parser("read", help="replay one captured read")
    replay_read.add_argument("--device", help="exact identifier or exact name")
    replay_read.add_argument("--characteristic", required=True)
    replay_read.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(replay_read)
    replay_read.set_defaults(func=command_replay)

    replay_subscribe = replay_operations.add_parser(
        "subscribe", help="replay notifications for one characteristic"
    )
    replay_subscribe.add_argument("--device", help="exact identifier or exact name")
    replay_subscribe.add_argument("--characteristic", required=True)
    replay_subscribe.add_argument("--duration", type=float, default=10.0)
    replay_subscribe.add_argument(
        "--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP
    )
    _add_json(replay_subscribe, jsonl=True)
    replay_subscribe.set_defaults(func=command_replay)

    replay_observe = replay_operations.add_parser(
        "observe", help="replay all or selected captured notification streams"
    )
    replay_observe.add_argument("--device", help="exact identifier or exact name")
    replay_observe.add_argument(
        "--characteristic",
        dest="characteristics",
        action="append",
        help="repeat to select characteristics; defaults to all notify/indicate traits",
    )
    replay_observe.add_argument("--duration", type=float, default=10.0)
    replay_observe.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(replay_observe, jsonl=True)
    replay_observe.set_defaults(func=command_replay)

    replay_run = replay_operations.add_parser(
        "run", help="run a read-only YAML workflow against captured evidence"
    )
    replay_run.add_argument("workflow", type=Path)
    _add_json(replay_run)
    replay_run.set_defaults(func=command_replay)

    replay_mcp = replay_operations.add_parser(
        "mcp", help="serve normal BLEA MCP tools from captured evidence"
    )
    replay_mcp.set_defaults(func=command_replay_mcp)

    read = subparsers.add_parser("read", help="read one GATT characteristic")
    read.add_argument("--device", required=True)
    read.add_argument("--characteristic", required=True)
    read.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(read)
    read.set_defaults(func=command_read)

    subscribe = subparsers.add_parser("subscribe", help="collect bounded notifications")
    subscribe.add_argument("--device", required=True)
    subscribe.add_argument("--characteristic", required=True)
    subscribe.add_argument("--duration", type=float, default=10.0)
    subscribe.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(subscribe, jsonl=True)
    subscribe.set_defaults(func=command_subscribe)

    observe = subparsers.add_parser(
        "observe", help="observe all or selected notify/indicate characteristics"
    )
    observe.add_argument("--device", required=True)
    observe.add_argument(
        "--characteristic",
        dest="characteristics",
        action="append",
        help="repeat to observe selected characteristics; defaults to all notify/indicate traits",
    )
    observe.add_argument("--duration", type=float, default=10.0)
    observe.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(observe, jsonl=True)
    observe.set_defaults(func=command_observe)

    exchange = subparsers.add_parser(
        "exchange", help="subscribe, perform one guarded write, and collect notifications"
    )
    exchange.add_argument("--device", required=True)
    exchange.add_argument("--write-characteristic", required=True)
    exchange.add_argument("--notify-characteristic", required=True)
    exchange_payload = exchange.add_mutually_exclusive_group(required=True)
    exchange_payload.add_argument("--hex", dest="hex_value")
    exchange_payload.add_argument("--text", dest="text_value")
    exchange_payload.add_argument("--base64", dest="base64_value")
    exchange.add_argument("--duration", type=float, default=5.0)
    exchange.add_argument("--response", action=argparse.BooleanOptionalAction, default=True)
    exchange.add_argument("--read-back", action="store_true")
    exchange.add_argument("--allow-write", action="store_true")
    exchange.add_argument(
        "--confirm-device", help="must exactly match the resolved device identifier"
    )
    exchange.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(exchange, jsonl=True)
    exchange.set_defaults(func=command_exchange)

    write = subparsers.add_parser("write", help="perform an explicitly authorized GATT write")
    write.add_argument("--device", required=True)
    write.add_argument("--characteristic", required=True)
    payload = write.add_mutually_exclusive_group(required=True)
    payload.add_argument("--hex", dest="hex_value")
    payload.add_argument("--text", dest="text_value")
    payload.add_argument("--base64", dest="base64_value")
    write.add_argument("--response", action=argparse.BooleanOptionalAction, default=True)
    write.add_argument("--read-back", action="store_true")
    write.add_argument("--allow-write", action="store_true")
    write.add_argument("--confirm-device", help="must exactly match the resolved device identifier")
    write.add_argument("--timeout", type=float, default=10.0, help=OPERATION_TIMEOUT_HELP)
    _add_json(write)
    write.set_defaults(func=command_write)

    run = subparsers.add_parser("run", help="run a guarded YAML BLE workflow")
    run.add_argument("workflow", type=Path)
    run.add_argument("--allow-write", action="store_true")
    _add_json(run)
    run.set_defaults(func=command_run)

    mcp = subparsers.add_parser("mcp", help="serve BLEA over local MCP stdio")
    mcp.set_defaults(func=command_mcp)
    return parser


def main(argv: list[str] | None = None) -> None:
    _configure_console()
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        code = asyncio.run(result) if inspect.isawaitable(result) else result
    except BleaError as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False) or getattr(args, "jsonl", False):
            print(_json(payload))
        else:
            print(f"ble: {exc}", file=sys.stderr)
        code = exc.exit_code
    except KeyboardInterrupt:
        print("ble: interrupted", file=sys.stderr)
        code = 130
    except Exception as exc:  # pragma: no cover - last-resort CLI boundary
        if getattr(args, "json", False) or getattr(args, "jsonl", False):
            print(
                _json(
                    {
                        "ok": False,
                        "reason": "unexpected_error",
                        "message": str(exc),
                        "exit_code": EXIT_CONFIG_ERROR,
                    }
                )
            )
        else:
            print(f"ble: unexpected error: {exc}", file=sys.stderr)
        code = EXIT_CONFIG_ERROR
    raise SystemExit(code)
