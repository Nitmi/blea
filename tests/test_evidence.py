from pathlib import Path

import pytest

from blea.errors import ConfigError, PermissionDeniedError
from blea.evidence import (
    EvidenceWriter,
    read_evidence,
    validate_events,
    validate_evidence,
    write_evidence,
)
from blea.models import bytes_evidence

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"


def test_golden_fixtures_validate() -> None:
    for name in ("minimal", "complete", "partial-failure"):
        result = validate_evidence(FIXTURES / f"{name}.blea.jsonl")
        assert result["complete"] is True

    incomplete = validate_evidence(
        FIXTURES / "damaged-missing-summary.blea.jsonl", require_complete=False
    )
    assert incomplete["complete"] is False

    with pytest.raises(ConfigError, match="summary is missing"):
        read_evidence(FIXTURES / "damaged-missing-summary.blea.jsonl")


def test_writer_normalizes_and_redacts_stably(tmp_path: Path) -> None:
    writer = EvidenceWriter(
        capture_id="55555555-5555-4555-8555-555555555555",
        redact_identifiers=True,
        clock=lambda: "2026-08-08T00:00:00.000Z",
    )
    writer.add(
        "manifest",
        source={
            "blea_version": "0.4.0",
            "platform": "test",
            "python": "3.12.0",
            "bleak_version": "1.1.1",
            "backend": "fake",
        },
        data={
            "parameters": {"selector": "id:AA:BB:CC:DD:EE:FF"},
            "read_only": True,
        },
    )
    device = {
        "identifier": "AA:BB:CC:DD:EE:FF",
        "name": "Sensor",
        "local_name": "Sensor",
        "rssi": -42,
        "tx_power": None,
        "service_uuids": [],
        "manufacturer_data": {},
        "service_data": {},
    }
    writer.add("advertisement", device=device)
    writer.add(
        "read",
        data={
            "characteristic": "2A19",
            "ok": True,
            "value": bytes_evidence(b"d"),
        },
    )
    writer.add(
        "summary",
        data={
            "status": "complete",
            "complete": True,
            "event_count": 4,
            "event_counts": {"advertisement": 1, "manifest": 1, "read": 1, "summary": 1},
        },
    )
    assert writer.events[0]["data"]["parameters"]["selector"] == "id:redacted:device-1"
    assert writer.events[1]["device"]["identifier"] == "redacted:device-1"
    assert writer.events[2]["data"]["characteristic"] == "00002a19-0000-1000-8000-00805f9b34fb"
    destination = writer.write(tmp_path / "capture.blea.jsonl")
    assert read_evidence(destination)[-1]["kind"] == "summary"


def test_invalid_byte_evidence_is_rejected() -> None:
    events = read_evidence(FIXTURES / "complete.blea.jsonl")
    events[3]["data"]["value"]["hex"] = "65"
    with pytest.raises(ConfigError, match="encodings disagree"):
        validate_events(events)


def test_failed_read_requires_structured_error() -> None:
    error = PermissionDeniedError("pairing required", operation="read").to_dict()
    writer = EvidenceWriter(
        capture_id="66666666-6666-4666-8666-666666666666",
        clock=lambda: "2026-08-08T00:00:00.000Z",
    )
    writer.add(
        "manifest",
        source={
            "blea_version": "0.4.0",
            "platform": "test",
            "python": "3.12.0",
            "bleak_version": "1.1.1",
            "backend": "fake",
        },
        data={"parameters": {}, "read_only": True},
    )
    writer.add(
        "read",
        data={"characteristic": "2a19", "ok": False, "error": error},
    )
    writer.add(
        "summary",
        data={
            "status": "complete_with_failures",
            "complete": True,
            "event_count": 3,
            "event_counts": {"manifest": 1, "read": 1, "summary": 1},
        },
    )
    assert validate_events(writer.events)["complete"] is True


def test_write_requires_extension_and_existing_parent(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="extension"):
        write_evidence(tmp_path / "capture.jsonl", [])
    with pytest.raises(ConfigError, match="directory does not exist"):
        write_evidence(tmp_path / "missing" / "capture.blea.jsonl", [])
