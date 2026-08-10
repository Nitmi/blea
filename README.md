# BLEA

<!-- mcp-name: io.github.Nitmi/blea -->

[![Agent Plugins 1.0.0](https://img.shields.io/badge/Agent_Plugins-1.0.0-0F766E)](https://agent-plugins.org/)
[![Agent Skill](https://img.shields.io/badge/Agent-Skill-2563EB)](skills/ble/SKILL.md)
[![skills.sh](https://skills.sh/b/nitmi/blea)](https://www.skills.sh/nitmi/blea/ble)
[![ClawHub Skill](https://img.shields.io/badge/ClawHub-Skill-D97706)](https://clawhub.ai/nitmi/skills/blea)
[![CLI](https://img.shields.io/badge/Interface-CLI-374151)](#cli)
[![MCP](https://img.shields.io/badge/Protocol-MCP-C2410C)](#mcp-and-agent-plugin)
[![PyPI](https://img.shields.io/pypi/v/blea)](https://pypi.org/project/blea/)
[![CI](https://github.com/Nitmi/blea/actions/workflows/ci.yml/badge.svg)](https://github.com/Nitmi/blea/actions/workflows/ci.yml)

BLEA — **Bluetooth Low Energy Automation** — is a universal Agent Plugin for safe, deterministic
BLE work. It gives AI agents a portable Skill, a machine-readable CLI, a local MCP server, guarded
automation, and offline evidence capture, diff, and replay.

The project is designed for the BLE work agents repeatedly need: checking OS Bluetooth access,
finding the right nearby device, discovering GATT services, preserving and comparing raw byte
evidence, reading and subscribing before changing state, and making writes only after explicit
authorization.

| Surface | Role |
| --- | --- |
| Agent Plugin 1.0.0 | Packages the portable identity, Skill, and MCP declaration. |
| Agent Skill | Teaches agents the BLE workflow, safety boundaries, and recovery rules. |
| CLI | Emits deterministic JSON/JSONL for scripts, terminals, and any agent with shell access. |
| MCP | Exposes the same runtime as local, structured tools with managed BLE sessions. |

The `ble` executable is the shared runtime. Regular subcommands use the CLI surface; `ble mcp`
starts the stdio MCP server. The MCP protocol is therefore a first-class interface, not a second
tool hidden inside an unrelated CLI product.

## Install

BLEA requires Python 3.10 or newer. Bluetooth access is provided by the operating system through
[Bleak](https://github.com/hbldh/bleak).

### Quick install with an Agent prompt

Give this prompt to an Agent with shell access:

```text
Read https://github.com/Nitmi/blea and follow its README to install BLEA for me.
```

For manual installation, use the detailed paths below.

Install the latest published Python runtime from PyPI:

```shell
uv tool install blea
ble --help
```

For Codex, install the public Git marketplace package after the runtime:

```shell
codex plugin marketplace add Nitmi/blea
codex plugin add blea@blea
```

To install only the portable Agent Skill with the Skills CLI:

```shell
npx skills add Nitmi/blea --skill ble
```

For OpenClaw, install the same portable Skill from its public ClawHub listing:

```shell
openclaw skills install @nitmi/blea
# Or use the registry CLI directly:
clawhub install @nitmi/blea
```

The Skill teaches the workflow and safety policy; it does not install the Python runtime. Install
`blea` from PyPI as shown above before asking an agent to access live Bluetooth hardware or start
the local MCP server. The indexed Skill page is
[`skills.sh/nitmi/blea/ble`](https://www.skills.sh/nitmi/blea/ble), and the ClawHub release is
[`@nitmi/blea`](https://clawhub.ai/nitmi/skills/blea).

Start a new Agent task after installation. The marketplace package supplies the BLE Skill and MCP
declaration; it does not install the Python runtime or operating-system Bluetooth permissions. See
[`docs/installation.md`](docs/installation.md) for the release-tag, legacy `0.6.0` bridge, and update
procedure.

From a checkout, install the command runtime directly from the working tree:

```shell
uv tool install --editable .
ble --version
```

For repository development:

```shell
uv sync --extra dev
uv run ble --help
```

The Python runtime and Agent Plugin are two related installation units: PyPI installs the `ble`
executable, while an Agent Plugins 1.0.0 client loads this repository directory to discover
`plugin.json`, `mcp.json`, and `skills/ble`. Install the runtime first so the plugin's local stdio
MCP server can find `ble` on `PATH`. See [`docs/installation.md`](docs/installation.md) for source,
plugin, update, permissions, and verification paths.

## CLI

```shell
ble doctor --json
ble scan --timeout 8 --json
ble inspect --device "id:AA:BB:CC:DD:EE:FF" --json
ble probe --device "id:AA:BB:CC:DD:EE:FF" --max-reads 16 --json
ble probe --device "id:AA:BB:CC:DD:EE:FF" --max-reads 16 --read-offset 16 --json
ble read --device "id:AA:BB:CC:DD:EE:FF" --characteristic 2a19 --json
ble subscribe --device "id:AA:BB:CC:DD:EE:FF" --characteristic 2a37 --duration 15 --jsonl
ble observe --device "id:AA:BB:CC:DD:EE:FF" --duration 10 --jsonl
ble diff before.blea.jsonl after.blea.jsonl --json
ble replay capture.blea.jsonl inspect --json
ble replay capture.blea.jsonl read --characteristic 2a19 --json
ble exchange \
  --device "id:AA:BB:CC:DD:EE:FF" \
  --write-characteristic 12345678-1234-1234-1234-1234567890ab \
  --notify-characteristic 87654321-4321-4321-4321-ba0987654321 \
  --text ping --duration 5 --allow-write \
  --confirm-device "AA:BB:CC:DD:EE:FF" --jsonl
```

Device names are accepted only when exactly one observed device has that name. Prefer the
platform identifier returned by `scan`; on macOS this is normally a UUID rather than a MAC address.

Every binary value includes `length`, `hex`, `base64`, and replacement-safe UTF-8 representations.
JSON errors use stable reasons and exit codes suitable for agent recovery.

`probe` is paginated. Follow `next_read_offset` until it is `null`. Page counts and
characteristic-level failures live under `read_page`; `ok=true` means the page executed, while
`read_page.has_failures` tells you whether any characteristic failed. Every result includes a small
`profile_summary`. The CLI includes the full GATT profile by default and supports
`--no-include-profile`; the MCP tool omits it by default to keep repeated pages compact.

Connection commands report `timeout_scope=per_backend_operation`. Their timeout applies separately
to discovery, connection, and each GATT operation, rather than bounding the total command time.
GATT entries include `uuid_namespace`. BLEA reports library descriptions only for canonical
Bluetooth Base UUIDs, avoiding false standard names inferred from the leading bytes of custom
128-bit UUIDs.

`observe` discovers notify/indicate characteristics and watches them over one connection for a
bounded duration. Omit `--characteristic` to select all event-capable traits, or repeat it for
explicit selection. The result separates subscription failures, cleanup failures, and notification
events; a quiet window is evidence only for that sample window.

Save a complete read-only evidence package after diagnosis:

```shell
ble capture \
  --device "id:AA:BB:CC:DD:EE:FF" \
  --output capture.blea.jsonl \
  --max-reads 128 \
  --observe-duration 10 \
  --redact-identifiers \
  --json
```

`capture` records the advertisement, normalized GATT profile, each readable-characteristic result,
bounded notifications, operation errors, and a validated final summary. It performs no writes,
pairing, or configuration changes and replaces the destination only after an atomic close. The
JSONL file is the authoritative artifact; the command's JSON result is a compact summary. The
Evidence Format v1 contract and deterministic validator live in
[`docs/evidence-format-v1.md`](docs/evidence-format-v1.md).

Compare two complete evidence packages without a Bluetooth adapter or device:

```shell
ble diff before.blea.jsonl after.blea.jsonl --json
ble diff before.blea.jsonl after.blea.jsonl --fail-on-change --json
```

`diff` validates both Evidence Format v1 inputs, projects them into stable BLE semantics, and emits
sorted JSON Pointer changes. It ignores capture IDs, timestamps, runtime metadata, sampling
duration, and RSSI movement within 5 dBm by default. Use `--strict-rssi` for exact RSSI comparison.
Device identifiers must match unless an intentional cross-device comparison uses
`--allow-different-devices`. Binary payloads are atomic changes, so one changed value does not
produce separate Hex, Base64, and UTF-8 noise. A normal difference exits successfully;
`--fail-on-change` returns code `3` after printing the complete result for CI. The contract lives in
[`docs/diff-format-v1.md`](docs/diff-format-v1.md).

Replay a complete evidence package without a Bluetooth adapter or physical device:

```shell
ble replay capture.blea.jsonl inspect --json
ble replay capture.blea.jsonl probe --max-reads 32 --json
ble replay capture.blea.jsonl read --characteristic 2a19 --json
ble replay capture.blea.jsonl observe --duration 10 --jsonl
ble replay capture.blea.jsonl run examples/replay-read-only.yaml --json
```

Replay reconstructs advertisements, GATT, reads, captured failures, subscription outcomes, and
the notification timeline through the same read-only backend interfaces used for live devices.
The default `--speed 0` mode is immediate and deterministic; place a positive `--speed` before the
operation to preserve recorded notification gaps at that multiplier. Missing observations return
the stable `replay_miss` reason instead of an invented value or success. Replay never accesses a
real adapter and never sends or simulates writes or exchanges. The full contract lives in
[`docs/replay-format-v1.md`](docs/replay-format-v1.md).

## Guarded writes

A write requires both `--allow-write` and an exact confirmation of the resolved identifier:

```shell
ble write \
  --device "id:AA:BB:CC:DD:EE:FF" \
  --characteristic 12345678-1234-1234-1234-1234567890ab \
  --hex 01 \
  --allow-write \
  --confirm-device "AA:BB:CC:DD:EE:FF" \
  --read-back \
  --json
```

YAML writes and exchanges add two more guards: the workflow must enable writes and each state-changing
step must declare `dangerous: true` plus successful prerequisite steps. The policy must also carry an
exact `confirm_device` identifier. See `examples/guarded-write.yaml` and
`examples/esp32-burst-exchange.yaml`.

For protocols where a write triggers notifications, use `exchange`. It enables the notification
subscription before performing the guarded write, then collects events for a bounded duration.
The write and notify characteristics may be the same or different. This avoids the race created by
launching standalone session subscribe and write operations concurrently.

For repeatable checks, an `exchange` step can assert the exact notification count, UTF-8 or Hex
content, the final notification, and subscription cleanup. Run a guarded YAML workflow with the
independent invocation gate:

```shell
ble run examples/esp32-burst-exchange.yaml --allow-write --json
```

Replace the example's device identifier and confirmation with the exact value returned by a fresh
scan before running it.

## MCP and Agent Plugin

Start the local stdio server directly:

```shell
ble mcp
```

The MCP surface includes one-shot tools, the offline `ble_diff` comparator, the offline
`ble_replay` runner, and stateful session tools. Sessions let an agent connect
once, inspect, read, observe, subscribe, perform a guarded request/notification exchange or write,
and then disconnect. `ble_exchange` and `ble_session_exchange` atomically subscribe before writing.
`ble_session_list` exposes active leases and `ble_session_close_all` provides explicit recovery.
The server disconnects all sessions when the MCP client exits and reaps an inactive session after
120 seconds by default. Set `BLEA_SESSION_IDLE_SECONDS` to another positive duration, or `0` to
disable idle reaping while retaining shutdown cleanup.

Close a known session with `ble_session_close`. Reserve `ble_session_close_all` for recovering an
unknown session ID, a failed explicit close, or confirmed leaked state.

To expose the normal MCP BLE tools against one capture instead of hardware, launch a dedicated
replay server:

```shell
ble replay capture.blea.jsonl mcp
```

Every replay-backed BLE operation result identifies the evidence and carries
`replay.read_only=true`. Even if a client invokes an existing write or exchange tool with
live-device authorization fields, the ReplayBackend rejects it.

This repository is itself an [Agent Plugins 1.0.0](https://agent-plugins.org/) package:

```text
blea/
├── plugin.json                        portable plugin identity
├── mcp.json                           portable local stdio MCP declaration
├── skills/ble/SKILL.md                portable Agent Skill
├── .codex-plugin/plugin.json          root ChatGPT/Codex package metadata
├── .mcp.json                          root ChatGPT/Codex MCP declaration
├── .agents/plugins/marketplace.json   public Git marketplace catalog
└── plugins/blea/                      installable Codex distribution mirror
```

The plugin configuration expects the `ble` executable to be installed on `PATH`. The portable
specification distributes Skill and MCP metadata; native Bluetooth runtime installation and OS
permissions remain platform responsibilities.

The repository also carries [`server.json`](server.json), the official MCP Registry package
metadata for the PyPI distribution. It declares a local stdio server and the `mcp` package
argument; it does not advertise a hosted or remote BLE service. Releases publish this metadata to
the [official MCP Registry](https://registry.modelcontextprotocol.io/) with short-lived GitHub OIDC
credentials after the matching PyPI version succeeds. The namespace preserves the GitHub login's
canonical casing because Registry authorization and package ownership markers are case-sensitive.

The root [`Dockerfile`](Dockerfile) is a registry sandbox for MCP protocol introspection and
adapter-free tools. It builds the version-aligned release checkout and starts `ble mcp`, which lets
release-candidate CI run before that version exists on PyPI. It does not claim that a container can
access the host's Bluetooth adapter; use the native host installation above for live BLE work.

The OpenAI Plugins Directory submission uses the portable Skill only. BLEA's MCP server stays local
because live BLE requires the user's native host and adapter; it is not represented as a public
remote MCP endpoint. Submission copy, limitations, starter prompts, and review cases are recorded
in [`docs/openai-plugin-submission.md`](docs/openai-plugin-submission.md).

The public Codex distribution is registered at the immutable Git ref shown in the install section
and installed with `codex plugin add blea@blea`. A pinned ref does not advance to another release
when a marketplace snapshot is refreshed. To change releases, remove the configured marketplace,
add it again at the new `v<version>` tag, reinstall the Plugin, and start a new Agent task. Do not
treat installing the Python package alone as installing the Agent Skill or MCP declaration.

## Platform status

Windows 11 is hardware-verified with the ESP32-S3 BLEA test server, including discovery, GATT,
notifications, guarded exchange, capture, and replay. macOS and Linux currently pass the unit suite
and replay the same real-device evidence in CI, but their native Bluetooth adapter paths have not
been tested on physical hosts. Treat those two platforms as `CI + replay verified; native BLE
hardware unverified` until a published acceptance report says otherwise.

See [`docs/platform-acceptance.md`](docs/platform-acceptance.md) for the exact support tiers,
privacy requirements, hardware acceptance procedure, and current evidence matrix.

## Development

```shell
uv sync --extra dev
uv run python scripts/sync_codex_plugin.py --check
uv run python scripts/check_agent_package.py
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build --clear --no-create-gitignore
uv run python scripts/check_distribution.py dist
```

Unit tests use a fake BLE backend and do not require nearby hardware. CI runs on Windows, macOS,
and Linux. Each CI job also runs `examples/replay-read-only.yaml` against the checked-in complete
evidence fixture, providing an end-to-end CLI and Workflow smoke test with no adapter.

The root `.codex-plugin`, `.mcp.json`, and `skills` paths are the Codex Plugin source of truth. After
changing them, run `uv run python scripts/sync_codex_plugin.py`; CI uses `--check` to reject drift.
The repository-local Agent package checker validates both Codex Plugin roots, their MCP declaration,
Skill metadata, and `agents/openai.yaml` without relying on a maintainer's personal Skill directory.
The distribution checker also rejects missing marketplace files in the sdist and Plugin metadata in
the Python wheel.

Real adapter support is tracked separately from CI; CI success is not a native hardware support
claim.

Release history lives in [`CHANGELOG.md`](CHANGELOG.md). Maintainers should follow
[`docs/releasing.md`](docs/releasing.md) for artifact, Trusted Publishing, Plugin, and
post-publication gates.

## Companion project

For serial, UART, COM-port, USB-to-TTL, and firmware-console workflows, see
[`baud-cli`](https://github.com/Nitmi/baud-cli). It provides an agent-friendly `baud` CLI and
portable Skill with guarded YAML automation, structured output, and archived raw-byte evidence.
