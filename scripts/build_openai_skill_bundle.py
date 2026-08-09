from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

BUNDLE_FILES = (
    Path("SKILL.md"),
    Path("agents") / "openai.yaml",
    Path("references") / "safety.md",
    Path("references") / "workflows.md",
)
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class SkillBundleError(RuntimeError):
    """Raised when a portable OpenAI Skill bundle cannot be built safely."""


def _project_version(root: Path) -> str:
    manifest_path = root / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SkillBundleError(f"cannot read plugin version: {error}") from error
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not version:
        raise SkillBundleError("plugin.json version must be a non-empty string")
    return version


def _skill_files(root: Path) -> dict[Path, bytes]:
    skill_root = root / "skills" / "ble"
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise SkillBundleError("skills/ble must be a real directory")

    files: dict[Path, bytes] = {}
    for relative in BUNDLE_FILES:
        path = skill_root / relative
        if path.is_symlink() or not path.is_file():
            raise SkillBundleError(f"missing or unsafe Skill file: {relative.as_posix()}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise SkillBundleError(
                f"Skill file must contain UTF-8 text: {relative.as_posix()}"
            ) from error
        files[relative] = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return files


def build_openai_skill_bundle(root: Path, output_directory: Path) -> dict[str, object]:
    root = root.resolve()
    output_directory = output_directory.resolve()
    version = _project_version(root)
    files = _skill_files(root)

    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / f"blea-openai-skill-{version}.zip"
    temporary = output_directory / f".{target.name}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for relative in sorted(files, key=lambda path: path.as_posix()):
                info = zipfile.ZipInfo(
                    filename=f"ble/{relative.as_posix()}",
                    date_time=FIXED_ZIP_TIMESTAMP,
                )
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, files[relative])
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "ok": True,
        "operation": "openai_skill_bundle_build",
        "reason": "bundle_built",
        "exit_code": 0,
        "version": version,
        "path": str(target),
        "file_count": len(files),
        "sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build BLEA's deterministic OpenAI Skill bundle.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="BLEA repository root (defaults to this checkout).",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("dist"),
        help="Directory for the generated ZIP (defaults to dist).",
    )
    args = parser.parse_args(argv)

    try:
        report = build_openai_skill_bundle(args.root, args.output_directory)
    except (OSError, SkillBundleError) as error:
        report = {
            "ok": False,
            "operation": "openai_skill_bundle_build",
            "reason": "bundle_invalid",
            "exit_code": 1,
            "error": str(error),
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
