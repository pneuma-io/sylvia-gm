#!/usr/bin/env bash
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/ffmpeg-8.1.2"
vc_bin="$(cygpath -u "$VCToolsInstallDir")/bin/Hostx64/x64"
export PATH="$vc_bin:$PATH"
make_bin=$(cygpath -u "$FFMPEG_BUILD_MAKE")
"$make_bin" -j2 "SHELL=$FFMPEG_BUILD_SH" CCDEP=: CXXDEP=: ASDEP=: HOSTCCDEP=:
"$make_bin" "SHELL=$FFMPEG_BUILD_SH" CCDEP=: CXXDEP=: ASDEP=: HOSTCCDEP=: install
