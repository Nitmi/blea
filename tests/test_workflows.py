from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
SETUP_UV = "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990"


def test_workflows_use_node24_compatible_actions() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v7" in ci
    assert SETUP_UV in ci
    assert "actions/checkout@v7" in release
    assert SETUP_UV in release
    assert "actions/upload-artifact@v7" in release
    assert "actions/download-artifact@v8" in release

    deprecated = {
        "actions/checkout@v4",
        "astral-sh/setup-uv@v6",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
    }
    combined = ci + release
    assert all(reference not in combined for reference in deprecated)
