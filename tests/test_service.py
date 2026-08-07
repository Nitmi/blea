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
    assert result["operation_timeout_seconds"] == 0.1
    assert result["timeout_scope"] == "per_backend_operation"
    assert backend.connect_count == backend.disconnect_count == 1

    probed = await service.probe("Sensor", timeout=0.1, max_reads=1)
    assert probed["profile_summary"] == {
        "service_count": 1,
        "characteristic_count": 2,
        "readable_characteristic_count": 2,
        "writable_characteristic_count": 1,
        "subscribable_characteristic_count": 1,
    }
    assert probed["profile_included"] is True
    assert probed["profile"]["service_count"] == 1
    assert probed["read_page"] == {
        "offset": 0,
        "limit": 1,
        "attempted_count": 1,
        "success_count": 1,
        "failure_count": 0,
        "remaining_count": 1,
        "next_offset": 1,
        "has_more": True,
        "has_failures": False,
        "failure_reasons": {},
    }
    assert probed["next_read_offset"] == 1
    assert probed["status"] == "more"
    assert probed["reads"][0]["data"]["hex"] == "64"

    next_page = await service.probe(
        "Sensor", timeout=0.1, max_reads=1, read_offset=1, include_profile=False
    )
    assert next_page["read_page"]["offset"] == 1
    assert next_page["profile_included"] is False
    assert "profile" not in next_page
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
    assert result["status"] == "complete_with_failures"
    assert result["read_page"]["has_more"] is False
    assert result["read_page"]["has_failures"] is True
    assert result["read_page"]["success_count"] == 1
    assert result["read_page"]["failure_count"] == 1
    assert result["read_page"]["failure_reasons"] == {"permission_denied": 1}


@pytest.mark.asyncio
async def test_probe_rejects_non_progressing_windows() -> None:
    service = BleService(FakeBackend())

    with pytest.raises(ConfigError):
        await service.probe("Sensor", max_reads=0)
    with pytest.raises(ConfigError):
        await service.probe("Sensor", read_offset=-1)


@pytest.mark.asyncio
async def test_observe_auto_selects_subscribable_characteristics() -> None:
    backend = FakeBackend()
    service = BleService(backend)

    result = await service.observe("Sensor", duration=0, timeout=0.1)

    assert result["status"] == "complete"
    assert result["selection"] == {
        "mode": "auto",
        "candidate_count": 1,
        "requested_count": 1,
        "selected_count": 1,
    }
    assert result["subscription_summary"] == {
        "attempted_count": 1,
        "success_count": 1,
        "failure_count": 0,
        "failure_reasons": {},
    }
    assert result["notification_count"] == 1
    assert result["notification_counts"] == {BATTERY: 1}
    assert result["cleanup"]["failure_count"] == 0
    assert backend.connect_count == backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_observe_keeps_explicit_selection_failures() -> None:
    service = BleService(FakeBackend())

    result = await service.observe(
        "Sensor",
        characteristics=(CONTROL, "missing-characteristic", BATTERY),
        duration=0,
        timeout=0.1,
    )

    assert result["status"] == "complete_with_failures"
    assert result["subscription_summary"]["attempted_count"] == 3
    assert result["subscription_summary"]["success_count"] == 1
    assert result["subscription_summary"]["failure_count"] == 2
    assert result["subscription_summary"]["failure_reasons"] == {"config_error": 2}
    assert [item["characteristic"] for item in result["subscriptions"]] == [
        CONTROL,
        "missing-characteristic",
        BATTERY,
    ]


@pytest.mark.asyncio
async def test_observe_rejects_negative_duration_before_connecting() -> None:
    backend = FakeBackend()

    with pytest.raises(ConfigError):
        await BleService(backend).observe("Sensor", duration=-1)

    assert backend.connect_count == 0


@pytest.mark.asyncio
async def test_session_observe_reuses_the_open_connection() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=120)
    opened = await manager.open("Sensor", timeout=0.1)

    result = await manager.observe(opened["session_id"], duration=0)
    await manager.close(opened["session_id"])

    assert result["operation"] == "session_observe"
    assert result["notification_count"] == 1
    assert backend.connect_count == backend.disconnect_count == 1


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
async def test_exchange_requires_guard_and_reports_notifications() -> None:
    backend = FakeBackend()
    service = BleService(backend)

    with pytest.raises(GuardDeniedError):
        await service.exchange(
            "Sensor",
            CONTROL,
            BATTERY,
            b"request",
            duration=0,
            timeout=0.1,
        )

    assert backend.connect_count == 0

    result = await service.exchange(
        "Sensor",
        CONTROL,
        BATTERY,
        b"request",
        duration=0,
        allow_write=True,
        confirm_device="AA:BB:CC:DD:EE:FF",
        read_back=True,
        timeout=0.1,
    )

    assert result["operation"] == "exchange"
    assert result["write_characteristic"] == CONTROL
    assert result["notify_characteristic"] == BATTERY
    assert result["notification_count"] == 2
    assert [item["data"]["utf8"] for item in result["notifications"]] == ["ack", "done"]
    assert result["read_back"]["utf8"] == "request"
    assert backend.connect_count == backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_stateful_session_reuses_one_connection() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=120)

    opened = await manager.open("Sensor", timeout=0.1)
    session_id = opened["session_id"]
    assert opened["timeout_scope"] == "per_backend_operation"
    await manager.inspect(session_id)
    await manager.read(session_id, BATTERY)
    subscribed = await manager.subscribe(session_id, BATTERY, duration=0)
    await manager.close(session_id)

    assert subscribed["notification_count"] == 2
    assert backend.connect_count == 1
    assert backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_session_exchange_uses_one_locked_connection() -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=120)
    opened = await manager.open("Sensor", timeout=0.1)

    result = await manager.exchange(
        opened["session_id"],
        CONTROL,
        BATTERY,
        b"request",
        duration=0,
        allow_write=True,
        confirm_device="AA:BB:CC:DD:EE:FF",
    )
    await manager.close(opened["session_id"])

    assert result["operation"] == "session_exchange"
    assert result["session_id"] == opened["session_id"]
    assert result["notification_count"] == 2
    assert backend.connect_count == backend.disconnect_count == 1


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
    assert listed["sessions"][0]["operation_timeout_seconds"] == 0.1

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
