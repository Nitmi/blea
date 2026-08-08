from __future__ import annotations

import asyncio
import platform
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from blea import __version__
from blea.backend import BleakBackend, BleBackend, BleConnection
from blea.errors import (
    BleaError,
    ConfigError,
    DeviceUnavailableError,
    GuardDeniedError,
    ProtocolError,
    translate_backend_error,
)
from blea.evidence import EvidenceWriter, validate_events
from blea.models import DiscoveredDevice, GattProfile, bytes_evidence


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _operation_timeout(timeout: float) -> dict[str, Any]:
    return {
        "operation_timeout_seconds": timeout,
        "timeout_scope": "per_backend_operation",
    }


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _stable_error(exc: Exception, *, operation: str) -> dict[str, Any]:
    if isinstance(exc, BleaError):
        return exc.to_dict()
    return translate_backend_error(exc, operation=operation).to_dict()


def ensure_write_authorized(
    device: DiscoveredDevice, *, allow_write: bool, confirm_device: str | None
) -> None:
    if not allow_write:
        raise GuardDeniedError(
            "BLE writes are disabled; explicitly enable writes for this operation",
            expected_device=device.identifier,
        )
    confirmed = (confirm_device or "").removeprefix("id:")
    if confirmed.casefold() != device.identifier.casefold():
        raise GuardDeniedError(
            "write confirmation must exactly match the resolved device identifier",
            expected_device=device.identifier,
            received_confirmation=confirm_device,
        )


class BleService:
    def __init__(self, backend: BleBackend | None = None) -> None:
        self.backend = backend or BleakBackend()

    async def doctor(self, *, scan_timeout: float = 1.0) -> dict[str, Any]:
        started = time.monotonic()
        base = {
            "operation": "doctor",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "backend": self.backend.name,
            "bleak_version": _package_version("bleak"),
        }
        try:
            devices = await self.backend.discover(timeout=scan_timeout)
        except Exception as exc:
            if hasattr(exc, "to_dict"):
                error = exc.to_dict()
            else:
                error = {"reason": type(exc).__name__, "message": str(exc)}
            return {
                **base,
                "ok": False,
                "adapter_available": False,
                "devices_observed": 0,
                "duration_ms": _duration_ms(started),
                "error": error,
            }
        return {
            **base,
            "ok": True,
            "adapter_available": True,
            "devices_observed": len(devices),
            "duration_ms": _duration_ms(started),
        }

    async def scan(
        self,
        *,
        timeout: float = 5.0,
        name_contains: str | None = None,
        service_uuid: str | None = None,
    ) -> dict[str, Any]:
        if timeout <= 0:
            raise ConfigError("scan timeout must be greater than zero")
        started = time.monotonic()
        services = (service_uuid,) if service_uuid else ()
        devices = await self.backend.discover(timeout=timeout, service_uuids=services)
        if name_contains:
            needle = name_contains.casefold()
            devices = [
                item
                for item in devices
                if any(
                    needle in candidate.casefold()
                    for candidate in (item.name, item.local_name)
                    if candidate
                )
            ]
        return {
            "ok": True,
            "operation": "scan",
            "filters": {"name_contains": name_contains, "service_uuid": service_uuid},
            "count": len(devices),
            "devices": [device.to_dict() for device in devices],
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def resolve(self, selector: str, *, timeout: float = 5.0) -> DiscoveredDevice:
        if not selector.strip():
            raise ConfigError("device selector must not be empty")
        devices = await self.backend.discover(timeout=timeout)
        return self._select_device(selector, devices)

    @staticmethod
    def _select_device(selector: str, devices: list[DiscoveredDevice]) -> DiscoveredDevice:
        if not selector.strip():
            raise ConfigError("device selector must not be empty")
        kind, separator, raw_target = selector.partition(":")
        if separator and kind.casefold() in {"id", "name"}:
            mode = kind.casefold()
            target = raw_target
        else:
            mode = "auto"
            target = selector
        folded = target.casefold()

        if mode in {"id", "auto"}:
            identifier_matches = [
                device for device in devices if device.identifier.casefold() == folded
            ]
            if len(identifier_matches) == 1:
                return identifier_matches[0]

        name_matches = [
            device
            for device in devices
            if any(
                candidate.casefold() == folded
                for candidate in (device.name, device.local_name)
                if candidate
            )
        ]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            raise DeviceUnavailableError(
                "device name is ambiguous; select by identifier",
                selector=selector,
                candidates=[device.identifier for device in name_matches],
            )
        raise DeviceUnavailableError(
            "no BLE device matched the selector",
            selector=selector,
            observed=[device.to_dict() for device in devices],
        )

    async def _open(
        self, selector: str, *, timeout: float
    ) -> tuple[DiscoveredDevice, BleConnection]:
        device = await self.resolve(selector, timeout=timeout)
        connection = self.backend.connection(device, timeout=timeout)
        await connection.connect()
        return device, connection

    async def inspect(self, selector: str, *, timeout: float = 10.0) -> dict[str, Any]:
        started = time.monotonic()
        device, connection = await self._open(selector, timeout=timeout)
        try:
            profile = await connection.inspect()
        finally:
            await connection.disconnect()
        return {
            "ok": True,
            "operation": "inspect",
            "device": device.to_dict(),
            "profile_summary": profile.summary(),
            "profile": profile.to_dict(),
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def probe(
        self,
        selector: str,
        *,
        timeout: float = 10.0,
        max_reads: int = 32,
        read_offset: int = 0,
        include_profile: bool = True,
    ) -> dict[str, Any]:
        if max_reads <= 0:
            raise ConfigError("max_reads must be greater than zero")
        if read_offset < 0:
            raise ConfigError("read_offset must not be negative")
        started = time.monotonic()
        device, connection = await self._open(selector, timeout=timeout)
        reads: list[dict[str, Any]] = []
        failure_reasons: dict[str, int] = {}
        try:
            profile = await connection.inspect()
            readable = [
                characteristic
                for service in profile.services
                for characteristic in service.characteristics
                if "read" in characteristic.properties
            ]
            window = readable[read_offset : read_offset + max_reads]
            for characteristic in window:
                try:
                    data = await connection.read(characteristic.uuid)
                    reads.append(
                        {
                            "ok": True,
                            "characteristic": characteristic.uuid,
                            "data": bytes_evidence(data),
                        }
                    )
                except Exception as exc:
                    if hasattr(exc, "to_dict"):
                        error = exc.to_dict()
                    else:
                        error = {"reason": type(exc).__name__, "message": str(exc)}
                    reason = str(error.get("reason", "unknown_error"))
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                    reads.append(
                        {
                            "ok": False,
                            "characteristic": characteristic.uuid,
                            "error": error,
                        }
                    )
        finally:
            await connection.disconnect()
        success_count = sum(1 for item in reads if item["ok"])
        failure_count = len(reads) - success_count
        window_end = min(read_offset + len(reads), len(readable))
        reads_remaining = max(len(readable) - window_end, 0)
        next_read_offset = window_end if reads_remaining else None
        has_more = reads_remaining > 0
        has_failures = failure_count > 0
        if has_more:
            status = "more_with_failures" if has_failures else "more"
        else:
            status = "complete_with_failures" if has_failures else "complete"
        result = {
            "ok": True,
            "operation": "probe",
            "status": status,
            "device": device.to_dict(),
            "profile_summary": profile.summary(),
            "profile_included": include_profile,
            "read_page": {
                "offset": read_offset,
                "limit": max_reads,
                "attempted_count": len(reads),
                "success_count": success_count,
                "failure_count": failure_count,
                "remaining_count": reads_remaining,
                "next_offset": next_read_offset,
                "has_more": has_more,
                "has_failures": has_failures,
                "failure_reasons": dict(sorted(failure_reasons.items())),
            },
            "next_read_offset": next_read_offset,
            "reads": reads,
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }
        if include_profile:
            result["profile"] = profile.to_dict()
        return result

    async def read(
        self, selector: str, characteristic: str, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        started = time.monotonic()
        device, connection = await self._open(selector, timeout=timeout)
        try:
            data = await connection.read(characteristic)
        finally:
            await connection.disconnect()
        return {
            "ok": True,
            "operation": "read",
            "device": device.to_dict(),
            "characteristic": characteristic,
            "data": bytes_evidence(data),
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def subscribe(
        self,
        selector: str,
        characteristic: str,
        *,
        duration: float = 10.0,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("subscription duration must not be negative")
        started = time.monotonic()
        device, connection = await self._open(selector, timeout=timeout)
        try:
            notifications = await connection.subscribe(characteristic, duration=duration)
        finally:
            await connection.disconnect()
        return {
            "ok": True,
            "operation": "subscribe",
            "device": device.to_dict(),
            "characteristic": characteristic,
            "notification_count": len(notifications),
            "notifications": [item.to_dict() for item in notifications],
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def _observe_connected(
        self,
        device: DiscoveredDevice,
        connection: BleConnection,
        *,
        characteristics: tuple[str, ...] | None,
        duration: float,
        timeout: float,
        profile: GattProfile | None = None,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("observation duration must not be negative")

        started = time.monotonic()
        if profile is None:
            profile = await connection.inspect()
        discovered = [
            characteristic
            for service in profile.services
            for characteristic in service.characteristics
        ]
        discovered_by_uuid = {
            characteristic.uuid.casefold(): characteristic for characteristic in discovered
        }
        candidates = [
            characteristic
            for characteristic in discovered
            if {"notify", "indicate"}.intersection(characteristic.properties)
        ]

        requested: list[str] = []
        seen: set[str] = set()
        for value in characteristics or ():
            normalized = value.strip()
            if not normalized:
                raise ConfigError("observe characteristic must not be empty")
            folded = normalized.casefold()
            if folded not in seen:
                requested.append(normalized)
                seen.add(folded)

        mode = "explicit" if requested else "auto"
        planned: list[tuple[str, str | None, dict[str, Any] | None]] = []
        if requested:
            for requested_uuid in requested:
                characteristic = discovered_by_uuid.get(requested_uuid.casefold())
                if characteristic is None:
                    error = ConfigError(
                        "characteristic was not found in the discovered GATT profile",
                        characteristic=requested_uuid,
                    ).to_dict()
                    planned.append((requested_uuid, None, error))
                elif not {"notify", "indicate"}.intersection(characteristic.properties):
                    error = ConfigError(
                        "characteristic does not support notify or indicate",
                        characteristic=characteristic.uuid,
                        properties=list(characteristic.properties),
                    ).to_dict()
                    planned.append((characteristic.uuid, None, error))
                else:
                    planned.append((characteristic.uuid, characteristic.uuid, None))
        else:
            planned.extend((item.uuid, item.uuid, None) for item in candidates)

        selected = tuple(
            canonical for _, canonical, error in planned if canonical is not None and error is None
        )
        if selected:
            batch = await connection.observe(selected, duration=duration)
        else:
            batch = {
                "subscriptions": [],
                "notifications": [],
                "cleanup": {
                    "ok": True,
                    "started_count": 0,
                    "stopped_count": 0,
                    "failure_count": 0,
                    "errors": [],
                },
            }

        backend_attempts = {
            str(item["characteristic"]).casefold(): item for item in batch["subscriptions"]
        }
        subscriptions: list[dict[str, Any]] = []
        for reported_uuid, canonical, error in planned:
            if error is not None:
                subscriptions.append({"characteristic": reported_uuid, "ok": False, "error": error})
                continue
            attempt = backend_attempts.get(str(canonical).casefold())
            if attempt is None:
                error = ProtocolError(
                    "BLE backend omitted an observation subscription result",
                    characteristic=canonical,
                ).to_dict()
                subscriptions.append({"characteristic": reported_uuid, "ok": False, "error": error})
            else:
                subscriptions.append(attempt)

        notifications = sorted(batch["notifications"], key=lambda item: item.timestamp)
        notification_counts: dict[str, int] = {}
        for notification in notifications:
            notification_counts[notification.characteristic] = (
                notification_counts.get(notification.characteristic, 0) + 1
            )

        success_count = sum(1 for item in subscriptions if item["ok"])
        failure_count = len(subscriptions) - success_count
        failure_reasons: dict[str, int] = {}
        for item in subscriptions:
            if item["ok"]:
                continue
            reason = str(item["error"].get("reason", "unknown_error"))
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

        cleanup = batch["cleanup"]
        if mode == "auto" and not candidates:
            status = "no_subscribable_characteristics"
        elif failure_count or not cleanup["ok"]:
            status = "complete_with_failures"
        else:
            status = "complete"

        return {
            "ok": True,
            "operation": "observe",
            "status": status,
            "device": device.to_dict(),
            "profile_summary": profile.summary(),
            "selection": {
                "mode": mode,
                "candidate_count": len(candidates),
                "requested_count": len(requested) if requested else len(candidates),
                "selected_count": len(selected),
            },
            "subscription_summary": {
                "attempted_count": len(subscriptions),
                "success_count": success_count,
                "failure_count": failure_count,
                "failure_reasons": dict(sorted(failure_reasons.items())),
            },
            "subscriptions": subscriptions,
            "sample_duration_seconds": duration,
            "notification_count": len(notifications),
            "notification_counts": dict(sorted(notification_counts.items())),
            "notifications": [item.to_dict() for item in notifications],
            "cleanup": cleanup,
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def observe(
        self,
        selector: str,
        *,
        characteristics: tuple[str, ...] | None = None,
        duration: float = 10.0,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("observation duration must not be negative")
        started = time.monotonic()
        device, connection = await self._open(selector, timeout=timeout)
        try:
            result = await self._observe_connected(
                device,
                connection,
                characteristics=characteristics,
                duration=duration,
                timeout=timeout,
            )
        finally:
            await connection.disconnect()
        result["duration_ms"] = _duration_ms(started)
        return result

    async def capture(
        self,
        selector: str,
        output: str | Path,
        *,
        service_uuid: str | None = None,
        max_reads: int = 128,
        read_offset: int = 0,
        observe_duration: float = 10.0,
        timeout: float = 10.0,
        redact_identifiers: bool = False,
    ) -> dict[str, Any]:
        """Capture read-only BLE evidence into one atomically written JSONL package."""

        if not selector.strip():
            raise ConfigError("device selector must not be empty")
        if max_reads <= 0:
            raise ConfigError("max_reads must be greater than zero")
        if read_offset < 0:
            raise ConfigError("read_offset must not be negative")
        if observe_duration < 0:
            raise ConfigError("observation duration must not be negative")
        if timeout <= 0:
            raise ConfigError("operation timeout must be greater than zero")

        started = time.monotonic()
        writer = EvidenceWriter(redact_identifiers=redact_identifiers)
        parameters = {
            "selector": selector,
            "service_uuid": service_uuid,
            "max_reads": max_reads,
            "read_offset": read_offset,
            "observe_duration": observe_duration,
            "timeout": timeout,
            "redact_identifiers": redact_identifiers,
        }
        writer.add(
            "manifest",
            source={
                "blea_version": __version__,
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "bleak_version": _package_version("bleak"),
                "backend": self.backend.name,
            },
            data={"parameters": parameters, "read_only": True},
        )

        failures: list[dict[str, Any]] = []
        fatal_error: dict[str, Any] | None = None
        device: DiscoveredDevice | None = None
        connection: BleConnection | None = None
        connect_attempted = False
        profile: GattProfile | None = None
        read_records: list[dict[str, Any]] = []
        observation: dict[str, Any] | None = None

        def record_error(
            operation: str,
            exc: Exception | dict[str, Any],
            *,
            characteristic: str | None = None,
        ) -> dict[str, Any]:
            error = exc if isinstance(exc, dict) else _stable_error(exc, operation=operation)
            data: dict[str, Any] = {"operation": operation, "error": error}
            if characteristic is not None:
                data["characteristic"] = characteristic
            writer.add("error", data=data)
            failures.append({"operation": operation, **data})
            return error

        services = (service_uuid,) if service_uuid else ()
        try:
            devices = await self.backend.discover(timeout=timeout, service_uuids=services)
            device = self._select_device(selector, devices)
        except Exception as exc:
            fatal_error = record_error("discover", exc)

        if device is not None:
            writer.add("advertisement", device=device.to_dict())
            try:
                connection = self.backend.connection(device, timeout=timeout)
                connect_attempted = True
                await connection.connect()
            except Exception as exc:
                fatal_error = record_error("connect", exc)

        if connection is not None and fatal_error is None:
            try:
                profile = await connection.inspect()
                writer.add("profile", data=profile.to_dict())
            except Exception as exc:
                fatal_error = record_error("service_discovery", exc)

        read_page: dict[str, Any] = {
            "offset": read_offset,
            "limit": max_reads,
            "attempted_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "remaining_count": 0,
            "next_offset": None,
            "has_more": False,
        }
        if profile is not None and connection is not None and fatal_error is None:
            readable = sorted(
                (
                    characteristic
                    for service in profile.services
                    for characteristic in service.characteristics
                    if "read" in characteristic.properties
                ),
                key=lambda item: (item.uuid.casefold(), item.handle),
            )
            window = readable[read_offset : read_offset + max_reads]
            for characteristic in window:
                try:
                    value = await connection.read(characteristic.uuid)
                    record = {
                        "characteristic": characteristic.uuid,
                        "ok": True,
                        "value": bytes_evidence(value),
                    }
                    read_records.append(record)
                    writer.add("read", data=record)
                except Exception as exc:
                    error = _stable_error(exc, operation="read")
                    record = {
                        "characteristic": characteristic.uuid,
                        "ok": False,
                        "error": error,
                    }
                    read_records.append(record)
                    writer.add("read", data=record)
                    failures.append({"operation": "read", **record})
            next_offset = read_offset + len(window)
            remaining = max(len(readable) - next_offset, 0)
            read_page.update(
                {
                    "attempted_count": len(window),
                    "success_count": sum(1 for item in read_records if item["ok"]),
                    "failure_count": sum(1 for item in read_records if not item["ok"]),
                    "remaining_count": remaining,
                    "next_offset": next_offset if remaining else None,
                    "has_more": bool(remaining),
                }
            )

            try:
                observation = await self._observe_connected(
                    device,
                    connection,
                    characteristics=None,
                    duration=observe_duration,
                    timeout=timeout,
                    profile=profile,
                )
                for subscription in observation["subscriptions"]:
                    if not subscription.get("ok"):
                        record_error(
                            "subscribe",
                            subscription.get("error")
                            or ProtocolError("subscription failed").to_dict(),
                            characteristic=str(subscription.get("characteristic")),
                        )
                cleanup = observation.get("cleanup", {})
                for cleanup_error in cleanup.get("errors", []):
                    details = cleanup_error.get("details", {})
                    record_error(
                        "unsubscribe",
                        cleanup_error,
                        characteristic=details.get("characteristic"),
                    )
                for notification in observation["notifications"]:
                    writer.add(
                        "notification",
                        timestamp=notification["timestamp"],
                        data={
                            "characteristic": notification["characteristic"],
                            "value": notification["data"],
                        },
                    )
            except Exception as exc:
                record_error("observe", exc)

        if connection is not None and connect_attempted:
            try:
                await connection.disconnect()
            except Exception as exc:
                record_error("disconnect", exc)

        status = (
            "failed"
            if fatal_error is not None
            else ("complete_with_failures" if failures else "complete")
        )
        event_counts = Counter(event["kind"] for event in writer.events)
        event_counts["summary"] += 1
        summary: dict[str, Any] = {
            "status": status,
            "complete": True,
            "event_count": len(writer.events) + 1,
            "event_counts": dict(sorted(event_counts.items())),
            "read_page": read_page,
            "read_summary": {
                "attempted_count": len(read_records),
                "success_count": sum(1 for item in read_records if item["ok"]),
                "failure_count": sum(1 for item in read_records if not item["ok"]),
            },
            "failure_count": len(failures),
            "failures": failures,
        }
        if observation is not None:
            summary["observation"] = {
                key: observation[key]
                for key in (
                    "status",
                    "selection",
                    "subscription_summary",
                    "subscriptions",
                    "sample_duration_seconds",
                    "notification_count",
                    "notification_counts",
                    "cleanup",
                )
            }
        writer.add("summary", data=summary)
        destination = writer.write(output)
        evidence_report = validate_events(writer.events)
        result: dict[str, Any] = {
            "ok": fatal_error is None,
            "operation": "capture",
            "status": status,
            "output": str(destination),
            "capture_id": writer.capture_id,
            "event_count": evidence_report["event_count"],
            "device": writer.events[1].get("device") if device is not None else None,
            "read_page": read_page,
            "read_summary": summary["read_summary"],
            "observation": summary.get("observation"),
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": fatal_error.get("exit_code", 0) if fatal_error else 0,
        }
        if fatal_error is not None:
            result["error"] = fatal_error
        return result

    async def write(
        self,
        selector: str,
        characteristic: str,
        data: bytes,
        *,
        response: bool = True,
        allow_write: bool = False,
        confirm_device: str | None = None,
        read_back: bool = False,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        started = time.monotonic()
        device = await self.resolve(selector, timeout=timeout)
        ensure_write_authorized(device, allow_write=allow_write, confirm_device=confirm_device)
        connection = self.backend.connection(device, timeout=timeout)
        await connection.connect()
        try:
            await connection.write(characteristic, data, response=response)
            read_back_data = await connection.read(characteristic) if read_back else None
        finally:
            await connection.disconnect()
        return {
            "ok": True,
            "operation": "write",
            "device": device.to_dict(),
            "characteristic": characteristic,
            "written": bytes_evidence(data),
            "response": response,
            "read_back": bytes_evidence(read_back_data) if read_back_data is not None else None,
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def _exchange_connected(
        self,
        device: DiscoveredDevice,
        connection: BleConnection,
        write_characteristic: str,
        notify_characteristic: str,
        data: bytes,
        *,
        duration: float,
        response: bool,
        read_back: bool,
        timeout: float,
    ) -> dict[str, Any]:
        started = time.monotonic()
        notifications, read_back_data = await connection.exchange(
            write_characteristic,
            notify_characteristic,
            data,
            duration=duration,
            response=response,
            read_back=read_back,
        )
        return {
            "ok": True,
            "operation": "exchange",
            "device": device.to_dict(),
            "write_characteristic": write_characteristic,
            "notify_characteristic": notify_characteristic,
            "written": bytes_evidence(data),
            "response": response,
            "read_back": bytes_evidence(read_back_data) if read_back_data is not None else None,
            "sample_duration_seconds": duration,
            "notification_count": len(notifications),
            "notifications": [item.to_dict() for item in notifications],
            # A successful exchange means the one requested subscription was stopped.
            "cleanup": {
                "ok": True,
                "started_count": 1,
                "stopped_count": 1,
                "failure_count": 0,
                "errors": [],
            },
            **_operation_timeout(timeout),
            "duration_ms": _duration_ms(started),
            "exit_code": 0,
        }

    async def exchange(
        self,
        selector: str,
        write_characteristic: str,
        notify_characteristic: str,
        data: bytes,
        *,
        duration: float = 5.0,
        response: bool = True,
        allow_write: bool = False,
        confirm_device: str | None = None,
        read_back: bool = False,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("exchange duration must not be negative")
        started = time.monotonic()
        device = await self.resolve(selector, timeout=timeout)
        ensure_write_authorized(device, allow_write=allow_write, confirm_device=confirm_device)
        connection = self.backend.connection(device, timeout=timeout)
        await connection.connect()
        try:
            result = await self._exchange_connected(
                device,
                connection,
                write_characteristic,
                notify_characteristic,
                data,
                duration=duration,
                response=response,
                read_back=read_back,
                timeout=timeout,
            )
        finally:
            await connection.disconnect()
        result["duration_ms"] = _duration_ms(started)
        return result


@dataclass
class ActiveSession:
    id: str
    device: DiscoveredDevice
    connection: BleConnection
    operation_timeout_seconds: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)
    closing: bool = False


class SessionManager:
    def __init__(
        self,
        service: BleService | None = None,
        *,
        idle_timeout_seconds: float | None = None,
    ) -> None:
        self.service = service or BleService()
        self.idle_timeout_seconds = idle_timeout_seconds
        self._sessions: dict[str, ActiveSession] = {}

    def _get(self, session_id: str) -> ActiveSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ConfigError("unknown or closed BLE session", session_id=session_id) from exc

    @asynccontextmanager
    async def _use(self, session_id: str) -> AsyncIterator[ActiveSession]:
        session = self._get(session_id)
        session.last_used = time.monotonic()
        async with session.lock:
            if session.closing:
                raise ConfigError("unknown or closed BLE session", session_id=session_id)
            try:
                yield session
            finally:
                session.last_used = time.monotonic()

    async def _disconnect(self, session: ActiveSession, *, suppress_errors: bool) -> None:
        async with session.lock:
            try:
                await session.connection.disconnect()
            except Exception:
                if not suppress_errors:
                    raise

    async def open(self, selector: str, *, timeout: float = 10.0) -> dict[str, Any]:
        device = await self.service.resolve(selector, timeout=timeout)
        connection = self.service.backend.connection(device, timeout=timeout)
        await connection.connect()
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = ActiveSession(session_id, device, connection, timeout)
        return {
            "ok": True,
            "operation": "session_open",
            "session_id": session_id,
            "device": device.to_dict(),
            "idle_timeout_seconds": self.idle_timeout_seconds,
            **_operation_timeout(timeout),
            "exit_code": 0,
        }

    async def inspect(self, session_id: str) -> dict[str, Any]:
        async with self._use(session_id) as session:
            profile = await session.connection.inspect()
        return {
            "ok": True,
            "operation": "session_inspect",
            "session_id": session_id,
            "device": session.device.to_dict(),
            "profile_summary": profile.summary(),
            "profile": profile.to_dict(),
            **_operation_timeout(session.operation_timeout_seconds),
            "exit_code": 0,
        }

    async def read(self, session_id: str, characteristic: str) -> dict[str, Any]:
        async with self._use(session_id) as session:
            data = await session.connection.read(characteristic)
        return {
            "ok": True,
            "operation": "session_read",
            "session_id": session_id,
            "device": session.device.to_dict(),
            "characteristic": characteristic,
            "data": bytes_evidence(data),
            **_operation_timeout(session.operation_timeout_seconds),
            "exit_code": 0,
        }

    async def subscribe(
        self, session_id: str, characteristic: str, *, duration: float = 10.0
    ) -> dict[str, Any]:
        async with self._use(session_id) as session:
            notifications = await session.connection.subscribe(characteristic, duration=duration)
        return {
            "ok": True,
            "operation": "session_subscribe",
            "session_id": session_id,
            "device": session.device.to_dict(),
            "characteristic": characteristic,
            "notification_count": len(notifications),
            "notifications": [item.to_dict() for item in notifications],
            **_operation_timeout(session.operation_timeout_seconds),
            "exit_code": 0,
        }

    async def observe(
        self,
        session_id: str,
        *,
        characteristics: tuple[str, ...] | None = None,
        duration: float = 10.0,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("observation duration must not be negative")
        async with self._use(session_id) as session:
            result = await self.service._observe_connected(
                session.device,
                session.connection,
                characteristics=characteristics,
                duration=duration,
                timeout=session.operation_timeout_seconds,
            )
        result["operation"] = "session_observe"
        result["session_id"] = session_id
        return result

    async def write(
        self,
        session_id: str,
        characteristic: str,
        data: bytes,
        *,
        response: bool = True,
        allow_write: bool = False,
        confirm_device: str | None = None,
        read_back: bool = False,
    ) -> dict[str, Any]:
        async with self._use(session_id) as session:
            ensure_write_authorized(
                session.device, allow_write=allow_write, confirm_device=confirm_device
            )
            await session.connection.write(characteristic, data, response=response)
            read_back_data = await session.connection.read(characteristic) if read_back else None
        return {
            "ok": True,
            "operation": "session_write",
            "session_id": session_id,
            "device": session.device.to_dict(),
            "characteristic": characteristic,
            "written": bytes_evidence(data),
            "response": response,
            "read_back": bytes_evidence(read_back_data) if read_back_data is not None else None,
            **_operation_timeout(session.operation_timeout_seconds),
            "exit_code": 0,
        }

    async def exchange(
        self,
        session_id: str,
        write_characteristic: str,
        notify_characteristic: str,
        data: bytes,
        *,
        duration: float = 5.0,
        response: bool = True,
        allow_write: bool = False,
        confirm_device: str | None = None,
        read_back: bool = False,
    ) -> dict[str, Any]:
        if duration < 0:
            raise ConfigError("exchange duration must not be negative")
        async with self._use(session_id) as session:
            ensure_write_authorized(
                session.device, allow_write=allow_write, confirm_device=confirm_device
            )
            result = await self.service._exchange_connected(
                session.device,
                session.connection,
                write_characteristic,
                notify_characteristic,
                data,
                duration=duration,
                response=response,
                read_back=read_back,
                timeout=session.operation_timeout_seconds,
            )
        result["operation"] = "session_exchange"
        result["session_id"] = session_id
        return result

    async def close(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        session.closing = True
        self._sessions.pop(session_id, None)
        await self._disconnect(session, suppress_errors=False)
        return {
            "ok": True,
            "operation": "session_close",
            "session_id": session_id,
            "device": session.device.to_dict(),
            "exit_code": 0,
        }

    def list_sessions(self) -> dict[str, Any]:
        now = time.monotonic()
        items = [
            {
                "session_id": session.id,
                "device": session.device.to_dict(),
                "idle_seconds": round(max(now - session.last_used, 0.0), 3),
                "busy": session.lock.locked(),
                **_operation_timeout(session.operation_timeout_seconds),
            }
            for session in self._sessions.values()
        ]
        return {
            "ok": True,
            "operation": "session_list",
            "count": len(items),
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "sessions": items,
            "exit_code": 0,
        }

    async def close_idle(self, max_idle_seconds: float) -> list[str]:
        if max_idle_seconds <= 0:
            raise ConfigError("max_idle_seconds must be greater than zero")
        now = time.monotonic()
        expired: list[ActiveSession] = []
        for session_id, session in list(self._sessions.items()):
            if session.lock.locked() or now - session.last_used < max_idle_seconds:
                continue
            session.closing = True
            self._sessions.pop(session_id, None)
            expired.append(session)
        if expired:
            await asyncio.gather(
                *(self._disconnect(session, suppress_errors=True) for session in expired)
            )
        return [session.id for session in expired]

    async def close_all(self) -> int:
        active = list(self._sessions.values())
        self._sessions.clear()
        for session in active:
            session.closing = True
        if active:
            await asyncio.gather(
                *(self._disconnect(session, suppress_errors=True) for session in active)
            )
        return len(active)
