from __future__ import annotations

from blea.models import (
    CharacteristicInfo,
    DiscoveredDevice,
    GattProfile,
    Notification,
    ServiceInfo,
)

BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
CONTROL = "12345678-1234-1234-1234-1234567890ab"


class FakeConnection:
    def __init__(self, backend: FakeBackend, device: DiscoveredDevice) -> None:
        self.backend = backend
        self.device = device
        self.connected = False

    async def connect(self) -> None:
        self.connected = True
        self.backend.connect_count += 1

    async def disconnect(self) -> None:
        self.connected = False
        self.backend.disconnect_count += 1

    async def inspect(self) -> GattProfile:
        return GattProfile(
            (
                ServiceInfo(
                    uuid="0000180f-0000-1000-8000-00805f9b34fb",
                    handle=1,
                    description="Battery Service",
                    characteristics=(
                        CharacteristicInfo(BATTERY, 2, ("read", "notify"), "Battery Level"),
                        CharacteristicInfo(CONTROL, 3, ("read", "write"), "Control"),
                    ),
                ),
            )
        )

    async def read(self, characteristic: str) -> bytes:
        if characteristic in self.backend.read_errors:
            raise self.backend.read_errors[characteristic]
        return self.backend.values.get(characteristic, b"")

    async def write(self, characteristic: str, data: bytes, *, response: bool) -> None:
        self.backend.writes.append((self.device.identifier, characteristic, data, response))
        self.backend.values[characteristic] = data

    async def subscribe(self, characteristic: str, *, duration: float) -> list[Notification]:
        del duration
        return [
            Notification(characteristic, b"\x64", "2026-08-07T00:00:00.000Z"),
            Notification(characteristic, b"\x63", "2026-08-07T00:00:01.000Z"),
        ]


class FakeBackend:
    name = "fake"

    def __init__(self, devices: list[DiscoveredDevice] | None = None) -> None:
        self.devices = devices or [
            DiscoveredDevice(
                identifier="AA:BB:CC:DD:EE:FF",
                name="Sensor",
                local_name="Sensor",
                rssi=-42,
                service_uuids=("0000180f-0000-1000-8000-00805f9b34fb",),
                manufacturer_data={0x1234: b"\x01\x02"},
            )
        ]
        self.values = {BATTERY: b"\x64", CONTROL: b"\x00"}
        self.read_errors: dict[str, Exception] = {}
        self.writes: list[tuple[str, str, bytes, bool]] = []
        self.connect_count = 0
        self.disconnect_count = 0

    async def discover(
        self, *, timeout: float, service_uuids: tuple[str, ...] = ()
    ) -> list[DiscoveredDevice]:
        del timeout
        if not service_uuids:
            return list(self.devices)
        requested = {item.casefold() for item in service_uuids}
        return [
            device
            for device in self.devices
            if requested.intersection(item.casefold() for item in device.service_uuids)
        ]

    def connection(self, device: DiscoveredDevice, *, timeout: float) -> FakeConnection:
        del timeout
        return FakeConnection(self, device)
