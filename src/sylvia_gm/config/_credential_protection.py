"""Current-user Windows protection for owner-local provider secrets."""

from __future__ import annotations

import base64
import ctypes
import json
import sys
from collections.abc import Mapping

from sylvia_gm.config._resolver import ConfigError, resolve_vars

UNAVAILABLE_MESSAGE = (
    "Windows could not unlock this connection's saved credentials. "
    "Re-enter its API key and private headers, or remove the connection."
)
_SECRET_BYTE_LIMIT = 128 * 1024


def _dpapi(value: bytes, *, decrypt: bool) -> bytes:
    """Use DPAPI without prompts or machine-wide scope; never expose native error details."""
    if sys.platform != "win32":
        raise ConfigError("Saving or unlocking protected credentials requires Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob),
    ]
    operation.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = Blob(len(value), buffer)
    result = Blob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN only; deliberately omit CRYPTPROTECT_LOCAL_MACHINE.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ConfigError(
                UNAVAILABLE_MESSAGE if decrypt else "Windows credential protection failed"
            )
        if result.size > _SECRET_BYTE_LIMIT:
            raise ConfigError("Protected credentials exceed the supported size")
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(buffer, 0, len(value))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)


def protect_credentials(api_key: str, headers: Mapping[str, str]) -> dict[str, object]:
    """Protect both header names and values, then verify before a settings write."""
    payload = json.dumps(
        {"api_key": api_key, "headers": dict(headers)}, ensure_ascii=False
    ).encode()
    if len(payload) > _SECRET_BYTE_LIMIT // 2:
        raise ConfigError("Provider credentials exceed the supported size")
    encrypted = _dpapi(payload, decrypt=False)
    if _dpapi(encrypted, decrypt=True) != payload:
        raise ConfigError("Windows credential protection could not be verified")
    return {
        "version": 1, "protection": "windows-dpapi-user",
        "data": base64.b64encode(encrypted).decode(),
    }


def unprotect_credentials(
    envelope: object, *, resolve: bool = True
) -> tuple[str, dict[str, str]]:
    """Resolve one selected provider's secrets in memory, or fail without raw diagnostics."""
    try:
        if (
            not isinstance(envelope, Mapping)
            or set(envelope) != {"version", "protection", "data"}
            or type(envelope["version"]) is not int or envelope["version"] != 1
            or envelope["protection"] != "windows-dpapi-user"
            or not isinstance(envelope["data"], str)
            or len(envelope["data"]) > _SECRET_BYTE_LIMIT * 2
        ):
            raise ValueError("Unsupported envelope")
        payload = _dpapi(base64.b64decode(envelope["data"], validate=True), decrypt=True)
        material = json.loads(payload)
        if not isinstance(material, dict) or set(material) != {"api_key", "headers"}:
            raise ValueError("Invalid protected material")
        key, headers = material["api_key"], material["headers"]
        if not isinstance(key, str) or not isinstance(headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in headers.items()
        ):
            raise ValueError("Invalid protected material")
        # Preserve existing environment-reference semantics without persisting resolved values.
        return (resolve_vars(key), resolve_vars(headers)) if resolve else (key, headers)
    except (ConfigError, ValueError, TypeError, OSError, RecursionError):
        raise ConfigError(UNAVAILABLE_MESSAGE) from None
