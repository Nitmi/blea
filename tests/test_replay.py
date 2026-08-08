from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import blea.mcp_server as mcp_server
from blea.backend import BleakBackend
from blea.errors import (
    BleaError,
    ConfigError,
    GuardDeniedError,
    PermissionDeniedError,
    ReplayMissError,
)
from blea.evidence import read_evidence, write_evidence
from blea.replay import ReplayBackend, replay_operation
from blea.service import BleService

FIXTURES = Path(__file__).parent / "fixtures"
COMPLETE = FIXTURES / "evidence" / "complete.blea.jsonl"
PARTIAL = FIXTURES / "evidence" / "partial-failure.blea.jsonl"
DAMAGED = FIXTURES / "evidence" / "damaged-missing-summary.blea.jsonl"
BUTTON = FIXTURES / "diff" / "button-after.blea.jsonl"
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
CUSTOM = "12345678-1234-1234-1234-1234567890ab"


@pytest.mark.asyncio
async def test_replay_backend_runs_read_only_service_operations() -> None:
    backend = ReplayBackend(COMPLETE)
    service = BleService(backend)
    selector = "id:redacted:device-1"

    scan = await service.scan(timeout=0.1)
    inspected = await service.inspect(selector, timeout=0.1)
    probed = await service.probe(selector, timeout=0.1, max_reads=1)
    read = await service.read(selector, BATTERY, timeout=0.1)
    subscribed = await service.subscribe(selector, BATTERY, duration=0, timeout=0.1)
    observed = await service.observe(selector, duration=0, timeout=0.1)

    assert backend.name == "replay"
    assert scan["count"] == 1
    assert scan["devices"][0]["manufacturer_data"]["0x1234"]["hex"] == "0102"
    assert inspected["profile_summary"]["service_count"] == 1
    assert probed["read_page"]["success_count"] == 1
    assert read["data"]["hex"] == "64"
    assert subscribed["notification_count"] == 1
    assert subscribed["notifications"][0]["data"]["hex"] == "63"
    assert observed["status"] == "complete"
    assert observed["notification_count"] == 1


@pytest.mark.asyncio
async def test_replay_preserves_recorded_error_and_reports_missing_evidence() -> None:
    partial = BleService(ReplayBackend(PARTIAL))

    successful = await partial.read("id:AA:BB:CC:DD:EE:FF", BATTERY, timeout=0.1)
    assert successful["data"]["hex"] == "64"
    with pytest.raises(BleaError) as recorded:
        await partial.read("id:AA:BB:CC:DD:EE:FF", CUSTOM, timeout=0.1)
    assert recorded.value.reason == "permission_denied"
    assert recorded.value.exit_code == 8

    with pytest.raises(ReplayMissError, match="profile is absent"):
        await partial.inspect("id:AA:BB:CC:DD:EE:FF", timeout=0.1)
    with pytest.raises(ReplayMissError, match="read is absent"):
        await partial.read("id:AA:BB:CC:DD:EE:FF", "2a00", timeout=0.1)


@pytest.mark.asyncio
async def test_replay_never_simulates_writes_or_exchanges() -> None:
    service = BleService(ReplayBackend(COMPLETE))
    selector = "id:redacted:device-1"

    with pytest.raises(GuardDeniedError, match="never sent or simulated"):
        await service.write(
            selector,
            BATTERY,
            b"danger",
            allow_write=True,
            confirm_device="redacted:device-1",
            timeout=0.1,
        )
    with pytest.raises(GuardDeniedError, match="never sent or simulated"):
        await service.exchange(
            selector,
            BATTERY,
            BATTERY,
            b"danger",
            allow_write=True,
            confirm_device="redacted:device-1",
            duration=0,
            timeout=0.1,
        )


@pytest.mark.asyncio
async def test_replay_observe_preserves_recorded_subscription_failure(tmp_path: Path) -> None:
    events = read_evidence(COMPLETE)
    error = PermissionDeniedError("subscription denied", characteristic=BATTERY).to_dict()
    events[-1]["data"]["observation"] = {
        "subscriptions": [{"characteristic": BATTERY, "ok": False, "error": error}]
    }
    evidence = write_evidence(tmp_path / "failed-subscription.blea.jsonl", events)
    service = BleService(ReplayBackend(evidence))

    observed = await service.observe("id:redacted:device-1", duration=10, timeout=0.1)
    assert observed["status"] == "complete_with_failures"
    assert observed["subscription_summary"]["failure_reasons"] == {"permission_denied": 1}
    assert observed["notification_count"] == 0
    assert observed["cleanup"]["started_count"] == 0

    with pytest.raises(BleaError) as failure:
        await service.subscribe("id:redacted:device-1", BATTERY, duration=10, timeout=0.1)
    assert failure.value.reason == "permission_denied"


@pytest.mark.asyncio
async def test_replay_does_not_invent_uncaptured_subscription_success(tmp_path: Path) -> None:
    events = [event for event in read_evidence(COMPLETE) if event["kind"] != "notification"]
    events[-1]["data"]["event_count"] = len(events)
    events[-1]["data"]["event_counts"].pop("notification")
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    evidence = write_evidence(tmp_path / "no-observation.blea.jsonl", events)
    service = BleService(ReplayBackend(evidence))

    with pytest.raises(ReplayMissError, match="subscription is absent"):
        await service.subscribe("id:redacted:device-1", BATTERY, duration=0, timeout=0.1)

    observed = await service.observe("id:redacted:device-1", duration=0, timeout=0.1)
    assert observed["subscription_summary"]["failure_reasons"] == {"replay_miss": 1}
    assert observed["notification_count"] == 0


@pytest.mark.asyncio
async def test_replay_preserves_captured_observation_cleanup_failure(tmp_path: Path) -> None:
    events = read_evidence(COMPLETE)
    cleanup_error = PermissionDeniedError("unsubscribe denied", operation="unsubscribe").to_dict()
    events[-1]["data"]["observation"] = {
        "subscriptions": [{"characteristic": BATTERY, "ok": True}],
        "cleanup": {
            "ok": False,
            "started_count": 1,
            "stopped_count": 0,
            "failure_count": 1,
            "errors": [cleanup_error],
        },
    }
    evidence = write_evidence(tmp_path / "cleanup-failure.blea.jsonl", events)

    observed = await BleService(ReplayBackend(evidence)).observe(
        "id:redacted:device-1", duration=0, timeout=0.1
    )

    assert observed["status"] == "complete_with_failures"
    assert observed["cleanup"]["ok"] is False
    assert observed["cleanup"]["failure_count"] == 1
    assert observed["cleanup"]["errors"][0]["reason"] == "permission_denied"


@pytest.mark.asyncio
async def test_replay_duration_filters_timeline_and_speed_scales_delays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = ReplayBackend(BUTTON)
    instant_connection = instant.connection(instant.evidence.device, timeout=0.1)
    await instant_connection.connect()
    assert len(await instant_connection.subscribe(BATTERY, duration=4.0)) == 1
    assert len(await instant_connection.subscribe(BATTERY, duration=5.0)) == 2

    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_delay)
    scaled = ReplayBackend(BUTTON, speed=2.0)
    scaled_connection = scaled.connection(scaled.evidence.device, timeout=0.1)
    await scaled_connection.connect()
    notifications = await scaled_connection.subscribe(BATTERY, duration=5.0)

    assert [item.data for item in notifications] == [b"c", b"p"]
    assert len(delays) == 1
    assert 2.4 < delays[0] < 2.5


@pytest.mark.parametrize("speed", [-1, float("nan"), float("inf"), True])
def test_replay_rejects_invalid_speed(speed: object) -> None:
    with pytest.raises(ConfigError, match="replay speed"):
        ReplayBackend(COMPLETE, speed=speed)


def test_replay_rejects_incomplete_evidence() -> None:
    with pytest.raises(ConfigError, match="incomplete"):
        ReplayBackend(DAMAGED)


@pytest.mark.asyncio
async def test_replay_operation_is_deterministic_and_self_describing() -> None:
    first = await replay_operation(COMPLETE, "read", characteristic="2a19")
    second = await replay_operation(COMPLETE, "read", characteristic="2a19")

    assert first == second
    assert first["duration_ms"] == 0
    assert first["replay"] == {
        "schema_version": "1.0",
        "evidence_schema_version": "1.0",
        "evidence": str(COMPLETE.resolve()),
        "capture_id": "22222222-2222-4222-8222-222222222222",
        "capture_status": "complete",
        "timing": "instant",
        "speed": 0.0,
        "read_only": True,
    }


@pytest.mark.asyncio
async def test_replay_operation_never_calls_live_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        raise AssertionError("live BLE discovery must not run during replay")

    monkeypatch.setattr(BleakBackend, "discover", fail_if_called)

    result = await replay_operation(COMPLETE, "scan")
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_replay_runs_workflow_read_only_with_stable_session_ids(tmp_path: Path) -> None:
    workflow = tmp_path / "replay.yaml"
    workflow.write_text(
        f"""
name: offline-acceptance
device: "id:redacted:device-1"
steps:
  - id: profile
    action: inspect
    expect:
      service_count_at_least: 1
  - id: battery
    action: read
    characteristic: "{BATTERY}"
    requires: [profile]
    expect:
      equals_hex: "64"
  - id: events
    action: subscribe
    characteristic: "{BATTERY}"
    duration: 0
    requires: [profile]
    expect:
      notification_count: 1
""".strip(),
        encoding="utf-8",
    )

    first = await replay_operation(COMPLETE, "run", workflow=workflow)
    second = await replay_operation(COMPLETE, "run", workflow=workflow)

    assert first == second
    assert first["ok"] is True
    assert {step["session_id"] for step in first["steps"]} == {"replay-session-1"}


@pytest.mark.asyncio
async def test_replay_mcp_backend_uses_normal_tools_and_marks_results() -> None:
    original_service = mcp_server.service
    original_sessions = mcp_server.sessions
    try:
        mcp_server.configure_backend(ReplayBackend(COMPLETE))
        read = await mcp_server.ble_read("id:redacted:device-1", BATTERY, timeout=0.1)
        denied = await mcp_server.ble_write(
            "id:redacted:device-1",
            BATTERY,
            hex_value="01",
            allow_write=True,
            confirm_device="redacted:device-1",
            timeout=0.1,
        )
        invalid = await mcp_server.ble_write(
            "id:redacted:device-1",
            BATTERY,
            allow_write=True,
            confirm_device="redacted:device-1",
            timeout=0.1,
        )

        assert read["data"]["hex"] == "64"
        assert read["duration_ms"] == 0
        assert read["replay"]["read_only"] is True
        assert denied["reason"] == "guard_denied"
        assert denied["replay"]["read_only"] is True
        assert invalid["reason"] == "config_error"
        assert invalid["replay"]["read_only"] is True
    finally:
        await mcp_server.sessions.close_all()
        mcp_server.service = original_service
        mcp_server.sessions = original_sessions
