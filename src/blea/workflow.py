from __future__ import annotations

import base64
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
        if action not in {"inspect", "read", "subscribe", "write"}:
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
        if action == "write":
            if step.get("dangerous") is not True:
                raise ConfigError("write steps must declare dangerous: true", step=step_id)
            if not requires:
                raise ConfigError("write steps must require successful earlier steps", step=step_id)
            value = step.get("value")
            if not isinstance(value, dict):
                raise ConfigError("write step value must be an object", step=step_id)
            parse_payload(
                hex_value=value.get("hex"),
                text_value=value.get("text"),
                base64_value=value.get("base64"),
            )
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
    if "notifications_at_least" in expect:
        minimum = int(expect["notifications_at_least"])
        actual = int(result.get("notification_count", 0))
        if actual < minimum:
            return f"expected at least {minimum} notifications, received {actual}"
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
                    else:
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
