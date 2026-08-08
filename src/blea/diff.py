from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blea.errors import EXIT_ASSERTION_FAILED, ConfigError
from blea.evidence import EVIDENCE_SCHEMA_VERSION, normalize_uuid, read_evidence

DIFF_SCHEMA_VERSION = "1.0"
DEFAULT_RSSI_TOLERANCE_DBM = 5.0
_BYTE_FIELDS = frozenset({"length", "hex", "base64", "utf8"})


def _pointer(path: str, segment: str) -> str:
    escaped = str(segment).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _is_byte_evidence(value: Any) -> bool:
    return isinstance(value, dict) and _BYTE_FIELDS.issubset(value)


def _characteristic_key(value: str, occurrence: int, total: int) -> str:
    return value if total == 1 else f"{value}#{occurrence + 1}"


def _uuid(value: Any) -> Any:
    return normalize_uuid(value) if isinstance(value, str) else value


def _handle(item: dict[str, Any]) -> int:
    value = item.get("handle", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("GATT evidence handle must be an integer", handle=value)
    return value


def _index_items(
    items: list[dict[str, Any]], *, uuid_field: str = "uuid", sort_handle: bool = True
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(uuid_field), str):
            raise ConfigError("GATT evidence contains an item without a UUID")
        uuid = normalize_uuid(item[uuid_field])
        groups.setdefault(uuid, []).append(item)

    result: dict[str, dict[str, Any]] = {}
    for uuid in sorted(groups):
        group = groups[uuid]
        if sort_handle:
            group = sorted(group, key=_handle)
        for occurrence, item in enumerate(group):
            key = _characteristic_key(uuid, occurrence, len(group))
            result[key] = dict(item)
    return result


def _properties(value: Any) -> dict[str, bool]:
    if not isinstance(value, list):
        return {}
    return {str(item).casefold(): True for item in value}


def _descriptor_snapshot(descriptor: dict[str, Any]) -> dict[str, Any]:
    return {
        "uuid": _uuid(descriptor.get("uuid")),
        "uuid_namespace": descriptor.get("uuid_namespace"),
        "handle": descriptor.get("handle"),
        "description": descriptor.get("description"),
    }


def _characteristic_snapshot(characteristic: dict[str, Any]) -> dict[str, Any]:
    descriptors = characteristic.get("descriptors")
    descriptor_items = descriptors if isinstance(descriptors, list) else []
    return {
        "uuid": _uuid(characteristic.get("uuid")),
        "uuid_namespace": characteristic.get("uuid_namespace"),
        "handle": characteristic.get("handle"),
        "description": characteristic.get("description"),
        "properties": _properties(characteristic.get("properties")),
        "descriptors": {
            key: _descriptor_snapshot(value)
            for key, value in _index_items(descriptor_items).items()
        },
    }


def _profile_snapshot(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {"services": {}}
    services = profile.get("services")
    service_items = services if isinstance(services, list) else []
    normalized: dict[str, dict[str, Any]] = {}
    for key, service in _index_items(service_items).items():
        characteristics = service.get("characteristics")
        characteristic_items = characteristics if isinstance(characteristics, list) else []
        normalized[key] = {
            "uuid": _uuid(service.get("uuid")),
            "uuid_namespace": service.get("uuid_namespace"),
            "handle": service.get("handle"),
            "description": service.get("description"),
            "characteristics": {
                char_key: _characteristic_snapshot(value)
                for char_key, value in _index_items(characteristic_items).items()
            },
        }
    return {"services": normalized}


def _advertisement_snapshot(device: dict[str, Any]) -> dict[str, Any]:
    service_uuids = device.get("service_uuids")
    services = service_uuids if isinstance(service_uuids, list) else []
    return {
        "name": device.get("name"),
        "local_name": device.get("local_name"),
        "tx_power": device.get("tx_power"),
        "service_uuids": {normalize_uuid(str(item)): True for item in services},
        "manufacturer_data": dict(device.get("manufacturer_data") or {}),
        "service_data": {
            normalize_uuid(str(key)): value
            for key, value in dict(device.get("service_data") or {}).items()
        },
    }


def _read_snapshot(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    occurrences: Counter[str] = Counter()
    reads = [event.get("data", {}) for event in events if isinstance(event.get("data"), dict)]
    totals = Counter(normalize_uuid(str(item.get("characteristic", ""))) for item in reads)
    for item in reads:
        characteristic = normalize_uuid(str(item.get("characteristic", "")))
        occurrence = occurrences[characteristic]
        occurrences[characteristic] += 1
        key = _characteristic_key(characteristic, occurrence, totals[characteristic])
        value: dict[str, Any] = {
            "characteristic": characteristic,
            "ok": bool(item.get("ok")),
        }
        if value["ok"]:
            value["value"] = item.get("value")
        else:
            error = item.get("error") or {}
            value["error"] = {
                "reason": error.get("reason"),
                "exit_code": error.get("exit_code"),
            }
        result[key] = value
    return result


def _notification_snapshot(events: list[dict[str, Any]]) -> dict[str, Any]:
    sequence: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for event in events:
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        characteristic = normalize_uuid(str(data.get("characteristic", "")))
        counts[characteristic] += 1
        sequence.append({"characteristic": characteristic, "value": data.get("value")})
    return {
        "count": len(sequence),
        "counts": dict(sorted(counts.items())),
        "sequence": sequence,
    }


def _error_snapshot(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        error = data.get("error") or {}
        result.append(
            {
                "operation": data.get("operation"),
                "characteristic": _uuid(data.get("characteristic")),
                "reason": error.get("reason"),
                "exit_code": error.get("exit_code"),
            }
        )
    return result


@dataclass(frozen=True)
class SemanticEvidence:
    capture_id: str
    identifier: str
    advertisement: dict[str, Any]
    rssi: Any
    profile: dict[str, Any]
    reads: dict[str, dict[str, Any]]
    notifications: dict[str, Any]
    errors: list[dict[str, Any]]

    def payload(self) -> dict[str, Any]:
        return {
            "advertisement": self.advertisement,
            "profile": self.profile,
            "reads": self.reads,
            "notifications": self.notifications,
            "errors": self.errors,
        }


def _semantic_evidence(path: str | Path) -> SemanticEvidence:
    events = read_evidence(path)
    advertisements = [event for event in events if event.get("kind") == "advertisement"]
    if len(advertisements) != 1:
        raise ConfigError(
            "diff requires exactly one advertisement event",
            path=str(Path(path).expanduser().resolve()),
            count=len(advertisements),
        )
    device = advertisements[0].get("device")
    if not isinstance(device, dict) or not isinstance(device.get("identifier"), str):
        raise ConfigError("diff advertisement is missing a device identifier")
    profiles = [event.get("data") for event in events if event.get("kind") == "profile"]
    if len(profiles) > 1:
        raise ConfigError(
            "diff supports at most one profile event",
            path=str(Path(path).expanduser().resolve()),
            count=len(profiles),
        )
    profile = profiles[0] if profiles and isinstance(profiles[0], dict) else None
    reads = [event for event in events if event.get("kind") == "read"]
    notifications = [event for event in events if event.get("kind") == "notification"]
    errors = [event for event in events if event.get("kind") == "error"]
    return SemanticEvidence(
        capture_id=str(events[0]["capture_id"]),
        identifier=str(device["identifier"]),
        advertisement=_advertisement_snapshot(device),
        rssi=device.get("rssi"),
        profile=_profile_snapshot(profile),
        reads=_read_snapshot(reads),
        notifications=_notification_snapshot(notifications),
        errors=_error_snapshot(errors),
    )


@dataclass
class _Changes:
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0

    def add(self, category: str, path: str, **values: Any) -> None:
        getattr(self, category).append({"path": path, **values})

    def report(self) -> dict[str, Any]:
        for collection in (self.added, self.removed, self.changed):
            collection.sort(key=lambda item: item["path"])
        total = len(self.added) + len(self.removed) + len(self.changed) + self.unchanged
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "total": total,
        }


def _compare(before: Any, after: Any, path: str, changes: _Changes) -> None:
    if _is_byte_evidence(before) or _is_byte_evidence(after):
        if before == after:
            changes.unchanged += 1
        else:
            changes.add("changed", path, before=before, after=after)
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after), key=str.casefold)
        for key in keys:
            child_path = _pointer(path, str(key))
            if key not in before:
                changes.add("added", child_path, after=after[key])
            elif key not in after:
                changes.add("removed", child_path, before=before[key])
            else:
                _compare(before[key], after[key], child_path, changes)
        return
    if isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for index in range(common):
            _compare(before[index], after[index], _pointer(path, str(index)), changes)
        for index in range(common, len(before)):
            changes.add("removed", _pointer(path, str(index)), before=before[index])
        for index in range(common, len(after)):
            changes.add("added", _pointer(path, str(index)), after=after[index])
        return
    if type(before) is type(after) and before == after:
        changes.unchanged += 1
    else:
        changes.add("changed", path, before=before, after=after)


def _compare_rssi(before: Any, after: Any, tolerance: float, changes: _Changes) -> None:
    path = "/advertisement/rssi"
    if before == after:
        changes.unchanged += 1
        return
    numeric = all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in (before, after)
    )
    if numeric and abs(float(before) - float(after)) <= tolerance:
        changes.unchanged += 1
        return
    changes.add(
        "changed",
        path,
        before=before,
        after=after,
        delta_dbm=(float(after) - float(before)) if numeric else None,
        tolerance_dbm=tolerance,
    )


def diff_evidence(
    before: str | Path,
    after: str | Path,
    *,
    rssi_tolerance: float = DEFAULT_RSSI_TOLERANCE_DBM,
    strict_rssi: bool = False,
    allow_different_devices: bool = False,
    fail_on_change: bool = False,
) -> dict[str, Any]:
    if isinstance(rssi_tolerance, bool) or not isinstance(rssi_tolerance, (int, float)):
        raise ConfigError("RSSI tolerance must be a non-negative number")
    if not math.isfinite(rssi_tolerance) or rssi_tolerance < 0:
        raise ConfigError("RSSI tolerance must be finite and non-negative")
    tolerance = 0.0 if strict_rssi else float(rssi_tolerance)
    before_path = Path(before).expanduser().resolve()
    after_path = Path(after).expanduser().resolve()
    before_data = _semantic_evidence(before_path)
    after_data = _semantic_evidence(after_path)

    before_identifier = before_data.identifier.casefold()
    after_identifier = after_data.identifier.casefold()
    identity_match = before_identifier == after_identifier
    if not identity_match and not allow_different_devices:
        raise ConfigError(
            "evidence devices differ; pass allow_different_devices to compare explicitly",
            before_identifier=before_data.identifier,
            after_identifier=after_data.identifier,
        )

    changes = _Changes()
    if identity_match:
        changes.unchanged += 1
    else:
        changes.add(
            "changed",
            "/device/identifier",
            before=before_data.identifier,
            after=after_data.identifier,
        )
    _compare_rssi(before_data.rssi, after_data.rssi, tolerance, changes)
    _compare(before_data.payload(), after_data.payload(), "", changes)
    detail = changes.report()
    summary = {
        "added": len(detail["added"]),
        "removed": len(detail["removed"]),
        "changed": len(detail["changed"]),
        "unchanged": detail["unchanged"],
        "total": detail["total"],
    }
    has_changes = bool(summary["added"] or summary["removed"] or summary["changed"])
    return {
        "ok": True,
        "operation": "diff",
        "diff_schema_version": DIFF_SCHEMA_VERSION,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": "changed" if has_changes else "identical",
        "has_changes": has_changes,
        "before": {
            "path": str(before_path),
            "capture_id": before_data.capture_id,
            "identifier": before_data.identifier,
        },
        "after": {
            "path": str(after_path),
            "capture_id": after_data.capture_id,
            "identifier": after_data.identifier,
        },
        "policy": {
            "rssi_tolerance_dbm": tolerance,
            "strict_rssi": strict_rssi,
            "allow_different_devices": allow_different_devices,
            "ignored_fields": [
                "capture_id",
                "timestamp",
                "source.blea_version",
                "source.platform",
                "source.python",
                "source.bleak_version",
                "source.backend",
                "manifest.data.parameters",
                "summary.data",
                "notification.timestamp",
                "observe.sample_duration_seconds",
            ],
        },
        "summary": summary,
        "changes": {
            "added": detail["added"],
            "removed": detail["removed"],
            "changed": detail["changed"],
        },
        "exit_code": EXIT_ASSERTION_FAILED if fail_on_change and has_changes else 0,
    }
