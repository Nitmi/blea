---
name: ble
description: Use BLEA to diagnose and automate local Bluetooth Low Energy devices. Trigger for BLE adapter or permission problems, nearby-device scans, deterministic device selection, GATT service discovery, characteristic reads, bounded multi-characteristic observation, notification subscriptions, guarded request/notification exchanges, guarded writes, repeatable BLE YAML workflows, and raw-byte evidence collection through the `ble` CLI or BLEA MCP tools.
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
5. For event discovery, use bounded `ble_observe`/`ble observe` before writing; omit characteristics
   to observe all notify/indicate traits from the discovered profile.
6. When probing, continue with `next_read_offset` until it is `null`. `ok=true` means the page ran;
   it does not mean every characteristic read succeeded. Aggregate `read_page.success_count`,
   `failure_count`, and `failure_reasons` across pages, and preserve both successful reads and
   per-characteristic failures.
7. Prefer reads and bounded observation before considering a write. Treat a silent observation
   window as evidence only for that window, not proof that a characteristic never emits events.
8. When one authorized write is expected to trigger notifications, use `ble_exchange` or
   `ble_session_exchange`. These operations establish the subscription before writing and collect
   the response atomically; do not run standalone session subscribe and write tools concurrently.
9. Close the exact stateful MCP session once when the task is complete. Use `ble_session_list` when
   cleanup is uncertain. Use `ble_session_close_all` only when a session ID is unknown, an explicit
   close failed, or leaked state must be recovered; do not call it after a successful close.

Do not invent UUIDs, payload encodings, pairing requirements, or protocol semantics. Report the
observed evidence and distinguish it from an inference. Treat `uuid_namespace=custom` as a custom
128-bit UUID even when its leading bytes resemble a Bluetooth SIG assigned number.

## Commands and tools

- Diagnose: `ble doctor --json` or `ble_doctor`.
- Scan: `ble scan --timeout 8 --json` or `ble_scan`.
- Inspect: `ble inspect --device "id:<identifier>" --json` or `ble_inspect`.
- Probe readable characteristics: use `ble probe --device "id:<identifier>" --max-reads 32
  --read-offset <offset> --json` or `ble_probe`, following `next_read_offset` across pages.
- MCP probe results omit the full GATT tree by default while retaining `profile_summary`; call
  `ble_inspect` first or set `include_profile=true` when the full profile is needed on that page.
- Read: `ble read --device "id:<identifier>" --characteristic <uuid> --json` or `ble_read`.
- Notify: use `ble subscribe ... --jsonl` or `ble_subscribe` with a bounded duration.
- Observe all event-capable traits: use `ble observe --device "id:<identifier>" --duration 10
  --jsonl` or `ble_observe`. Pass `--characteristic <uuid>` repeatedly for explicit selection.
- Guarded request/notification exchange: use `ble exchange ... --jsonl`, `ble_exchange`, or
  `ble_session_exchange` to subscribe before one write and collect its resulting events.
- Multi-step work: open an MCP session, note its `idle_timeout_seconds`, use
  `ble_session_observe` when the connection should be reused, then close the session.
- Repeatable work: encode the sequence in a guarded YAML file and run `ble run`.

Read [workflows.md](references/workflows.md) before creating or editing workflow YAML. Read
[safety.md](references/safety.md) before any write, pairing-sensitive operation, firmware update,
lock, actuator, or other state-changing action.

`timeout` is a per-backend-operation bound, not a total command or tool deadline. Allow for device
discovery, connection, profile discovery, each requested read, and any subscription duration when
setting an outer Agent/tool timeout.

## Write policy

Treat every write as dangerous until the device protocol establishes otherwise.

- Require the user to authorize the specific state-changing operation.
- Require `allow_write=true` and `confirm_device=<resolved identifier>` for MCP writes.
- Require both `--allow-write` and `--confirm-device <resolved identifier>` for CLI writes.
- Never confirm with a friendly name, substring, stale identifier, or guessed address.
- Prefer write-with-response and read-back verification when supported.
- Prefer atomic exchange notification verification when a write triggers asynchronous events.
- Stop when the selected device is ambiguous or a prerequisite read/assertion fails.

Return the structured failure instead of bypassing a guard.
