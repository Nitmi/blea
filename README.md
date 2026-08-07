# BLEA

[![CI](https://github.com/Nitmi/blea/actions/workflows/ci.yml/badge.svg)](https://github.com/Nitmi/blea/actions/workflows/ci.yml)

BLEA — **Bluetooth Low Energy Automation** — is an agent-first BLE toolkit. It combines a
deterministic `ble` CLI, a local stateful MCP server, a guarded workflow runner, and a portable
Agent Plugin package.

The project is designed for the BLE work agents repeatedly need: checking OS Bluetooth access,
finding the right nearby device, discovering GATT services, preserving raw byte evidence, reading
and subscribing before changing state, and making writes only after explicit authorization.

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
ble read --device "id:AA:BB:CC:DD:EE:FF" --characteristic 2a19 --json
ble subscribe --device "id:AA:BB:CC:DD:EE:FF" --characteristic 2a37 --duration 15 --jsonl
```

Device names are accepted only when exactly one observed device has that name. Prefer the
platform identifier returned by `scan`; on macOS this is normally a UUID rather than a MAC address.

Every binary value includes `length`, `hex`, `base64`, and replacement-safe UTF-8 representations.
JSON errors use stable reasons and exit codes suitable for agent recovery.

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

YAML writes add two more guards: the workflow must enable writes and each write step must declare
`dangerous: true` plus successful prerequisite steps. See `examples/guarded-write.yaml`.

## MCP and Agent Plugin

Start the local stdio server directly:

```shell
ble mcp
```

The MCP surface includes one-shot tools and stateful session tools. Sessions let an agent connect
once, inspect, read, subscribe, optionally perform a guarded write, and then disconnect.

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
and Linux.
