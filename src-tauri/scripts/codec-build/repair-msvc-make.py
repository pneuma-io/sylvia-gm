"""Repair only generated dependency scanning and MSVC response-file plumbing.

Apply after configure in a freshly extracted source tree. This does not change
FFmpeg C code or enable any component. Originals and a source patch are retained.
"""

import difflib
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
source = root / "ffmpeg-8.1.2"
config = source / "ffbuild/config.mak"
library = source / "ffbuild/library.mak"
original = library.read_text()
needle = "$(Q)echo $$(filter %.o,$$^) > $$@.objs"
if needle not in original:
    raise SystemExit("Expected fresh FFmpeg 8.1.2 response-file rule missing")
changed = original.replace(needle, "$(Q)test -f $$@.objs")
library.with_suffix(".mak.original").write_text(original, newline="\n")
library.write_text(changed, newline="\n")
(root / "ffmpeg-build-changes.diff").write_text(
    "".join(
        difflib.unified_diff(
            original.splitlines(True),
            changed.splitlines(True),
            fromfile="a/ffbuild/library.mak",
            tofile="b/ffbuild/library.mak",
        )
    ),
    newline="\n",
)
lines = config.read_text().splitlines()
command = (
    'SLIB_CREATE_DEF_CMD="$(FFMPEG_BUILD_PYTHON)" ../recipes/create-msvc-def.py '
    '$(SUBDIR) $(SUBDIR)lib$(NAME).ver $$@'
)
if sum(line.startswith("SLIB_CREATE_DEF_CMD=") for line in lines) != 1:
    raise SystemExit("Expected one generated export command")
config.write_text(
    "\n".join(command if line.startswith("SLIB_CREATE_DEF_CMD=") else line for line in lines)
    + "\n",
    newline="\n",
)
