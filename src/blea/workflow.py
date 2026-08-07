from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import yaml

from blea.codec import parse_payload
from blea.errors import (
    EXIT_ASSERTION_FAILED,
    EXIT_GUARD_DENIED,
    EXIT_OK,
    BleaError,
    ConfigError,
)
from blea.service import SessionManager


def load_workflow(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("unable to read workflow", path=str(source)) from exc
    except yaml.YAMLError as exc:
        raise ConfigError("workflow is not valid YAML", path=str(source)) from exc
    if not isinstance(payload, dict):
        raise ConfigError("workflow root must be an object", path=str(source))

    name = payload.get("name")
    device = payload.get("device")
    steps = payload.get("steps")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("workflow name must be a non-empty string")
    if not isinstance(device, str) or not device.strip():
        raise ConfigError("workflow device must be a non-empty selector string")
    if not isinstance(steps, list) or not steps:
        raise ConfigError("workflow steps must be a non-empty array")

    seen: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ConfigError("workflow step must be an object", index=index)
        step_id = step.get("id")
        action = step.get("action")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ConfigError("workflow step id must be a non-empty string", index=index)
        if step_id in seen:
            raise ConfigError("workflow step ids must be unique", step=step_id)
        if action not in {"inspect", "read", "subscribe", "write", "exchange"}:
            raise ConfigError("unsupported workflow action", step=step_id, action=action)
        requires = step.get("requires", [])
        if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
            raise ConfigError("step requires must be an array of step ids", step=step_id)
        unknown = [item for item in requires if item not in seen]
        if unknown:
            raise ConfigError(
                "step requirements must reference earlier steps", step=step_id, unknown=unknown
            )
        if action in {"read", "subscribe", "write"} and not isinstance(
            step.get("characteristic"), str
        ):
            raise ConfigError("step characteristic must be a string", step=step_id)
        if action == "exchange":
            for field in ("write_characteristic", "notify_characteristic"):
                value = step.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ConfigError(f"exchange step {field} must be a string", step=step_id)
        if action in {"write", "exchange"}:
            if step.get("dangerous") is not True:
                raise ConfigError(
                    "write and exchange steps must declare dangerous: true", step=step_id
                )
            if not requires:
                raise ConfigError(
                    "write and exchange steps must require successful earlier steps",
                    step=step_id,
                )
            value = step.get("value")
            if not isinstance(value, dict):
                raise ConfigError("write and exchange step value must be an object", step=step_id)
            parse_payload(
                hex_value=value.get("hex"),
                text_value=value.get("text"),
                base64_value=value.get("base64"),
            )
            if action == "exchange":
                duration = step.get("duration", 5.0)
                if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                    raise ConfigError(
                        "exchange duration must be a non-negative number", step=step_id
                    )
                if duration < 0:
                    raise ConfigError(
                        "exchange duration must be a non-negative number", step=step_id
                    )
        if "expect" in step and step["expect"] is not None and not isinstance(step["expect"], dict):
            raise ConfigError("step expect must be an object", step=step_id)
        seen.add(step_id)
    return payload


def _expectation_failure(result: dict[str, Any], expect: Any) -> str | None:
    if expect is None:
        return None
    if not isinstance(expect, dict):
        return "expect must be an object"

    data = result.get("data") or result.get("read_back")
    raw = b""
    if isinstance(data, dict) and isinstance(data.get("base64"), str):
        raw = base64.b64decode(data["base64"])

    if "equals_hex" in expect:
        expected = parse_payload(hex_value=str(expect["equals_hex"]))
        if raw != expected:
            return f"expected data {expected.hex()}, received {raw.hex()}"
    if "contains_hex" in expect:
        expected = parse_payload(hex_value=str(expect["contains_hex"]))
        if expected not in raw:
            return f"expected data to contain {expected.hex()}, received {raw.hex()}"
    if "min_length" in expect:
        minimum = int(expect["min_length"])
        if len(raw) < minimum:
            return f"expected at least {minimum} bytes, received {len(raw)}"
    if "notification_count" in expect:
        expected_count = expect["notification_count"]
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            return "expect notification_count must be an integer"
        actual = int(result.get("notification_count", 0))
        if actual != expected_count:
            return f"expected {expected_count} notifications, received {actual}"
    if "notifications_at_least" in expect:
        minimum = int(expect["notifications_at_least"])
        actual = int(result.get("notification_count", 0))
        if actual < minimum:
            return f"expected at least {minimum} notifications, received {actual}"
    if "notifications_at_most" in expect:
        maximum = int(expect["notifications_at_most"])
        actual = int(result.get("notification_count", 0))
        if actual > maximum:
            return f"expected at most {maximum} notifications, received {actual}"

    notification_values: list[bytes] = []
    for item in result.get("notifications", []):
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("base64"), str):
            continue
        try:
            notification_values.append(base64.b64decode(data["base64"], validate=True))
        except (ValueError, binascii.Error):
            continue

    for key, label in (
        ("notifications_contain_utf8", "UTF-8"),
        ("notification_contains_utf8", "UTF-8"),
    ):
        if key not in expect:
            continue
        values = expect[key]
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return f"expect {key} must be a string or array of strings"
        decoded = [value.decode("utf-8", errors="replace") for value in notification_values]
        for value in values:
            if not any(value in item for item in decoded):
                return f"expected a notification containing {label} {value!r}"

    if "notification_utf8_counts" in expect:
        expected_counts = expect["notification_utf8_counts"]
        if not isinstance(expected_counts, dict) or not all(
            isinstance(value, str) for value in expected_counts
        ):
            return "expect notification_utf8_counts must map strings to integer counts"
        decoded = [value.decode("utf-8", errors="replace") for value in notification_values]
        for value, expected_count in expected_counts.items():
            if isinstance(expected_count, bool) or not isinstance(expected_count, int):
                return "expect notification_utf8_counts must map strings to integer counts"
            actual_count = sum(value in item for item in decoded)
            if actual_count != expected_count:
                return (
                    f"expected {expected_count} notifications containing UTF-8 {value!r}, "
                    f"received {actual_count}"
                )

    for key in ("notifications_contain_hex", "notification_contains_hex"):
        if key not in expect:
            continue
        values = expect[key]
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return f"expect {key} must be a string or array of strings"
        for value in values:
            try:
                expected = parse_payload(hex_value=value)
            except BleaError as exc:
                return f"invalid {key} value: {exc.message}"
            if not any(expected in item for item in notification_values):
                return f"expected a notification containing Hex {expected.hex()}"

    final_expect = expect.get("final_notification")
    if final_expect is not None:
        if not isinstance(final_expect, dict):
            return "expect final_notification must be an object"
        if not notification_values:
            return "expected a final notification, received none"
        final_value = notification_values[-1]
        final_text = final_value.decode("utf-8", errors="replace")
        if "utf8" in final_expect and final_text != str(final_expect["utf8"]):
            return (
                f"expected final notification UTF-8 {final_expect['utf8']!r}, "
                f"received {final_text!r}"
            )
        if "utf8_contains" in final_expect and str(final_expect["utf8_contains"]) not in final_text:
            return f"expected final notification to contain UTF-8 {final_expect['utf8_contains']!r}"
        if "utf8_endswith" in final_expect and not final_text.endswith(
            str(final_expect["utf8_endswith"])
        ):
            return (
                f"expected final notification UTF-8 to end with "
                f"{final_expect['utf8_endswith']!r}, received {final_text!r}"
            )
        for key, exact in (("hex", True), ("hex_contains", False)):
            if key not in final_expect:
                continue
            try:
                expected = parse_payload(hex_value=str(final_expect[key]))
            except BleaError as exc:
                return f"invalid final_notification {key} value: {exc.message}"
            matches = final_value == expected if exact else expected in final_value
            if not matches:
                comparator = "" if exact else " to contain"
                return f"expected final notification{comparator} Hex {expected.hex()}"

    cleanup_expect = expect.get("cleanup")
    if cleanup_expect is not None:
        if not isinstance(cleanup_expect, dict):
            return "expect cleanup must be an object"
        cleanup = result.get("cleanup")
        if not isinstance(cleanup, dict):
            return "expected cleanup evidence, but the operation returned none"
        for key, expected in cleanup_expect.items():
            if cleanup.get(key) != expected:
                return f"expected cleanup {key}={expected!r}, received {cleanup.get(key)!r}"

    if "service_count_at_least" in expect:
        minimum = int(expect["service_count_at_least"])
        actual = int(result.get("profile", {}).get("service_count", 0))
        if actual < minimum:
            return f"expected at least {minimum} services, received {actual}"
    return None


async def run_workflow(
    path: str | Path,
    *,
    allow_write: bool = False,
    manager: SessionManager | None = None,
) -> dict[str, Any]:
    workflow = load_workflow(path)
    manager = manager or SessionManager()
    timeout = float(workflow.get("timeout", 10.0))
    policy = workflow.get("policy") or {}
    if not isinstance(policy, dict):
        raise ConfigError("workflow policy must be an object")

    opened = await manager.open(workflow["device"], timeout=timeout)
    session_id = opened["session_id"]
    step_results: list[dict[str, Any]] = []
    successful: set[str] = set()
    exit_code = EXIT_OK
    failed_step: str | None = None

    try:
        for step in workflow["steps"]:
            step_id = step["id"]
            action = step["action"]
            missing = [item for item in step.get("requires", []) if item not in successful]
            if missing:
                result = {
                    "id": step_id,
                    "action": action,
                    "ok": False,
                    "reason": "guard_denied",
                    "message": "required earlier steps did not succeed",
                    "missing": missing,
                    "exit_code": EXIT_GUARD_DENIED,
                }
            else:
                try:
                    if action == "inspect":
                        operation = await manager.inspect(session_id)
                    elif action == "read":
                        operation = await manager.read(session_id, step["characteristic"])
                    elif action == "subscribe":
                        operation = await manager.subscribe(
                            session_id,
                            step["characteristic"],
                            duration=float(step.get("duration", 5.0)),
                        )
                    elif action == "write":
                        value = step["value"]
                        data = parse_payload(
                            hex_value=value.get("hex"),
                            text_value=value.get("text"),
                            base64_value=value.get("base64"),
                        )
                        operation = await manager.write(
                            session_id,
                            step["characteristic"],
                            data,
                            response=bool(step.get("response", True)),
                            allow_write=allow_write and policy.get("allow_write") is True,
                            confirm_device=policy.get("confirm_device"),
                            read_back=bool(step.get("read_back", False)),
                        )
                    else:
                        value = step["value"]
                        data = parse_payload(
                            hex_value=value.get("hex"),
                            text_value=value.get("text"),
                            base64_value=value.get("base64"),
                        )
                        operation = await manager.exchange(
                            session_id,
                            step["write_characteristic"],
                            step["notify_characteristic"],
                            data,
                            duration=float(step.get("duration", 5.0)),
                            response=bool(step.get("response", True)),
                            allow_write=allow_write and policy.get("allow_write") is True,
                            confirm_device=policy.get("confirm_device"),
                            read_back=bool(step.get("read_back", False)),
                        )
                    result = {"id": step_id, "action": action, **operation}
                    failure = _expectation_failure(operation, step.get("expect"))
                    if failure:
                        result.update(
                            {
                                "ok": False,
                                "reason": "assertion_failed",
                                "message": failure,
                                "exit_code": EXIT_ASSERTION_FAILED,
                            }
                        )
                except BleaError as exc:
                    result = {"id": step_id, "action": action, **exc.to_dict()}

            step_results.append(result)
            if result.get("ok"):
                successful.add(step_id)
                continue
            if exit_code == EXIT_OK:
                exit_code = int(result.get("exit_code", EXIT_ASSERTION_FAILED))
                failed_step = step_id
            if not step.get("continue_on_failure", False):
                break
    finally:
        await manager.close(session_id)

    return {
        "ok": exit_code == EXIT_OK,
        "operation": "workflow",
        "name": workflow["name"],
        "device": opened["device"],
        "steps": step_results,
        "failed_step": failed_step,
        "exit_code": exit_code,
    }
