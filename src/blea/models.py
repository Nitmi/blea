from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

BLUETOOTH_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def bytes_evidence(value: bytes) -> dict[str, Any]:
    return {
        "length": len(value),
        "hex": value.hex(),
        "base64": base64.b64encode(value).decode("ascii"),
        "utf8": value.decode("utf-8", errors="replace"),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uuid_namespace(value: str) -> str:
    """Classify canonical Bluetooth Base UUIDs separately from custom UUIDs."""

    try:
        normalized = str(UUID(value))
    except ValueError:
        return "unknown"
    if normalized.endswith(BLUETOOTH_BASE_UUID_SUFFIX):
        return "bluetooth-base"
    return "custom"


@dataclass(frozen=True)
class DiscoveredDevice:
    identifier: str
    name: str | None = None
    local_name: str | None = None
    rssi: int | None = None
    tx_power: int | None = None
    service_uuids: tuple[str, ...] = ()
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_data: dict[str, bytes] = field(default_factory=dict)
    native: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "local_name": self.local_name,
            "rssi": self.rssi,
            "tx_power": self.tx_power,
            "service_uuids": list(self.service_uuids),
            "manufacturer_data": {
                f"0x{company_id:04x}": bytes_evidence(value)
                for company_id, value in sorted(self.manufacturer_data.items())
            },
            "service_data": {
                uuid: bytes_evidence(value) for uuid, value in sorted(self.service_data.items())
            },
        }


@dataclass(frozen=True)
class DescriptorInfo:
    uuid: str
    handle: int
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "uuid_namespace": uuid_namespace(self.uuid),
            "handle": self.handle,
            "description": self.description,
        }


@dataclass(frozen=True)
class CharacteristicInfo:
    uuid: str
    handle: int
    properties: tuple[str, ...]
    description: str | None = None
    descriptors: tuple[DescriptorInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "uuid_namespace": uuid_namespace(self.uuid),
            "handle": self.handle,
            "description": self.description,
            "properties": list(self.properties),
            "descriptors": [descriptor.to_dict() for descriptor in self.descriptors],
        }


@dataclass(frozen=True)
class ServiceInfo:
    uuid: str
    handle: int
    description: str | None = None
    characteristics: tuple[CharacteristicInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "uuid_namespace": uuid_namespace(self.uuid),
            "handle": self.handle,
            "description": self.description,
            "characteristics": [item.to_dict() for item in self.characteristics],
        }


@dataclass(frozen=True)
class GattProfile:
    services: tuple[ServiceInfo, ...]

    def summary(self) -> dict[str, int]:
        characteristics = [
            characteristic
            for service in self.services
            for characteristic in service.characteristics
        ]
        return {
            "service_count": len(self.services),
            "characteristic_count": len(characteristics),
            "readable_characteristic_count": sum(
                "read" in characteristic.properties for characteristic in characteristics
            ),
            "writable_characteristic_count": sum(
                bool({"write", "write-without-response"}.intersection(characteristic.properties))
                for characteristic in characteristics
            ),
            "subscribable_characteristic_count": sum(
                bool({"notify", "indicate"}.intersection(characteristic.properties))
                for characteristic in characteristics
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "services": [service.to_dict() for service in self.services],
        }


@dataclass(frozen=True)
class Notification:
    characteristic: str
    data: bytes
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "characteristic": self.characteristic,
            "data": bytes_evidence(self.data),
        }
