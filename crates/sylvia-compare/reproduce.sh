#!/usr/bin/env bash
# Check committed Python references against N fresh Python runs before calling them reproducible:
#   crates/sylvia-compare/reproduce.sh N SESSION...   (see docs/rust-port/comparison-tool.md)
# Run from the checkout root. Prints one line per run and a tally; exit 1 if any run differed.
set -uo pipefail
runs="$1"; shift
case "$(uname -s)" in
  Linux) platform=linux; exe=""; python="$PWD/.venv/bin/python" ;;
  *) platform=windows; exe=".exe"; python="$(pwd -W 2>/dev/null || pwd)/.venv/Scripts/python.exe" ;;
esac
[ -x "$python" ] || python="${SYLVIA_REFERENCE_PYTHON:?no Python reference environment}"
compare="target/debug/sylvia-compare$exe"
work="${REPRO_WORK:-$(mktemp -d)}"
mkdir -p "$work"
failed=0
for name in "$@"; do
  same=0
  for run in $(seq 1 "$runs"); do
    out="$work/$name-$run"
    if "$compare" check --session "crates/sylvia-compare/sessions/$name.session.json" \
        --reference "crates/sylvia-compare/recordings/$platform/$name.recording.json" \
        --work "$out" --limit 10 --owner "$python" --arg -B --arg -m --arg sylvia_gm.interfaces.owner_process \
        > "$out.log" 2>&1; then
      same=$((same + 1)); echo "$name run $run: same"
    else
      failed=1; echo "$name run $run: DIFFERENT"; sed 's/^/    /' "$out.log" | head -24
    fi
    rm -rf "$out"
  done
  echo "== $name ($platform): $same of $runs runs same"
done
exit $failed
