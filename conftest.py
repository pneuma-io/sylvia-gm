"""Give direct pytest runs the same import and fixture defaults as the journey runner."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
os.environ.setdefault(
    "SRD_LITE_CANDIDATE",
    str(ROOT / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"),
)
if "SYLVIA_QA_FIXTURE_ROOT" not in os.environ:
    owned = ROOT.parent / f"{ROOT.name}-checks"
    owned.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "SYLVIA_QA_FIXTURE_ROOT", tempfile.mkdtemp(prefix="pytest-", dir=owned)
    )
os.environ.setdefault(
    "SYLVIA_RUNTIME_CACHE_DIR",
    str(Path(os.environ["SYLVIA_QA_FIXTURE_ROOT"]) / "runtime-cache"),
)
