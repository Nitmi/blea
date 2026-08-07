import pytest

from blea.codec import parse_payload
from blea.errors import ConfigError


def test_parse_payload_encodings() -> None:
    assert parse_payload(hex_value="0x01 02:ff") == b"\x01\x02\xff"
    assert parse_payload(text_value="BLE") == b"BLE"
    assert parse_payload(base64_value="AQI=") == b"\x01\x02"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"hex_value": "01", "text_value": "x"},
        {"hex_value": "0"},
        {"base64_value": "not base64"},
    ],
)
def test_parse_payload_rejects_ambiguous_or_invalid_input(kwargs: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        parse_payload(**kwargs)
