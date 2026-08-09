# Changelog

All notable changes to BLEA are documented here. BLEA follows Semantic Versioning while its public
API, evidence formats, and Agent Plugin integration mature toward 1.0.

## [Unreleased]

### Added

- Add a Bluetooth-first BLEA icon for Codex plugin cards and composer surfaces, with synchronized
  512 px and 128 px transparent PNG assets in the public marketplace package.

## [0.6.3] - 2026-08-09

Registry namespace correction patch. BLE operations, write guards, and Evidence Format v1 are
unchanged.

### Fixed

- Preserve the canonical GitHub login casing in `io.github.Nitmi/blea`, matching the namespace
  granted by official MCP Registry GitHub OIDC authentication.
- Derive the release workflow's PyPI ownership-marker check from `server.json` instead of embedding
  a second server name that can drift.
- Normalize OpenAI Skill bundle text to UTF-8/LF and use stable ZIP storage so Windows and Linux
  builds produce the same bytes.

### Safety and platform status

- No live BLE behavior, protocol operation, write authorization, or platform support claim changes
  in this release.

### Upgrade notes

- Install `blea==0.6.3` and the Codex marketplace at `--ref v0.6.3`.
- Version `0.6.2` remains a valid GitHub/PyPI release, but its lowercase Registry namespace marker
  cannot be changed after PyPI publication; use `0.6.3` for official Registry discovery.

## [0.6.2] - 2026-08-09

Registry and discovery release. BLE operations, write guards, and Evidence Format v1 are unchanged.

### Added

- Official MCP Registry publication metadata and a release job that publishes with GitHub OIDC only
  after the matching PyPI package succeeds.
- A deterministic OpenAI skills-only submission bundle, public listing copy, starter prompts, and
  five positive plus three negative review cases.
- Public agent discovery metadata for Agent Plugins 1.0.0, Skill, CLI, MCP, and skills.sh surfaces.

### Changed

- The registry sandbox now installs the release checkout, removing the build dependency on a PyPI
  version that cannot exist before the release workflow runs.
- Skill documentation links use an immutable public release, so uploaded bundles do not depend on a
  repository checkout layout.
- Glama-specific package metadata is no longer required; official MCP Registry publication is the
  authoritative MCP directory path.

### Safety and platform status

- No live BLE behavior or write authorization rule changes in this release.
- The OpenAI listing is skills-only because BLEA's MCP transport is a local stdio process, not a
  hosted service. Hosted environments must not claim access to a user's local Bluetooth adapter.
- Windows 11 remains hardware verified; macOS and Linux remain CI and replay verified with native
  Bluetooth hardware paths unverified.

### Upgrade notes

- Install `blea==0.6.2` and the Codex marketplace at `--ref v0.6.2`, then start a new Agent task.
- Official MCP Registry publication occurs automatically after PyPI in the release workflow.

### Known issue

- The official Registry rejected the lowercase `io.github.nitmi/blea` name because GitHub OIDC
  granted the canonical, case-sensitive `io.github.Nitmi/*` namespace. PyPI metadata is immutable,
  so the correction is released as `0.6.3` rather than rewriting this release.

## [0.6.1] - 2026-08-09

Distribution hardening patch. BLE operations, safety gates, and Evidence Format v1 are unchanged.

### Added

- Public Codex Git marketplace metadata and an installable `plugins/blea` distribution mirror.
- A deterministic Codex Plugin synchronization command with a read-only `--check` mode.
- Cross-platform CI gates for Plugin mirror drift, the public marketplace contract, and sdist/wheel
  content boundaries.
- A repository-local Agent package validator for Codex manifests, MCP declarations, Skill metadata,
  and `agents/openai.yaml`, with official validators retained as release-time compatibility checks.
- Reproducible Codex marketplace installation guidance using a verified commit for the `0.6.0`
  bridge and immutable, version-aligned release tags beginning with `v0.6.1`.

### Changed

- GitHub Actions dependencies upgraded to Node.js 24-compatible releases while preserving the
  existing setup-uv cache-pruning behavior.

### Safety and platform status

- No BLE protocol operation or write authorization behavior changes in this release.
- Windows 11 remains hardware verified; macOS and Linux remain CI and replay verified with native
  Bluetooth hardware paths unverified.

### Upgrade notes

- Install both `blea==0.6.1` and the Codex marketplace at `--ref v0.6.1`, then start a new Agent
  task so the matching Skill and MCP declaration are loaded.
- The protected `v0.6.0` tag remains unchanged. Its legacy marketplace bridge continues to use the
  verified full commit SHA documented in `docs/installation.md`.

## [0.6.0] - 2026-08-09

First public release.

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
- BLE operation timeouts preserve external task cancellation on Python 3.10 and clean up active
  notification subscriptions.

### Platform status

- Windows 11 is hardware-verified with the ESP32-S3 test server.
- macOS and Linux pass unit tests and real-evidence replay in CI, but their native Bluetooth adapter
  paths have not been tested on physical hosts.

### Upgrade notes

- This is the first intended public package release, so there is no supported public predecessor.
- Users of unpublished local builds should reinstall the Python runtime and refresh the Agent Plugin
  together so CLI, MCP, Skill, and evidence behavior remain version-aligned.
