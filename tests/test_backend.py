import asyncio
from types import SimpleNamespace

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


class BatchNotificationClient:
    def __init__(self, target: object, *, timeout: float) -> None:
        del target, timeout
        self.is_connected = True
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def start_notify(self, characteristic: str, callback: object) -> None:
        del callback
        self.started.append(characteristic)

    async def stop_notify(self, characteristic: str) -> None:
        self.stopped.append(characteristic)


class HangingBatchNotificationClient(BatchNotificationClient):
    async def start_notify(self, characteristic: str, callback: object) -> None:
        if characteristic == "second":
            await asyncio.sleep(60)
            return
        await super().start_notify(characteristic, callback)


class ExchangeClient:
    def __init__(self, target: object, *, timeout: float) -> None:
        del target, timeout
        self.is_connected = True
        self.events: list[str] = []
        self.callback: object | None = None
        self.notify_characteristic = ""
        self.wrote = asyncio.Event()

    async def start_notify(self, characteristic: str, callback: object) -> None:
        self.events.append("subscribe")
        self.callback = callback
        self.notify_characteristic = characteristic

    async def write_gatt_char(self, characteristic: str, data: bytes, *, response: bool) -> None:
        del characteristic, data, response
        self.events.append("write")
        self.wrote.set()
        assert callable(self.callback)
        self.callback(SimpleNamespace(uuid=self.notify_characteristic), bytearray(b"reply"))

    async def read_gatt_char(self, characteristic: str) -> bytes:
        del characteristic
        self.events.append("read")
        return b"state"

    async def stop_notify(self, characteristic: str) -> None:
        del characteristic
        self.events.append("unsubscribe")


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


@pytest.mark.asyncio
async def test_batch_observe_stops_every_started_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", BatchNotificationClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.1)

    result = await connection.observe(("first", "second"), duration=0)

    assert [item["characteristic"] for item in result["subscriptions"]] == [
        "first",
        "second",
    ]
    assert all(item["ok"] for item in result["subscriptions"])
    assert result["cleanup"]["started_count"] == 2
    assert result["cleanup"]["stopped_count"] == 2
    assert connection._client.started == ["first", "second"]
    assert connection._client.stopped == ["first", "second"]


@pytest.mark.asyncio
async def test_cancelled_batch_observe_cleans_up_started_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", HangingBatchNotificationClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.1)
    task = asyncio.create_task(connection.observe(("first", "second"), duration=60))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection._client.started == ["first"]
    assert connection._client.stopped == ["first"]


@pytest.mark.asyncio
async def test_exchange_subscribes_before_writing_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", ExchangeClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.1)

    notifications, read_back = await connection.exchange(
        "write-characteristic",
        "notify-characteristic",
        b"request",
        duration=0,
        response=True,
        read_back=True,
    )

    assert connection._client.events == ["subscribe", "write", "read", "unsubscribe"]
    assert [item.data for item in notifications] == [b"reply"]
    assert read_back == b"state"


@pytest.mark.asyncio
async def test_cancelled_exchange_stops_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", ExchangeClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.1)
    task = asyncio.create_task(
        connection.exchange(
            "write-characteristic",
            "notify-characteristic",
            b"request",
            duration=60,
            response=True,
            read_back=False,
        )
    )
    await asyncio.wait_for(connection._client.wrote.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection._client.events == ["subscribe", "write", "unsubscribe"]
