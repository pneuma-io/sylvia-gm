"""Run the guided generators in their order: draft, advance, start, finalize, ruleset.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_guided_all.py

The draft generator writes the new profile (`guided/seed.sqlite3.gz`) the others start from,
so it runs first. Each runs in its own interpreter, with this one's flags, as when run alone.
Reference tooling only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("draft", "advance", "start", "finalize", "ruleset")


def main() -> int:
    flags = [flag for flag in ("-I" if sys.flags.isolated else "", "-B" if sys.dont_write_bytecode else "") if flag]
    for name in ORDER:
        subprocess.run([sys.executable, *flags, str(HERE / f"generate_guided_{name}_runs.py")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
