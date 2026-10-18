"""Build the checked-in SRD Lite launch package through ordinary admission."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path

from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.infrastructure.package_archive import admit_package_archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = bundled_srd_archive()
    with admit_package_archive(io.BytesIO(raw)) as admitted:
        validate_admitted_package_runtimes(admitted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(f"Admitted Lite: {args.output} ({len(raw)} bytes, sha256={hashlib.sha256(raw).hexdigest()})")


if __name__ == "__main__":
    main()
