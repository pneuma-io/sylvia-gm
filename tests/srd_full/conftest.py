"""Keep historical Full qualification separate from the shipped Alpha test selection."""

from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--srd-full-legacy",
        action="store_true",
        default=False,
        help="Collect historical Full cases after rebinding their fixture inputs; see the Full README.",
    )


def pytest_ignore_collect(collection_path: Path, config):
    if collection_path.name in {
        "test_full_package_drafts.py",
        "conftest.py",
        "__init__.py",
    }:
        return False
    return collection_path.name.startswith("test_") and not config.getoption(
        "--srd-full-legacy"
    )
