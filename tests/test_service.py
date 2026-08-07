import pytest

from blea.errors import DeviceUnavailableError, GuardDeniedError
from blea.models import DiscoveredDevice
from blea.service import BleService, SessionManager
from tests.fakes import BATTERY, CONTROL, FakeBackend


@pytest.mark.asyncio
async def test_scan_and_read_preserve_structured_evidence() -> None:
    backend = FakeBackend()
    service = BleService(backend)

    scanned = await service.scan(timeout=0.1)
    assert scanned["count"] == 1
    assert scanned["devices"][0]["manufacturer_data"]["0x1234"]["hex"] == "0102"

    result = await service.read("id:AA:BB:CC:DD:EE:FF", BATTERY, timeout=0.1)
    assert result["data"] == {"length": 1, "hex": "64", "base64": "ZA==", "utf8": "d"}
    assert backend.connect_count == backend.disconnect_count == 1

    probed = await service.probe("Sensor", timeout=0.1, max_reads=1)
    assert probed["readable_count"] == 2
    assert probed["reads_attempted"] == 1
    assert probed["reads"][0]["data"]["hex"] == "64"


@pytest.mark.asyncio
async def test_device_names_must_resolve_uniquely() -> None:
    devices = [
        DiscoveredDevice("AA:BB:CC:DD:EE:01", name="Sensor"),
        DiscoveredDevice("AA:BB:CC:DD:EE:02", name="Sensor"),
    ]
    service = BleService(FakeBackend(devices))

    with pytest.raises(DeviceUnavailableError) as raised:
        await service.resolve("name:Sensor", timeout=0.1)
    assert raised.value.details["candidates"] == [
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    ]


@pytest.mark.asyncio
async def test_write_requires_enablement_and_exact_identifier() -> None:
    backend = FakeBackend()
    service = BleService(backend)

    with pytest.raises(GuardDeniedError):
        await service.write("Sensor", CONTROL, b"\x01", timeout=0.1)
    with pytest.raises(GuardDeniedError):
        await service.write(
            "Sensor",
            CONTROL,
            b"\x01",
            allow_write=True,
            confirm_device="Sensor",
            timeout=0.1,
        )

    result = await service.write(
        "Sensor",
        CONTROL,
        b"\x01",
        allow_write=True,
        confirm_device="AA:BB:CC:DD:EE:FF",
        read_back=True,
        timeout=0.1,
    )
    assert result["read_back"]["hex"] == "01"
    assert backend.writes == [("AA:BB:CC:DD:EE:FF", CONTROL, b"\x01", True)]


@pytest.mark.asyncio
async def test_stateful_session_reuses_one_connection() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend))

    opened = await manager.open("Sensor", timeout=0.1)
    session_id = opened["session_id"]
    await manager.inspect(session_id)
    await manager.read(session_id, BATTERY)
    subscribed = await manager.subscribe(session_id, BATTERY, duration=0)
    await manager.close(session_id)

    assert subscribed["notification_count"] == 2
    assert backend.connect_count == 1
    assert backend.disconnect_count == 1
