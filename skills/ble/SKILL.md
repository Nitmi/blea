---
name: ble
description: Use BLEA to diagnose and automate local Bluetooth Low Energy devices. Trigger for BLE adapter or permission problems, nearby-device scans, deterministic device selection, GATT service discovery, characteristic reads, notification subscriptions, guarded writes, repeatable BLE YAML workflows, and raw-byte evidence collection through the `ble` CLI or BLEA MCP tools.
---

# BLEA

Use BLEA for local BLE work. Prefer BLEA MCP tools when available; otherwise run the equivalent
`ble` CLI command with JSON output.

## Diagnostic sequence

1. Run `ble_doctor` or `ble doctor --json` when adapter availability is unknown.
2. Scan and preserve the returned identifier, names, RSSI, advertised services, and raw advertising
   evidence.
3. Select by exact identifier. Use an exact name only when one observed device has that name.
4. Inspect the GATT profile before choosing characteristics.
5. Prefer reads and bounded subscriptions before considering a write.
6. Close stateful MCP sessions when the task is complete.

Do not invent UUIDs, payload encodings, pairing requirements, or protocol semantics. Report the
observed evidence and distinguish it from an inference.

## Commands and tools

- Diagnose: `ble doctor --json` or `ble_doctor`.
- Scan: `ble scan --timeout 8 --json` or `ble_scan`.
- Inspect: `ble inspect --device "id:<identifier>" --json` or `ble_inspect`.
- Probe readable characteristics: `ble probe --device "id:<identifier>" --json` or `ble_probe`.
- Read: `ble read --device "id:<identifier>" --characteristic <uuid> --json` or `ble_read`.
- Notify: use `ble subscribe ... --jsonl` or `ble_subscribe` with a bounded duration.
- Multi-step work: open an MCP session, operate on it, then close it.
- Repeatable work: encode the sequence in a guarded YAML file and run `ble run`.

Read [workflows.md](references/workflows.md) before creating or editing workflow YAML. Read
[safety.md](references/safety.md) before any write, pairing-sensitive operation, firmware update,
lock, actuator, or other state-changing action.

## Write policy

Treat every write as dangerous until the device protocol establishes otherwise.

- Require the user to authorize the specific state-changing operation.
- Require `allow_write=true` and `confirm_device=<resolved identifier>` for MCP writes.
- Require both `--allow-write` and `--confirm-device <resolved identifier>` for CLI writes.
- Never confirm with a friendly name, substring, stale identifier, or guessed address.
- Prefer write-with-response and read-back verification when supported.
- Stop when the selected device is ambiguous or a prerequisite read/assertion fails.

Return the structured failure instead of bypassing a guard.
