#!/usr/bin/env bash
# Stage the native libraries the dictation port loads, exactly as the product ships them, for
# the `dictation` checks and the dictation vector generators (Windows, Git Bash).
#
#   bash docs/rust-port/tools/stage_dictation_natives.sh <empty folder outside the checkout>
#
# Run from the repository root after `uv sync --extra dev --extra dictation --python 3.12`.
# It writes:
#   <folder>/ffmpeg-work/   the project's LGPL FFmpeg 8.1.2 DLLs, recovered and verified by
#                           src-tauri/scripts/provision_ffmpeg_lgpl.py (it downloads the
#                           v0.3.0-alpha.2 public release archive and checks its SHA-256)
#   <folder>/pyav-lgpl/     PyAV 18.0.0 bound to those DLLs by src-tauri/scripts/stage_pyav_lgpl.py
#                           (the argument generate_dictation_decode_vectors.py takes)
#   <folder>/native/        the DLLs the Rust owner loads (SYLVIA_DICTATION_NATIVE_DIR):
#                           swresample-6.dll, avutil-60.dll and NumPy's OpenBLAS DLL
# and checks each file in native/ against docs/rust-port/third-party-ports.md's hashes.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <empty folder outside the checkout>" >&2
  exit 2
fi
out="$1"
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
  echo "$out must be new or empty" >&2
  exit 2
fi
mkdir -p "$out/native"
python=.venv/Scripts/python.exe

"$python" src-tauri/scripts/provision_ffmpeg_lgpl.py --work-dir "$out/ffmpeg-work" > /dev/null
"$python" src-tauri/scripts/stage_pyav_lgpl.py --ffmpeg "$out/ffmpeg-work/ffmpeg-lgpl" \
  --output "$out/pyav-lgpl" --manifest licenses/dictation/PYAV-LGPL-BUILD.json > /dev/null
cp "$out/ffmpeg-work/ffmpeg-lgpl/swresample-6.dll" "$out/ffmpeg-work/ffmpeg-lgpl/avutil-60.dll" "$out/native/"
cp .venv/Lib/site-packages/numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll "$out/native/"

expected="3d950dfb94a55fc3b25fdd55ea4c49aa0361506d0c304d2615d3fa49a21634ad  swresample-6.dll
4afe3a74c43b9860d64ebdbdaef30b8758d907a1d3128ff8fc0e28d3b7227008  avutil-60.dll
ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8  libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll"
(cd "$out/native" && echo "$expected" | sha256sum --check --quiet)
echo "staged: $out/native"
