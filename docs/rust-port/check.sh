#!/usr/bin/env bash
# The Rust port's check command. Run from anywhere inside the checkout, on Linux or in Git Bash.
#
#   docs/rust-port/check.sh                  format, lint, tests, then the Rust owner against
#                                            every recorded session for this platform
#   docs/rust-port/check.sh --record-python  also (re)record every session from the Python
#                                            owner first; needs the snapshot's .venv
#
# Format, lint and test failures fail the script. Session differences do not: they are the
# port's remaining work, printed one line per session. Report those lines in the pull request.
set -euo pipefail
cd "$(dirname "$0")/../.."

case "$(uname -s)" in
  Linux) platform=linux; exe=""; python="$PWD/.venv/bin/python" ;;
  *) platform=windows; exe=".exe"; python="$(pwd -W 2>/dev/null || pwd)/.venv/Scripts/python.exe" ;;
esac
command -v cargo >/dev/null 2>&1 || . "$HOME/.cargo/env"

echo "== format"
cargo fmt --all --check
echo "== lint"
cargo clippy --workspace --all-targets -- -D warnings
echo "== tests"
cargo test --workspace
echo "== build"
cargo build -p sylvia-owner -p sylvia-compare
# Vector hooks (sylvia-engine's `vector-hooks`) are test tooling: the shipped owner must not
# contain them, so its dependency graph without dev-dependencies must not enable the feature.
if cargo tree -p sylvia-owner -e features,no-dev -i sylvia-engine | grep -q 'vector-hooks'; then
  echo "sylvia-owner enables sylvia-engine's vector-hooks feature"; exit 1
fi

compare="target/debug/sylvia-compare$exe"
owner="target/debug/sylvia-gm-owner$exe"
# WebUI host sessions ("webui": true) run the host instead of the owner.
host="target/debug/sylvia-gm$exe"
sessions=crates/sylvia-compare/sessions
recordings="crates/sylvia-compare/recordings/$platform"
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
python_owner=(--owner "$python" --arg -B --arg -m --arg sylvia_gm.interfaces.owner_process)
python_host=(--owner "$python" --arg -B --arg -m --arg sylvia_gm.main)
is_webui() { grep -q '"webui": true' "$1"; }

if [ "${1:-}" = "--record-python" ]; then
  echo "== record from the Python owner ($platform)"
  [ -x "$python" ] || { echo "no Python reference environment at $python"; exit 2; }
  mkdir -p "$recordings"
  for session in "$sessions"/*.session.json; do
    name="$(basename "$session" .session.json)"
    if is_webui "$session"; then python_command=("${python_host[@]}"); else python_command=("${python_owner[@]}"); fi
    "$compare" record --session "$session" --out "$work/record-$name" "${python_command[@]}"
    # A reference is only kept if Python reproduces it: anything that varies between two
    # Python runs must be declared in the session's rules first.
    "$compare" check --session "$session" --reference "$work/record-$name/$name.recording.json" \
      --work "$work/again-$name" "${python_command[@]}"
    cp "$work/record-$name/$name.recording.json" "$recordings/"
  done
fi

echo "== Rust owner against the Python recordings ($platform)"
for session in "$sessions"/*.session.json; do
  name="$(basename "$session" .session.json)"
  reference="$recordings/$name.recording.json"
  if [ ! -f "$reference" ]; then
    echo "$name: no $platform recording yet"
    continue
  fi
  program="$owner"; if is_webui "$session"; then program="$host"; fi
  "$compare" check --session "$session" --reference "$reference" --owner "$program" \
    --work "$work/rust-$name" --limit 0 || true
done
