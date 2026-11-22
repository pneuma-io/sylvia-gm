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
#                           swresample-6.dll, avutil-60.dll, NumPy's OpenBLAS DLL, the ctranslate2
#                           wheel's ctranslate2.dll and libiomp5md.dll, the onnxruntime wheel's
#                           onnxruntime.dll and onnxruntime_providers_shared.dll, and faster-whisper's
#                           silero_vad_v6.onnx (dictation::speech::verify checks the same list)
#   <folder>/ct2-lib/       the import libraries ct2rs links against (LIBRARY_PATH for the
#                           `dictation` build), from docs/rust-port/tools/ct2_import_lib.sh
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
site=.venv/Lib/site-packages
cp "$site/ctranslate2/ctranslate2.dll" "$site/ctranslate2/libiomp5md.dll" "$out/native/"
cp "$site/onnxruntime/capi/onnxruntime.dll" "$site/onnxruntime/capi/onnxruntime_providers_shared.dll" "$out/native/"
cp "$site/faster_whisper/assets/silero_vad_v6.onnx" "$out/native/"
bash docs/rust-port/tools/ct2_import_lib.sh "$out/ct2-lib" > /dev/null

expected="3d950dfb94a55fc3b25fdd55ea4c49aa0361506d0c304d2615d3fa49a21634ad  swresample-6.dll
4afe3a74c43b9860d64ebdbdaef30b8758d907a1d3128ff8fc0e28d3b7227008  avutil-60.dll
ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8  libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll
ca1127837d032bf8217d0cbcd68e8cef1f93bda3111eff00ced98795d9e51160  ctranslate2.dll
982233366b0afcda1e0f55a0b134097e35b779613f54ddb69e685e6cd06b755f  libiomp5md.dll
aa7737cc5c987286f09f6f12304fa786ea697b11b4ca7ab59a027ff4ce343a9c  onnxruntime.dll
029af27204cff24103c5837a0eb507e4a31ef269116f400c6b25c5457ddc9a32  onnxruntime_providers_shared.dll
4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2  silero_vad_v6.onnx"
(cd "$out/native" && echo "$expected" | sha256sum --check --quiet)
echo "staged: $out/native"
