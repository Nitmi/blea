import asyncio

import pytest

import blea.backend
from blea.backend import BleakConnection
from blea.errors import BleTimeoutError
from blea.models import DiscoveredDevice


class HangingClient:
    def __init__(self, target: object, *, timeout: float) -> None:
        del target, timeout
        self.is_connected = True

    async def read_gatt_char(self, characteristic: str) -> bytes:
        del characteristic
        await asyncio.sleep(60)
        return b""


@pytest.mark.asyncio
async def test_connection_enforces_backend_operation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(blea.backend, "BleakClient", HangingClient)
    connection = BleakConnection(DiscoveredDevice("test-device"), timeout=0.01)

    with pytest.raises(BleTimeoutError):
        await connection.read("test-characteristic")
