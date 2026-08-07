# BLEA workflow YAML

Use workflows for repeatable multi-step device checks. A workflow keeps one connection open for all
steps.

```yaml
name: battery-check
device: "id:AA:BB:CC:DD:EE:FF"
timeout: 10
steps:
  - id: inspect
    action: inspect
    expect:
      service_count_at_least: 1

  - id: battery
    action: read
    characteristic: "00002a19-0000-1000-8000-00805f9b34fb"
    requires: [inspect]
    expect:
      min_length: 1
```

Supported actions are `inspect`, `read`, `subscribe`, and `write`. Supported assertions are
`equals_hex`, `contains_hex`, `min_length`, `notifications_at_least`, and
`service_count_at_least`.

A write step must declare `dangerous: true`, require successful earlier steps, and encode exactly
one of `hex`, `text`, or `base64`:

```yaml
policy:
  allow_write: true
  confirm_device: "AA:BB:CC:DD:EE:FF"
steps:
  - id: current-state
    action: read
    characteristic: "12345678-1234-1234-1234-1234567890ab"
    expect:
      min_length: 1

  - id: change-state
    action: write
    characteristic: "12345678-1234-1234-1234-1234567890ab"
    value:
      hex: "01"
    dangerous: true
    requires: [current-state]
    response: true
    read_back: true
```

The operator must also pass `ble run workflow.yaml --allow-write`. The file and invocation gates
are intentionally independent.

