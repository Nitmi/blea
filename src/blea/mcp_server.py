from __future__ import annotations

import asyncio
import math
import os
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

with warnings.catch_warnings():
    # mcp 1.29 currently asks pydantic-settings to resolve FastMCP's forward-referenced
    # lifespan field during import. It is harmless for BLEA's stdio server, but noisy for agents.
    warnings.filterwarnings("ignore", message=r"Field 'lifespan' has an incomplete definition:.*")
    from mcp.server.fastmcp import FastMCP

from blea import __version__
from blea.codec import parse_payload
from blea.diff import DEFAULT_RSSI_TOLERANCE_DBM, diff_evidence
from blea.errors import BleaError
from blea.replay import replay_operation
from blea.service import BleService, SessionManager

DEFAULT_SESSION_IDLE_SECONDS = 120.0


def _session_idle_seconds() -> float | None:
    raw = os.environ.get("BLEA_SESSION_IDLE_SECONDS", str(DEFAULT_SESSION_IDLE_SECONDS))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("BLEA_SESSION_IDLE_SECONDS must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("BLEA_SESSION_IDLE_SECONDS must be finite and non-negative")
    return value or None


service = BleService()
sessions = SessionManager(service, idle_timeout_seconds=_session_idle_seconds())


def configure_backend(backend: Any) -> None:
    """Configure a fresh MCP process before serving any requests."""

    global service, sessions
    if sessions.list_sessions()["count"]:
        raise RuntimeError("cannot replace the MCP backend while sessions are open")
    service = BleService(backend)
    sessions = SessionManager(service, idle_timeout_seconds=_session_idle_seconds())


async def _reap_idle_sessions(manager: SessionManager) -> None:
    idle_seconds = manager.idle_timeout_seconds
    if idle_seconds is None:
        return
    interval = min(max(idle_seconds / 2, 1.0), 30.0)
    while True:
        await asyncio.sleep(interval)
        await manager.close_idle(idle_seconds)


async def _finish_cleanup(manager: SessionManager) -> int:
    cleanup = asyncio.create_task(manager.close_all())
    try:
        return await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


@asynccontextmanager
async def mcp_lifespan(_: FastMCP[Any]) -> AsyncIterator[None]:
    reaper = (
        asyncio.create_task(_reap_idle_sessions(sessions))
        if sessions.idle_timeout_seconds is not None
        else None
    )
    try:
        yield None
    finally:
        if reaper is not None:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
        await _finish_cleanup(sessions)


with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r"Field 'lifespan' has an incomplete definition:.*")
    mcp = FastMCP("BLEA", lifespan=mcp_lifespan)
# FastMCP does not expose its low-level server version in the constructor. Without this,
# initialize reports the MCP SDK version instead of the BLEA product version.
mcp._mcp_server.version = __version__


def _annotate_backend(result: dict[str, Any]) -> dict[str, Any]:
    metadata = getattr(service.backend, "metadata", None)
    if not callable(metadata):
        return result
    if getattr(service.backend, "instant", False) and "duration_ms" in result:
        result["duration_ms"] = 0
    result.setdefault("replay", metadata())
    return result


async def _safe(call: Any, *, include_backend_context: bool = True) -> dict[str, Any]:
    try:
        # WinRT BLE awaits can switch ContextVar contexts on Windows. A child task keeps the
        # MCP SDK's request context intact so it can serialize and send the tool response.
        result = await asyncio.create_task(call)
    except BleaError as exc:
        result = exc.to_dict()
    return _annotate_backend(result) if include_backend_context else result


@mcp.tool()
async def ble_doctor(scan_timeout: float = 1.0) -> dict[str, Any]:
    """Check whether the local Bluetooth adapter and OS BLE backend are usable."""

    return await _safe(service.doctor(scan_timeout=scan_timeout))


@mcp.tool()
async def ble_scan(
    timeout: float = 5.0,
    name_contains: str | None = None,
    service_uuid: str | None = None,
) -> dict[str, Any]:
    """Scan nearby BLE advertisements and return stable structured evidence."""

    return await _safe(
        service.scan(
            timeout=timeout,
            name_contains=name_contains,
            service_uuid=service_uuid,
        )
    )


@mcp.tool()
async def ble_inspect(device: str, timeout: float = 10.0) -> dict[str, Any]:
    """Discover GATT; timeout applies separately to scan, connect, and GATT operations."""

    return await _safe(service.inspect(device, timeout=timeout))


@mcp.tool()
async def ble_probe(
    device: str,
    timeout: float = 10.0,
    max_reads: int = 32,
    read_offset: int = 0,
    include_profile: bool = False,
) -> dict[str, Any]:
    """Read one GATT page; inspect read_page and follow next_read_offset until null.

    ok=true means the page ran, not that every characteristic read succeeded. timeout applies
    separately to scan, connect, profile discovery, and each read. The full profile is omitted by
    default to keep repeated pages compact; use ble_inspect first or set include_profile=true.
    """

    return await _safe(
        service.probe(
            device,
            timeout=timeout,
            max_reads=max_reads,
            read_offset=read_offset,
            include_profile=include_profile,
        )
    )


@mcp.tool()
async def ble_capture(
    device: str,
    output: str,
    service_uuid: str | None = None,
    max_reads: int = 128,
    read_offset: int = 0,
    observe_duration: float = 10.0,
    redact_identifiers: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Save a validated read-only BLE evidence package as .blea.jsonl.

    Capture performs one discovery and one connection, then records advertisements, GATT,
    bounded reads, and bounded notifications. It never writes, pairs, or changes device
    configuration. The output path is replaced atomically only after the final summary validates.
    """

    return await _safe(
        service.capture(
            device,
            output,
            service_uuid=service_uuid,
            max_reads=max_reads,
            read_offset=read_offset,
            observe_duration=observe_duration,
            timeout=timeout,
            redact_identifiers=redact_identifiers,
        )
    )


@mcp.tool()
async def ble_diff(
    before: str,
    after: str,
    rssi_tolerance: float = DEFAULT_RSSI_TOLERANCE_DBM,
    strict_rssi: bool = False,
    allow_different_devices: bool = False,
    fail_on_change: bool = False,
) -> dict[str, Any]:
    """Compare two validated BLEA evidence files without accessing Bluetooth hardware.

    Transient capture metadata and notification timestamps are ignored. RSSI uses a 5 dBm
    tolerance by default; strict_rssi compares exact values. Device identifiers must match unless
    allow_different_devices is explicitly enabled.
    """

    return await _safe(
        asyncio.to_thread(
            diff_evidence,
            before,
            after,
            rssi_tolerance=rssi_tolerance,
            strict_rssi=strict_rssi,
            allow_different_devices=allow_different_devices,
            fail_on_change=fail_on_change,
        ),
        include_backend_context=False,
    )


@mcp.tool()
async def ble_replay(
    evidence: str,
    operation: str,
    speed: float = 0.0,
    device: str | None = None,
    characteristic: str | None = None,
    characteristics: list[str] | None = None,
    workflow: str | None = None,
    timeout: float = 10.0,
    duration: float = 10.0,
    max_reads: int = 32,
    read_offset: int = 0,
    include_profile: bool = False,
    name_contains: str | None = None,
    service_uuid: str | None = None,
) -> dict[str, Any]:
    """Replay scan/inspect/probe/read/subscribe/observe/run from evidence without BLE hardware.

    speed=0 returns matching notifications immediately. Positive speed values preserve recorded
    notification gaps at the requested multiplier. Replay is always read-only and never exposes a
    write or exchange operation.
    """

    return await _safe(
        replay_operation(
            evidence,
            operation,
            speed=speed,
            device=device,
            characteristic=characteristic,
            characteristics=tuple(characteristics) if characteristics else None,
            workflow=workflow,
            timeout=timeout,
            duration=duration,
            max_reads=max_reads,
            read_offset=read_offset,
            include_profile=include_profile,
            name_contains=name_contains,
            service_uuid=service_uuid,
        ),
        include_backend_context=False,
    )


@mcp.tool()
async def ble_read(device: str, characteristic: str, timeout: float = 10.0) -> dict[str, Any]:
    """Read a characteristic; timeout applies separately to scan, connect, and GATT operations."""

    return await _safe(service.read(device, characteristic, timeout=timeout))


@mcp.tool()
async def ble_subscribe(
    device: str,
    characteristic: str,
    duration: float = 10.0,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Collect bounded notifications; timeout is per backend operation, not a total deadline."""

    return await _safe(
        service.subscribe(device, characteristic, duration=duration, timeout=timeout)
    )


@mcp.tool()
async def ble_observe(
    device: str,
    characteristics: list[str] | None = None,
    duration: float = 10.0,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Observe notify/indicate characteristics on one connection for a bounded duration.

    When characteristics is omitted, all discovered notify/indicate characteristics are selected.
    Per-characteristic subscription failures remain in subscriptions while other subscriptions
    continue. The tool always reports cleanup evidence and never writes or pairs.
    """

    return await _safe(
        service.observe(
            device,
            characteristics=tuple(characteristics) if characteristics else None,
            duration=duration,
            timeout=timeout,
        )
    )


@mcp.tool()
async def ble_exchange(
    device: str,
    write_characteristic: str,
    notify_characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    duration: float = 5.0,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Subscribe first, perform one guarded write, then collect resulting notifications."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return _annotate_backend(exc.to_dict())
    return await _safe(
        service.exchange(
            device,
            write_characteristic,
            notify_characteristic,
            data,
            duration=duration,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
            timeout=timeout,
        )
    )


@mcp.tool()
async def ble_write(
    device: str,
    characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Write with guards; timeout is per backend operation, not a total tool deadline."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return _annotate_backend(exc.to_dict())
    return await _safe(
        service.write(
            device,
            characteristic,
            data,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
            timeout=timeout,
        )
    )


@mcp.tool()
async def ble_session_open(device: str, timeout: float = 10.0) -> dict[str, Any]:
    """Open a leased connection; timeout applies per backend operation for the session."""

    return await _safe(sessions.open(device, timeout=timeout))


@mcp.tool()
async def ble_session_inspect(session_id: str) -> dict[str, Any]:
    """Discover services on an open BLE session."""

    return await _safe(sessions.inspect(session_id))


@mcp.tool()
async def ble_session_read(session_id: str, characteristic: str) -> dict[str, Any]:
    """Read a characteristic through an open BLE session."""

    return await _safe(sessions.read(session_id, characteristic))


@mcp.tool()
async def ble_session_subscribe(
    session_id: str, characteristic: str, duration: float = 10.0
) -> dict[str, Any]:
    """Collect bounded notifications through an open BLE session."""

    return await _safe(sessions.subscribe(session_id, characteristic, duration=duration))


@mcp.tool()
async def ble_session_observe(
    session_id: str,
    characteristics: list[str] | None = None,
    duration: float = 10.0,
) -> dict[str, Any]:
    """Observe notify/indicate characteristics through one existing session connection."""

    return await _safe(
        sessions.observe(
            session_id,
            characteristics=tuple(characteristics) if characteristics else None,
            duration=duration,
        )
    )


@mcp.tool()
async def ble_session_exchange(
    session_id: str,
    write_characteristic: str,
    notify_characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    duration: float = 5.0,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
) -> dict[str, Any]:
    """Atomically subscribe, write, and collect notifications through one open session."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return _annotate_backend(exc.to_dict())
    return await _safe(
        sessions.exchange(
            session_id,
            write_characteristic,
            notify_characteristic,
            data,
            duration=duration,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
        )
    )


@mcp.tool()
async def ble_session_write(
    session_id: str,
    characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
) -> dict[str, Any]:
    """Write through an open session with the same explicit write guard."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return _annotate_backend(exc.to_dict())
    return await _safe(
        sessions.write(
            session_id,
            characteristic,
            data,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
        )
    )


@mcp.tool()
async def ble_session_close(session_id: str) -> dict[str, Any]:
    """Disconnect and forget an open BLE session."""

    return await _safe(sessions.close(session_id))


@mcp.tool()
async def ble_session_list() -> dict[str, Any]:
    """List open BLE sessions, their devices, idle time, and lease timeout."""

    return _annotate_backend(sessions.list_sessions())


@mcp.tool()
async def ble_session_close_all() -> dict[str, Any]:
    """Recover unknown or leaked state by closing every session owned by this server."""

    async def close_all() -> dict[str, Any]:
        count = await sessions.close_all()
        return {
            "ok": True,
            "operation": "session_close_all",
            "closed_count": count,
            "exit_code": 0,
        }

    return await _safe(close_all())


def run(backend: Any | None = None) -> None:
    if backend is not None:
        configure_backend(backend)
    mcp.run(transport="stdio")
