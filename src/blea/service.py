from __future__ import annotations

import asyncio
import platform
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from blea.backend import BleakBackend, BleBackend, BleConnection
from blea.errors import ConfigError, DeviceUnavailableError, GuardDeniedError
from blea.models import DiscoveredDevice, bytes_evidence


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
