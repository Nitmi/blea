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

Install the Python runtime first. The stdio declaration runs `ble mcp`, so the Agent process must
inherit a `PATH` that can resolve the `ble` executable.

### Codex Git marketplace

The original `v0.6.0` tag predates the Git marketplace package. For the currently published
`0.6.0` runtime, add the verified post-release snapshot by its full commit SHA and install BLEA:

```shell
codex plugin marketplace add Nitmi/blea --ref 81afc6b3e1a85741d6d02ff95a4deb63248eb951
codex plugin add blea@blea
```

The first command registers an immutable snapshot of the public repository. The second installs the
Codex package from `plugins/blea`. Start a new Agent task after installation so Codex loads the BLE
Skill and the local `ble mcp` server.

Starting with the next patch release, use the same version for PyPI and the Git marketplace. After
`v0.6.1` is published, the version-aligned installation is:

```shell
uv tool install "blea==0.6.1"
codex plugin marketplace add Nitmi/blea --ref v0.6.1
codex plugin add blea@blea
```

Release tags matching `v*` are protected against deletion and non-fast-forward updates in the BLEA
repository. Do not use a moving branch such as `main` for a reproducible installation. Before a
release is documented as installable, its tag must contain both `.agents/plugins/marketplace.json`
and `plugins/blea/.codex-plugin/plugin.json`.

The marketplace installs Plugin metadata only. It does not install Python, Bleak, the `ble` command,
or operating-system Bluetooth permissions. Keep the PyPI runtime and Plugin versions aligned.

For other Agent Plugins 1.0 clients, point the client at a checked-out repository root. The root
Codex metadata, MCP declaration, and Skill are mirrored byte-for-byte into `plugins/blea`, with
repository tests preventing drift.

Local development uses a local marketplace entry and should follow the Codex `plugin-creator`
cachebuster and reinstall flow rather than editing installed marketplace state by hand.

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

An immutable marketplace ref can be refreshed, but it cannot advance to another release. To move
to `v0.6.1` after that release is published, replace the configured marketplace source and reinstall
the Agent Plugin separately:

```shell
codex plugin marketplace remove blea
codex plugin marketplace add Nitmi/blea --ref v0.6.1
codex plugin add blea@blea
```

Then start a new Agent task so updated skills and MCP tools are loaded. Keep the base versions in
`pyproject.toml`, `src/blea/__init__.py`, `plugin.json`, `.codex-plugin/plugin.json`, and
`plugins/blea/.codex-plugin/plugin.json` aligned.

## Uninstall

Remove the Python command runtime with:

```shell
uv tool uninstall blea
```

Remove the Agent Plugin through the client that installed it. Uninstalling the Python runtime does
not remove plugin metadata, and removing the plugin does not uninstall the Python runtime.
