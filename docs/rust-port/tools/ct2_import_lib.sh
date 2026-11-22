#!/usr/bin/env bash
# Generate the import library that links ct2rs (`system` feature) against the CTranslate2 DLL
# the product ships: the locked ctranslate2 4.8.3 wheel's own `ctranslate2.dll` (Windows, Git
# Bash, MSVC Build Tools). The generated files are build input only and are never committed.
#
#   bash docs/rust-port/tools/ct2_import_lib.sh <output folder outside the checkout, or target/...>
#
# Run from the repository root after `uv sync --extra dev --extra dictation --python 3.12`. It:
#   1. checks `ctranslate2.dll` (and `libiomp5md.dll` beside it) against the SHA-256 of the
#      locked wheel's files, before generating anything;
#   2. lists the DLL's exports with MSVC's `dumpbin /exports`, writes them to a `.def`, and
#      makes `ctranslate2.lib` with `lib /def /machine:x64`;
#   3. makes empty `cpu_features.lib`, `cublas.lib` and `cublasLt.lib`, names ct2rs's `system` link
#      step always emits (see below);
#   4. copies the two DLLs beside the libraries, for the tests' `SYLVIA_DICTATION_NATIVE_DIR`.
# Build with `LIBRARY_PATH=<output folder>` so ct2rs's build script finds the libraries.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <output folder>" >&2
  exit 2
fi
out="$1"
mkdir -p "$out"
package=.venv/Lib/site-packages/ctranslate2

expected="ca1127837d032bf8217d0cbcd68e8cef1f93bda3111eff00ced98795d9e51160  ctranslate2.dll
982233366b0afcda1e0f55a0b134097e35b779613f54ddb69e685e6cd06b755f  libiomp5md.dll"
(cd "$package" && echo "$expected" | sha256sum --check --quiet) || {
  echo "the installed ctranslate2 is not the locked 4.8.3 wheel's" >&2
  exit 1
}

vswhere="/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
install="$("$vswhere" -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 \
  -property installationPath | tr -d '\r')"
version="$(tr -d '\r\n ' < "$install/VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt")"
tools="$install/VC/Tools/MSVC/$version/bin/Hostx64/x64"
[ -x "$tools/dumpbin.exe" ] && [ -x "$tools/lib.exe" ] || {
  echo "MSVC dumpbin/lib not found under $tools" >&2
  exit 1
}

# dumpbin's export table: "ordinal hint RVA name" rows between the header and "Summary".
"$tools/dumpbin.exe" -nologo -exports "$package/ctranslate2.dll" > "$out/ctranslate2.exports.txt"
{
  echo "LIBRARY ctranslate2.dll"
  echo "EXPORTS"
  awk '/ordinal +hint +RVA +name/ { table = 1; next }
       /^ *Summary/ { table = 0 }
       table && NF >= 4 && $1 ~ /^[0-9]+$/ { print "    " $4 }' "$out/ctranslate2.exports.txt"
} > "$out/ctranslate2.def"
count="$(($(wc -l < "$out/ctranslate2.def") - 2))"
[ "$count" -gt 1000 ] || { echo "only $count exports parsed" >&2; exit 1; }
"$tools/lib.exe" -nologo -machine:x64 -def:"$(cygpath -w "$out/ctranslate2.def")" \
  -out:"$(cygpath -w "$out/ctranslate2.lib")" > /dev/null

# Empty libraries for the names ct2rs 0.10.2's `system` link step always emits: `cpu_features`
# on x86-64 (inside the wheel's DLL), and `cublas`/`cublasLt` whenever its `cuda-dynamic-loading`
# feature is off, CUDA or not (build.rs `link_system_libraries`). Nothing references them.
for name in cpu_features cublas cublasLt; do
  printf 'LIBRARY %s\nEXPORTS\n' "$name" > "$out/$name.def"
  "$tools/lib.exe" -nologo -machine:x64 -def:"$(cygpath -w "$out/$name.def")" \
    -out:"$(cygpath -w "$out/$name.lib")" > /dev/null
done

cp "$package/ctranslate2.dll" "$package/libiomp5md.dll" "$out/"
echo "generated $out/ctranslate2.lib ($count exports) and the empty cpu_features, cublas, cublasLt libraries"
