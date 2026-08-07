from __future__ import annotations

import base64
import binascii
import re

from blea.errors import ConfigError


def parse_payload(
    *, hex_value: str | None = None, text_value: str | None = None, base64_value: str | None = None
) -> bytes:
    supplied = [value is not None for value in (hex_value, text_value, base64_value)]
    if sum(supplied) != 1:
        raise ConfigError("provide exactly one of hex, text, or base64 payload")

    if text_value is not None:
        return text_value.encode("utf-8")
    if base64_value is not None:
        try:
            return base64.b64decode(base64_value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ConfigError("invalid base64 payload") from exc

    assert hex_value is not None
    cleaned = re.sub(r"0x", "", hex_value, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\s,:_\-]", "", cleaned)
    if not cleaned or len(cleaned) % 2:
        raise ConfigError("hex payload must contain a non-empty even number of digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ConfigError("invalid hex payload") from exc
