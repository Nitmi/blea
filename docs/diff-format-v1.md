# BLEA Diff Format v1

`ble diff` compares two validated `.blea.jsonl` files offline. It never starts a BLE scan,
opens a connection, writes a characteristic, pairs, or changes device state.

## Identity policy

Each input must contain exactly one `advertisement` event with a device identifier. The identifiers
must match case-insensitively by default. This prevents a comparison between two nearby devices
from silently looking like a firmware change. Use `--allow-different-devices` only when a
cross-device comparison is intentional; the identity change is then reported at
`/device/identifier`.

Redacted identifiers such as `redacted:device-1` compare normally. Names are not used as identity.

## Semantic projection

The comparator projects each capture into stable fields:

- Advertisement name, local name, TX power, advertised service UUID set, manufacturer data, and
  service data.
- GATT services, characteristics, descriptors, handles, descriptions, UUID namespaces, and
  characteristic property sets.
- Read outcome and value for every readable characteristic. Byte evidence is compared atomically,
  so a changed payload produces one change rather than separate Hex/Base64/UTF-8 changes.
- Notification count, per-characteristic counts, ordered source/payload sequence, and operation
  errors.

Capture IDs, timestamps, source/runtime metadata, manifest parameters, notification timestamps, and
observation sample duration are intentionally ignored. Object keys and collection identifiers are
sorted before comparison. Service UUID sets and property sets are treated as sets; notification
sequence is ordered.

## RSSI policy

RSSI is compared separately. The default tolerance is 5 dBm, so normal signal fluctuation is not a
change. `--strict-rssi` sets the tolerance to zero. A changed RSSI record contains the before and
after values, delta, and applied tolerance.

## Result and exit code

The machine result always includes `status` (`identical` or `changed`), `summary` counts, and
sorted `changes.added`, `changes.removed`, and `changes.changed` arrays. Every change has a stable
JSON Pointer path and the relevant before/after evidence. `summary.unchanged` counts unchanged
semantic leaves and `summary.total` is the total comparison count.

Diffs do not fail a normal invocation. `--fail-on-change` makes a changed comparison return exit
code `3` (`assertion_failed`) for CI while retaining the complete JSON result.

