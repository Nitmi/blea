from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERIFIED_060_MARKETPLACE_REF = "81afc6b3e1a85741d6d02ff95a4deb63248eb951"
ADD_COMMAND = re.compile(r"codex plugin marketplace add Nitmi/blea --ref (\S+)")
IMMUTABLE_REF = re.compile(r"(?:[0-9a-f]{40}|v\d+\.\d+\.\d+)")
LATEST_MARKETPLACE_COMMAND = "codex plugin marketplace add Nitmi/blea"


def _marketplace_refs(path: Path) -> list[str]:
    return ADD_COMMAND.findall(path.read_text(encoding="utf-8"))


def test_readme_install_examples_follow_latest_release() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'uv tool install "blea==' not in readme
    assert "uv tool install blea" in readme
    assert f"{LATEST_MARKETPLACE_COMMAND}\n" in readme
    assert f"{LATEST_MARKETPLACE_COMMAND} --ref " not in readme


def test_reproducible_installation_examples_use_immutable_refs() -> None:
    installation_refs = _marketplace_refs(ROOT / "docs" / "installation.md")

    assert VERIFIED_060_MARKETPLACE_REF in installation_refs
    assert "v0.6.4" in installation_refs
    assert "v0.6.0" not in installation_refs
    assert all(IMMUTABLE_REF.fullmatch(ref) for ref in installation_refs)


def test_release_runbook_requires_the_version_tag() -> None:
    runbook = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert "codex plugin marketplace add Nitmi/blea --ref v<version>" in runbook
    assert 'git show "v<version>:.agents/plugins/marketplace.json"' in runbook
    assert 'git show "v<version>:plugins/blea/.codex-plugin/plugin.json"' in runbook
    assert "--ref main" not in runbook
