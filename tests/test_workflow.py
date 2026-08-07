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

EXCHANGE_WORKFLOW = """
name: guarded-exchange-test
device: "id:AA:BB:CC:DD:EE:FF"
policy:
  allow_write: true
  confirm_device: "AA:BB:CC:DD:EE:FF"
steps:
  - id: inspect
    action: inspect
  - id: command
    action: exchange
    write_characteristic: "12345678-1234-1234-1234-1234567890ab"
    notify_characteristic: "00002a19-0000-1000-8000-00805f9b34fb"
    value:
      text: request
    duration: 0
    response: true
    read_back: true
    dangerous: true
    requires: [inspect]
    expect:
      notification_count: 2
      notifications_contain_utf8: [ack]
      notification_utf8_counts:
        ack: 1
        done: 1
      notifications_contain_hex: [646f6e65]
      final_notification:
        utf8: done
      cleanup:
        ok: true
        started_count: 1
        stopped_count: 1
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

    misconfirmed_path = tmp_path / "misconfirmed-exchange.yaml"
    misconfirmed_path.write_text(
        EXCHANGE_WORKFLOW.replace(
            'confirm_device: "AA:BB:CC:DD:EE:FF"',
            'confirm_device: "00:00:00:00:00:00"',
        ),
        encoding="utf-8",
    )
    misconfirmed_backend = FakeBackend()
    misconfirmed = await run_workflow(
        misconfirmed_path,
        allow_write=True,
        manager=SessionManager(BleService(misconfirmed_backend)),
    )
    assert misconfirmed["ok"] is False
    assert misconfirmed["steps"][-1]["reason"] == "guard_denied"
    assert misconfirmed_backend.writes == []

    allowed_backend = FakeBackend()
    allowed = await run_workflow(
        path,
        allow_write=True,
        manager=SessionManager(BleService(allowed_backend)),
    )
    assert allowed["ok"] is True
    assert allowed_backend.values[CONTROL] == b"\x01"


@pytest.mark.asyncio
async def test_workflow_exchange_is_atomic_and_asserts_notifications(tmp_path: Path) -> None:
    path = tmp_path / "exchange.yaml"
    path.write_text(EXCHANGE_WORKFLOW, encoding="utf-8")

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
    step = allowed["steps"][-1]
    assert step["action"] == "exchange"
    assert [item["data"]["utf8"] for item in step["notifications"]] == ["ack", "done"]
    assert step["cleanup"]["stopped_count"] == 1
    assert allowed_backend.writes == [("AA:BB:CC:DD:EE:FF", CONTROL, b"request", True)]


@pytest.mark.asyncio
async def test_workflow_exchange_reports_notification_assertion_failure(tmp_path: Path) -> None:
    path = tmp_path / "failed-exchange.yaml"
    path.write_text(
        EXCHANGE_WORKFLOW.replace("utf8: done", "utf8: missing"),
        encoding="utf-8",
    )

    result = await run_workflow(
        path,
        allow_write=True,
        manager=SessionManager(BleService(FakeBackend())),
    )

    assert result["ok"] is False
    assert result["failed_step"] == "command"
    assert result["steps"][-1]["reason"] == "assertion_failed"
    assert "final notification" in result["steps"][-1]["message"]


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


def test_workflow_rejects_unguarded_exchange(tmp_path: Path) -> None:
    path = tmp_path / "unsafe-exchange.yaml"
    path.write_text(
        """
name: unsafe-exchange
device: Sensor
steps:
  - id: inspect
    action: inspect
  - id: exchange
    action: exchange
    write_characteristic: control
    notify_characteristic: events
    value: {text: "01"}
    requires: [inspect]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="dangerous"):
        load_workflow(path)


def test_workflow_exchange_requires_an_earlier_step(tmp_path: Path) -> None:
    path = tmp_path / "exchange-without-prerequisite.yaml"
    path.write_text(
        """
name: exchange-without-prerequisite
device: Sensor
steps:
  - id: exchange
    action: exchange
    write_characteristic: control
    notify_characteristic: events
    value: {text: "01"}
    dangerous: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="successful earlier steps"):
        load_workflow(path)
