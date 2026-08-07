from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from bleak import BleakClient, BleakScanner

from blea.errors import translate_backend_error
from blea.models import (
    CharacteristicInfo,
    DescriptorInfo,
    DiscoveredDevice,
    GattProfile,
    Notification,
    ServiceInfo,
    uuid_namespace,
)


def _trusted_uuid_description(uuid: str, description: str | None) -> str | None:
    # Bleak resolves custom UUIDs by their leading 16 bits, which can produce false SIG names.
    return description if uuid_namespace(uuid) == "bluetooth-base" else None


class BleConnection(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def inspect(self) -> GattProfile: ...

    async def read(self, characteristic: str) -> bytes: ...

    async def write(self, characteristic: str, data: bytes, *, response: bool) -> None: ...

    async def subscribe(self, characteristic: str, *, duration: float) -> list[Notification]: ...

    async def exchange(
        self,
        write_characteristic: str,
        notify_characteristic: str,
        data: bytes,
        *,
        duration: float,
        response: bool,
        read_back: bool,
    ) -> tuple[list[Notification], bytes | None]: ...

    async def observe(
        self, characteristics: tuple[str, ...], *, duration: float
    ) -> dict[str, Any]: ...


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
                    descriptors = []
                    for descriptor in characteristic.descriptors:
                        descriptor_uuid = str(descriptor.uuid)
                        descriptors.append(
                            DescriptorInfo(
                                uuid=descriptor_uuid,
                                handle=int(descriptor.handle),
                                description=_trusted_uuid_description(
                                    descriptor_uuid, getattr(descriptor, "description", None)
                                ),
                            )
                        )
                    characteristic_uuid = str(characteristic.uuid)
                    characteristics.append(
                        CharacteristicInfo(
                            uuid=characteristic_uuid,
                            handle=int(characteristic.handle),
                            properties=tuple(sorted(characteristic.properties)),
                            description=_trusted_uuid_description(
                                characteristic_uuid,
                                getattr(characteristic, "description", None),
                            ),
                            descriptors=tuple(descriptors),
                        )
                    )
                service_uuid = str(service.uuid)
                services.append(
                    ServiceInfo(
                        uuid=service_uuid,
                        handle=int(service.handle),
                        description=_trusted_uuid_description(
                            service_uuid, getattr(service, "description", None)
                        ),
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
        notify_started = False

        def callback(sender: object, data: bytearray) -> None:
            sender_uuid = str(getattr(sender, "uuid", characteristic))
            notifications.append(Notification(sender_uuid, bytes(data)))

        try:
            await asyncio.wait_for(
                self._client.start_notify(characteristic, callback), timeout=self._timeout
            )
            notify_started = True
            try:
                await asyncio.sleep(max(duration, 0.0))
            finally:
                if notify_started:
                    await asyncio.wait_for(
                        self._client.stop_notify(characteristic), timeout=self._timeout
                    )
            return notifications
        except Exception as exc:
            raise translate_backend_error(exc, operation="subscribe") from exc

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
        notifications: list[Notification] = []

        def callback(sender: object, value: bytearray) -> None:
            sender_uuid = str(getattr(sender, "uuid", notify_characteristic))
            notifications.append(Notification(sender_uuid, bytes(value)))

        try:
            await asyncio.wait_for(
                self._client.start_notify(notify_characteristic, callback), timeout=self._timeout
            )
        except Exception as exc:
            raise translate_backend_error(exc, operation="subscribe") from exc

        cleanup_error: Exception | None = None
        try:
            await self.write(write_characteristic, data, response=response)
            read_back_data = await self.read(write_characteristic) if read_back else None
            await asyncio.sleep(max(duration, 0.0))
        finally:
            try:
                await asyncio.wait_for(
                    self._client.stop_notify(notify_characteristic), timeout=self._timeout
                )
            except Exception as exc:
                cleanup_error = translate_backend_error(exc, operation="unsubscribe")

        if cleanup_error is not None:
            raise cleanup_error
        return notifications, read_back_data

    async def observe(self, characteristics: tuple[str, ...], *, duration: float) -> dict[str, Any]:
        notifications: list[Notification] = []
        subscriptions: list[dict[str, Any]] = []
        started: list[str] = []
        cleanup_errors: list[dict[str, Any]] = []

        def callback_factory(default_characteristic: str) -> Callable[[object, bytearray], None]:
            def callback(sender: object, data: bytearray) -> None:
                sender_uuid = str(getattr(sender, "uuid", default_characteristic))
                notifications.append(Notification(sender_uuid, bytes(data)))

            return callback

        try:
            for characteristic in characteristics:
                try:
                    await asyncio.wait_for(
                        self._client.start_notify(characteristic, callback_factory(characteristic)),
                        timeout=self._timeout,
                    )
                except Exception as exc:
                    error = translate_backend_error(exc, operation="subscribe").to_dict()
                    subscriptions.append(
                        {"characteristic": characteristic, "ok": False, "error": error}
                    )
                else:
                    started.append(characteristic)
                    subscriptions.append({"characteristic": characteristic, "ok": True})
            if started:
                await asyncio.sleep(max(duration, 0.0))
        finally:
            for characteristic in started:
                try:
                    await asyncio.wait_for(
                        self._client.stop_notify(characteristic), timeout=self._timeout
                    )
                except Exception as exc:
                    cleanup_errors.append(
                        translate_backend_error(exc, operation="unsubscribe").to_dict()
                    )

        return {
            "subscriptions": subscriptions,
            "notifications": notifications,
            "cleanup": {
                "ok": not cleanup_errors,
                "started_count": len(started),
                "stopped_count": len(started) - len(cleanup_errors),
                "failure_count": len(cleanup_errors),
                "errors": cleanup_errors,
            },
        }


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
