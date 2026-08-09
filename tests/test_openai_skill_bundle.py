from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.build_openai_skill_bundle import (
    BUNDLE_FILES,
    SkillBundleError,
    build_openai_skill_bundle,
)

ROOT = Path(__file__).parents[1]


def test_repository_openai_skill_bundle_is_complete_and_deterministic(tmp_path: Path) -> None:
    first = build_openai_skill_bundle(ROOT, tmp_path)
    first_bytes = Path(first["path"]).read_bytes()
    second = build_openai_skill_bundle(ROOT, tmp_path)

    assert first["version"] == "0.6.2"
    assert first["file_count"] == len(BUNDLE_FILES)
    assert second["sha256"] == first["sha256"]
    assert Path(second["path"]).read_bytes() == first_bytes

    with zipfile.ZipFile(second["path"]) as archive:
        assert archive.namelist() == [
            f"ble/{relative.as_posix()}"
            for relative in sorted(BUNDLE_FILES, key=lambda path: path.as_posix())
        ]
        for relative in BUNDLE_FILES:
            assert (
                archive.read(f"ble/{relative.as_posix()}")
                == (ROOT / "skills" / "ble" / relative).read_bytes()
            )


def test_openai_skill_bundle_rejects_missing_required_file(tmp_path: Path) -> None:
    (tmp_path / "plugin.json").write_text('{"version":"0.6.2"}\n', encoding="utf-8")
    skill_root = tmp_path / "skills" / "ble"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# BLEA\n", encoding="utf-8")

    with pytest.raises(SkillBundleError, match="missing or unsafe Skill file"):
        build_openai_skill_bundle(tmp_path, tmp_path / "out")
