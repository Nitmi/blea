from __future__ import annotations

import json
from pathlib import Path

from scripts.check_mcp_registry import check_mcp_registry, validate_mcp_registry

ROOT = Path(__file__).parents[1]


def _write_fixture(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """
[project]
name = "blea"
version = "0.6.1"

[project.scripts]
blea = "blea.cli:main"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# BLEA\n\n<!-- mcp-name: io.github.nitmi/blea -->\n",
        encoding="utf-8",
    )
    server = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": "io.github.nitmi/blea",
        "title": "BLEA",
        "description": "Safe BLE diagnostics for AI agents.",
        "repository": {
            "url": "https://github.com/Nitmi/blea",
            "source": "github",
            "id": "1327917598",
        },
        "version": "0.6.1",
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "blea",
                "version": "0.6.1",
                "transport": {"type": "stdio"},
                "packageArguments": [{"type": "positional", "value": "mcp"}],
            }
        ],
        "websiteUrl": "https://github.com/Nitmi/blea",
    }
    (root / "server.json").write_text(json.dumps(server), encoding="utf-8")
    (root / "glama.json").write_text(
        json.dumps(
            {
                "$schema": "https://glama.ai/mcp/schemas/server.json",
                "maintainers": ["Nitmi"],
            }
        ),
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(
        "FROM python:3.13-slim@sha256:"
        "9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6\n"
        "ARG BLEA_VERSION=0.6.1\n"
        'RUN python -m pip install --no-cache-dir "blea==${BLEA_VERSION}"\n'
        'ENTRYPOINT ["ble", "mcp"]\n',
        encoding="utf-8",
    )


def test_repository_mcp_registry_metadata_is_aligned() -> None:
    report = check_mcp_registry(ROOT)

    assert report["ok"] is True
    assert report["reason"] == "mcp_registry_metadata_valid"


def test_mcp_registry_metadata_accepts_aligned_fixture(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    assert validate_mcp_registry(tmp_path) == []


def test_mcp_registry_metadata_rejects_release_drift(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    server_path = tmp_path / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    server["version"] = "0.6.0"
    server["packages"][0]["packageArguments"] = []
    server_path.write_text(json.dumps(server), encoding="utf-8")
    (tmp_path / "README.md").write_text("# BLEA\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.13-slim\nARG BLEA_VERSION=0.6.0\n",
        encoding="utf-8",
    )

    errors = validate_mcp_registry(tmp_path)

    assert "server.json version must match project.version" in errors
    assert "server.json must declare exactly the version-aligned PyPI stdio package" in errors
    assert "README.md is missing the exact MCP Registry ownership marker" in errors
    assert any("Dockerfile is missing version-aligned line" in error for error in errors)
