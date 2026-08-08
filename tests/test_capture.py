from pathlib import Path

import pytest

from blea.errors import ConfigError, PermissionDeniedError
from blea.evidence import read_evidence
from blea.service import BleService
from tests.fakes import CONTROL, FakeBackend


@pytest.mark.asyncio
async def test_capture_writes_one_read_only_evidence_package(tmp_path: Path) -> None:
    backend = FakeBackend()
    destination = tmp_path / "sensor.blea.jsonl"

    result = await BleService(backend).capture(
        "Sensor",
        destination,
        max_reads=1,
        observe_duration=0,
        timeout=0.1,
        redact_identifiers=True,
    )

    assert result["ok"] is True
    assert result["status"] == "complete"
    assert result["output"] == str(destination)
    assert result["read_page"] == {
        "offset": 0,
        "limit": 1,
        "attempted_count": 1,
        "success_count": 1,
        "failure_count": 0,
        "remaining_count": 1,
        "next_offset": 1,
        "has_more": True,
    }
    assert result["device"]["identifier"] == "redacted:device-1"
    assert backend.connect_count == backend.disconnect_count == 1
    assert backend.writes == []

    events = read_evidence(destination)
    assert [event["kind"] for event in events] == [
        "manifest",
        "advertisement",
        "profile",
        "read",
        "notification",
        "summary",
    ]
    assert events[0]["data"]["read_only"] is True
    assert events[3]["data"]["value"]["hex"] == "64"
    assert events[4]["data"]["value"]["hex"] == "64"


@pytest.mark.asyncio
async def test_capture_preserves_read_failures_and_continues_observe(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.read_errors[CONTROL] = PermissionDeniedError("pairing required")
    destination = tmp_path / "partial.blea.jsonl"

    result = await BleService(backend).capture(
        "Sensor", destination, max_reads=2, observe_duration=0, timeout=0.1
    )

    assert result["ok"] is True
    assert result["status"] == "complete_with_failures"
    events = read_evidence(destination)
    reads = [event for event in events if event["kind"] == "read"]
    assert [item["data"]["ok"] for item in reads] == [True, False]
    assert reads[1]["data"]["error"]["reason"] == "permission_denied"
    assert any(event["kind"] == "notification" for event in events)
    assert backend.connect_count == backend.disconnect_count == 1
    assert backend.writes == []


@pytest.mark.asyncio
async def test_capture_serializes_selection_failure(tmp_path: Path) -> None:
    destination = tmp_path / "missing.blea.jsonl"

    result = await BleService(FakeBackend()).capture(
        "name:Missing", destination, observe_duration=0, timeout=0.1
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"]["reason"] == "device_unavailable"
    events = read_evidence(destination)
    assert [event["kind"] for event in events] == ["manifest", "error", "summary"]
    assert events[1]["data"]["operation"] == "discover"


@pytest.mark.asyncio
async def test_capture_rejects_invalid_options_before_scanning(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = BleService(backend)

    with pytest.raises(ConfigError):
        await service.capture("Sensor", tmp_path / "bad.blea.jsonl", max_reads=0)
    with pytest.raises(ConfigError):
        await service.capture("Sensor", tmp_path / "bad.blea.jsonl", read_offset=-1)
    with pytest.raises(ConfigError):
        await service.capture("Sensor", tmp_path / "bad.blea.jsonl", observe_duration=-1)
    assert backend.connect_count == backend.disconnect_count == 0
