"""Generate reference vectors for `sylvia-gm-owner claude-settings` (`claude_settings.main()`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-owner/tests/vectors/generate_claude_settings_cli_vectors.py

Each case runs the real `main()` in a fresh process, offline, in an empty work folder that is
also its home and profile, with `sys.argv[0]` set to the Rust program name so the usage and
error texts can be compared byte for byte. The vector keeps each case's arguments (`<work>`
for the work folder), exit code, and stdout and stderr exactly as written (on Windows with
Python's `\\r\\n`). `--install` is left out: it downloads Claude Code. The installer has its own
vectors (`sylvia-provider/tests/install_vectors.rs`). Reference tooling only; retired with
the Python source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[3]
OUTPUT = HERE / ("claude_settings_cli_vectors.%s.json" % ("windows" if os.name == "nt" else "linux"))
PROG = "sylvia-gm-owner claude-settings"
RUNNER = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = sys.argv.pop(1); "
    "from sylvia_gm.config.claude_settings import main; main()"
)
CONFIG = "<work>/data/config.yaml"

CASES = [
    [],
    ["-h"],
    ["--help", "--config"],
    ["--config", CONFIG, "-h"],
    ["--config", CONFIG],
    ["--config", "config.yaml"],
    [f"--config={CONFIG}"],
    ["--conf", CONFIG],
    ["--c", CONFIG],
    ["--config", "<work>/data/other.yaml"],
    ["--config", "<work>/data"],
    ["--config", ""],
    ["--config"],
    ["--config", "--login"],
    ["--config", CONFIG, "--lo"],
    ["--config", CONFIG, "--log"],
    ["--lo", "--config", CONFIG],
    ["-h", "--lo"],
    ["--config", CONFIG, "--login"],
    ["--config", CONFIG, "--login", "--console"],
    ["--config", CONFIG, "--logi", "--cons"],
    ["--config", CONFIG, "--logout"],
    ["--config", CONFIG, "--logout", "--login"],
    ["--config", CONFIG, "--model", "claude-opus-5-5"],
    ["--config", CONFIG, "--model", "claude-opus-5-5", "--context-window", "200000"],
    ["--config", CONFIG, "--model=claude-opus-5-5", "--context-window= 1_000 "],
    ["--config", CONFIG, "--model", "claude-opus-5-5", "--context-window", "x"],
    ["--config", CONFIG, "--model", "claude-opus-5-5", "--context-window", "-5"],
    ["--config", CONFIG, "--model", "claude-opus-5-5", "--context-window", "1.5"],
    ["--config", CONFIG, "--model", "claude-opus-5-5", "--context-window"],
    ["--config", CONFIG, "--model", ""],
    ["--config", CONFIG, "--model"],
    ["--config", CONFIG, "--model", "-x"],
    ["--config", CONFIG, "--model", "-5"],
    ["--config", CONFIG, "--context-window", "4096"],
    ["--config", CONFIG, "--install=yes"],
    ["--config", CONFIG, "--console=it's"],
    ["--config", CONFIG, "-hx"],
    ["--config", CONFIG, "-hh"],
    ["--config", CONFIG, "-h=x"],
    ["--config", CONFIG, "-h-x"],
    ["--config", CONFIG, "--install="],
    ["--config", CONFIG, "extra", "--nope"],
    ["--config", CONFIG, "-x", "-"],
    ["--config", CONFIG, "--", "--login"],
    ["--config", CONFIG, "--config", "<work>/data/other.yaml"],
    ["--config", "<work>/data/other.yaml", "--config", CONFIG],
    ["--nope", "--config", CONFIG, "--model"],
    ["--config", CONFIG, "two words"],
    ["--config", CONFIG, "--model", "café"],
]


def run(args: list[str]) -> dict[str, object]:
    work = Path(tempfile.mkdtemp(prefix="sylvia-cli-"))
    try:
        (work / "data").mkdir()
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "PATH", "COMSPEC") if key in os.environ}
        env.update({
            "HOME": str(work), "USERPROFILE": str(work), "APPDATA": str(work),
            "LOCALAPPDATA": str(work), "TEMP": str(work), "TMP": str(work),
            "XDG_CONFIG_HOME": str(work / "xdg"), "PYTHONIOENCODING": "utf-8",
        })
        # `<work>` with forward slashes, so a case reads the same on every platform.
        real = [arg.replace("<work>", work.as_posix()) for arg in args]
        done = subprocess.run(
            [sys.executable, "-I", "-c", RUNNER, str(ROOT / "src"), PROG, *real],
            cwd=work, env=env, capture_output=True, timeout=120,
        )
        def scrub(data: bytes) -> str:
            text = data.decode("utf-8")
            for form in (work.as_posix(), str(work)):
                text = text.replace(form, "<work>")
            return text
        return {"args": args, "exit": done.returncode, "stdout": scrub(done.stdout), "stderr": scrub(done.stderr)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    cases = [run(args) for args in CASES]
    OUTPUT.write_text(json.dumps({"prog": PROG, "cases": cases}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
