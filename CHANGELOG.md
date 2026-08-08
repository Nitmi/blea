# Changelog

All notable changes to BLEA are documented here. BLEA follows Semantic Versioning while its public
API, evidence formats, and Agent Plugin integration mature toward 1.0.

## [0.6.0] - 2026-08-08

First public release candidate.

### Added

- Deterministic `ble` CLI for adapter diagnosis, scanning, GATT inspection, paginated probing,
  reads, bounded subscriptions, and multi-characteristic observation.
- Local stdio MCP server with one-shot operations and leased stateful sessions.
- Explicitly guarded writes and request/notification exchanges, including YAML Workflow assertions.
- Evidence Format v1 and read-only `capture` packages containing advertisement, GATT, read,
  notification, error, and cleanup evidence.
- Semantic offline `diff` with stable JSON Pointer paths, RSSI tolerance, identity guards, and a CI
  failure mode.
- Read-only `replay` through the CLI, MCP, and Workflow surfaces without adapter access.
- Portable Agent Plugins 1.0 metadata, BLE Agent Skill, and a deterministic ESP32-S3 test server.

### Safety

- Writes require an explicit allow gate and exact resolved-device confirmation.
- Workflow writes additionally require `dangerous: true`, successful prerequisites, and policy
  authorization.
- Capture and replay never pair, write, or change device configuration; replay rejects every write
  and exchange path.

### Platform status

- Windows 11 is hardware-verified with the ESP32-S3 test server.
- macOS and Linux pass unit tests and real-evidence replay in CI, but their native Bluetooth adapter
  paths have not been tested on physical hosts.

### Upgrade notes

- This is the first intended public package release, so there is no supported public predecessor.
- Users of unpublished local builds should reinstall the Python runtime and refresh the Agent Plugin
  together so CLI, MCP, Skill, and evidence behavior remain version-aligned.
