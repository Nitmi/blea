# BLEA Installation

BLEA has two installation units. The Python package installs the `ble` command and MCP runtime. The
Agent Plugin directory supplies the portable manifest, MCP declaration, and BLE Skill. Installing
only one unit does not install the other.

## Requirements

- Python 3.10 through 3.13.
- `uv` for the documented tool installation flow.
- An operating-system Bluetooth Low Energy adapter for live commands.
- No adapter for `diff`, `replay`, or the repository's unit and replay tests.

Windows 11 is the only hardware-verified platform for the first release. macOS and Linux are CI and
replay verified but native BLE hardware unverified. See [platform-acceptance.md](platform-acceptance.md).

## Python runtime

After version 0.6.0 is available on PyPI:

```shell
uv tool install "blea==0.6.0"
ble --version
ble doctor --scan-timeout 2 --json
```

For a source checkout before publication or during development:

```shell
uv tool install --editable .
ble --version
```

The editable form is intentionally a developer path. A public release acceptance must reinstall the
immutable wheel from PyPI before claiming that the published package works.

## Agent Plugin

The repository root conforms to Agent Plugins 1.0 and contains:

- `plugin.json` and `mcp.json` for portable clients;
- `.codex-plugin/plugin.json` and `.mcp.json` for Codex-specific discovery;
- `skills/ble/SKILL.md` and its referenced safety and Workflow guidance.

Install the Python runtime first, then point an Agent Plugins 1.0 client at the checked-out repository
directory. The stdio declaration runs `ble mcp`, so the client process must inherit a `PATH` that can
resolve the `ble` executable.

BLEA does not yet publish a Codex Git marketplace entry. Do not advertise a one-command Codex
marketplace install until the GitHub repository, marketplace source, install, and fresh-task pickup
have all been verified. Local development uses a local marketplace entry and should follow the
Codex `plugin-creator` update flow rather than editing marketplace state by hand.

## Offline verification

The following smoke test needs no adapter and is safe to run in CI:

```shell
ble replay tests/fixtures/evidence/complete.blea.jsonl run examples/replay-read-only.yaml --json
```

It must complete with `ok=true`. Replay is read-only and must not discover or connect to live BLE
hardware.

## Updates

For a published Python package:

```shell
uv tool upgrade blea
ble --version
```

Refresh or reinstall the Agent Plugin from its original source separately, then start a new Agent
task so updated skills and MCP tools are loaded. Keep the base versions in `pyproject.toml`,
`src/blea/__init__.py`, `plugin.json`, and `.codex-plugin/plugin.json` aligned.

## Uninstall

Remove the Python command runtime with:

```shell
uv tool uninstall blea
```

Remove the Agent Plugin through the client that installed it. Uninstalling the Python runtime does
not remove plugin metadata, and removing the plugin does not uninstall the Python runtime.
