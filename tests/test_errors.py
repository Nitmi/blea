import asyncio

from blea.errors import BleTimeoutError, PermissionDeniedError, translate_backend_error


def test_backend_authentication_error_is_permission_denied() -> None:
    error = translate_backend_error(
        RuntimeError("Protocol Error 0x05: Insufficient Authentication"), operation="read"
    )
    assert isinstance(error, PermissionDeniedError)
    assert error.reason == "permission_denied"


def test_backend_timeout_has_stable_reason() -> None:
    error = translate_backend_error(asyncio.TimeoutError(), operation="subscribe")
    assert isinstance(error, BleTimeoutError)
    assert error.reason == "timeout"
