from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from blea.diff import diff_evidence
from blea.evidence import read_evidence
from blea.replay import ReplayBackend
from blea.service import BleService

WINDOWS_CAPTURE = Path(__file__).parent / "fixtures" / "platform" / "windows-esp32-s3.blea.jsonl"
WINDOWS_CAPTURE_SHA256 = "83e8e15c9ae277b815b6e415987deb8aa1951cfaffb8a2c8c95280911048baed"


def test_windows_hardware_capture_passes_public_artifact_gate() -> None:
    raw = WINDOWS_CAPTURE.read_bytes()
    text = raw.decode("utf-8")
    events = read_evidence(WINDOWS_CAPTURE)
    manifest = events[0]
    advertisement = events[1]["device"]

    assert hashlib.sha256(raw).hexdigest() == WINDOWS_CAPTURE_SHA256
    assert b"\r" not in raw
    assert manifest["source"]["platform"].startswith("Windows-")
    assert manifest["data"]["parameters"]["redact_identifiers"] is True
    assert manifest["data"]["parameters"]["selector"] == "id:redacted:device-1"
    assert advertisement["identifier"] == "redacted:device-1"
    assert events[-1]["data"]["event_count"] == 22

    assert re.search(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", text) is None
    assert re.search(r"(?i)[a-z]:\\\\|users\\\\", text) is None
    for sensitive in ("serial", "token", "password", "secret", "device-2"):
        assert sensitive not in text.casefold()


@pytest.mark.asyncio
async def test_windows_hardware_capture_replays_offline() -> None:
    backend = ReplayBackend(WINDOWS_CAPTURE)
    service = BleService(backend)
    selector = "id:redacted:device-1"

    inspected = await service.inspect(selector, timeout=0.1)
    probed = await service.probe(
        selector,
        timeout=0.1,
        max_reads=128,
        include_profile=False,
    )
    observed = await service.observe(selector, duration=3, timeout=0.1)
    identical = diff_evidence(WINDOWS_CAPTURE, WINDOWS_CAPTURE)

    assert inspected["profile_summary"] == {
        "service_count": 4,
        "characteristic_count": 14,
        "readable_characteristic_count": 13,
        "writable_characteristic_count": 2,
        "subscribable_characteristic_count": 4,
    }
    assert probed["read_page"]["attempted_count"] == 13
    assert probed["read_page"]["success_count"] == 13
    assert probed["next_read_offset"] is None
    assert observed["status"] == "complete_with_failures"
    assert observed["subscription_summary"]["success_count"] == 3
    assert observed["subscription_summary"]["failure_reasons"] == {"permission_denied": 1}
    assert observed["notification_count"] == 4
    assert observed["cleanup"]["ok"] is True
    assert identical["status"] == "identical"
