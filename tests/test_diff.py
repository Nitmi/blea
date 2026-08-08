from __future__ import annotations

import json
from pathlib import Path

import pytest

from blea.cli import main
from blea.diff import diff_evidence
from blea.errors import EXIT_ASSERTION_FAILED, ConfigError
from blea.evidence import read_evidence, write_evidence
from blea.mcp_server import ble_diff

FIXTURES = Path(__file__).parent / "fixtures"
BASELINE = FIXTURES / "evidence" / "complete.blea.jsonl"
DIFF_FIXTURES = FIXTURES / "diff"
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"


def _paths(result: dict[str, object], category: str) -> list[str]:
    changes = result["changes"]
    assert isinstance(changes, dict)
    values = changes[category]
    assert isinstance(values, list)
    return [item["path"] for item in values]


def _variant_from(source: Path, tmp_path: Path, mutate) -> Path:
    events = read_evidence(source)
    mutate(events)
    destination = tmp_path / "variant.blea.jsonl"
    return write_evidence(destination, events)


def _variant(tmp_path: Path, mutate) -> Path:
    return _variant_from(BASELINE, tmp_path, mutate)


def test_identical_and_transient_fields_are_stable() -> None:
    identical = diff_evidence(BASELINE, BASELINE)
    transient = diff_evidence(BASELINE, DIFF_FIXTURES / "transient.blea.jsonl")

    assert identical["status"] == transient["status"] == "identical"
    assert (
        identical["changes"]
        == transient["changes"]
        == {
            "added": [],
            "removed": [],
            "changed": [],
        }
    )
    assert identical["summary"] == transient["summary"]


def test_uuid_case_is_semantically_normalized(tmp_path: Path) -> None:
    def mutate(events: list[dict[str, object]]) -> None:
        events[1]["device"]["service_uuids"][0] = events[1]["device"]["service_uuids"][0].upper()
        service = events[2]["data"]["services"][0]
        service["uuid"] = service["uuid"].upper()
        characteristic = service["characteristics"][0]
        characteristic["uuid"] = characteristic["uuid"].upper()
        events[3]["data"]["characteristic"] = events[3]["data"]["characteristic"].upper()
        events[4]["data"]["characteristic"] = events[4]["data"]["characteristic"].upper()

    changed_case = _variant(tmp_path, mutate)
    assert diff_evidence(BASELINE, changed_case)["status"] == "identical"


def test_rssi_tolerance_and_strict_mode() -> None:
    evidence = DIFF_FIXTURES / "transient.blea.jsonl"
    assert diff_evidence(BASELINE, evidence)["has_changes"] is False

    strict = diff_evidence(BASELINE, evidence, strict_rssi=True)
    assert _paths(strict, "changed") == ["/advertisement/rssi"]
    assert strict["changes"]["changed"][0] == {
        "path": "/advertisement/rssi",
        "before": -42,
        "after": -46,
        "delta_dbm": -4.0,
        "tolerance_dbm": 0.0,
    }


def test_button_notification_diff_preserves_count_and_order() -> None:
    result = diff_evidence(BASELINE, DIFF_FIXTURES / "button-after.blea.jsonl")

    assert result["summary"] == {
        "added": 1,
        "removed": 0,
        "changed": 2,
        "unchanged": 22,
        "total": 25,
    }
    assert _paths(result, "added") == ["/notifications/sequence/1"]
    added = result["changes"]["added"][0]["after"]
    assert added["characteristic"] == BATTERY
    assert added["value"]["hex"] == "70"
    assert _paths(result, "changed") == [
        "/notifications/count",
        f"/notifications/counts/{BATTERY}",
    ]


def test_config_value_diff_is_one_atomic_byte_change() -> None:
    result = diff_evidence(BASELINE, DIFF_FIXTURES / "config-after.blea.jsonl")

    assert result["summary"]["changed"] == 1
    assert _paths(result, "changed") == [f"/reads/{BATTERY}/value"]
    change = result["changes"]["changed"][0]
    assert change["before"]["hex"] == "64"
    assert change["after"]["hex"] == "65"


def test_advertisement_payloads_and_service_set_are_compared(tmp_path: Path) -> None:
    def mutate(events: list[dict[str, object]]) -> None:
        device = events[1]["device"]
        device["name"] = "Sensor v2"
        device["service_uuids"].append("0000180a-0000-1000-8000-00805f9b34fb")
        device["manufacturer_data"]["0x1234"] = {
            "base64": "AwQ=",
            "hex": "0304",
            "length": 2,
            "utf8": "\u0003\u0004",
        }
        device["service_data"]["0000180f-0000-1000-8000-00805f9b34fb"] = {
            "base64": "AQ==",
            "hex": "01",
            "length": 1,
            "utf8": "\u0001",
        }

    changed = _variant(tmp_path, mutate)
    result = diff_evidence(BASELINE, changed)

    assert _paths(result, "added") == [
        "/advertisement/service_data/0000180f-0000-1000-8000-00805f9b34fb",
        "/advertisement/service_uuids/0000180a-0000-1000-8000-00805f9b34fb",
    ]
    assert _paths(result, "changed") == [
        "/advertisement/manufacturer_data/0x1234",
        "/advertisement/name",
    ]


def test_firmware_diff_reports_gatt_and_read_changes() -> None:
    result = diff_evidence(BASELINE, DIFF_FIXTURES / "firmware-after.blea.jsonl")
    characteristic_root = f"/profile/services/{SERVICE}/characteristics"

    assert _paths(result, "added") == [
        f"{characteristic_root}/{BATTERY}/descriptors/00002902-0000-1000-8000-00805f9b34fb",
        f"{characteristic_root}/{BATTERY}/properties/indicate",
        f"{characteristic_root}/00002a1a-0000-1000-8000-00805f9b34fb",
        "/reads/00002a1a-0000-1000-8000-00805f9b34fb",
    ]
    assert _paths(result, "removed") == [f"{characteristic_root}/{BATTERY}/properties/notify"]
    assert _paths(result, "changed") == []
    assert result["summary"]["added"] == 4
    assert result["summary"]["removed"] == 1


def test_gatt_service_addition_is_one_structured_change(tmp_path: Path) -> None:
    def mutate(events: list[dict[str, object]]) -> None:
        events[2]["data"]["services"].append(
            {
                "uuid": "0000180a-0000-1000-8000-00805f9b34fb",
                "handle": 10,
                "description": "Device Information",
                "characteristics": [],
            }
        )

    changed = _variant(tmp_path, mutate)
    result = diff_evidence(BASELINE, changed)
    assert _paths(result, "added") == ["/profile/services/0000180a-0000-1000-8000-00805f9b34fb"]


def test_notification_order_changes_ordered_payload_paths(tmp_path: Path) -> None:
    before = DIFF_FIXTURES / "button-after.blea.jsonl"

    def mutate(events: list[dict[str, object]]) -> None:
        events[4]["data"], events[5]["data"] = events[5]["data"], events[4]["data"]

    reordered = _variant_from(before, tmp_path, mutate)
    result = diff_evidence(before, reordered)
    assert _paths(result, "changed") == [
        "/notifications/sequence/0/value",
        "/notifications/sequence/1/value",
    ]


def test_device_identity_requires_explicit_cross_device_policy(tmp_path: Path) -> None:
    different = _variant(
        tmp_path,
        lambda events: events[1]["device"].__setitem__("identifier", "redacted:device-2"),
    )

    with pytest.raises(ConfigError, match="devices differ"):
        diff_evidence(BASELINE, different)

    allowed = diff_evidence(BASELINE, different, allow_different_devices=True)
    assert _paths(allowed, "changed") == ["/device/identifier"]


@pytest.mark.parametrize("tolerance", [-1, float("nan"), float("inf"), True])
def test_invalid_rssi_tolerance_is_rejected(tolerance: object) -> None:
    with pytest.raises(ConfigError, match="RSSI tolerance"):
        diff_evidence(BASELINE, BASELINE, rssi_tolerance=tolerance)


def test_damaged_input_is_rejected_before_comparison() -> None:
    damaged = FIXTURES / "evidence" / "damaged-missing-summary.blea.jsonl"
    with pytest.raises(ConfigError, match="incomplete"):
        diff_evidence(BASELINE, damaged)


def test_invalid_gatt_handle_is_a_structured_config_error(tmp_path: Path) -> None:
    def mutate(events: list[dict[str, object]]) -> None:
        events[2]["data"]["services"][0]["handle"] = "one"

    invalid = _variant(tmp_path, mutate)
    with pytest.raises(ConfigError, match="handle must be an integer"):
        diff_evidence(BASELINE, invalid)


def test_fail_on_change_returns_ci_exit_code() -> None:
    changed = diff_evidence(
        BASELINE,
        DIFF_FIXTURES / "config-after.blea.jsonl",
        fail_on_change=True,
    )
    assert changed["ok"] is True
    assert changed["exit_code"] == EXIT_ASSERTION_FAILED
    assert diff_evidence(BASELINE, BASELINE, fail_on_change=True)["exit_code"] == 0


def test_cli_emits_complete_json_before_ci_failure(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match=str(EXIT_ASSERTION_FAILED)):
        main(
            [
                "diff",
                str(BASELINE),
                str(DIFF_FIXTURES / "config-after.blea.jsonl"),
                "--fail-on-change",
                "--json",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "changed"
    assert payload["exit_code"] == EXIT_ASSERTION_FAILED


@pytest.mark.asyncio
async def test_mcp_diff_runs_offline() -> None:
    result = await ble_diff(str(BASELINE), str(BASELINE))
    assert result["status"] == "identical"
