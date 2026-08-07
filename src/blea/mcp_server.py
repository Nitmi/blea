from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from blea.codec import parse_payload
from blea.errors import BleaError
from blea.service import BleService, SessionManager

mcp = FastMCP("BLEA")
service = BleService()
sessions = SessionManager(service)


async def _safe(call: Any) -> dict[str, Any]:
    try:
        return await call
    except BleaError as exc:
        return exc.to_dict()


@mcp.tool()
async def ble_doctor(scan_timeout: float = 1.0) -> dict[str, Any]:
    """Check whether the local Bluetooth adapter and OS BLE backend are usable."""

    return await service.doctor(scan_timeout=scan_timeout)


@mcp.tool()
async def ble_scan(
    timeout: float = 5.0,
    name_contains: str | None = None,
    service_uuid: str | None = None,
) -> dict[str, Any]:
    """Scan nearby BLE advertisements and return stable structured evidence."""

    return await _safe(
        service.scan(
            timeout=timeout,
            name_contains=name_contains,
            service_uuid=service_uuid,
        )
    )


@mcp.tool()
async def ble_inspect(device: str, timeout: float = 10.0) -> dict[str, Any]:
    """Connect to one exactly selected device and discover its GATT profile."""

    return await _safe(service.inspect(device, timeout=timeout))


@mcp.tool()
async def ble_probe(device: str, timeout: float = 10.0, max_reads: int = 32) -> dict[str, Any]:
    """Discover GATT and safely read up to max_reads readable characteristics."""

    return await _safe(service.probe(device, timeout=timeout, max_reads=max_reads))


@mcp.tool()
async def ble_read(device: str, characteristic: str, timeout: float = 10.0) -> dict[str, Any]:
    """Read a GATT characteristic and return hex, base64, UTF-8, and raw length evidence."""

    return await _safe(service.read(device, characteristic, timeout=timeout))


@mcp.tool()
async def ble_subscribe(
    device: str,
    characteristic: str,
    duration: float = 10.0,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Collect notifications from a GATT characteristic for a bounded duration."""

    return await _safe(
        service.subscribe(device, characteristic, duration=duration, timeout=timeout)
    )


@mcp.tool()
async def ble_write(
    device: str,
    characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Write only after explicit enablement and exact resolved-device confirmation."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return exc.to_dict()
    return await _safe(
        service.write(
            device,
            characteristic,
            data,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
            timeout=timeout,
        )
    )


@mcp.tool()
async def ble_session_open(device: str, timeout: float = 10.0) -> dict[str, Any]:
    """Open a stateful local BLE connection for a multi-step diagnostic workflow."""

    return await _safe(sessions.open(device, timeout=timeout))


@mcp.tool()
async def ble_session_inspect(session_id: str) -> dict[str, Any]:
    """Discover services on an open BLE session."""

    return await _safe(sessions.inspect(session_id))


@mcp.tool()
async def ble_session_read(session_id: str, characteristic: str) -> dict[str, Any]:
    """Read a characteristic through an open BLE session."""

    return await _safe(sessions.read(session_id, characteristic))


@mcp.tool()
async def ble_session_subscribe(
    session_id: str, characteristic: str, duration: float = 10.0
) -> dict[str, Any]:
    """Collect bounded notifications through an open BLE session."""

    return await _safe(sessions.subscribe(session_id, characteristic, duration=duration))


@mcp.tool()
async def ble_session_write(
    session_id: str,
    characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
) -> dict[str, Any]:
    """Write through an open session with the same explicit write guard."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return exc.to_dict()
    return await _safe(
        sessions.write(
            session_id,
            characteristic,
            data,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
        )
    )


@mcp.tool()
async def ble_session_close(session_id: str) -> dict[str, Any]:
    """Disconnect and forget an open BLE session."""

    return await _safe(sessions.close(session_id))


def run() -> None:
    mcp.run(transport="stdio")
