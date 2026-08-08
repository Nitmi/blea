from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

from scripts.check_distribution import (
    EXPECTED_MARKETPLACE,
    REQUIRED_SDIST_FILES,
    check_distributions,
)
from scripts.sync_codex_plugin import check_plugin_sync, sync_plugin

ROOT = Path(__file__).parents[1]


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_sdist(path: Path) -> None:
    files = {name: b"placeholder\n" for name in REQUIRED_SDIST_FILES}
    files[".agents/plugins/marketplace.json"] = json.dumps(EXPECTED_MARKETPLACE).encode()
    files[".codex-plugin/plugin.json"] = b'{"name":"blea"}\n'
    files["plugins/blea/.codex-plugin/plugin.json"] = files[".codex-plugin/plugin.json"]
    files[".mcp.json"] = b'{"mcpServers":{}}\n'
    files["plugins/blea/.mcp.json"] = files[".mcp.json"]
    files["skills/ble/SKILL.md"] = b"---\nname: ble\n---\n"
    files["plugins/blea/skills/ble/SKILL.md"] = files["skills/ble/SKILL.md"]

    with tarfile.open(path, "w:gz") as archive:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(f"blea-0.6.0/{name}")
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))


def _write_wheel(path: Path, *, include_plugin: bool = False) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("blea/__init__.py", '__version__ = "0.6.0"\n')
        archive.writestr("blea-0.6.0.dist-info/METADATA", "Name: blea\nVersion: 0.6.0\n")
        archive.writestr("blea-0.6.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        if include_plugin:
            archive.writestr("blea/plugin.json", "{}\n")


def test_repository_plugin_mirror_is_in_sync() -> None:
    report = check_plugin_sync(ROOT)

    assert report["ok"] is True
    assert report["reason"] == "in_sync"
    assert report["exit_code"] == 0


def test_sync_plugin_repairs_managed_drift_and_preserves_unmanaged_files(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "pyproject.toml", b"[project]\nname = 'blea'\n")
    _write(tmp_path / ".codex-plugin" / "plugin.json", b'{"name":"blea"}\n')
    _write(tmp_path / ".codex-plugin" / "notes.json", b'{"source":true}\n')
    _write(tmp_path / ".mcp.json", b'{"mcpServers":{}}\n')
    _write(tmp_path / "skills" / "ble" / "SKILL.md", b"source skill\n")

    packaged = tmp_path / "plugins" / "blea"
    _write(packaged / ".codex-plugin" / "plugin.json", b'{"name":"old"}\n')
    _write(packaged / "skills" / "ble" / "stale.txt", b"stale\n")
    _write(packaged / "assets" / "icon.bin", b"keep\n")

    before = check_plugin_sync(tmp_path)
    assert before["ok"] is False
    assert ".codex-plugin/plugin.json" in before["changed"]
    assert ".mcp.json" in before["missing"]
    assert "skills/ble/stale.txt" in before["extra"]

    result = sync_plugin(tmp_path)

    assert result["ok"] is True
    assert result["reason"] == "synchronized"
    assert result["exit_code"] == 0
    assert check_plugin_sync(tmp_path)["ok"] is True
    assert not (packaged / "skills" / "ble" / "stale.txt").exists()
    assert (packaged / "assets" / "icon.bin").read_bytes() == b"keep\n"
    assert (packaged / ".codex-plugin" / "notes.json").read_bytes() == b'{"source":true}\n'


def test_distribution_check_accepts_expected_artifacts(tmp_path: Path) -> None:
    _write_sdist(tmp_path / "blea-0.6.0.tar.gz")
    _write_wheel(tmp_path / "blea-0.6.0-py3-none-any.whl")

    report = check_distributions(tmp_path)

    assert report["ok"] is True
    assert report["reason"] == "distributions_valid"
    assert report["exit_code"] == 0


def test_distribution_check_rejects_plugin_metadata_in_wheel(tmp_path: Path) -> None:
    _write_sdist(tmp_path / "blea-0.6.0.tar.gz")
    _write_wheel(tmp_path / "blea-0.6.0-py3-none-any.whl", include_plugin=True)

    report = check_distributions(tmp_path)

    assert report["ok"] is False
    assert report["reason"] == "distribution_invalid"
    assert "wheel contains Plugin metadata: blea/plugin.json" in report["errors"]
