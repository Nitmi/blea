import pytest

from blea.errors import ConfigError, DeviceUnavailableError, GuardDeniedError, PermissionDeniedError
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
    assert probed["read_success_count"] == 1
    assert probed["read_failure_count"] == 0
    assert probed["reads_remaining"] == 1
    assert probed["next_read_offset"] == 1
    assert probed["status"] == "partial"
    assert probed["reads"][0]["data"]["hex"] == "64"

    next_page = await service.probe("Sensor", timeout=0.1, max_reads=1, read_offset=1)
    assert next_page["read_offset"] == 1
    assert next_page["next_read_offset"] is None
    assert next_page["status"] == "complete"
    assert next_page["reads"][0]["characteristic"] == CONTROL


@pytest.mark.asyncio
async def test_probe_summarizes_partial_read_failures() -> None:
    backend = FakeBackend()
    backend.read_errors[CONTROL] = PermissionDeniedError("pairing required")
    service = BleService(backend)

    result = await service.probe("Sensor", timeout=0.1, max_reads=2)

    assert result["ok"] is True
    assert result["partial"] is True
    assert result["read_success_count"] == 1
    assert result["read_failure_count"] == 1
    assert result["reads_remaining"] == 0
    assert result["failure_reasons"] == {"permission_denied": 1}


@pytest.mark.asyncio
async def test_probe_rejects_non_progressing_windows() -> None:
    service = BleService(FakeBackend())

    with pytest.raises(ConfigError):
        await service.probe("Sensor", max_reads=0)
    with pytest.raises(ConfigError):
        await service.probe("Sensor", read_offset=-1)


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
    manager = SessionManager(BleService(backend), idle_timeout_seconds=120)

    opened = await manager.open("Sensor", timeout=0.1)
    session_id = opened["session_id"]
    await manager.inspect(session_id)
    await manager.read(session_id, BATTERY)
    subscribed = await manager.subscribe(session_id, BATTERY, duration=0)
    await manager.close(session_id)

    assert subscribed["notification_count"] == 2
    assert backend.connect_count == 1
    assert backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_session_manager_lists_and_reaps_idle_connections() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=5)
    opened = await manager.open("Sensor", timeout=0.1)
    session_id = opened["session_id"]
    manager._sessions[session_id].last_used -= 10

    listed = manager.list_sessions()
    assert listed["count"] == 1
    assert listed["idle_timeout_seconds"] == 5

    closed = await manager.close_idle(5)

    assert closed == [session_id]
    assert manager.list_sessions()["count"] == 0
    assert backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_idle_reaper_skips_a_busy_session() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=5)
    opened = await manager.open("Sensor", timeout=0.1)
    session = manager._sessions[opened["session_id"]]
    session.last_used -= 10

    async with session.lock:
        assert await manager.close_idle(5) == []

    assert manager.list_sessions()["count"] == 1
    assert await manager.close_all() == 1
    assert backend.disconnect_count == 1
