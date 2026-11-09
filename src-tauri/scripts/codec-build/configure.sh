#!/usr/bin/env bash
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/ffmpeg-8.1.2"
vc_bin="$(cygpath -u "$VCToolsInstallDir")/bin/Hostx64/x64"
export PATH="$vc_bin:$PATH"
./configure --toolchain=msvc --arch=x86_64 --target-os=win64 --enable-shared --disable-static --disable-programs --disable-doc --disable-debug --disable-autodetect --disable-x86asm --disable-network --disable-gpl --disable-nonfree --disable-version3 --prefix=../ffmpeg-lgpl
