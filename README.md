# BLEA

[![CI](https://github.com/Nitmi/blea/actions/workflows/ci.yml/badge.svg)](https://github.com/Nitmi/blea/actions/workflows/ci.yml)

BLEA — **Bluetooth Low Energy Automation** — is an agent-first BLE toolkit. It combines a
deterministic `ble` CLI, a local stateful MCP server, a guarded workflow runner, offline evidence
replay, and a portable Agent Plugin package.

The project is designed for the BLE work agents repeatedly need: checking OS Bluetooth access,
finding the right nearby device, discovering GATT services, preserving and comparing raw byte
evidence, reading and subscribing before changing state, and making writes only after explicit
authorization.

## Install

BLEA requires Python 3.10 or newer. Bluetooth access is provided by the operating system through
[Bleak](https://github.com/hbldh/bleak).

```shell
uv tool install blea
ble --help
```

From a checkout:

```shell
uv sync --extra dev
uv run ble --help
```

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

This repository is itself an [Agent Plugins 1.0](https://agent-plugins.org/) package:

```text
blea/
├── plugin.json                 portable plugin identity
├── mcp.json                    portable local stdio MCP declaration
├── skills/ble/SKILL.md         portable Agent Skill
├── .codex-plugin/plugin.json   ChatGPT/Codex package metadata
└── .mcp.json                   ChatGPT/Codex MCP declaration
```

The plugin configuration expects the `ble` executable to be installed on `PATH`. The portable
specification distributes Skill and MCP metadata; native Bluetooth runtime installation and OS
permissions remain platform responsibilities.

## Development

```shell
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Unit tests use a fake BLE backend and do not require nearby hardware. CI runs on Windows, macOS,
and Linux. Each CI job also runs `examples/replay-read-only.yaml` against the checked-in complete
evidence fixture, providing an end-to-end CLI and Workflow smoke test with no adapter.
