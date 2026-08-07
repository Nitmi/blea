from __future__ import annotations

import json
from pathlib import Path

import pytest

from blea.cli import build_parser
from blea.mcp_server import mcp

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
    }.issubset(names)
