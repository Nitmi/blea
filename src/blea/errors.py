from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_GUARD_DENIED = 2
EXIT_ASSERTION_FAILED = 3
EXIT_DEVICE_UNAVAILABLE = 4
EXIT_TIMEOUT = 5
EXIT_PROTOCOL_ERROR = 6
EXIT_CONFIG_ERROR = 7
EXIT_PERMISSION_DENIED = 8


@dataclass
class BleaError(Exception):
    message: str
    exit_code: int
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": self.reason,
            "message": self.message,
            "details": self.details,
            "exit_code": self.exit_code,
        }


class GuardDeniedError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_GUARD_DENIED, "guard_denied", details)


class AssertionFailedError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_ASSERTION_FAILED, "assertion_failed", details)


class DeviceUnavailableError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_DEVICE_UNAVAILABLE, "device_unavailable", details)


class BleTimeoutError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_TIMEOUT, "timeout", details)


class ProtocolError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_PROTOCOL_ERROR, "protocol_error", details)


class ConfigError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_CONFIG_ERROR, "config_error", details)


class PermissionDeniedError(BleaError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, EXIT_PERMISSION_DENIED, "permission_denied", details)


def translate_backend_error(exc: Exception, *, operation: str) -> BleaError:
    """Map platform-specific Bleak failures into a stable public error contract."""

    if isinstance(exc, BleaError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return BleTimeoutError(f"BLE {operation} timed out", operation=operation)

    name = type(exc).__name__
    message = str(exc) or name
    lowered = message.casefold()
    if any(
        token in lowered
        for token in (
            "permission",
            "access denied",
            "not authorized",
            "insufficient authentication",
            "insufficient encryption",
        )
    ):
        return PermissionDeniedError(
            f"BLE {operation} was denied by the operating system",
            operation=operation,
            backend_error=name,
            backend_message=message,
        )
    if name in {"BleakDeviceNotFoundError", "BleakBluetoothNotAvailableError"}:
        return DeviceUnavailableError(
            f"BLE {operation} could not access the adapter or device",
            operation=operation,
            backend_error=name,
            backend_message=message,
        )
    return ProtocolError(
        f"BLE {operation} failed: {message}",
        operation=operation,
        backend_error=name,
    )
