import asyncio

import pytest

import blea.backend
from blea.backend import BleakConnection, _trusted_uuid_description
from blea.errors import BleTimeoutError
from blea.models import DiscoveredDevice


class HangingClient:
    def __init__(self, target: object, *, timeout: float) -> None:
        del target, timeout
        self.is_connected = True

    async def read_gatt_char(self, characteristic: str) -> bytes:
        del characteristic
        await asyncio.sleep(60)
        return b""


class NotificationClient:
    def __init__(self, target: object, *, timeout: float) -> None:
        del target, timeout
        self.is_connected = True
        self.started = False
        self.stopped = False

    async def start_notify(self, characteristic: str, callback: object) -> None:
        del characteristic, callback
        self.started = True

    async def stop_notify(self, characteristic: str) -> None:
        del characteristic
        self.stopped = True


@pytest.mark.asyncio
async def test_connection_enforces_backend_operation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", HangingClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.01)

    with pytest.raises(BleTimeoutError):
        await connection.read("test-characteristic")


def test_custom_uuid_does_not_inherit_a_false_sig_description() -> None:
    custom = "00000001-1fb5-459e-8fcc-c5c9c331914b"
    battery = "00002a19-0000-1000-8000-00805f9b34fb"

    assert _trusted_uuid_description(custom, "SDP") is None
    assert _trusted_uuid_description(battery, "Battery Level") == "Battery Level"


@pytest.mark.asyncio
async def test_cancelled_subscription_stops_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", NotificationClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.1)
    task = asyncio.create_task(connection.subscribe("test-characteristic", duration=60))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection._client.started is True
    assert connection._client.stopped is True
