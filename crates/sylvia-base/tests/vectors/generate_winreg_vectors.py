"""Generate reference vectors for sylvia_base::winreg (Windows only).

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-base/tests/vectors/generate_winreg_vectors.py

Each value below is written with `reg import` into a scratch key under
HKEY_CURRENT_USER\\Software (deleted in both views afterwards, and checked), as raw bytes, so the
shapes `Reg2Py` treats differently can be made. Then Python's `winreg.OpenKey` and
`winreg.QueryValueEx` read each one back, and the result is recorded: a string as UTF-16 code
units (it may hold a lone surrogate), a number, or the `OSError`'s `winerror`. A one- to
three-byte `REG_DWORD` is left out: CPython reads past the returned bytes there. This script is
reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "winreg_vectors.json"
# A key of its own directly under Software, so deleting it leaves no parent behind.
SUBKEY = rf"Software\sylvia-gm-port-test-winreg-vectors-{os.getpid()}"


def sz(text: str, *, nul: bool = True) -> str:
    return (text + ("\0" if nul else "")).encode("utf-16-le", "surrogatepass").hex()


VALUES = [
    ("Plain", 1, sz("Sylvia GM")),
    ("Odd", 1, sz("0.4.1") + "41"),
    ("NoNul", 1, sz("pneuma-io", nul=False)),
    ("EmbeddedNul", 1, sz("sylvia-gm.exe\0junk")),
    ("Empty", 1, ""),
    ("OnlyNul", 1, "0000"),
    ("Pair", 1, sz("die \U0001F3B2")),
    ("LoneSurrogate", 1, sz("Sylvia \udc00GM")),
    ("Long", 1, sz("x" * 5000)),
    ("Expand", 2, sz("%LOCALAPPDATA%\\Sylvia")),
    ("ProxyEnable", 4, "01000000"),
    ("ProxyZero", 4, "00000000"),
    ("DwordEmpty", 4, ""),
    ("DwordLong", 4, "0102030405"),
    ("Binary", 3, "0102"),
    ("Qword", 11, "0100000000000000"),
    ("Multi", 7, sz("a\0b\0")),
    ("", 1, sz("the default value")),
]


def delete_scratch_key(subkey: str) -> None:
    """Delete a scratch HKCU key in both registry views, then make sure it is gone in both."""
    for view in ("/reg:64", "/reg:32"):
        # A view where the key is already gone answers 1; the query below is the check.
        subprocess.run(["reg", "delete", "HKCU\\" + subkey, "/f", view], capture_output=True)
    for view in ("/reg:64", "/reg:32"):
        if subprocess.run(["reg", "query", "HKCU\\" + subkey, view], capture_output=True).returncode == 0:
            raise RuntimeError(f"The scratch key HKCU\\{subkey} is still there ({view}).")


def reg_file() -> str:
    lines = ["Windows Registry Editor Version 5.00", "", f"[HKEY_CURRENT_USER\\{SUBKEY}]"]
    for name, kind, data in VALUES:
        label = "@" if name == "" else json.dumps(name)
        lines.append(f"{label}=hex({kind:x}):" + ",".join(data[i:i + 2] for i in range(0, len(data), 2)))
    return "\r\n".join(lines) + "\r\n"


def units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def read(subkey: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError as error:
        return {"error": error.winerror}
    if isinstance(value, str):
        return {"kind": kind, "units": units(value)}
    if isinstance(value, int):
        return {"kind": kind, "number": value}
    return {"kind": kind, "other": type(value).__name__}


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="sylvia-winreg-"))
    try:
        path = folder / "values.reg"
        path.write_bytes(b"\xff\xfe" + reg_file().encode("utf-16-le"))
        subprocess.run(["reg", "import", str(path)], check=True, capture_output=True)
        cases = [{"name": name, "kind": kind, "data": data, "result": read(SUBKEY, name)} for name, kind, data in VALUES]
        cases.append({"name": "Missing", "kind": None, "data": None, "result": read(SUBKEY, "Missing")})
        missing_key = read(SUBKEY + r"\absent", "")
    finally:
        delete_scratch_key(SUBKEY)
        shutil.rmtree(folder)
    vectors = {"python": sys.version.split()[0], "values": cases, "missing_key": missing_key}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
