from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path

import pytest

import blea.mcp_server as mcp_server
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
    assert portable["version"] == codex["version"]
    assert portable_mcp["mcpServers"]["blea"]["command"] == "ble"
    assert codex_mcp["mcpServers"]["blea"] == {"command": "ble", "args": ["mcp"]}


def test_cli_exposes_product_surfaces() -> None:
    parser = build_parser()
    assert parser.parse_args(["scan", "--json"]).subcommand == "scan"
    probe = parser.parse_args(["probe", "--device", "Sensor", "--read-offset", "32"])
    assert probe.read_offset == 32
    assert parser.parse_args(["mcp"]).subcommand == "mcp"


@pytest.mark.asyncio
async def test_mcp_exposes_one_shot_and_stateful_tools() -> None:
    names = {tool.name for tool in await mcp.list_tools()}
    assert {
        "ble_doctor",
        "ble_scan",
        "ble_inspect",
        "ble_probe",
        "ble_read",
        "ble_subscribe",
        "ble_write",
        "ble_session_open",
        "ble_session_read",
        "ble_session_close",
        "ble_session_list",
        "ble_session_close_all",
    }.issubset(names)


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
