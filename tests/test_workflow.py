from pathlib import Path

import pytest

from blea.errors import ConfigError
from blea.service import BleService, SessionManager
from blea.workflow import load_workflow, run_workflow
from tests.fakes import CONTROL, FakeBackend

WORKFLOW = """
name: guarded-test
device: "id:AA:BB:CC:DD:EE:FF"
policy:
  allow_write: true
  confirm_device: "AA:BB:CC:DD:EE:FF"
steps:
  - id: inspect
    action: inspect
    expect:
      service_count_at_least: 1
  - id: current
    action: read
    characteristic: "12345678-1234-1234-1234-1234567890ab"
    requires: [inspect]
    expect:
      equals_hex: "00"
  - id: command
    action: write
    characteristic: "12345678-1234-1234-1234-1234567890ab"
    value:
      hex: "01"
    dangerous: true
    requires: [inspect, current]
    read_back: true
"""


@pytest.mark.asyncio
async def test_workflow_write_needs_file_and_invocation_gates(tmp_path: Path) -> None:
    path = tmp_path / "flow.yaml"
    path.write_text(WORKFLOW, encoding="utf-8")

    blocked_backend = FakeBackend()
    blocked = await run_workflow(
        path,
        allow_write=False,
        manager=SessionManager(BleService(blocked_backend)),
    )
    assert blocked["ok"] is False
    assert blocked["failed_step"] == "command"
    assert blocked["steps"][-1]["reason"] == "guard_denied"
    assert blocked_backend.writes == []

    allowed_backend = FakeBackend()
    allowed = await run_workflow(
        path,
        allow_write=True,
        manager=SessionManager(BleService(allowed_backend)),
    )
    assert allowed["ok"] is True
    assert allowed_backend.values[CONTROL] == b"\x01"


def test_workflow_rejects_unguarded_write(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        """
name: unsafe
device: Sensor
steps:
  - id: write
    action: write
    characteristic: abcd
    value: {hex: "01"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="dangerous"):
        load_workflow(path)
