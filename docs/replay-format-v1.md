# BLEA Replay Format v1

BLEA Replay v1 projects a complete `.blea.jsonl` Evidence Format v1 file into the existing
read-only BLE backend interfaces. It lets the CLI, MCP tools, and read-only workflows exercise
captured BLE behavior without scanning for or connecting to a physical device.

## Input contract

Replay validates the entire evidence stream before returning any operation result. The input must:

- pass the Evidence Format v1 validator, including a final complete `summary`;
- contain exactly one `advertisement` event;
- contain at most one `profile` event;
- contain no duplicate `read` event for the same normalized characteristic UUID.

The capture may have status `complete`, `complete_with_failures`, or `failed` as long as the stream
itself is complete and contains the evidence required by the requested operation. Truncated,
damaged, or incompatible streams fail with `config_error` before a backend is created.

## Operations

The direct CLI shape is:

```text
ble replay <evidence.blea.jsonl> [--speed MULTIPLIER] \
  {scan,inspect,probe,read,subscribe,observe,run}
```

`scan` returns the captured advertisement. `inspect` and `probe` require a captured profile.
`read` returns the captured value or re-raises its captured structured error. `subscribe` and
`observe` use captured subscription outcomes and notifications. When the requested set matches the
capture's full subscription set, `observe` also preserves its cleanup result; subset replay cannot
attribute an unscoped cleanup error to one characteristic. `run` invokes the existing YAML workflow
engine with its invocation-level write gate permanently disabled.

The captured identifier is selected automatically when `--device` is omitted. An explicit device
still uses BLEA's exact identifier or unambiguous exact-name rules.

## Missing evidence and captured failures

Replay distinguishes an absent observation from an observed failure:

- A missing profile, characteristic read, or subscription attempt returns `replay_miss` with exit
  code `4` and lists available evidence where useful.
- A captured read or subscription failure keeps its original `reason`, `exit_code`, message, and
  structured details.
- A notify/indicate property alone does not prove a subscription succeeded. Replay requires either
  a recorded subscription result or at least one captured notification for that characteristic.
- A successful recorded subscription with no notifications replays as a successful quiet window.

Replay never invents a value, notification, pairing result, or protocol meaning.

## Time model

`--speed 0` is the default deterministic mode. It returns every matching notification inside the
requested duration immediately and normalizes top-level operation durations to zero. Replay
workflows also replace transient internal session identifiers with a stable replay identifier.

Positive speed values preserve gaps between captured notifications:

- `--speed 1` uses recorded gaps;
- `--speed 2` runs gaps twice as fast;
- `--speed 0.5` runs gaps at half speed.

Evidence v1 does not record the exact observation-window start. The first captured notification is
therefore time offset zero, later notifications use their timestamp delta from that event, and
Replay does not add trailing silence after the final matching notification. `duration` filters this
relative timeline before playback.

## MCP modes

The normal BLEA MCP server exposes `ble_replay` for a single offline operation. To make the normal
read-only BLE tools operate against one evidence file, start a dedicated stdio server:

```shell
ble replay capture.blea.jsonl mcp
ble replay capture.blea.jsonl --speed 2 mcp
```

Results served by that process include a `replay` object with the evidence path, capture ID,
schemas, timing mode, speed, capture status, and `read_only: true`. Stateful session tools remain
available, but their connections exist only inside ReplayBackend.

## Safety boundary

ReplayBackend never calls a scanner or creates a Bleak connection. Direct Replay commands do not
offer `write` or `exchange`. A write or exchange attempted through a replay-backed MCP session is
rejected with `guard_denied` even when all normal live-device authorization fields are present.
Replay v1 does not simulate writes or pairings.

## CI example

This repository runs the following adapter-free smoke path on Windows, macOS, and Linux:

```shell
ble replay tests/fixtures/evidence/complete.blea.jsonl \
  run examples/replay-read-only.yaml --json
```

Agents can use the same evidence for `ble_replay`, compare it with `ble_diff`, explain the stable
semantic changes, and test recovery from captured permission failures without BLE hardware.
