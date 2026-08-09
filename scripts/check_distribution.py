from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

REQUIRED_SDIST_FILES = frozenset(
    {
        ".agents/plugins/marketplace.json",
        ".codex-plugin/plugin.json",
        ".mcp.json",
        "CHANGELOG.md",
        "LICENSE",
        "README.md",
        "docs/installation.md",
        "docs/releasing.md",
        "mcp.json",
        "plugin.json",
        "plugins/blea/.codex-plugin/plugin.json",
        "plugins/blea/.mcp.json",
        "plugins/blea/skills/ble/SKILL.md",
        "scripts/check_agent_package.py",
        "scripts/check_distribution.py",
        "scripts/check_mcp_registry.py",
        "scripts/sync_codex_plugin.py",
        "server.json",
        "skills/ble/SKILL.md",
        "tests/fixtures/evidence/complete.blea.jsonl",
    }
)
EXPECTED_MARKETPLACE = {
    "name": "blea",
    "interface": {"displayName": "BLEA"},
    "plugins": [
        {
            "name": "blea",
            "source": {"source": "local", "path": "./plugins/blea"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Developer Tools",
        }
    ],
}
FORBIDDEN_WHEEL_FILES = frozenset({".mcp.json", "mcp.json", "plugin.json", "server.json"})
FORBIDDEN_WHEEL_DIRECTORIES = frozenset({".agents", ".codex-plugin", "plugins", "skills"})


class DistributionError(RuntimeError):
    """Raised when an archive cannot be inspected safely."""


def _safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise DistributionError(f"archive contains unsafe path: {name}")
    return tuple(part for part in path.parts if part not in {"", "."})


def _read_sdist(path: Path) -> dict[str, bytes]:
    raw_files: dict[tuple[str, ...], bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = _safe_parts(member.name)
            if len(parts) < 2:
                raise DistributionError(f"sdist file lacks a top-level directory: {member.name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise DistributionError(f"cannot read sdist member: {member.name}")
            if parts in raw_files:
                raise DistributionError(f"sdist contains duplicate path: {member.name}")
            raw_files[parts] = stream.read()

    roots = {parts[0] for parts in raw_files}
    if len(roots) != 1:
        raise DistributionError("sdist must contain exactly one top-level directory")
    return {"/".join(parts[1:]): content for parts, content in raw_files.items()}


def _read_wheel(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = _safe_parts(info.filename)
            name = "/".join(parts)
            if name in files:
                raise DistributionError(f"wheel contains duplicate path: {name}")
            files[name] = archive.read(info)
    return files


def _check_sdist(files: dict[str, bytes]) -> list[str]:
    errors = [
        f"sdist missing required file: {name}"
        for name in sorted(REQUIRED_SDIST_FILES - files.keys())
    ]

    marketplace_path = ".agents/plugins/marketplace.json"
    if marketplace_path in files:
        try:
            marketplace = json.loads(files[marketplace_path])
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"sdist marketplace is invalid JSON: {error}")
        else:
            if marketplace != EXPECTED_MARKETPLACE:
                errors.append("sdist marketplace contract does not match BLEA's public entry")

    for source, packaged in (
        (".codex-plugin/plugin.json", "plugins/blea/.codex-plugin/plugin.json"),
        (".mcp.json", "plugins/blea/.mcp.json"),
    ):
        if source in files and packaged in files and files[source] != files[packaged]:
            errors.append(f"sdist plugin mirror differs: {source} != {packaged}")

    source_skills = {
        name.removeprefix("skills/"): content
        for name, content in files.items()
        if name.startswith("skills/")
    }
    packaged_skills = {
        name.removeprefix("plugins/blea/skills/"): content
        for name, content in files.items()
        if name.startswith("plugins/blea/skills/")
    }
    if source_skills != packaged_skills:
        errors.append("sdist root Skills differ from the packaged Plugin Skills")
    return errors


def _check_wheel(files: dict[str, bytes]) -> list[str]:
    errors: list[str] = []
    if "blea/__init__.py" not in files:
        errors.append("wheel missing runtime package: blea/__init__.py")

    def is_dist_info(name: str) -> bool:
        parts = PurePosixPath(name).parts
        return len(parts) >= 2 and parts[0].endswith(".dist-info")

    metadata = [name for name in files if is_dist_info(name)]
    if not any(name.endswith(".dist-info/METADATA") for name in metadata):
        errors.append("wheel missing .dist-info/METADATA")

    forbidden = sorted(
        name
        for name in files
        if PurePosixPath(name).name in FORBIDDEN_WHEEL_FILES
        or FORBIDDEN_WHEEL_DIRECTORIES.intersection(PurePosixPath(name).parts)
    )
    errors.extend(f"wheel contains Plugin metadata: {name}" for name in forbidden)

    unexpected = sorted(
        name for name in files if not name.startswith("blea/") and not is_dist_info(name)
    )
    errors.extend(f"wheel contains unexpected top-level file: {name}" for name in unexpected)
    return errors


def check_distributions(dist_directory: Path) -> dict[str, object]:
    dist_directory = dist_directory.resolve()
    errors: list[str] = []
    sdists = sorted(dist_directory.glob("blea-*.tar.gz")) if dist_directory.is_dir() else []
    wheels = sorted(dist_directory.glob("blea-*.whl")) if dist_directory.is_dir() else []

    if len(sdists) != 1:
        errors.append(f"expected exactly one BLEA sdist, found {len(sdists)}")
    if len(wheels) != 1:
        errors.append(f"expected exactly one BLEA wheel, found {len(wheels)}")

    sdist_files: dict[str, bytes] = {}
    wheel_files: dict[str, bytes] = {}
    if len(sdists) == 1:
        try:
            sdist_files = _read_sdist(sdists[0])
            errors.extend(_check_sdist(sdist_files))
        except (OSError, DistributionError, tarfile.TarError) as error:
            errors.append(f"cannot inspect sdist: {error}")
    if len(wheels) == 1:
        try:
            wheel_files = _read_wheel(wheels[0])
            errors.extend(_check_wheel(wheel_files))
        except (OSError, DistributionError, zipfile.BadZipFile) as error:
            errors.append(f"cannot inspect wheel: {error}")

    errors = sorted(set(errors))
    ok = not errors
    return {
        "ok": ok,
        "operation": "distribution_check",
        "reason": "distributions_valid" if ok else "distribution_invalid",
        "exit_code": 0 if ok else 1,
        "dist_directory": str(dist_directory),
        "sdist": sdists[0].name if len(sdists) == 1 else None,
        "wheel": wheels[0].name if len(wheels) == 1 else None,
        "sdist_file_count": len(sdist_files),
        "wheel_file_count": len(wheel_files),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate BLEA's source and wheel distribution boundaries."
    )
    parser.add_argument(
        "dist_directory",
        nargs="?",
        type=Path,
        default=Path("dist"),
        help="Directory containing one BLEA sdist and one wheel (default: dist).",
    )
    args = parser.parse_args(argv)
    report = check_distributions(args.dist_directory)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
