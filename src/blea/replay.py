from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from blea.errors import BleaError, ConfigError, GuardDeniedError, ReplayMissError
from blea.evidence import EVIDENCE_SCHEMA_VERSION, normalize_uuid, read_evidence
from blea.models import (
    CharacteristicInfo,
    DescriptorInfo,
    DiscoveredDevice,
    GattProfile,
    Notification,
    ServiceInfo,
)

REPLAY_SCHEMA_VERSION = "1.0"
REPLAY_OPERATIONS = frozenset({"scan", "inspect", "probe", "read", "subscribe", "observe", "run"})


def _stable_workflow_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "replay-session-1"
                if key == "session_id"
                else 0
                if key == "duration_ms"
                else _stable_workflow_result(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_stable_workflow_result(item) for item in value]
    return value


def _bytes(value: Any, *, context: str) -> bytes:
    if not isinstance(value, dict) or not isinstance(value.get("hex"), str):
        raise ConfigError("replay byte evidence is missing", context=context)
    try:
        return bytes.fromhex(value["hex"])
    except ValueError as exc:
        raise ConfigError("replay byte evidence is invalid", context=context) from exc


def _string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("replay evidence requires a non-empty string", context=context)
    return value


def _handle(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("replay GATT handle must be an integer", context=context, handle=value)
    return value


def _timestamp(value: Any, *, context: str) -> datetime:
    text = _string(value, context=context)
    try:
        return datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ConfigError("replay notification timestamp is invalid", context=context) from exc


@dataclass(frozen=True)
class RecordedError:
    message: str
    exit_code: int
    reason: str
    details: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any, *, context: str) -> RecordedError:
        if not isinstance(value, dict):
            raise ConfigError("replay evidence error is missing", context=context)
        message = _string(value.get("message"), context=f"{context}.message")
        reason = _string(value.get("reason"), context=f"{context}.reason")
        exit_code = value.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ConfigError("replay evidence error exit_code must be an integer", context=context)
        details = value.get("details") or {}
        if not isinstance(details, dict):
            raise ConfigError("replay evidence error details must be an object", context=context)
        return cls(message, exit_code, reason, dict(details))

    def exception(self) -> BleaError:
        return BleaError(self.message, self.exit_code, self.reason, dict(self.details))


@dataclass(frozen=True)
class TimedNotification:
    notification: Notification
    offset_seconds: float


@dataclass(frozen=True)
class ReplayEvidence:
    path: Path
    capture_id: str
    device: DiscoveredDevice
    profile: GattProfile | None
    reads: dict[str, bytes | RecordedError]
    notifications: tuple[TimedNotification, ...]
    subscriptions: dict[str, RecordedError | None]
    cleanup: dict[str, Any] | None
    source: dict[str, Any]
    status: str

    @classmethod
    def load(cls, path: str | Path) -> ReplayEvidence:
        source_path = Path(path).expanduser().resolve()
        events = read_evidence(source_path)
        advertisements = [event for event in events if event.get("kind") == "advertisement"]
        if len(advertisements) != 1:
            raise ConfigError(
                "replay requires exactly one advertisement event",
                path=str(source_path),
                count=len(advertisements),
            )
        device = _load_device(advertisements[0].get("device"))

        profile_events = [event.get("data") for event in events if event.get("kind") == "profile"]
        if len(profile_events) > 1:
            raise ConfigError(
                "replay supports at most one profile event",
                path=str(source_path),
                count=len(profile_events),
            )
        profile = _load_profile(profile_events[0]) if profile_events else None
        reads = _load_reads(events)
        notifications = _load_notifications(events)
        subscriptions = _load_subscriptions(events)
        cleanup = _load_cleanup(events)
        manifest = events[0]
        summary = events[-1].get("data", {})
        return cls(
            path=source_path,
            capture_id=str(manifest["capture_id"]),
            device=device,
            profile=profile,
            reads=reads,
            notifications=notifications,
            subscriptions=subscriptions,
            cleanup=cleanup,
            source=dict(manifest.get("source") or {}),
            status=str(summary.get("status", "complete")),
        )


def _load_device(value: Any) -> DiscoveredDevice:
    if not isinstance(value, dict):
        raise ConfigError("replay advertisement device is missing")
    manufacturer_data: dict[int, bytes] = {}
    for key, payload in value.get("manufacturer_data", {}).items():
        try:
            company_id = int(str(key), 0)
        except ValueError as exc:
            raise ConfigError("replay manufacturer identifier is invalid", identifier=key) from exc
        if not 0 <= company_id <= 0xFFFF:
            raise ConfigError("replay manufacturer identifier is out of range", identifier=key)
        manufacturer_data[company_id] = _bytes(
            payload, context=f"advertisement.manufacturer_data.{key}"
        )
    service_data = {
        normalize_uuid(str(key)): _bytes(payload, context=f"advertisement.service_data.{key}")
        for key, payload in value.get("service_data", {}).items()
    }
    service_uuids = value.get("service_uuids")
    if not isinstance(service_uuids, list) or not all(
        isinstance(item, str) for item in service_uuids
    ):
        raise ConfigError("replay advertisement service UUIDs must be a string array")
    return DiscoveredDevice(
        identifier=_string(value.get("identifier"), context="advertisement.identifier"),
        name=value.get("name"),
        local_name=value.get("local_name"),
        rssi=value.get("rssi"),
        tx_power=value.get("tx_power"),
        service_uuids=tuple(normalize_uuid(item) for item in service_uuids),
        manufacturer_data=manufacturer_data,
        service_data=service_data,
    )


def _load_profile(value: Any) -> GattProfile:
    if not isinstance(value, dict) or not isinstance(value.get("services"), list):
        raise ConfigError("replay profile is invalid")
    services: list[ServiceInfo] = []
    for service_index, service in enumerate(value["services"]):
        context = f"profile.services[{service_index}]"
        if not isinstance(service, dict) or not isinstance(service.get("characteristics"), list):
            raise ConfigError("replay service is invalid", context=context)
        characteristics: list[CharacteristicInfo] = []
        for char_index, characteristic in enumerate(service["characteristics"]):
            char_context = f"{context}.characteristics[{char_index}]"
            if not isinstance(characteristic, dict):
                raise ConfigError("replay characteristic is invalid", context=char_context)
            properties = characteristic.get("properties")
            descriptors = characteristic.get("descriptors")
            if not isinstance(properties, list) or not all(
                isinstance(item, str) for item in properties
            ):
                raise ConfigError(
                    "replay characteristic properties are invalid", context=char_context
                )
            if not isinstance(descriptors, list):
                raise ConfigError(
                    "replay characteristic descriptors are invalid", context=char_context
                )
            descriptor_models: list[DescriptorInfo] = []
            for descriptor_index, descriptor in enumerate(descriptors):
                descriptor_context = f"{char_context}.descriptors[{descriptor_index}]"
                if not isinstance(descriptor, dict):
                    raise ConfigError("replay descriptor is invalid", context=descriptor_context)
                descriptor_models.append(
                    DescriptorInfo(
                        uuid=normalize_uuid(
                            _string(descriptor.get("uuid"), context=f"{descriptor_context}.uuid")
                        ),
                        handle=_handle(
                            descriptor.get("handle"), context=f"{descriptor_context}.handle"
                        ),
                        description=descriptor.get("description"),
                    )
                )
            characteristics.append(
                CharacteristicInfo(
                    uuid=normalize_uuid(
                        _string(characteristic.get("uuid"), context=f"{char_context}.uuid")
                    ),
                    handle=_handle(characteristic.get("handle"), context=f"{char_context}.handle"),
                    properties=tuple(sorted(item.casefold() for item in properties)),
                    description=characteristic.get("description"),
                    descriptors=tuple(descriptor_models),
                )
            )
        services.append(
            ServiceInfo(
                uuid=normalize_uuid(_string(service.get("uuid"), context=f"{context}.uuid")),
                handle=_handle(service.get("handle"), context=f"{context}.handle"),
                description=service.get("description"),
                characteristics=tuple(characteristics),
            )
        )
    return GattProfile(tuple(services))


def _load_reads(events: list[dict[str, Any]]) -> dict[str, bytes | RecordedError]:
    reads: dict[str, bytes | RecordedError] = {}
    for event in events:
        if event.get("kind") != "read":
            continue
        data = event.get("data", {})
        characteristic = normalize_uuid(
            _string(data.get("characteristic"), context="read.characteristic")
        )
        if characteristic in reads:
            raise ConfigError(
                "replay evidence contains duplicate read events", characteristic=characteristic
            )
        if data.get("ok") is True:
            reads[characteristic] = _bytes(data.get("value"), context=f"read.{characteristic}")
        else:
            reads[characteristic] = RecordedError.from_dict(
                data.get("error"), context=f"read.{characteristic}.error"
            )
    return reads


def _load_notifications(events: list[dict[str, Any]]) -> tuple[TimedNotification, ...]:
    captured: list[tuple[int, datetime, str, bytes, str]] = []
    for index, event in enumerate(events):
        if event.get("kind") != "notification":
            continue
        data = event.get("data", {})
        characteristic = normalize_uuid(
            _string(data.get("characteristic"), context="notification.characteristic")
        )
        timestamp_text = _string(event.get("timestamp"), context="notification.timestamp")
        captured.append(
            (
                index,
                _timestamp(timestamp_text, context="notification.timestamp"),
                characteristic,
                _bytes(data.get("value"), context=f"notification.{characteristic}"),
                timestamp_text,
            )
        )
    if not captured:
        return ()
    captured.sort(key=lambda item: (item[1], item[0]))
    origin = captured[0][1]
    return tuple(
        TimedNotification(
            Notification(characteristic, payload, timestamp_text),
            max((timestamp - origin).total_seconds(), 0.0),
        )
        for _, timestamp, characteristic, payload, timestamp_text in captured
    )


def _load_subscriptions(events: list[dict[str, Any]]) -> dict[str, RecordedError | None]:
    results: dict[str, RecordedError | None] = {}
    summary = events[-1].get("data", {})
    observation = summary.get("observation") if isinstance(summary, dict) else None
    subscriptions = observation.get("subscriptions") if isinstance(observation, dict) else None
    if isinstance(subscriptions, list):
        for index, item in enumerate(subscriptions):
            if not isinstance(item, dict):
                raise ConfigError("replay observation subscription is invalid", index=index)
            characteristic = normalize_uuid(
                _string(
                    item.get("characteristic"),
                    context=f"summary.observation.subscriptions[{index}].characteristic",
                )
            )
            results[characteristic] = (
                None
                if item.get("ok") is True
                else RecordedError.from_dict(
                    item.get("error"),
                    context=f"summary.observation.subscriptions[{index}].error",
                )
            )
    for event in events:
        if event.get("kind") != "error":
            continue
        data = event.get("data", {})
        if data.get("operation") != "subscribe":
            continue
        error = RecordedError.from_dict(data.get("error"), context="subscribe.error")
        characteristic = data.get("characteristic") or error.details.get("characteristic")
        if isinstance(characteristic, str):
            results.setdefault(normalize_uuid(characteristic), error)
    return results


def _load_cleanup(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    summary = events[-1].get("data", {})
    observation = summary.get("observation") if isinstance(summary, dict) else None
    cleanup = observation.get("cleanup") if isinstance(observation, dict) else None
    if cleanup is None:
        return None
    if not isinstance(cleanup, dict) or not isinstance(cleanup.get("ok"), bool):
        raise ConfigError("replay observation cleanup is invalid")
    counts: dict[str, int] = {}
    for key in ("started_count", "stopped_count", "failure_count"):
        value = cleanup.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError("replay cleanup count is invalid", field=key, value=value)
        counts[key] = value
    errors = cleanup.get("errors")
    if not isinstance(errors, list):
        raise ConfigError("replay cleanup errors must be an array")
    validated_errors: list[dict[str, Any]] = []
    for index, error in enumerate(errors):
        recorded = RecordedError.from_dict(
            error, context=f"summary.observation.cleanup.errors[{index}]"
        )
        validated_errors.append(recorded.exception().to_dict())
    if counts["failure_count"] != len(validated_errors):
        raise ConfigError(
            "replay cleanup failure count does not match errors",
            failure_count=counts["failure_count"],
            error_count=len(validated_errors),
        )
    if counts["stopped_count"] + counts["failure_count"] != counts["started_count"]:
        raise ConfigError("replay cleanup counts are inconsistent", **counts)
    if cleanup["ok"] != (counts["failure_count"] == 0):
        raise ConfigError("replay cleanup status is inconsistent")
    return {"ok": cleanup["ok"], **counts, "errors": validated_errors}


class ReplayConnection:
    def __init__(self, backend: ReplayBackend) -> None:
        self.backend = backend
        self.connected = False

    def _require_connected(self) -> None:
        if not self.connected:
            raise ConfigError("replay connection is not active")

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def inspect(self) -> GattProfile:
        self._require_connected()
        if self.backend.evidence.profile is None:
            raise ReplayMissError(
                "GATT profile is absent from replay evidence",
                evidence=str(self.backend.evidence.path),
            )
        return self.backend.evidence.profile

    async def read(self, characteristic: str) -> bytes:
        self._require_connected()
        normalized = normalize_uuid(characteristic)
        try:
            recorded = self.backend.evidence.reads[normalized]
        except KeyError as exc:
            raise ReplayMissError(
                "characteristic read is absent from replay evidence",
                characteristic=normalized,
                evidence=str(self.backend.evidence.path),
                available=sorted(self.backend.evidence.reads),
            ) from exc
        if isinstance(recorded, RecordedError):
            raise recorded.exception()
        return recorded

    def _profile_characteristic(self, characteristic: str) -> CharacteristicInfo | None:
        profile = self.backend.evidence.profile
        if profile is None:
            return None
        normalized = normalize_uuid(characteristic)
        return next(
            (
                item
                for service in profile.services
                for item in service.characteristics
                if item.uuid.casefold() == normalized.casefold()
            ),
            None,
        )

    def _subscription_error(self, characteristic: str) -> RecordedError | None:
        return self.backend.evidence.subscriptions.get(normalize_uuid(characteristic))

    async def _play(
        self, characteristics: tuple[str, ...], *, duration: float
    ) -> list[Notification]:
        selected = {normalize_uuid(item) for item in characteristics}
        records = [
            item
            for item in self.backend.evidence.notifications
            if item.notification.characteristic in selected and item.offset_seconds <= duration
        ]
        started = asyncio.get_running_loop().time()
        for item in records:
            if self.backend.speed > 0:
                target = item.offset_seconds / self.backend.speed
                delay = target - (asyncio.get_running_loop().time() - started)
                if delay > 0:
                    await asyncio.sleep(delay)
        return [item.notification for item in records]

    async def subscribe(self, characteristic: str, *, duration: float) -> list[Notification]:
        self._require_connected()
        normalized = normalize_uuid(characteristic)
        recorded_error = self._subscription_error(normalized)
        if recorded_error is not None:
            raise recorded_error.exception()
        profile_characteristic = self._profile_characteristic(normalized)
        has_notifications = any(
            item.notification.characteristic == normalized
            for item in self.backend.evidence.notifications
        )
        has_subscription = normalized in self.backend.evidence.subscriptions
        if profile_characteristic is not None and not {
            "notify",
            "indicate",
        }.intersection(profile_characteristic.properties):
            raise ConfigError(
                "characteristic does not support notify or indicate",
                characteristic=normalized,
                properties=list(profile_characteristic.properties),
            )
        if not has_notifications and not has_subscription:
            raise ReplayMissError(
                "subscription is absent from replay evidence",
                characteristic=normalized,
                evidence=str(self.backend.evidence.path),
            )
        return await self._play((normalized,), duration=duration)

    async def observe(self, characteristics: tuple[str, ...], *, duration: float) -> dict[str, Any]:
        self._require_connected()
        subscriptions: list[dict[str, Any]] = []
        successful: list[str] = []
        for characteristic in characteristics:
            normalized = normalize_uuid(characteristic)
            recorded_error = self._subscription_error(normalized)
            if recorded_error is not None:
                subscriptions.append(
                    {
                        "characteristic": normalized,
                        "ok": False,
                        "error": recorded_error.exception().to_dict(),
                    }
                )
            elif (
                not any(
                    item.notification.characteristic == normalized
                    for item in self.backend.evidence.notifications
                )
                and normalized not in self.backend.evidence.subscriptions
            ):
                missing = ReplayMissError(
                    "subscription is absent from replay evidence",
                    characteristic=normalized,
                    evidence=str(self.backend.evidence.path),
                )
                subscriptions.append(
                    {
                        "characteristic": normalized,
                        "ok": False,
                        "error": missing.to_dict(),
                    }
                )
            else:
                successful.append(normalized)
                subscriptions.append({"characteristic": normalized, "ok": True})
        notifications = await self._play(tuple(successful), duration=duration)
        cleanup = {
            "ok": True,
            "started_count": len(successful),
            "stopped_count": len(successful),
            "failure_count": 0,
            "errors": [],
        }
        requested = {normalize_uuid(item) for item in characteristics}
        if self.backend.evidence.cleanup is not None and requested == set(
            self.backend.evidence.subscriptions
        ):
            cleanup = self.backend.evidence.cleanup
        return {
            "subscriptions": subscriptions,
            "notifications": notifications,
            "cleanup": cleanup,
        }

    async def write(self, characteristic: str, data: bytes, *, response: bool) -> None:
        del characteristic, data, response
        raise GuardDeniedError(
            "replay evidence is read-only; writes are never sent or simulated",
            evidence=str(self.backend.evidence.path),
        )

    async def exchange(
        self,
        write_characteristic: str,
        notify_characteristic: str,
        data: bytes,
        *,
        duration: float,
        response: bool,
        read_back: bool,
    ) -> tuple[list[Notification], bytes | None]:
        del write_characteristic, notify_characteristic, data, duration, response, read_back
        raise GuardDeniedError(
            "replay evidence is read-only; exchanges are never sent or simulated",
            evidence=str(self.backend.evidence.path),
        )


class ReplayBackend:
    name = "replay"

    def __init__(self, evidence: str | Path | ReplayEvidence, *, speed: float = 0.0) -> None:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise ConfigError("replay speed must be a non-negative number")
        if not math.isfinite(speed) or speed < 0:
            raise ConfigError("replay speed must be finite and non-negative")
        self.evidence = (
            evidence if isinstance(evidence, ReplayEvidence) else ReplayEvidence.load(evidence)
        )
        self.speed = float(speed)

    @property
    def instant(self) -> bool:
        return self.speed == 0

    def metadata(self) -> dict[str, Any]:
        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence": str(self.evidence.path),
            "capture_id": self.evidence.capture_id,
            "capture_status": self.evidence.status,
            "timing": "instant" if self.instant else "scaled",
            "speed": self.speed,
            "read_only": True,
        }

    async def discover(
        self, *, timeout: float, service_uuids: tuple[str, ...] = ()
    ) -> list[DiscoveredDevice]:
        del timeout
        if service_uuids:
            advertised = {item.casefold() for item in self.evidence.device.service_uuids}
            requested = {normalize_uuid(item).casefold() for item in service_uuids}
            if not advertised.intersection(requested):
                return []
        return [self.evidence.device]

    def connection(self, device: DiscoveredDevice, *, timeout: float) -> ReplayConnection:
        del timeout
        if device.identifier.casefold() != self.evidence.device.identifier.casefold():
            raise ReplayMissError(
                "device is absent from replay evidence",
                identifier=device.identifier,
                evidence=str(self.evidence.path),
            )
        return ReplayConnection(self)


async def replay_operation(
    evidence: str | Path,
    operation: str,
    *,
    speed: float = 0.0,
    device: str | None = None,
    characteristic: str | None = None,
    characteristics: tuple[str, ...] | None = None,
    workflow: str | Path | None = None,
    timeout: float = 10.0,
    duration: float = 10.0,
    max_reads: int = 32,
    read_offset: int = 0,
    include_profile: bool = True,
    name_contains: str | None = None,
    service_uuid: str | None = None,
) -> dict[str, Any]:
    """Run one read-only BLEA operation against captured evidence."""

    normalized_operation = operation.strip().casefold()
    if normalized_operation not in REPLAY_OPERATIONS:
        raise ConfigError(
            "unsupported replay operation",
            operation=operation,
            supported=sorted(REPLAY_OPERATIONS),
        )

    from blea.service import BleService, SessionManager

    backend = ReplayBackend(evidence, speed=speed)
    service = BleService(backend)
    selector = device or f"id:{backend.evidence.device.identifier}"
    if normalized_operation == "scan":
        result = await service.scan(
            timeout=timeout,
            name_contains=name_contains,
            service_uuid=service_uuid,
        )
    elif normalized_operation == "inspect":
        result = await service.inspect(selector, timeout=timeout)
    elif normalized_operation == "probe":
        result = await service.probe(
            selector,
            timeout=timeout,
            max_reads=max_reads,
            read_offset=read_offset,
            include_profile=include_profile,
        )
    elif normalized_operation == "read":
        if not characteristic:
            raise ConfigError("replay read requires a characteristic")
        result = await service.read(selector, characteristic, timeout=timeout)
    elif normalized_operation == "subscribe":
        if not characteristic:
            raise ConfigError("replay subscribe requires a characteristic")
        result = await service.subscribe(
            selector,
            characteristic,
            duration=duration,
            timeout=timeout,
        )
    elif normalized_operation == "observe":
        result = await service.observe(
            selector,
            characteristics=characteristics,
            duration=duration,
            timeout=timeout,
        )
    else:
        if workflow is None:
            raise ConfigError("replay run requires a workflow path")
        from blea.workflow import run_workflow

        result = await run_workflow(
            workflow,
            allow_write=False,
            manager=SessionManager(service, idle_timeout_seconds=None),
        )
        result = _stable_workflow_result(result)

    if backend.instant and "duration_ms" in result:
        result["duration_ms"] = 0
    result["replay"] = backend.metadata()
    return result
