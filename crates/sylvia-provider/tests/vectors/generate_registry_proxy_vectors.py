"""Generate reference vectors for the Windows registry proxy read (Windows only).

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_registry_proxy_vectors.py

Each case's `ProxyEnable` and `ProxyServer` are written as raw bytes, of any registry type, with
`reg import` into its own subkey of one scratch key under HKEY_CURRENT_USER\\Software. That key is
deleted in both registry views afterwards, and the deletion is checked. Python's own
`urllib.request.getproxies_registry()` then reads each subkey, through the real `winreg`, in
place of Internet Settings, and its result is recorded. Nothing reads or changes the real proxy
settings. A lone surrogate cannot cross JSON into Rust, so it is recorded as U+FFFD and the case is
marked `row_3` (differences row 3). Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "registry_proxy_vectors.json"
# A key of its own directly under Software, so deleting it leaves no parent behind.
ROOT = rf"Software\sylvia-gm-port-test-registry-proxies-{os.getpid()}"

REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ, REG_QWORD = 0, 1, 2, 3, 4, 7, 11

LONE_SURROGATE = chr(0xDC00)


def sz(text: str, *, nul: bool = True) -> str:
    return (text + ("\0" if nul else "")).encode("utf-16-le", "surrogatepass").hex()


def dword(number: int) -> str:
    return number.to_bytes(4, "little").hex()


def qword(number: int) -> str:
    return number.to_bytes(8, "little").hex()


SERVER = (REG_SZ, sz("proxy.example:8080"))
ON = (REG_DWORD, dword(1))

# (ProxyEnable, ProxyServer); None leaves the value out.
CASES = [
    (ON, SERVER),
    ((REG_DWORD, dword(0)), SERVER),
    ((REG_DWORD, ""), SERVER),
    ((REG_DWORD, dword(7)), SERVER),
    ((REG_QWORD, qword(1)), SERVER),
    ((REG_QWORD, qword(0)), SERVER),
    ((REG_QWORD, ""), SERVER),
    ((REG_QWORD, qword(1 << 40)), SERVER),
    ((REG_BINARY, "01"), SERVER),
    ((REG_BINARY, "00"), SERVER),
    ((REG_BINARY, ""), SERVER),
    ((REG_NONE, ""), SERVER),
    ((REG_NONE, "01"), SERVER),
    ((REG_MULTI_SZ, sz("a\0")), SERVER),
    ((REG_MULTI_SZ, ""), SERVER),
    ((REG_MULTI_SZ, "0000"), SERVER),
    ((REG_MULTI_SZ, "00000000"), SERVER),
    ((REG_SZ, "0000"), SERVER),
    ((REG_SZ, sz("0")), SERVER),
    ((REG_EXPAND_SZ, sz("1")), SERVER),
    (None, SERVER),
    (ON, None),
    (ON, (REG_DWORD, dword(5))),
    (ON, (REG_DWORD, "")),
    (ON, (REG_QWORD, qword(8080))),
    (ON, (REG_BINARY, "703a31")),
    (ON, (REG_BINARY, "270aff5c22")),
    (ON, (REG_BINARY, "2722")),
    (ON, (REG_BINARY, "")),
    (ON, (REG_NONE, "")),
    (ON, (REG_NONE, "68747470")),
    (ON, (REG_MULTI_SZ, sz("http=a\0https=b\0"))),
    (ON, (REG_MULTI_SZ, sz("a\0b", nul=False))),
    (ON, (REG_MULTI_SZ, "")),
    (ON, (REG_MULTI_SZ, sz("\0\0x\0"))),
    (ON, (REG_MULTI_SZ, sz("it's\0") + "41")),
    (ON, (REG_EXPAND_SZ, sz("http=h:1;https=h:2"))),
    (ON, (REG_SZ, sz("socks=s:1080"))),
    (ON, (REG_SZ, sz("http=a;bad"))),
    (ON, (REG_SZ, "")),
    # Differences row 3: the lone surrogate stays in Python's str and reads as U+FFFD in Rust.
    (ON, (REG_SZ, sz("pr" + LONE_SURROGATE + "xy:8080"))),
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
    lines = ["Windows Registry Editor Version 5.00", ""]
    for index, (enable, server) in enumerate(CASES):
        lines.append(f"[HKEY_CURRENT_USER\\{ROOT}\\case{index}]")
        for name, value in (("ProxyEnable", enable), ("ProxyServer", server)):
            if value is None:
                continue
            kind, data = value
            lines.append(f'"{name}"=hex({kind:x}):' + ",".join(data[i:i + 2] for i in range(0, len(data), 2)))
        lines.append("")
    return "\r\n".join(lines) + "\r\n"


def replaced(text: str) -> str:
    """The text with each lone surrogate replaced by U+FFFD, as a Rust string holds it."""
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def read(subkey: str) -> list:
    real_open = winreg.OpenKey

    def open_case(root, _path, *args, **kwargs):
        return real_open(root, subkey, *args, **kwargs)

    winreg.OpenKey = open_case
    try:
        return list(urllib.request.getproxies_registry().items())
    finally:
        winreg.OpenKey = real_open


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="sylvia-registry-proxies-"))
    try:
        path = folder / "values.reg"
        path.write_bytes(b"\xff\xfe" + reg_file().encode("utf-16-le", "surrogatepass"))
        subprocess.run(["reg", "import", str(path)], check=True, capture_output=True)
        records = []
        for index, (enable, server) in enumerate(CASES):
            values = [{"name": name, "kind": value[0], "data": value[1]}
                      for name, value in (("ProxyEnable", enable), ("ProxyServer", server)) if value is not None]
            proxies = read(rf"{ROOT}\case{index}")
            record = {"values": values, "proxies": [[replaced(key), replaced(value)] for key, value in proxies]}
            if any(replaced(text) != text for pair in proxies for text in pair):
                record["row_3"] = True
            records.append(record)
        missing_key = read(rf"{ROOT}\absent")
    finally:
        delete_scratch_key(ROOT)
        shutil.rmtree(folder)
    vectors = {"python": sys.version.split()[0], "cases": records, "missing_key": missing_key}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(records)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
