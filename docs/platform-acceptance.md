# BLEA Cross-Platform Hardware Acceptance

This contract separates adapter-free CI from real Bluetooth validation. A green replay job proves
that BLEA's evidence semantics are portable; it does not prove that the host adapter, permissions,
or native Bluetooth backend work.

## Status matrix

| Platform | Native backend | Published identifier form | Read-only hardware | Guarded exchange | Redacted capture |
| --- | --- | --- | --- | --- | --- |
| Windows 11 | WinRT | Address-like; never publish raw | Passed | Passed | `tests/fixtures/platform/windows-esp32-s3.blea.jsonl` |
| macOS | CoreBluetooth | Host-local UUID; never treat as a MAC | Pending | Pending | Pending |
| Linux | BlueZ D-Bus | Address-like; never publish raw | Pending | Pending | Pending |

The matrix records tested configurations, not blanket support for every adapter or OS build. BLEA's
current environment pins Bleak 1.1.1. Record the resolved versions in every report instead of
assuming the dependency or native stack matches another platform.

## Upstream platform facts

- Bleak selects WinRT, CoreBluetooth, or BlueZ D-Bus by operating system and documents its current
  supported platform floors in the [backend overview](https://bleak.readthedocs.io/en/latest/backends/).
- CoreBluetooth exposes a host-specific peripheral UUID rather than a Bluetooth address. The UUID
  may differ on another Mac, so correlate the test server by its advertised name, service UUID,
  and firmware behavior, then redact the identifier. macOS also requires user Bluetooth consent;
  bundled applications need `NSBluetoothAlwaysUsageDescription`. See Bleak's
  [macOS backend](https://bleak.readthedocs.io/en/latest/backends/macos.html).
- Bleak communicates with BlueZ over the system D-Bus on Linux and requires BlueZ 5.55 or newer.
  User-namespace D-Bus authentication needs explicit handling. See the
  [Linux backend](https://bleak.readthedocs.io/en/latest/backends/linux.html) and the upstream
  [BlueZ Adapter API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Adapter.rst).
- Windows discovery uses the WinRT GAP/GATT APIs. Microsoft's
  [BLE overview](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/bluetooth-low-energy-overview)
  distinguishes advertisement discovery from connected GATT access. For console hangs, also check
  Bleak's documented MTA/STA constraint in
  [troubleshooting](https://bleak.readthedocs.io/en/latest/troubleshooting.html).
- Native stacks cache GATT state. When firmware changes its service structure, record whether the
  device was forgotten or the cache was otherwise refreshed; do not silently present a cached
  profile as the new firmware. Bleak documents the known BlueZ behavior in its troubleshooting
  guide.

## Acceptance levels

`CI replay` requires the complete unit suite plus the checked-in replay Workflow on every CI OS.
It must not access a Bluetooth adapter.

`Read-only hardware` requires all of the following on one awake ESP32-S3 test server:

1. `ble doctor --json` reports the selected native backend and adapter availability.
2. An 8-second scan returns exactly one `BLEA-S3-TEST` advertising service
   `b1ea0001-6d4e-4b4e-9b57-4d54d3f60001`.
3. Selection uses the exact fresh identifier. The report and committed artifacts never retain it.
4. `inspect` returns the expected test firmware profile. Record service, characteristic, readable,
   writable, and subscribable counts rather than assuming the Windows baseline.
5. Paginated `probe` follows `next_read_offset` to `null` and retains every structured failure.
6. A bounded `observe` records every subscription attempt, notification, and cleanup result.
   A platform-specific Service Changed denial is acceptable only when it remains a structured
   failure and the other characteristics continue.
7. `capture --redact-identifiers` creates a complete Evidence v1 file. Replay inspect/probe/observe
   and a self-diff must succeed with no adapter access.
8. No pairing, writes, or configuration changes occur. The final MCP session count is zero.

`Full hardware` adds one separately authorized guarded exchange. The report must name the exact
payload, prove both write gates were present, record notification assertions and read-back, and
show zero sessions after cleanup. A prior authorization from another platform or run is not valid.

## Command sequence

Replace placeholders only in the local terminal. Never put the raw identifier in a committed file.

```shell
ble --version
ble doctor --scan-timeout 2 --json
ble scan --timeout 8 --name-contains BLEA-S3-TEST --json
ble inspect --device "id:<exact-fresh-identifier>" --json
ble probe --device "id:<exact-fresh-identifier>" --max-reads 32 --json
ble observe --device "id:<exact-fresh-identifier>" --duration 3 --json
ble capture --device "id:<exact-fresh-identifier>" \
  --output <platform>-esp32-s3.blea.jsonl --max-reads 128 \
  --observe-duration 3 --redact-identifiers --json
ble replay <platform>-esp32-s3.blea.jsonl inspect --json
ble replay <platform>-esp32-s3.blea.jsonl probe --max-reads 128 --json
ble replay <platform>-esp32-s3.blea.jsonl observe --duration 3 --json
ble diff <platform>-esp32-s3.blea.jsonl <platform>-esp32-s3.blea.jsonl --json
```

Run guarded exchange only after a new, platform-specific user authorization. Use the repository's
`examples/esp32-burst-exchange.yaml`, replacing both identifier placeholders locally, then verify
the Workflow assertions and session cleanup.

## Report template

Create one report per OS under `docs/platforms/`. Do not include raw device or adapter addresses,
CoreBluetooth UUIDs, user paths, host names, account names, or pairing secrets.

```markdown
# <Platform> ESP32-S3 Acceptance

- Date / operator:
- OS build / architecture:
- Python / BLEA / Bleak / native backend:
- Adapter model / driver or BlueZ version:
- Test firmware version / source commit:
- Identifier form: address-like | CoreBluetooth UUID (raw value omitted)
- Permission state and any prompt:
- Pairing performed: no

## Read-only results

- Doctor / unique scan:
- GATT counts:
- Probe attempts / successes / failures / final offset:
- Observe subscriptions / notifications / cleanup:
- Capture status / event count / SHA-256:
- Replay and self-diff:
- Final session count:

## Guarded exchange

- User authorization for this run:
- Exact payload (not an identifier or secret):
- Write gates / response / read-back:
- Notification assertions:
- Cleanup / final session count:

## Platform observations

- Expected native behavior:
- Unexpected behavior or workaround:
- Evidence retained locally:
- Redacted artifact committed:
```

## Artifact gate

Before committing a capture:

- require `redact_identifiers: true` in the manifest;
- verify no MAC-like address, drive/user path, host name, token, password, serial number, or secret;
- validate it through `ReplayBackend` and self-diff;
- retain LF line endings so its SHA-256 is portable;
- record the capture hash in the platform report.

Cross-platform diff should compare the three redacted captures and classify remaining changes as
firmware/runtime state, notification timing/content, or a real native-backend normalization defect.
Do not force the files to appear identical by deleting legitimate evidence.
