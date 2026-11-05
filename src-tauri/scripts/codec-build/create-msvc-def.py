"""Use MSVC response files to avoid native GNU Make command-line truncation.

Fresh-source build only: every .o under the selected library was compiled by the
current unchanged configuration. No source C files or selected features change.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

folder, version_script, target = map(Path, sys.argv[1:])
objects = sorted(folder.rglob("*.o"))
if not objects:
    raise SystemExit("No compiled library objects")
response = Path(str(target) + ".objs")
response.write_text("\n".join('"' + p.as_posix() + '"' for p in objects) + "\n", encoding="ascii")
archive = Path(str(target) + ".export.lib")
subprocess.run(
    ["lib.exe", "-nologo", "-out:" + str(archive), "@" + str(response)],
    check=True,
    stdout=subprocess.DEVNULL,
)
with target.with_suffix(".def").open("wb") as output:
    env = os.environ.copy()
    env.pop("AR", None)
    env.pop("NM", None)
    subprocess.run(
        [
            os.environ["FFMPEG_BUILD_SH"],
            "compat/windows/makedef",
            str(version_script),
            str(archive),
        ],
        check=True,
        stdout=output,
        env=env,
    )
Path(str(target) + ".object-inputs.json").write_text(
    json.dumps([p.as_posix() for p in objects], indent=2) + "\n", encoding="utf-8"
)
