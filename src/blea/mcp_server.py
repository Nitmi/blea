from __future__ import annotations

import asyncio
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from mcp.server.fastmcp import FastMCP

from blea import __version__
from blea.codec import parse_payload
from blea.errors import BleaError
from blea.service import BleService, SessionManager

DEFAULT_SESSION_IDLE_SECONDS = 120.0


def _session_idle_seconds() -> float | None:
    raw = os.environ.get("BLEA_SESSION_IDLE_SECONDS", str(DEFAULT_SESSION_IDLE_SECONDS))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("BLEA_SESSION_IDLE_SECONDS must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("BLEA_SESSION_IDLE_SECONDS must be finite and non-negative")
    return value or None


service = BleService()
sessions = SessionManager(service, idle_timeout_seconds=_session_idle_seconds())


async def _reap_idle_sessions(manager: SessionManager) -> None:
    idle_seconds = manager.idle_timeout_seconds
    if idle_seconds is None:
        return
    interval = min(max(idle_seconds / 2, 1.0), 30.0)
    while True:
        await asyncio.sleep(interval)
        await manager.close_idle(idle_seconds)


async def _finish_cleanup(manager: SessionManager) -> int:
    cleanup = asyncio.create_task(manager.close_all())
    try:
        return await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


@asynccontextmanager
async def mcp_lifespan(_: FastMCP[Any]) -> AsyncIterator[None]:
    reaper = (
        asyncio.create_task(_reap_idle_sessions(sessions))
        if sessions.idle_timeout_seconds is not None
        else None
    )
    try:
        yield None
    finally:
        if reaper is not None:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
        await _finish_cleanup(sessions)


mcp = FastMCP("BLEA", lifespan=mcp_lifespan)
# FastMCP does not expose its low-level server version in the constructor. Without this,
# initialize reports the MCP SDK version instead of the BLEA product version.
mcp._mcp_server.version = __version__


async def _safe(call: Any) -> dict[str, Any]:
    try:
        # WinRT BLE awaits can switch ContextVar contexts on Windows. A child task keeps the
        # MCP SDK's request context intact so it can serialize and send the tool response.
        return await asyncio.create_task(call)
    except BleaError as exc:
        return exc.to_dict()


@mcp.tool()
async def ble_doctor(scan_timeout: float = 1.0) -> dict[str, Any]:
    """Check whether the local Bluetooth adapter and OS BLE backend are usable."""

    return await _safe(service.doctor(scan_timeout=scan_timeout))


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
    """Discover GATT; timeout applies separately to scan, connect, and GATT operations."""

    return await _safe(service.inspect(device, timeout=timeout))


@mcp.tool()
async def ble_probe(
    device: str,
    timeout: float = 10.0,
    max_reads: int = 32,
    read_offset: int = 0,
    include_profile: bool = False,
) -> dict[str, Any]:
    """Read one GATT page; inspect read_page and follow next_read_offset until null.

    ok=true means the page ran, not that every characteristic read succeeded. timeout applies
    separately to scan, connect, profile discovery, and each read. The full profile is omitted by
    default to keep repeated pages compact; use ble_inspect first or set include_profile=true.
    """

    return await _safe(
        service.probe(
            device,
            timeout=timeout,
            max_reads=max_reads,
            read_offset=read_offset,
            include_profile=include_profile,
        )
    )


@mcp.tool()
async def ble_read(device: str, characteristic: str, timeout: float = 10.0) -> dict[str, Any]:
    """Read a characteristic; timeout applies separately to scan, connect, and GATT operations."""

    return await _safe(service.read(device, characteristic, timeout=timeout))


@mcp.tool()
async def ble_subscribe(
    device: str,
    characteristic: str,
    duration: float = 10.0,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Collect bounded notifications; timeout is per backend operation, not a total deadline."""

    return await _safe(
        service.subscribe(device, characteristic, duration=duration, timeout=timeout)
    )


@mcp.tool()
async def ble_observe(
    device: str,
    characteristics: list[str] | None = None,
    duration: float = 10.0,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Observe notify/indicate characteristics on one connection for a bounded duration.

    When characteristics is omitted, all discovered notify/indicate characteristics are selected.
    Per-characteristic subscription failures remain in subscriptions while other subscriptions
    continue. The tool always reports cleanup evidence and never writes or pairs.
    """

    return await _safe(
        service.observe(
            device,
            characteristics=tuple(characteristics) if characteristics else None,
            duration=duration,
            timeout=timeout,
        )
    )


@mcp.tool()
async def ble_exchange(
    device: str,
    write_characteristic: str,
    notify_characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    duration: float = 5.0,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Subscribe first, perform one guarded write, then collect resulting notifications."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return exc.to_dict()
    return await _safe(
        service.exchange(
            device,
            write_characteristic,
            notify_characteristic,
            data,
            duration=duration,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
            timeout=timeout,
        )
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
    """Write with guards; timeout is per backend operation, not a total tool deadline."""

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
    """Open a leased connection; timeout applies per backend operation for the session."""

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
async def ble_session_observe(
    session_id: str,
    characteristics: list[str] | None = None,
    duration: float = 10.0,
) -> dict[str, Any]:
    """Observe notify/indicate characteristics through one existing session connection."""

    return await _safe(
        sessions.observe(
            session_id,
            characteristics=tuple(characteristics) if characteristics else None,
            duration=duration,
        )
    )


@mcp.tool()
async def ble_session_exchange(
    session_id: str,
    write_characteristic: str,
    notify_characteristic: str,
    hex_value: str | None = None,
    text_value: str | None = None,
    base64_value: str | None = None,
    duration: float = 5.0,
    response: bool = True,
    read_back: bool = False,
    allow_write: bool = False,
    confirm_device: str | None = None,
) -> dict[str, Any]:
    """Atomically subscribe, write, and collect notifications through one open session."""

    try:
        data = parse_payload(hex_value=hex_value, text_value=text_value, base64_value=base64_value)
    except BleaError as exc:
        return exc.to_dict()
    return await _safe(
        sessions.exchange(
            session_id,
            write_characteristic,
            notify_characteristic,
            data,
            duration=duration,
            response=response,
            read_back=read_back,
            allow_write=allow_write,
            confirm_device=confirm_device,
        )
    )


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


@mcp.tool()
async def ble_session_list() -> dict[str, Any]:
    """List open BLE sessions, their devices, idle time, and lease timeout."""

    return sessions.list_sessions()


@mcp.tool()
async def ble_session_close_all() -> dict[str, Any]:
    """Recover unknown or leaked state by closing every session owned by this server."""

    async def close_all() -> dict[str, Any]:
        count = await sessions.close_all()
        return {
            "ok": True,
            "operation": "session_close_all",
            "closed_count": count,
            "exit_code": 0,
        }

    return await _safe(close_all())


def run() -> None:
    mcp.run(transport="stdio")
