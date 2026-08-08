from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from blea.errors import ConfigError
from blea.models import utc_now

EVIDENCE_SCHEMA_VERSION = "1.0"
EVIDENCE_EXTENSION = ".blea.jsonl"
EVIDENCE_KINDS = frozenset(
    {"manifest", "advertisement", "profile", "read", "notification", "error", "summary"}
)
_BYTE_FIELDS = frozenset({"length", "hex", "base64", "utf8"})
_SOURCE_FIELDS = frozenset({"blea_version", "platform", "python", "bleak_version", "backend"})
_SUMMARY_STATUSES = frozenset({"complete", "complete_with_failures", "failed"})


def normalize_uuid(value: str) -> str:
    candidate = str(value).strip().casefold()
    compact = candidate.removeprefix("0x")
    if len(compact) in {4, 8}:
        try:
            return f"{int(compact, 16):08x}-0000-1000-8000-00805f9b34fb"
        except ValueError:
            pass
    try:
        return str(UUID(candidate)).lower()
    except (AttributeError, ValueError):
        return candidate


@dataclass
class IdentifierRedactor:
    enabled: bool = False
    _tokens: dict[str, str] = field(default_factory=dict, repr=False)

    def identifier(self, value: str) -> str:
        if not self.enabled:
            return value
        key = value.casefold()
        if key not in self._tokens:
            self._tokens[key] = f"redacted:device-{len(self._tokens) + 1}"
        return self._tokens[key]

    def selector(self, value: str) -> str:
        if not self.enabled:
            return value
        kind, separator, target = value.partition(":")
        if separator and kind.casefold() == "id":
            return f"id:{self.identifier(target)}"
        return value


def normalize_device(
    device: dict[str, Any], redactor: IdentifierRedactor | None = None
) -> dict[str, Any]:
    result = dict(device)
    if isinstance(result.get("identifier"), str) and redactor is not None:
        result["identifier"] = redactor.identifier(result["identifier"])
    if isinstance(result.get("service_uuids"), list):
        result["service_uuids"] = sorted(
            (normalize_uuid(value) for value in result["service_uuids"]), key=str.casefold
        )
    service_data = result.get("service_data")
    if isinstance(service_data, dict):
        result["service_data"] = {
            normalize_uuid(str(key)): value
            for key, value in sorted(service_data.items(), key=lambda item: str(item[0]).casefold())
        }
    return result


def _normalize_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    result = dict(descriptor)
    if isinstance(result.get("uuid"), str):
        result["uuid"] = normalize_uuid(result["uuid"])
    return result


def _normalize_characteristic(characteristic: dict[str, Any]) -> dict[str, Any]:
    result = dict(characteristic)
    if isinstance(result.get("uuid"), str):
        result["uuid"] = normalize_uuid(result["uuid"])
    if isinstance(result.get("properties"), list):
        result["properties"] = sorted(
            (str(value).casefold() for value in result["properties"]), key=str.casefold
        )
    descriptors = result.get("descriptors")
    if isinstance(descriptors, list):
        result["descriptors"] = sorted(
            (_normalize_descriptor(item) for item in descriptors if isinstance(item, dict)),
            key=lambda item: (str(item.get("uuid", "")), int(item.get("handle", 0))),
        )
    return result


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    result = dict(profile)
    services = result.get("services")
    if not isinstance(services, list):
        return result
    normalized_services: list[dict[str, Any]] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        normalized = dict(service)
        if isinstance(normalized.get("uuid"), str):
            normalized["uuid"] = normalize_uuid(normalized["uuid"])
        characteristics = normalized.get("characteristics")
        if isinstance(characteristics, list):
            normalized["characteristics"] = sorted(
                (
                    _normalize_characteristic(item)
                    for item in characteristics
                    if isinstance(item, dict)
                ),
                key=lambda item: (str(item.get("uuid", "")), int(item.get("handle", 0))),
            )
        normalized_services.append(normalized)
    result["services"] = sorted(
        normalized_services,
        key=lambda item: (str(item.get("uuid", "")), int(item.get("handle", 0))),
    )
    return result


def _normalize_characteristic_references(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if key == "characteristic" and isinstance(child, str):
                normalized[key] = normalize_uuid(child)
            else:
                normalized[key] = _normalize_characteristic_references(child)
        return normalized
    if isinstance(value, list):
        return [_normalize_characteristic_references(item) for item in value]
    return value


def normalize_data(
    kind: str, data: dict[str, Any], redactor: IdentifierRedactor | None = None
) -> dict[str, Any]:
    result = dict(data)
    if kind == "manifest":
        parameters = result.get("parameters")
        if isinstance(parameters, dict):
            normalized_parameters = dict(parameters)
            selector = normalized_parameters.get("selector")
            if isinstance(selector, str) and redactor is not None:
                normalized_parameters["selector"] = redactor.selector(selector)
            result["parameters"] = normalized_parameters
        return result
    if kind == "profile":
        return normalize_profile(result)
    return _normalize_characteristic_references(result)


@dataclass
class EvidenceWriter:
    capture_id: str = field(default_factory=lambda: str(uuid4()))
    redact_identifiers: bool = False
    clock: Callable[[], str] = utc_now
    events: list[dict[str, Any]] = field(default_factory=list)
    redactor: IdentifierRedactor = field(init=False)

    def __post_init__(self) -> None:
        self.redactor = IdentifierRedactor(self.redact_identifiers)

    def add(
        self,
        kind: str,
        *,
        source: dict[str, Any] | None = None,
        device: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        if kind not in EVIDENCE_KINDS:
            raise ConfigError("unsupported evidence event kind", kind=kind)
        event: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "capture_id": self.capture_id,
            "sequence": len(self.events) + 1,
            "timestamp": timestamp or self.clock(),
            "kind": kind,
        }
        if source is not None:
            event["source"] = dict(source)
        if device is not None:
            event["device"] = normalize_device(device, self.redactor)
        if data is not None:
            event["data"] = normalize_data(kind, data, self.redactor)
        self.events.append(event)
        return event

    def write(self, path: str | Path) -> Path:
        return write_evidence(path, self.events)


def _validate_timestamp(value: Any, *, line: int) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ConfigError("evidence timestamp must be an ISO-8601 UTC string", line=line)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ConfigError("evidence timestamp is invalid", line=line) from exc
    if parsed.tzinfo is None:
        raise ConfigError("evidence timestamp must include UTC", line=line)


def _require_fields(
    value: dict[str, Any], required: set[str] | frozenset[str], *, line: int, context: str
) -> None:
    missing = sorted(required.difference(value))
    if missing:
        raise ConfigError(f"{context} is missing required fields", line=line, missing=missing)


def _validate_bytes(value: dict[str, Any], *, path: str) -> None:
    if not _BYTE_FIELDS.issubset(value):
        return
    length = value["length"]
    hex_value = value["hex"]
    base64_value = value["base64"]
    utf8_value = value["utf8"]
    if isinstance(length, bool) or not isinstance(length, int) or length < 0:
        raise ConfigError("byte evidence length must be a non-negative integer", path=path)
    if not isinstance(hex_value, str) or len(hex_value) % 2:
        raise ConfigError("byte evidence hex must contain an even number of digits", path=path)
    try:
        raw = bytes.fromhex(hex_value)
        decoded = base64.b64decode(base64_value, validate=True)
    except (TypeError, ValueError, binascii.Error) as exc:
        raise ConfigError("byte evidence encoding is invalid", path=path) from exc
    if len(raw) != length or decoded != raw:
        raise ConfigError("byte evidence length and encodings disagree", path=path)
    if not isinstance(utf8_value, str) or utf8_value != raw.decode("utf-8", errors="replace"):
        raise ConfigError("byte evidence UTF-8 representation disagrees", path=path)


def _validate_nested(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        _validate_bytes(value, path=path)
        for key, child in value.items():
            _validate_nested(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_nested(child, path=f"{path}[{index}]")


def _validate_error(value: Any, *, line: int) -> None:
    if not isinstance(value, dict):
        raise ConfigError("evidence error must be an object", line=line)
    _require_fields(value, {"reason", "message", "exit_code"}, line=line, context="error")
    if not isinstance(value["reason"], str) or not value["reason"]:
        raise ConfigError("evidence error reason must be a non-empty string", line=line)
    if not isinstance(value["message"], str) or not value["message"]:
        raise ConfigError("evidence error message must be a non-empty string", line=line)
    if isinstance(value["exit_code"], bool) or not isinstance(value["exit_code"], int):
        raise ConfigError("evidence error exit_code must be an integer", line=line)


def _validate_device(value: dict[str, Any], *, line: int) -> None:
    _require_fields(
        value,
        {
            "identifier",
            "name",
            "local_name",
            "rssi",
            "tx_power",
            "service_uuids",
            "manufacturer_data",
            "service_data",
        },
        line=line,
        context="device evidence",
    )
    if not isinstance(value["identifier"], str) or not value["identifier"]:
        raise ConfigError("device identifier must be a non-empty string", line=line)
    if not isinstance(value["service_uuids"], list) or not all(
        isinstance(item, str) for item in value["service_uuids"]
    ):
        raise ConfigError("device service_uuids must be a string array", line=line)
    if not isinstance(value["manufacturer_data"], dict) or not isinstance(
        value["service_data"], dict
    ):
        raise ConfigError("device advertisement payloads must be objects", line=line)


def _validate_manifest(event: dict[str, Any], *, line: int) -> None:
    source = event.get("source")
    data = event.get("data")
    if not isinstance(source, dict) or not isinstance(data, dict):
        raise ConfigError("manifest must include source and data objects", line=line)
    _require_fields(source, _SOURCE_FIELDS, line=line, context="manifest source")
    _require_fields(data, {"parameters", "read_only"}, line=line, context="manifest data")
    if not isinstance(data["parameters"], dict):
        raise ConfigError("manifest parameters must be an object", line=line)
    if data["read_only"] is not True:
        raise ConfigError("capture evidence must declare read_only=true", line=line)


def _validate_read(data: dict[str, Any], *, line: int) -> None:
    _require_fields(data, {"characteristic", "ok"}, line=line, context="read event")
    if not isinstance(data["characteristic"], str) or not data["characteristic"]:
        raise ConfigError("read event characteristic must be a non-empty string", line=line)
    if not isinstance(data["ok"], bool):
        raise ConfigError("read event ok must be a boolean", line=line)
    if data["ok"]:
        if not isinstance(data.get("value"), dict):
            raise ConfigError("successful read event must include value", line=line)
    else:
        _validate_error(data.get("error"), line=line)


def _validate_notification(data: dict[str, Any], *, line: int) -> None:
    _require_fields(data, {"characteristic", "value"}, line=line, context="notification event")
    if not isinstance(data["characteristic"], str) or not data["characteristic"]:
        raise ConfigError("notification characteristic must be a non-empty string", line=line)
    if not isinstance(data["value"], dict):
        raise ConfigError("notification event value must be an object", line=line)


def _validate_summary(data: dict[str, Any], *, line: int) -> None:
    _require_fields(
        data,
        {"status", "complete", "event_count", "event_counts"},
        line=line,
        context="summary",
    )
    if not isinstance(data["status"], str) or data["status"] not in _SUMMARY_STATUSES:
        raise ConfigError("summary status is invalid", line=line, status=data["status"])
    if data["complete"] is not True:
        raise ConfigError("persisted evidence summary must declare complete=true", line=line)
    if isinstance(data["event_count"], bool) or not isinstance(data["event_count"], int):
        raise ConfigError("summary event_count must be an integer", line=line)
    if not isinstance(data["event_counts"], dict):
        raise ConfigError("summary event_counts must be an object", line=line)


def validate_events(
    events: Iterable[dict[str, Any]], *, require_complete: bool = True
) -> dict[str, Any]:
    records = list(events)
    if not records:
        raise ConfigError("evidence file must contain at least one event")
    capture_id: str | None = None
    kinds: Counter[str] = Counter()
    for expected_sequence, event in enumerate(records, start=1):
        line = expected_sequence
        if not isinstance(event, dict):
            raise ConfigError("evidence event must be an object", line=line)
        required = {"schema_version", "capture_id", "sequence", "timestamp", "kind"}
        _require_fields(event, required, line=line, context="evidence event")
        if event["schema_version"] != EVIDENCE_SCHEMA_VERSION:
            raise ConfigError("unsupported evidence schema version", line=line)
        if not isinstance(event["capture_id"], str) or not event["capture_id"].strip():
            raise ConfigError("evidence capture_id must be a non-empty string", line=line)
        try:
            UUID(event["capture_id"])
        except ValueError as exc:
            raise ConfigError("evidence capture_id must be a UUID", line=line) from exc
        if capture_id is None:
            capture_id = event["capture_id"]
        elif event["capture_id"] != capture_id:
            raise ConfigError("evidence capture_id changed within one file", line=line)
        if isinstance(event["sequence"], bool) or event["sequence"] != expected_sequence:
            raise ConfigError("evidence sequence must be contiguous and start at 1", line=line)
        _validate_timestamp(event["timestamp"], line=line)
        kind = event["kind"]
        if not isinstance(kind, str) or kind not in EVIDENCE_KINDS:
            raise ConfigError("unsupported evidence event kind", line=line, kind=kind)
        kinds[kind] += 1
        if kind == "manifest":
            if line != 1:
                raise ConfigError("manifest must be the first event", line=line)
            _validate_manifest(event, line=line)
        elif kind == "advertisement":
            if not isinstance(event.get("device"), dict):
                raise ConfigError("advertisement must include device evidence", line=line)
            _validate_device(event["device"], line=line)
        else:
            data = event.get("data")
            if not isinstance(data, dict):
                raise ConfigError(f"{kind} event must include data", line=line)
            if kind == "profile" and not isinstance(data.get("services"), list):
                raise ConfigError("profile event must include services", line=line)
            if kind == "read":
                _validate_read(data, line=line)
            elif kind == "notification":
                _validate_notification(data, line=line)
            elif kind == "error":
                if not isinstance(data.get("operation"), str) or not data["operation"]:
                    raise ConfigError("error event must include operation", line=line)
                _validate_error(data.get("error"), line=line)
            elif kind == "summary":
                _validate_summary(data, line=line)
        _validate_nested(event, path=f"line[{line}]")
    if records[0]["kind"] != "manifest" or kinds["manifest"] != 1:
        raise ConfigError("evidence must contain exactly one leading manifest")
    complete = records[-1]["kind"] == "summary" and kinds["summary"] == 1
    if kinds["summary"] > 1 or (kinds["summary"] == 1 and records[-1]["kind"] != "summary"):
        raise ConfigError("evidence summary must be unique and final")
    if require_complete and not complete:
        raise ConfigError("evidence is incomplete because its final summary is missing")
    if complete:
        summary = records[-1]["data"]
        expected_counts = dict(sorted(kinds.items()))
        if summary["event_count"] != len(records):
            raise ConfigError("summary event_count does not match the file", line=len(records))
        if summary["event_counts"] != expected_counts:
            raise ConfigError("summary event_counts do not match the file", line=len(records))
    return {
        "capture_id": capture_id,
        "event_count": len(records),
        "kinds": dict(sorted(kinds.items())),
        "complete": complete,
    }


def _load_evidence(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError("unable to read evidence file", path=str(source)) from exc
    if not lines:
        raise ConfigError("evidence file is empty", path=str(source))
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ConfigError("evidence file contains a blank line", line=line_number)
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError("evidence JSONL line is invalid", line=line_number) from exc
        events.append(value)
    return events


def validate_evidence(path: str | Path, *, require_complete: bool = True) -> dict[str, Any]:
    return validate_events(_load_evidence(path), require_complete=require_complete)


def read_evidence(path: str | Path, *, require_complete: bool = True) -> list[dict[str, Any]]:
    events = _load_evidence(path)
    validate_events(events, require_complete=require_complete)
    return events


def write_evidence(path: str | Path, events: Iterable[dict[str, Any]]) -> Path:
    destination = Path(path).expanduser().resolve()
    if not destination.name.casefold().endswith(EVIDENCE_EXTENSION):
        raise ConfigError(
            "evidence output must use the .blea.jsonl extension", path=str(destination)
        )
    if not destination.parent.exists():
        raise ConfigError("evidence output directory does not exist", path=str(destination.parent))
    records = list(events)
    validate_events(records)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            for event in records:
                handle.write(
                    json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                )
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except OSError as exc:
        raise ConfigError("unable to write evidence file", path=str(destination)) from exc
    finally:
        if temporary_path and os.path.exists(temporary_path):
            with suppress(OSError):
                os.unlink(temporary_path)
    return destination
