"""Public packaged-owner identity before storage, providers or migration can be opened."""

from __future__ import annotations

import json
import platform
import sys

from sylvia_gm import __version__

# Bump when stored semantics become incompatible even without SQL DDL changes.
STORAGE_COMPATIBILITY = 1


def runtime_identity() -> dict[str, object]:
    architecture = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(platform.machine().lower(), "unsupported")
    system = "windows" if sys.platform == "win32" else sys.platform
    return {
        "format_version": 1,
        "product": "Sylvia GM",
        "component": "owner",
        "version": __version__,
        "storage_compatibility": STORAGE_COMPATIBILITY,
        "target": f"{system}-{architecture}",
    }


def main() -> int:
    print(json.dumps(runtime_identity(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
