# Windows ESP32-S3 Acceptance

- Date: 2026-08-08
- OS: Windows 11 10.0.26200, x64
- Capture runtime: Python 3.13.13, BLEA 0.4.0, Bleak 1.1.1, WinRT backend
- Replay runtime: BLEA 0.6.0
- Test server: `BLEA-S3-TEST`, firmware 0.1.0
- Identifier form: address-like; raw value omitted and not retained in the committed capture
- Permission state: adapter available; no pairing or permission prompt
- Pairing performed: no

## Read-only results

- Fresh scan selected exactly one device by the private identifier.
- GATT contained 4 services and 14 characteristics: 13 readable, 2 writable, and 4
  notify/indicate-capable.
- Probe attempted all 13 readable characteristics, succeeded on all 13, and ended with
  `next_read_offset=null`.
- Observe attempted 4 subscriptions. Three custom characteristics succeeded; Windows rejected
  Service Changed (`00002a05-...`) with structured `permission_denied`. The 3-second window
  captured 4 notifications.
- Cleanup succeeded: 3 subscriptions started, 3 stopped, and no cleanup error remained.
- Capture status was `complete_with_failures`, reflecting the retained Service Changed denial, and
  contained 22 Evidence v1 events.
- Replay 0.6.0 reproduced the same profile, reads, subscription outcome, notifications, and cleanup
  with `duration_ms=0`. Self-diff is identical.
- A replay-backed stdio MCP server reported 22 tools, `read_only=true`, and zero final sessions.

## Guarded exchange

- The user separately authorized one `burst:5` Workflow write for this Windows run.
- Both invocation and file policy gates were enabled, and the exact private identifier was confirmed
  locally. The write used response and read-back.
- The device emitted one `ok:burst:5` acknowledgement followed by five burst notifications; the
  final notification ended with `left=0`.
- Workflow cleanup succeeded and the final MCP session count was zero.

## Published artifact

- Capture: `tests/fixtures/platform/windows-esp32-s3.blea.jsonl`
- SHA-256: `83e8e15c9ae277b815b6e415987deb8aa1951cfaffb8a2c8c95280911048baed`
- The manifest has `redact_identifiers=true` and uses only `redacted:device-1`.
- Privacy scan found no MAC-like address, user/drive path, additional device token, serial number,
  password, token, or secret.

This result establishes the Windows configuration only. macOS and Linux remain separate hardware
acceptance targets under [`platform-acceptance.md`](../platform-acceptance.md).
