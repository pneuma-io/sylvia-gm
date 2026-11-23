"""Generate reference vectors for the owner's campaign creation catalog (lane 5, migration step 3).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_creation_catalog_vectors.py

The frozen `interfaces/owner_process._campaign_creation_catalog` runs on a fresh copy of each of
lane 5's legacy Builder bases (`crates/sylvia-store/tests/vectors/legacy_builder`), as is and
with junk in each saved library, with and without participants. Each case records the catalog,
or the error's class, code and message.

The launch migration's own cases (the catalog then `migrate_legacy_builder_chats`) are lane 1's
`crates/sylvia-app/tests/vectors/builder_migration_vectors.json`, which the Rust test reuses.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog  # noqa: E402

FIXTURES = ROOT / "crates/sylvia-store/tests/vectors/legacy_builder"
OUTPUT = Path(__file__).parent / "creation_catalog_vectors.json"

JUNK = {
    "plain": {},
    "junk-plan": {"campaign-plans/plan-0123456789abcdef01234567/record.json": "{not json"},
    "plan-not-utf8": {"campaign-plans/plan-0123456789abcdef01234567/record.json": b"\xff\xfe"},
    "junk-participant": {"participants/participant-0123456789abcdef01234567/record.json": "[1, 2]"},
    "junk-profile": {"character-profiles/profile-0123456789abcdef01234567/record.json": "{}"},
    "stray-plan-file": {"campaign-plans/readme.txt": "not a plan"},
    # The first built-in starter, copied into the saved library once the catalog made it.
    "saved-participant": {},
}


def prepare(root: Path, base: str, files: dict, junk: dict) -> None:
    root.mkdir(parents=True)
    (root / "authority.sqlite3").write_bytes(gzip.decompress((FIXTURES / f"base-{base}.sqlite3.gz").read_bytes()))
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    for relative, content in junk.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")


def thaw(value):
    from collections.abc import Mapping, Sequence

    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [thaw(item) for item in value]
    return value


def main() -> int:
    bases = json.loads((FIXTURES / "store_runs.json").read_text(encoding="utf-8"))["bases"]
    work = Path(tempfile.mkdtemp(prefix="sylvia-creation-catalog-"))
    cases = []
    try:
        for base in ("open", "returned", "stopped"):
            for label, junk in JUNK.items():
                for participants in (True, False):
                    root = (work / f"{base}-{label}-{participants}").resolve()
                    prepare(root, base, bases[base]["files"], junk)
                    with AuthorityOwner.open(root / "authority.sqlite3", "catalog") as owner:
                        if label == "saved-participant":
                            _campaign_creation_catalog(root, owner)
                            starter = sorted((root / "bundled-srd-starters" / "participants").iterdir())[0]
                            shutil.copytree(starter, root / "participants" / starter.name)
                        try:
                            outcome = {"catalog": thaw(list(_campaign_creation_catalog(
                                root, owner, include_participants=participants)))}
                        except Exception as error:  # noqa: BLE001
                            outcome = {"error": [type(error).__name__, getattr(error, "code", None), str(error)]}
                    cases.append({"base": base, "junk": label, "include_participants": participants, **outcome})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    vectors = {"python": sys.version.split()[0], "junk": {k: {p: (c.hex() if isinstance(c, bytes) else c)
                                                                for p, c in v.items()} for k, v in JUNK.items()},
               "binary": [k for k, v in JUNK.items() if any(isinstance(c, bytes) for c in v.values())],
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    for case in cases:
        print(case["base"], case["junk"], case["include_participants"],
              case.get("error") or [(row.get("kind"), row.get("id")) for row in case["catalog"]][:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
