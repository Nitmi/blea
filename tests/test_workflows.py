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


def test_release_publishes_mcp_registry_after_pypi_with_oidc() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'MCP_PUBLISHER_VERSION: "1.8.1"' in release
    assert "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc" in release
    assert "registry:" in release
    assert "needs: publish" in release
    assert "id-token: write" in release
    assert "./mcp-publisher login github-oidc" in release
    assert "./mcp-publisher validate" in release
    assert "./mcp-publisher publish" in release
    assert "Wait for the published PyPI ownership marker" in release
    assert 'server_name="$(python -c' in release
    assert "sys.argv[1]" in release
    assert "io.github.nitmi/blea" not in release
    assert "io.github.Nitmi/blea" not in release
    assert "MCP_REGISTRY_TOKEN" not in release


def test_ci_and_release_build_openai_skill_bundle() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    command = "python scripts/build_openai_skill_bundle.py --output-directory openai-dist"
    assert command in ci
    assert command in release
    assert "name: openai-skill-bundle" in release
