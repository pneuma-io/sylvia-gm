"""Current-user protection for owner-local provider secrets.

Windows uses current-user DPAPI. Linux seals with AES-256-GCM under a per-user key
file in the state folder, away from the data folder that holds the settings file.
Each platform opens only its own envelope kind; other platforms fail closed.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from sylvia_gm.config._resolver import ConfigError, resolve_vars

if sys.platform == "linux":
    UNAVAILABLE_MESSAGE = (
        "This computer could not unlock this connection's saved credentials. "
        "Re-enter its API key and private headers, or remove the connection."
    )
    _PROTECTION = "linux-user-key-file"
    _VERIFY_FAILED = "Saved credential protection could not be verified"
else:
    UNAVAILABLE_MESSAGE = (
        "Windows could not unlock this connection's saved credentials. "
        "Re-enter its API key and private headers, or remove the connection."
    )
    _PROTECTION = "windows-dpapi-user"
    _VERIFY_FAILED = "Windows credential protection could not be verified"
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


if sys.platform == "linux":
    import fcntl
    import stat

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _KEY_FILE_NAME = "credential-key"
    _KEY_BYTES = 32
    _NONCE_BYTES = 12
    _TAG_BYTES = 16
    _ASSOCIATED_DATA = b"sylvia-gm:linux-user-key-file:v1"
    _PROTECT_FAILED = "Saved credential protection failed"

    def _key_folder() -> Path:
        """Return ``<state>/sylvia-gm``, where state honours only an absolute XDG_STATE_HOME."""
        configured = os.environ.get("XDG_STATE_HOME", "")
        absolute = os.path.isabs(configured)
        state = Path(configured) if absolute else Path.home() / ".local" / "state"
        folder = state / "sylvia-gm"
        if not folder.is_absolute():
            raise OSError("key folder is not absolute")
        return folder

    def _create_private_folders(folder: Path) -> None:
        """Create each missing folder with mode 0700; never change an existing folder."""
        missing: list[Path] = []
        current = folder
        while not os.path.lexists(current) and current.parent != current:
            missing.append(current)
            current = current.parent
        for path in reversed(missing):
            with suppress(FileExistsError):
                os.mkdir(path, 0o700)

    def _open_key_folder(*, create: bool) -> int:
        """Open the key folder without following a link and prove it is private."""
        folder = _key_folder()
        if create:
            _create_private_folders(folder)
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            status = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_mode & 0o022
            ):
                raise OSError("key folder is not private")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _read_key(folder: int) -> bytes:
        """Read the key through the folder descriptor; raise FileNotFoundError only if absent."""
        descriptor = os.open(
            _KEY_FILE_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=folder,
        )
        try:
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_mode & 0o077
                or status.st_nlink != 1
                or status.st_size != _KEY_BYTES
            ):
                raise OSError("key file is not private")
            key = b""
            while len(key) <= _KEY_BYTES:
                chunk = os.read(descriptor, _KEY_BYTES + 1 - len(key))
                if not chunk:
                    break
                key += chunk
        finally:
            os.close(descriptor)
        if len(key) != _KEY_BYTES:
            raise OSError("key file has the wrong size")
        return key

    def _create_key(folder: int) -> None:
        """Publish a new key by hard link so an existing key is never replaced."""
        temporary = f".{_KEY_FILE_NAME}-{os.urandom(8).hex()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=folder,
        )
        try:
            try:
                remaining = memoryview(os.urandom(_KEY_BYTES))
                while remaining:
                    remaining = remaining[os.write(descriptor, remaining):]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            # Losing a race with another process leaves its key in place; read that one.
            with suppress(FileExistsError):
                os.link(
                    temporary, _KEY_FILE_NAME,
                    src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False,
                )
        finally:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=folder)
        os.fsync(folder)

    def _user_key(*, create: bool) -> bytes:
        """Read the current user's key; only protection may create a missing one.

        A folder lock keeps readers from seeing a new key while its temporary link
        still exists, which would otherwise look like a second hard link.
        """
        folder = _open_key_folder(create=create)
        try:
            fcntl.flock(folder, fcntl.LOCK_EX if create else fcntl.LOCK_SH)
            try:
                return _read_key(folder)
            except FileNotFoundError:
                if not create:
                    raise
            _create_key(folder)
            return _read_key(folder)
        finally:
            os.close(folder)

    def _key_file_seal(value: bytes, *, decrypt: bool) -> bytes:
        """Seal or open with AES-256-GCM; never expose the key, plaintext or OS details."""
        try:
            if decrypt:
                if not _NONCE_BYTES + _TAG_BYTES <= len(value) <= _SECRET_BYTE_LIMIT:
                    raise ValueError("sealed value has an unsupported size")
                cipher = AESGCM(_user_key(create=False))
                result = cipher.decrypt(
                    value[:_NONCE_BYTES], value[_NONCE_BYTES:], _ASSOCIATED_DATA
                )
            else:
                cipher = AESGCM(_user_key(create=True))
                nonce = os.urandom(_NONCE_BYTES)
                result = nonce + cipher.encrypt(nonce, value, _ASSOCIATED_DATA)
        except Exception:
            raise ConfigError(UNAVAILABLE_MESSAGE if decrypt else _PROTECT_FAILED) from None
        if len(result) > _SECRET_BYTE_LIMIT:
            raise ConfigError("Protected credentials exceed the supported size")
        return result


def _platform_seal(value: bytes, *, decrypt: bool) -> bytes:
    """Seal or open bytes with the current platform's user-scoped protection."""
    if sys.platform == "linux":
        return _key_file_seal(value, decrypt=decrypt)
    return _dpapi(value, decrypt=decrypt)


def protect_credentials(api_key: str, headers: Mapping[str, str]) -> dict[str, object]:
    """Protect both header names and values, then verify before a settings write."""
    payload = json.dumps(
        {"api_key": api_key, "headers": dict(headers)}, ensure_ascii=False
    ).encode()
    if len(payload) > _SECRET_BYTE_LIMIT // 2:
        raise ConfigError("Provider credentials exceed the supported size")
    encrypted = _platform_seal(payload, decrypt=False)
    if _platform_seal(encrypted, decrypt=True) != payload:
        raise ConfigError(_VERIFY_FAILED)
    return {
        "version": 1, "protection": _PROTECTION,
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
            or envelope["protection"] != _PROTECTION
            or not isinstance(envelope["data"], str)
            or len(envelope["data"]) > _SECRET_BYTE_LIMIT * 2
        ):
            raise ValueError("Unsupported envelope")
        payload = _platform_seal(base64.b64decode(envelope["data"], validate=True), decrypt=True)
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
