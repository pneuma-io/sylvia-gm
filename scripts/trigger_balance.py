"""Run TD balance instrumentation from this checkout without installing a second lab."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "development/trigger-discipline/source")]

from balance_suite.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
