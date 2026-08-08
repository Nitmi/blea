from __future__ import annotations

import inspect
import json
from contextvars import ContextVar
from pathlib import Path

import pytest

import blea.mcp_server as mcp_server
from blea import __version__
from blea.cli import build_parser
from blea.mcp_server import mcp
from blea.service import BleService, SessionManager
from tests.fakes import FakeBackend

ROOT = Path(__file__).parents[1]


def test_portable_and_codex_manifests_reference_same_server() -> None:
    portable = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    portable_mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert portable["name"] == codex["name"] == "blea"
    assert portable["version"] == __version__
    assert codex["version"].split("+", 1)[0] == __version__
    assert "+codex." in codex["version"]
    assert portable_mcp["mcpServers"]["blea"]["command"] == "ble"
    assert codex_mcp["mcpServers"]["blea"] == {"command": "ble", "args": ["mcp"]}


def test_cli_exposes_product_surfaces() -> None:
    parser = build_parser()
    assert parser.parse_args(["scan", "--json"]).subcommand == "scan"
    probe = parser.parse_args(["probe", "--device", "Sensor", "--read-offset", "32"])
    assert probe.read_offset == 32
    assert probe.include_profile is True
    compact_probe = parser.parse_args(["probe", "--device", "Sensor", "--no-include-profile"])
    assert compact_probe.include_profile is False
    capture = parser.parse_args(
        [
            "capture",
            "--device",
            "Sensor",
            "--output",
            "sensor.blea.jsonl",
            "--redact-identifiers",
        ]
    )
    assert capture.max_reads == 128
    assert capture.redact_identifiers is True
    diff = parser.parse_args(["diff", "before.blea.jsonl", "after.blea.jsonl", "--fail-on-change"])
    assert diff.rssi_tolerance == 5.0
    assert diff.fail_on_change is True
    replay = parser.parse_args(
        [
            "replay",
            "capture.blea.jsonl",
            "--speed",
            "2",
            "read",
            "--characteristic",
            "2a19",
        ]
    )
    assert replay.replay_operation == "read"
    assert replay.speed == 2.0
    assert replay.device is None
    observe = parser.parse_args(["observe", "--device", "Sensor", "--characteristic", "2a19"])
    assert observe.characteristics == ["2a19"]
    exchange = parser.parse_args(
        [
            "exchange",
            "--device",
            "Sensor",
            "--write-characteristic",
            "control",
            "--notify-characteristic",
            "events",
            "--text",
            "ping",
        ]
    )
    assert exchange.text_value == "ping"
    assert exchange.duration == 5.0
    assert parser.parse_args(["mcp"]).subcommand == "mcp"


@pytest.mark.asyncio
async def test_mcp_exposes_one_shot_and_stateful_tools() -> None:
    names = {tool.name for tool in await mcp.list_tools()}
    assert {
        "ble_doctor",
        "ble_scan",
        "ble_inspect",
        "ble_probe",
        "ble_capture",
        "ble_diff",
        "ble_replay",
        "ble_observe",
        "ble_exchange",
        "ble_read",
        "ble_subscribe",
        "ble_write",
        "ble_session_open",
        "ble_session_read",
        "ble_session_close",
        "ble_session_observe",
        "ble_session_exchange",
        "ble_session_list",
        "ble_session_close_all",
    }.issubset(names)
    assert inspect.signature(mcp_server.ble_probe).parameters["include_profile"].default is False


def test_mcp_initialize_reports_blea_version() -> None:
    options = mcp._mcp_server.create_initialization_options()
    assert options.server_name == "BLEA"
    assert options.server_version == __version__


@pytest.mark.asyncio
async def test_mcp_lifespan_closes_open_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    manager = SessionManager(BleService(backend), idle_timeout_seconds=None)
    await manager.open("Sensor", timeout=0.1)
    monkeypatch.setattr(mcp_server, "sessions", manager)

    async with mcp_server.mcp_lifespan(mcp):
        assert manager.list_sessions()["count"] == 1

    assert manager.list_sessions()["count"] == 0
    assert backend.disconnect_count == 1


@pytest.mark.asyncio
async def test_mcp_ble_calls_isolate_request_context() -> None:
    marker = ContextVar("marker", default="outside")

    async def change_context() -> dict[str, object]:
        marker.set("inside")
        return {"ok": True}

    assert await mcp_server._safe(change_context()) == {"ok": True}
    assert marker.get() == "outside"
