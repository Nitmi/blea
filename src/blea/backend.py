from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from bleak import BleakClient, BleakScanner

from blea.errors import translate_backend_error
from blea.models import (
    CharacteristicInfo,
    DescriptorInfo,
    DiscoveredDevice,
    GattProfile,
    Notification,
    ServiceInfo,
)


class BleConnection(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def inspect(self) -> GattProfile: ...

    async def read(self, characteristic: str) -> bytes: ...

    async def write(self, characteristic: str, data: bytes, *, response: bool) -> None: ...

    async def subscribe(self, characteristic: str, *, duration: float) -> list[Notification]: ...


class BleBackend(Protocol):
    name: str

    async def discover(
        self, *, timeout: float, service_uuids: tuple[str, ...] = ()
    ) -> list[DiscoveredDevice]: ...

    def connection(self, device: DiscoveredDevice, *, timeout: float) -> BleConnection: ...


class BleakConnection:
    def __init__(self, device: DiscoveredDevice, *, timeout: float) -> None:
        target = device.native if device.native is not None else device.identifier
        self._client = BleakClient(target, timeout=timeout)
        self._timeout = timeout

    async def connect(self) -> None:
        try:
            await asyncio.wait_for(self._client.connect(), timeout=self._timeout)
            if not self._client.is_connected:
                raise RuntimeError("backend returned without an active connection")
        except Exception as exc:
            raise translate_backend_error(exc, operation="connect") from exc

    async def disconnect(self) -> None:
        try:
            if self._client.is_connected:
                await asyncio.wait_for(self._client.disconnect(), timeout=self._timeout)
        except Exception as exc:
            raise translate_backend_error(exc, operation="disconnect") from exc

    async def inspect(self) -> GattProfile:
        try:
            services: list[ServiceInfo] = []
            for service in self._client.services:
                characteristics: list[CharacteristicInfo] = []
                for characteristic in service.characteristics:
                    descriptors = tuple(
                        DescriptorInfo(
                            uuid=str(descriptor.uuid),
                            handle=int(descriptor.handle),
                            description=getattr(descriptor, "description", None),
                        )
                        for descriptor in characteristic.descriptors
                    )
                    characteristics.append(
                        CharacteristicInfo(
                            uuid=str(characteristic.uuid),
                            handle=int(characteristic.handle),
                            properties=tuple(sorted(characteristic.properties)),
                            description=getattr(characteristic, "description", None),
                            descriptors=descriptors,
                        )
                    )
                services.append(
                    ServiceInfo(
                        uuid=str(service.uuid),
                        handle=int(service.handle),
                        description=getattr(service, "description", None),
                        characteristics=tuple(characteristics),
                    )
                )
            return GattProfile(tuple(services))
        except Exception as exc:
            raise translate_backend_error(exc, operation="service discovery") from exc

    async def read(self, characteristic: str) -> bytes:
        try:
            return bytes(
                await asyncio.wait_for(
                    self._client.read_gatt_char(characteristic), timeout=self._timeout
                )
            )
        except Exception as exc:
            raise translate_backend_error(exc, operation="read") from exc

    async def write(self, characteristic: str, data: bytes, *, response: bool) -> None:
        try:
            await asyncio.wait_for(
                self._client.write_gatt_char(characteristic, data, response=response),
                timeout=self._timeout,
            )
        except Exception as exc:
            raise translate_backend_error(exc, operation="write") from exc

    async def subscribe(self, characteristic: str, *, duration: float) -> list[Notification]:
        notifications: list[Notification] = []

        def callback(sender: object, data: bytearray) -> None:
            sender_uuid = str(getattr(sender, "uuid", characteristic))
            notifications.append(Notification(sender_uuid, bytes(data)))

        try:
            await asyncio.wait_for(
                self._client.start_notify(characteristic, callback), timeout=self._timeout
            )
            await asyncio.sleep(max(duration, 0.0))
            await asyncio.wait_for(self._client.stop_notify(characteristic), timeout=self._timeout)
            return notifications
        except Exception as exc:
            raise translate_backend_error(exc, operation="subscribe") from exc


class BleakBackend:
    name = "bleak"

    async def discover(
        self, *, timeout: float, service_uuids: tuple[str, ...] = ()
    ) -> list[DiscoveredDevice]:
        try:
            discovered = await asyncio.wait_for(
                BleakScanner.discover(
                    timeout=timeout,
                    return_adv=True,
                    service_uuids=list(service_uuids) or None,
                ),
                timeout=timeout + 2.0,
            )
        except Exception as exc:
            raise translate_backend_error(exc, operation="scan") from exc

        devices = []
        for device, advertisement in discovered.values():
            devices.append(
                DiscoveredDevice(
                    identifier=str(device.address),
                    name=device.name,
                    local_name=advertisement.local_name,
                    rssi=advertisement.rssi,
                    tx_power=advertisement.tx_power,
                    service_uuids=tuple(sorted(advertisement.service_uuids)),
                    manufacturer_data=dict(advertisement.manufacturer_data),
                    service_data=dict(advertisement.service_data),
                    native=device,
                )
            )
        return sorted(devices, key=lambda item: item.identifier.casefold())

    def connection(self, device: DiscoveredDevice, *, timeout: float) -> BleakConnection:
        return BleakConnection(device, timeout=timeout)


NotificationCallback = Callable[[Notification], None]
