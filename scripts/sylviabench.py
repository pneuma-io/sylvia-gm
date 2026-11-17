"""Run the source-checkout benchmark without including it in the application wheel."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sylviabench.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
