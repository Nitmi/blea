from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.check_agent_package import check_agent_packages, validate_agent_package

ROOT = Path(__file__).parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _valid_plugin(parent: Path) -> Path:
    plugin = parent / "blea"
    manifest = {
        "name": "blea",
        "version": "0.6.0+codex.1",
        "description": "BLE diagnostics",
        "author": {"name": "Nitmi", "url": "https://github.com/Nitmi"},
        "homepage": "https://github.com/Nitmi/blea",
        "repository": "https://github.com/Nitmi/blea",
        "license": "MIT",
        "keywords": ["ble"],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "BLEA",
            "shortDescription": "Diagnose Bluetooth Low Energy devices.",
            "longDescription": "Agent-first BLE diagnostics.",
            "developerName": "Nitmi",
            "category": "Developer Tools",
            "capabilities": ["Read"],
            "websiteURL": "https://github.com/Nitmi/blea",
            "brandColor": "#2563EB",
            "defaultPrompt": ["Inspect a nearby BLE device."],
        },
    }
    _write(plugin / ".codex-plugin" / "plugin.json", json.dumps(manifest))
    _write(
        plugin / ".mcp.json",
        json.dumps({"mcpServers": {"blea": {"command": "ble", "args": ["mcp"]}}}),
    )
    _write(
        plugin / "skills" / "ble" / "SKILL.md",
        "---\n"
        "name: ble\n"
        "description: Diagnose Bluetooth Low Energy devices.\n"
        "---\n\n"
        "# BLE\n\n"
        "Inspect safely.\n",
    )
    agent = {
        "interface": {
            "display_name": "BLEA",
            "short_description": "Diagnose Bluetooth Low Energy devices",
            "default_prompt": "Use $ble to inspect a nearby device safely.",
        }
    }
    _write(plugin / "skills" / "ble" / "agents" / "openai.yaml", yaml.safe_dump(agent))
    return plugin


def _read_manifest(plugin: Path) -> dict[str, object]:
    return json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))


def test_repository_agent_packages_pass_local_validation() -> None:
    report = check_agent_packages(ROOT)

    assert report["ok"] is True
    assert report["reason"] == "agent_packages_valid"
    assert report["package_count"] == 2
    assert [package["path"] for package in report["packages"]] == [".", "plugins/blea"]


def test_agent_package_accepts_minimal_valid_fixture(tmp_path: Path) -> None:
    plugin = _valid_plugin(tmp_path)

    assert validate_agent_package(plugin) == []


def test_agent_package_rejects_manifest_contract_drift(tmp_path: Path) -> None:
    plugin = _valid_plugin(tmp_path)
    manifest = _read_manifest(plugin)
    manifest["version"] = "0.6"
    manifest["hooks"] = "./hooks.json"
    manifest["description"] = "[TODO: describe plugin]"
    manifest["license"] = ""
    manifest["apps"] = "./.app.json"
    _write(plugin / ".codex-plugin" / "plugin.json", json.dumps(manifest))

    errors = validate_agent_package(plugin)

    assert "plugin manifest version must be strict SemVer" in errors
    assert "plugin manifest field `hooks` is not supported" in errors
    assert "plugin manifest contains a [TODO: ...] placeholder" in errors
    assert "plugin manifest field `license` must be a non-empty string" in errors
    assert "BLEA plugin manifest must not declare apps" in errors


def test_agent_package_rejects_skill_metadata_drift(tmp_path: Path) -> None:
    plugin = _valid_plugin(tmp_path)
    _write(
        plugin / "skills" / "ble" / "SKILL.md",
        "---\nname: wrong\ndescription: Use <device>.\nmetadata: {}\n---\n\n# BLE\n",
    )
    agent_path = plugin / "skills" / "ble" / "agents" / "openai.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["interface"]["short_description"] = "Too short"
    agent["interface"]["default_prompt"] = "Inspect a device."
    _write(agent_path, yaml.safe_dump(agent))

    errors = validate_agent_package(plugin)

    assert "skill `ble` field `metadata` is not supported" in errors
    assert "skill `ble` frontmatter name must match its directory" in errors
    assert "skill `ble` description cannot contain angle brackets" in errors
    assert any("short_description` must be 25-64" in error for error in errors)
    assert any("default_prompt` must mention `$ble`" in error for error in errors)


def test_agent_package_rejects_mcp_and_asset_escape(tmp_path: Path) -> None:
    plugin = _valid_plugin(tmp_path)
    _write(
        plugin / ".mcp.json",
        json.dumps({"mcpServers": {"blea": {"command": "python", "args": ["server.py"]}}}),
    )
    manifest = _read_manifest(plugin)
    manifest["interface"]["composerIcon"] = "../outside.png"
    _write(plugin / ".codex-plugin" / "plugin.json", json.dumps(manifest))

    errors = validate_agent_package(plugin)

    assert "`.mcp.json` server `blea` must run `ble mcp`" in errors
    assert (
        "plugin manifest field `interface.composerIcon` must stay inside the Plugin package"
        in errors
    )
