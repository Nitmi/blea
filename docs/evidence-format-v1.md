# BLEA Evidence Format v1

BLEA capture files are UTF-8 JSONL documents. Each line is one independent event and
the `.blea.jsonl` extension identifies the format. The JSONL stream is authoritative;
human-readable capture summaries are derived output.

## Common event envelope

Every event contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | Exactly `1.0` for this format. |
| `capture_id` | UUID string | Stable for all events in one capture. |
| `sequence` | integer | Starts at `1` and increases without gaps. |
| `timestamp` | string | ISO-8601 UTC with a trailing `Z`. |
| `kind` | string | One of the event kinds below. |

The first event is `manifest`; a complete file ends with exactly one `summary` event.
Unknown fields are retained for forward-compatible readers, but required fields and
normalization rules are validated strictly.

## Event kinds

`manifest` carries `source` and `data`:

```json
{
  "source": {
    "blea_version": "0.4.0",
    "platform": "Windows-...",
    "python": "3.12.0",
    "bleak_version": "1.1.1",
    "backend": "bleak"
  },
  "data": {
    "parameters": {
      "selector": "id:AA:BB:CC:DD:EE:FF",
      "service_uuid": null,
      "max_reads": 128,
      "read_offset": 0,
      "observe_duration": 10.0,
      "timeout": 10.0,
      "redact_identifiers": false
    },
    "read_only": true
  }
}
```

`advertisement` stores one normalized `device` object. Device data includes the platform
identifier, names, RSSI and TX power, advertised service UUIDs, manufacturer data, and
service data. Binary maps use the byte representation described below.

`profile` stores the normalized GATT profile under `data.services`. Services,
characteristics, and descriptors are sorted by UUID and handle. Characteristic properties
are sorted case-insensitively.

`read` records every attempted readable characteristic. Successful reads use
`{"ok": true, "value": <byte evidence>}`; failed reads use
`{"ok": false, "error": <BLEA error>}`. The characteristic UUID is always present.

`notification` records each received value with `characteristic` and `value`. Subscription
attempt results, including failed subscriptions and cleanup errors, are preserved in the
observation section of `summary` and as `error` events when an operation-level error exists.

`error` records an operation-level failure with `data.operation` and a stable BLEA error
object. The error object contains at least `reason`, `message`, and integer `exit_code`.
Permission denial, timeout, disconnect, and backend-specific failures therefore remain
machine-readable without leaking platform exception classes into the public contract.

`summary` is final and contains `status`, `complete`, `event_count`, and `event_counts`.
`status` is one of `complete`, `complete_with_failures`, or `failed`. A persisted file always
has `complete: true`: this means the JSONL file was closed and validated, not that every BLE
operation succeeded. A failed or partially successful capture can still be a useful evidence
package. A file ending before `summary` is incomplete and must be rejected by the default
validator; recovery tooling may inspect it with `require_complete=false`.

## Binary values

Every raw byte value is represented by an object containing all four fields:

```json
{"length": 2, "hex": "0102", "base64": "AQI=", "utf8": "\\u0001\\u0002"}
```

`length` and both encodings must agree. `utf8` uses replacement-safe decoding and is a
convenience view, never the source of truth.

## Normalization

- UUIDs are canonical lowercase UUID strings whenever they are valid UUIDs.
- UUID lists and GATT collections are sorted; properties are sorted case-insensitively.
- JSON written by BLEA uses sorted object keys and compact separators for deterministic output.
- Binary map keys are normalized before sorting; manufacturer IDs use the `0x0000` form from
  the core device model.
- Timestamps, RSSI, notification timing, capture IDs, and backend source metadata are retained
  but are transient inputs for future `diff` operations.

## Identifier redaction

`--redact-identifiers` replaces platform device identifiers and `id:` selectors with stable
per-capture ordinal tokens such as `redacted:device-1`. The token is deterministic for the
first device in a single-device capture, so repeated captures remain comparable without
exposing a MAC address or CoreBluetooth UUID. Names, payloads, manufacturer bytes, and service
data are not guessed at or heuristically redacted; callers must remove those fields separately
when they are sensitive.

## Atomic output and validation

BLEA writes to a temporary file in the destination directory, flushes and fsyncs it, then uses
an atomic replace. A truncated destination is therefore never presented as a successful new
capture. `blea.evidence.validate_events` is the deterministic schema-equivalent validator;
`validate_evidence(path)` validates a file directly. Golden fixtures cover minimal, complete,
partial-failure, and damaged streams.
