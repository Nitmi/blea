from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\."
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
TODO_MARKER = "[TODO:"
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_LINES = 500

PLUGIN_KEYS = {
    "id",
    "name",
    "version",
    "description",
    "skills",
    "apps",
    "mcpServers",
    "interface",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
INTERFACE_KEYS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "websiteURL",
    "privacyPolicyURL",
    "termsOfServiceURL",
    "brandColor",
    "composerIcon",
    "logo",
    "logoDark",
    "screenshots",
    "defaultPrompt",
    "default_prompt",
}
AGENT_INTERFACE_KEYS = {
    "display_name",
    "short_description",
    "icon_small",
    "icon_large",
    "brand_color",
    "default_prompt",
}


def _load_json(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"missing {label}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"{label} must contain valid JSON")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return payload


def _load_yaml(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"missing {label}")
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        errors.append(f"{label} must contain valid YAML")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a YAML object")
        return None
    return payload


def _reject_unknown(
    payload: dict[str, Any], allowed: set[str], label: str, errors: list[str]
) -> None:
    for key in sorted((key for key in payload if key not in allowed), key=str):
        errors.append(f"{label} field `{key}` is not supported")


def _non_empty_string(
    payload: dict[str, Any], key: str, label: str, errors: list[str]
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} field `{key}` must be a non-empty string")
        return None
    return value.strip()


def _optional_non_empty_string(
    payload: dict[str, Any], key: str, label: str, errors: list[str]
) -> None:
    if key in payload:
        _non_empty_string(payload, key, label, errors)


def _validate_https(value: Any, label: str, errors: list[str]) -> None:
    parsed = urlparse(value) if isinstance(value, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.netloc:
        errors.append(f"{label} must be an absolute https:// URL")


def _validate_asset(
    plugin_root: Path,
    base: Path,
    value: Any,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty relative path")
        return
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        errors.append(f"{label} must stay inside the Plugin package")
        return
    resolved = (base / candidate.as_posix()).resolve()
    if not resolved.is_relative_to(plugin_root.resolve()):
        errors.append(f"{label} must stay inside the Plugin package")
    elif not resolved.is_file():
        errors.append(f"{label} points to a missing file")


def _contains_todo(value: Any) -> bool:
    if isinstance(value, str):
        return TODO_MARKER in value
    if isinstance(value, list):
        return any(_contains_todo(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_todo(item) for item in value.values())
    return False


def _validate_mcp(plugin_root: Path, errors: list[str]) -> None:
    payload = _load_json(plugin_root / ".mcp.json", "`.mcp.json`", errors)
    if payload is None:
        return
    _reject_unknown(payload, {"mcpServers"}, "`.mcp.json`", errors)
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        errors.append("`.mcp.json` field `mcpServers` must be an object")
        return
    if set(servers) != {"blea"}:
        errors.append("`.mcp.json` must declare exactly the `blea` server")
        return
    if servers["blea"] != {"command": "ble", "args": ["mcp"]}:
        errors.append("`.mcp.json` server `blea` must run `ble mcp`")


def _skill_frontmatter(path: Path, skill_name: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        errors.append(f"skill `{skill_name}` SKILL.md cannot be read")
        return None
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", content, re.DOTALL)
    if match is None:
        errors.append(f"skill `{skill_name}` SKILL.md must start with closed YAML frontmatter")
        return None
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        errors.append(f"skill `{skill_name}` frontmatter must be valid YAML")
        return None
    if not isinstance(frontmatter, dict):
        errors.append(f"skill `{skill_name}` frontmatter must be an object")
        return None
    if not content[match.end() :].strip():
        errors.append(f"skill `{skill_name}` must contain instructions after its frontmatter")
    if len(content.splitlines()) > MAX_SKILL_LINES:
        errors.append(f"skill `{skill_name}` must not exceed {MAX_SKILL_LINES} lines")
    return frontmatter


def _validate_agent_yaml(plugin_root: Path, skill_root: Path, errors: list[str]) -> None:
    skill_name = skill_root.name
    label = f"skill `{skill_name}` agents/openai.yaml"
    payload = _load_yaml(skill_root / "agents" / "openai.yaml", label, errors)
    if payload is None:
        return
    _reject_unknown(payload, {"interface", "policy", "dependencies"}, label, errors)
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        errors.append(f"{label} field `interface` must be an object")
        return
    _reject_unknown(interface, AGENT_INTERFACE_KEYS, f"{label} interface", errors)
    _non_empty_string(interface, "display_name", f"{label} interface", errors)
    short_description = _non_empty_string(
        interface, "short_description", f"{label} interface", errors
    )
    if short_description is not None and not 25 <= len(short_description) <= 64:
        errors.append(f"{label} field `interface.short_description` must be 25-64 characters")
    default_prompt = _non_empty_string(interface, "default_prompt", f"{label} interface", errors)
    if default_prompt is not None and f"${skill_name}" not in default_prompt:
        errors.append(f"{label} field `interface.default_prompt` must mention `${skill_name}`")

    brand_color = interface.get("brand_color")
    if brand_color is not None and (
        not isinstance(brand_color, str) or HEX_COLOR_RE.fullmatch(brand_color) is None
    ):
        errors.append(f"{label} field `interface.brand_color` must use #RRGGBB")
    for field in ("icon_small", "icon_large"):
        if field in interface:
            _validate_asset(
                plugin_root,
                skill_root,
                interface[field],
                f"{label} field `interface.{field}`",
                errors,
            )

    policy = payload.get("policy")
    if policy is not None:
        if not isinstance(policy, dict):
            errors.append(f"{label} field `policy` must be an object")
        else:
            _reject_unknown(policy, {"allow_implicit_invocation"}, f"{label} policy", errors)
            value = policy.get("allow_implicit_invocation")
            if value is not None and not isinstance(value, bool):
                errors.append(f"{label} field `policy.allow_implicit_invocation` must be boolean")

    dependencies = payload.get("dependencies")
    if dependencies is not None:
        if not isinstance(dependencies, dict):
            errors.append(f"{label} field `dependencies` must be an object")
        else:
            _reject_unknown(dependencies, {"tools"}, f"{label} dependencies", errors)
            tools = dependencies.get("tools")
            if not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools):
                errors.append(f"{label} field `dependencies.tools` must be an array of objects")


def _validate_skills(plugin_root: Path, errors: list[str]) -> None:
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        errors.append("missing `skills` directory")
        return
    entries = sorted(skills_root.iterdir(), key=lambda path: path.name)
    skill_roots = [path for path in entries if path.is_dir() and not path.name.startswith(".")]
    unexpected = [path.name for path in entries if path not in skill_roots]
    errors.extend(f"unexpected entry in `skills`: {name}" for name in unexpected)
    if {path.name for path in skill_roots} != {"ble"}:
        errors.append("Plugin must contain exactly the `ble` Skill")

    for skill_root in skill_roots:
        skill_name = skill_root.name
        skill_md = skill_root / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"skill `{skill_name}` is missing SKILL.md")
            continue
        frontmatter = _skill_frontmatter(skill_md, skill_name, errors)
        if frontmatter is None:
            continue
        _reject_unknown(frontmatter, {"name", "description"}, f"skill `{skill_name}`", errors)
        declared_name = _non_empty_string(frontmatter, "name", f"skill `{skill_name}`", errors)
        if declared_name is not None:
            if NAME_RE.fullmatch(declared_name) is None or len(declared_name) > MAX_NAME_LENGTH:
                errors.append(
                    f"skill `{skill_name}` name must be lowercase hyphen-case <= 64 chars"
                )
            if declared_name != skill_name:
                errors.append(f"skill `{skill_name}` frontmatter name must match its directory")
        description = _non_empty_string(frontmatter, "description", f"skill `{skill_name}`", errors)
        if description is not None:
            if "<" in description or ">" in description:
                errors.append(f"skill `{skill_name}` description cannot contain angle brackets")
            if len(description) > MAX_DESCRIPTION_LENGTH:
                errors.append(
                    f"skill `{skill_name}` description must not exceed "
                    f"{MAX_DESCRIPTION_LENGTH} chars"
                )
        _validate_agent_yaml(plugin_root, skill_root, errors)


def _validate_interface(plugin_root: Path, interface: Any, errors: list[str]) -> None:
    if not isinstance(interface, dict):
        errors.append("plugin manifest field `interface` must be an object")
        return
    _reject_unknown(interface, INTERFACE_KEYS, "plugin manifest interface", errors)
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        _non_empty_string(interface, field, "plugin manifest interface", errors)

    capabilities = interface.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(value, str) and value.strip() for value in capabilities)
    ):
        errors.append("plugin manifest field `interface.capabilities` must be non-empty strings")

    prompts = interface.get("defaultPrompt", interface.get("default_prompt"))
    if "defaultPrompt" in interface and "default_prompt" in interface:
        errors.append("plugin manifest must not declare both default prompt field variants")
    if (
        not isinstance(prompts, list)
        or not 1 <= len(prompts) <= 3
        or not all(
            isinstance(value, str) and value.strip() and len(value) <= 128 for value in prompts
        )
    ):
        errors.append("plugin manifest default prompts must contain 1-3 strings of <= 128 chars")

    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        if field in interface:
            _validate_https(interface[field], f"plugin manifest field `interface.{field}`", errors)
    color = interface.get("brandColor")
    if color is not None and (not isinstance(color, str) or HEX_COLOR_RE.fullmatch(color) is None):
        errors.append("plugin manifest field `interface.brandColor` must use #RRGGBB")
    for field in ("composerIcon", "logo", "logoDark"):
        if field in interface:
            _validate_asset(
                plugin_root,
                plugin_root,
                interface[field],
                f"plugin manifest field `interface.{field}`",
                errors,
            )
    screenshots = interface.get("screenshots", [])
    if not isinstance(screenshots, list):
        errors.append("plugin manifest field `interface.screenshots` must be an array")
    else:
        for index, screenshot in enumerate(screenshots):
            _validate_asset(
                plugin_root,
                plugin_root,
                screenshot,
                f"plugin manifest field `interface.screenshots[{index}]`",
                errors,
            )


def validate_agent_package(plugin_root: Path, *, expected_name: str = "blea") -> list[str]:
    plugin_root = plugin_root.resolve()
    errors: list[str] = []
    manifest = _load_json(
        plugin_root / ".codex-plugin" / "plugin.json", "`.codex-plugin/plugin.json`", errors
    )
    if manifest is None:
        return errors
    if _contains_todo(manifest):
        errors.append("plugin manifest contains a [TODO: ...] placeholder")
    _reject_unknown(manifest, PLUGIN_KEYS, "plugin manifest", errors)

    name = _non_empty_string(manifest, "name", "plugin manifest", errors)
    if name is not None:
        if NAME_RE.fullmatch(name) is None or len(name) > MAX_NAME_LENGTH:
            errors.append("plugin manifest name must be lowercase hyphen-case <= 64 chars")
        if name != expected_name or plugin_root.name != expected_name:
            errors.append("plugin manifest name, expected name, and package directory must match")
    version = _non_empty_string(manifest, "version", "plugin manifest", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append("plugin manifest version must be strict SemVer")
    _non_empty_string(manifest, "description", "plugin manifest", errors)
    _optional_non_empty_string(manifest, "id", "plugin manifest", errors)
    _optional_non_empty_string(manifest, "license", "plugin manifest", errors)
    if "apps" in manifest:
        errors.append("BLEA plugin manifest must not declare apps")

    author = manifest.get("author")
    if not isinstance(author, dict):
        errors.append("plugin manifest field `author` must be an object")
    else:
        _reject_unknown(author, {"name", "email", "url"}, "plugin manifest author", errors)
        _non_empty_string(author, "name", "plugin manifest author", errors)
        _optional_non_empty_string(author, "email", "plugin manifest author", errors)
        if "url" in author:
            _validate_https(author["url"], "plugin manifest field `author.url`", errors)

    if manifest.get("skills") not in {"skills", "./skills", "skills/", "./skills/"}:
        errors.append("plugin manifest field `skills` must resolve to `skills`")
    if manifest.get("mcpServers") not in {".mcp.json", "./.mcp.json"}:
        errors.append("plugin manifest field `mcpServers` must resolve to `.mcp.json`")
    for field in ("homepage", "repository"):
        if field in manifest:
            _validate_https(manifest[field], f"plugin manifest field `{field}`", errors)
    if "keywords" in manifest and (
        not isinstance(manifest["keywords"], list)
        or not all(isinstance(value, str) and value.strip() for value in manifest["keywords"])
    ):
        errors.append("plugin manifest field `keywords` must be an array of non-empty strings")

    _validate_interface(plugin_root, manifest.get("interface"), errors)
    _validate_mcp(plugin_root, errors)
    _validate_skills(plugin_root, errors)
    return sorted(set(errors))


def check_agent_packages(repository_root: Path) -> dict[str, object]:
    repository_root = repository_root.resolve()
    package_roots = (repository_root, repository_root / "plugins" / "blea")
    packages: list[dict[str, object]] = []
    all_errors: list[str] = []
    for plugin_root in package_roots:
        label = (
            "."
            if plugin_root == repository_root
            else plugin_root.relative_to(repository_root).as_posix()
        )
        errors = validate_agent_package(plugin_root)
        packages.append({"path": label, "ok": not errors, "errors": errors})
        all_errors.extend(f"{label}: {error}" for error in errors)
    ok = not all_errors
    return {
        "ok": ok,
        "operation": "agent_package_check",
        "reason": "agent_packages_valid" if ok else "agent_package_invalid",
        "exit_code": 0 if ok else 1,
        "repository_root": str(repository_root),
        "package_count": len(packages),
        "packages": packages,
        "errors": all_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BLEA's repository-local Agent packages.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="BLEA repository root (defaults to the script's repository).",
    )
    args = parser.parse_args(argv)
    report = check_agent_packages(args.root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
