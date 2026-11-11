"""Give direct pytest runs the same import and fixture defaults as the journey runner."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
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
# Match the normal runner: optional evidence archival is an explicit selection.
os.environ.setdefault("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "0")
if "SRD_LITE_CANDIDATE" not in os.environ:
    from scripts.journey_fixture_inputs import prepare_candidate

    prepare_candidate(os.environ, ROOT, Path(os.environ["SYLVIA_QA_FIXTURE_ROOT"]))


def pytest_configure(config):
    """Keep direct pytest's disposable data inside this invocation's work item."""
    if config.option.basetemp is None:
        attempts = Path(os.environ["SYLVIA_QA_FIXTURE_ROOT"]) / "attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = tempfile.mkdtemp(prefix="pytest-", dir=attempts)


def pytest_collection_modifyitems(items):
    """Known failures still execute; an unexpected pass fails until the entry is reviewed."""
    import pytest
    from scripts.known_test_defects import KNOWN_DEFECTS, failure_reason

    for item in items:
        if item.nodeid in KNOWN_DEFECTS:
            item.add_marker(pytest.mark.xfail(strict=True, reason=failure_reason(item.nodeid)))
