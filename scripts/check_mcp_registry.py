from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI job
    import tomli as tomllib

MCP_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
GITHUB_OWNER = "Nitmi"
MCP_NAME = f"io.github.{GITHUB_OWNER}/blea"
REPOSITORY_URL = f"https://github.com/{GITHUB_OWNER}/blea"
REPOSITORY_ID = "1327917598"
PYPI_PACKAGE = "blea"
PYTHON_IMAGE = (
    "python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6"
)


def _load_json(
    path: Path, errors: list[str], *, label: str = "server.json"
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing {label}")
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"{label} must contain valid JSON")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return payload


def _load_project(root: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append("missing pyproject.toml")
        return None
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        errors.append("pyproject.toml must contain valid TOML")
        return None
    project = payload.get("project")
    if not isinstance(project, dict):
        errors.append("pyproject.toml must contain a project table")
        return None
    return project


def validate_mcp_registry(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    server = _load_json(root / "server.json", errors)
    project = _load_project(root, errors)
    if server is None or project is None:
        return sorted(set(errors))

    version = project.get("version")
    if not isinstance(version, str) or not version:
        errors.append("project.version must be a non-empty string")
        version = None
    if project.get("name") != PYPI_PACKAGE:
        errors.append(f"project.name must be {PYPI_PACKAGE!r}")

    expected_top_level = {
        "$schema",
        "name",
        "title",
        "description",
        "repository",
        "version",
        "packages",
        "websiteUrl",
    }
    unknown = sorted(set(server) - expected_top_level)
    errors.extend(f"server.json field {field!r} is not used by BLEA" for field in unknown)
    if server.get("$schema") != MCP_SCHEMA:
        errors.append("server.json must use the pinned official MCP Registry schema")
    if server.get("name") != MCP_NAME:
        errors.append(f"server.json name must be {MCP_NAME!r}")
    if server.get("title") != "BLEA":
        errors.append("server.json title must be 'BLEA'")
    description = server.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 100:
        errors.append("server.json description must contain 1-100 characters")
    if server.get("version") != version:
        errors.append("server.json version must match project.version")
    if server.get("websiteUrl") != REPOSITORY_URL:
        errors.append("server.json websiteUrl must point to the public repository")
    if server.get("repository") != {
        "url": REPOSITORY_URL,
        "source": "github",
        "id": REPOSITORY_ID,
    }:
        errors.append("server.json repository metadata must match the public GitHub repository")

    expected_package = {
        "registryType": "pypi",
        "identifier": PYPI_PACKAGE,
        "version": version,
        "transport": {"type": "stdio"},
        "packageArguments": [{"type": "positional", "value": "mcp"}],
    }
    if server.get("packages") != [expected_package]:
        errors.append("server.json must declare exactly the version-aligned PyPI stdio package")

    scripts = project.get("scripts")
    if not isinstance(scripts, dict) or scripts.get("blea") != "blea.cli:main":
        errors.append("project.scripts must expose the package-named 'blea' executable")

    try:
        readme = (root / "README.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError):
        errors.append("README.md must be readable")
    else:
        marker = f"<!-- mcp-name: {MCP_NAME} -->"
        if marker not in readme:
            errors.append("README.md is missing the exact MCP Registry ownership marker")

    try:
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8").replace("\r\n", "\n")
    except (FileNotFoundError, OSError, UnicodeError):
        errors.append("Dockerfile must be readable")
    else:
        expected_lines = {
            f"FROM {PYTHON_IMAGE}",
            f"ARG BLEA_VERSION={version}",
            "WORKDIR /opt/blea",
            "COPY . .",
            "RUN python -m pip install --no-cache-dir .",
            'LABEL org.opencontainers.image.version="${BLEA_VERSION}"',
            f'LABEL io.modelcontextprotocol.server.name="{MCP_NAME}"',
            'ENTRYPOINT ["ble", "mcp"]',
        }
        missing_lines = sorted(expected_lines - set(dockerfile.splitlines()))
        errors.extend(
            f"Dockerfile is missing version-aligned line: {line}" for line in missing_lines
        )
        if "blea==${BLEA_VERSION}" in dockerfile:
            errors.append("Dockerfile must build the checkout before its PyPI version exists")
    return sorted(set(errors))


def check_mcp_registry(root: Path) -> dict[str, object]:
    root = root.resolve()
    errors = validate_mcp_registry(root)
    ok = not errors
    return {
        "ok": ok,
        "operation": "mcp_registry_check",
        "reason": "mcp_registry_metadata_valid" if ok else "mcp_registry_metadata_invalid",
        "exit_code": 0 if ok else 1,
        "repository_root": str(root),
        "server_name": MCP_NAME,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BLEA's MCP Registry metadata.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="BLEA repository root (defaults to this checkout).",
    )
    args = parser.parse_args(argv)
    report = check_mcp_registry(args.root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
