from __future__ import annotations

import argparse
import json
import shutil
from contextlib import suppress
from pathlib import Path

PLUGIN_RELATIVE = Path("plugins") / "blea"
MANAGED_FILES = (Path(".mcp.json"),)
MANAGED_TREES = (Path(".codex-plugin"), Path("skills"))


class PluginSyncError(RuntimeError):
    """Raised when the repository or generated mirror has an unsafe layout."""


def _validate_root(root: Path) -> Path:
    root = root.resolve()
    if not (root / "pyproject.toml").is_file():
        raise PluginSyncError(f"not a BLEA repository root: {root}")

    for relative in MANAGED_FILES:
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise PluginSyncError(
                f"required source file is missing or unsafe: {relative.as_posix()}"
            )

    for relative in MANAGED_TREES:
        source = root / relative
        if source.is_symlink() or not source.is_dir():
            raise PluginSyncError(
                f"required source directory is missing or unsafe: {relative.as_posix()}"
            )

    return root


def _destination(root: Path) -> Path:
    destination = root / PLUGIN_RELATIVE
    resolved = destination.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise PluginSyncError(f"plugin destination escapes repository root: {destination}")
    return destination


def _tree_files(base: Path, relative: Path, *, required: bool) -> dict[Path, bytes]:
    tree = base / relative
    if not tree.exists():
        if required:
            raise PluginSyncError(f"required directory is missing: {relative.as_posix()}")
        return {}
    if tree.is_symlink() or not tree.is_dir():
        raise PluginSyncError(f"managed directory has an unsafe type: {tree}")

    files: dict[Path, bytes] = {}
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            raise PluginSyncError(f"managed tree contains a symbolic link: {entry}")
        if entry.is_file():
            entry_relative = relative / entry.relative_to(tree)
            files[entry_relative] = entry.read_bytes()
    return files


def _managed_files(base: Path, *, source: bool) -> dict[Path, bytes]:
    files: dict[Path, bytes] = {}
    for relative in MANAGED_FILES:
        path = base / relative
        if not path.exists():
            if source:
                raise PluginSyncError(f"required file is missing: {relative.as_posix()}")
            continue
        if path.is_symlink() or not path.is_file():
            raise PluginSyncError(f"managed file has an unsafe type: {path}")
        files[relative] = path.read_bytes()

    for relative in MANAGED_TREES:
        files.update(_tree_files(base, relative, required=source))
    return files


def check_plugin_sync(root: Path) -> dict[str, object]:
    root = _validate_root(root)
    destination = _destination(root)
    source_files = _managed_files(root, source=True)
    destination_files = _managed_files(destination, source=False)

    source_paths = set(source_files)
    destination_paths = set(destination_files)
    missing = sorted(path.as_posix() for path in source_paths - destination_paths)
    extra = sorted(path.as_posix() for path in destination_paths - source_paths)
    changed = sorted(
        path.as_posix()
        for path in source_paths & destination_paths
        if source_files[path] != destination_files[path]
    )
    ok = not (missing or extra or changed)
    return {
        "ok": ok,
        "operation": "plugin_sync_check",
        "reason": "in_sync" if ok else "plugin_drift",
        "exit_code": 0 if ok else 1,
        "source": str(root),
        "destination": str(destination),
        "managed_files": len(source_files),
        "missing": missing,
        "extra": extra,
        "changed": changed,
    }


def _prepare_parent(destination: Path, target: Path) -> None:
    parents: list[Path] = []
    parent = target.parent
    while parent != destination:
        parents.append(parent)
        parent = parent.parent
    for candidate in reversed(parents):
        if candidate.is_symlink():
            raise PluginSyncError(f"managed path contains a symbolic link: {candidate}")
        if candidate.exists() and not candidate.is_dir():
            candidate.unlink()
        candidate.mkdir(exist_ok=True)


def _prune_empty_directories(destination: Path) -> None:
    for relative in MANAGED_TREES:
        tree = destination / relative
        if not tree.is_dir():
            continue
        directories = [path for path in tree.rglob("*") if path.is_dir()]
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            with suppress(OSError):
                directory.rmdir()


def sync_plugin(root: Path) -> dict[str, object]:
    root = _validate_root(root)
    destination = _destination(root)
    before = check_plugin_sync(root)

    destination.mkdir(parents=True, exist_ok=True)
    for relative_text in before["extra"]:
        path = destination / Path(relative_text)
        if path.is_symlink() or not path.is_file():
            raise PluginSyncError(f"refusing to remove unsafe managed path: {path}")
        path.unlink()

    copied = sorted([*before["missing"], *before["changed"]])
    for relative_text in copied:
        relative = Path(relative_text)
        source = root / relative
        target = destination / relative
        _prepare_parent(destination, target)
        if target.is_symlink():
            raise PluginSyncError(f"refusing to replace symbolic link: {target}")
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
        shutil.copy2(source, target)

    _prune_empty_directories(destination)
    after = check_plugin_sync(root)
    return {
        **after,
        "operation": "plugin_sync",
        "reason": "synchronized" if after["ok"] else "plugin_drift",
        "copied": copied,
        "removed": before["extra"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize BLEA's root Codex Plugin into its marketplace package."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="BLEA repository root (defaults to the script's repository).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without changing the marketplace package.",
    )
    args = parser.parse_args(argv)

    try:
        report = check_plugin_sync(args.root) if args.check else sync_plugin(args.root)
    except (OSError, PluginSyncError) as error:
        report = {
            "ok": False,
            "operation": "plugin_sync_check" if args.check else "plugin_sync",
            "reason": "invalid_plugin_layout",
            "exit_code": 2,
            "error": str(error),
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
