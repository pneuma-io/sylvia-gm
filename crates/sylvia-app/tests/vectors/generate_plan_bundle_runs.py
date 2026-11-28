"""Plan bundles and import-repair offers through Python's handler.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_bundle_runs.py

It builds one portable Plan as `tests/test_plan_bundles.py` does (the Signal and Static test
packages, a materialized Relay Island setting, the Builder's example Plan saved with its retained
package snapshot, then `export_plan_bundle`), and keeps its bytes as `plan_bundle/relay.sylvia-gm-plan`; each selected file below
built from it is kept in `plan_bundle/selected/`.

Then `handle_install_package_file` runs as the owner runs it, on an empty owner and storage root,
for a sequence of selected files:

- the Plan, again from another path, without an install identity, and with an upper-case suffix;
- damaged Plans: not a ZIP, an extra member, a missing snapshot, a wrong format version,
  unreadable `plan.json`, a bzip2 member, a snapshot that is not a ZIP;
- a repeated repair offer, a receipt changed on disk, an offer without an install identity;
- a package archive, the same install again, a damaged package, a package that is not a ZIP;
- a missing file and a folder.

Each run records the reply (an offer's `repair_id` checked against
`sha256(canonical([install_id, source_sha256, str(source)]))` and masked, since it hashes a
machine path) or the failure's class, code and message, then the storage root's files (each
receipt with its `source_path` made relative to the selected-files folder, each other file's
SHA-256) and the installed candidates. After the sequence, `export_plan_bundle` runs on the
imported Plan (the exact selected bytes again, a changed version, an unknown and a malformed
identity), and `inspect_repair_source` on each selected file. It writes `plan_bundle/runs.json`;
the owner's `commands::packages` test replays every step. Reference tooling only.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import types
import zipfile
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.agent._builder_session import _campaign_plan_example  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources  # noqa: E402
from sylvia_gm.application.import_repair import inspect_repair_source  # noqa: E402
from sylvia_gm.application.plan_bundle import export_plan_bundle  # noqa: E402
from sylvia_gm.application.plan_snapshot import retained_plan_snapshot  # noqa: E402
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan  # noqa: E402
from sylvia_gm.builder._setting_materializer import materialize_setting_draft  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.interfaces.commands import packages as handlers  # noqa: E402
from sylvia_gm.interfaces.owner_process import _json_value  # noqa: E402
from tests.test_builder_library import install_character_packages  # noqa: E402

OUT = HERE / "plan_bundle"
RIICHI = REPOSITORY / "rulesets" / "riichi-mahjong" / "riichi-mahjong-0.1.0.sylvia-gm-package"


def portable_plan(root: Path) -> bytes:
    """`tests/test_plan_bundles.py`'s `portable_plan`: one exported reusable Plan."""
    root.mkdir()
    world_files = root / "workspace" / "setting-draft" / "files"
    world_files.mkdir(parents=True)
    (world_files / "world.md").write_text("# Relay Island\nA silent radio tower.", encoding="utf-8", newline="\n")
    with AuthorityOwner.open(root / "source.db", "plan-export-source") as owner:
        original, _ = install_character_packages(owner)
        with materialize_setting_draft(
            owner, root / "workspace", candidate_id=original.candidate_id,
            package_id="local.relay-island", version="1.0.0", title="Relay Island",
        ) as world:
            source = owner.install_package_archive(world.archive, install_id="island")
        authority = campaign_plan_sources(owner, source.package_lock)
        payload = json.loads(_campaign_plan_example(authority))
        payload.update(id="relay-adventure", title="The Silent Relay", premise="Find the lost signal.")
        plan = save_reusable_campaign_plan(
            root, operation_id="relay-adventure", source_payload=payload,
            source_markdown="# The Silent Relay\n\nFind the lost signal.",
            validate_plan=authority.validate,
            package_snapshot=retained_plan_snapshot(owner, payload),
        )
        output = io.BytesIO()
        export_plan_bundle(root, plan.artifact_id, expected_content_sha256=plan.content_sha256, target=output)
        return output.getvalue()


def members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        return {info.filename: bundle.read(info) for info in bundle.infolist()}


def rezip(files: Mapping[str, bytes], *, bzip2: frozenset[str] = frozenset()) -> bytes:
    """A bundle as `export_plan_bundle` writes one, with `bzip2` members compressed so."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, value in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_BZIP2 if name in bzip2 else zipfile.ZIP_STORED
            bundle.writestr(info, value)
    return output.getvalue()


def selected_files(plan: bytes) -> dict[str, bytes | None]:
    """Each selected file's name and bytes; `None` is a folder. All but the folder and the
    repository's own Riichi archive are kept in `plan_bundle/selected/` for the replay."""
    good = members(plan)
    record = json.loads(good["record.json"])
    wrong_version = canonical_json_bytes({**record, "format_version": 2})
    package = RIICHI.read_bytes()
    package_files = members(package)
    declarations = "packages/pneuma.riichi-mahjong/content/content-declarations.json"
    damaged_package = io.BytesIO()
    with zipfile.ZipFile(damaged_package, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in package_files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, b"{}" if name == declarations else value)
    snapshot = next(name for name in good if name not in {"record.json", "plan.json", "plan.md"})
    return {
        "relay.sylvia-gm-plan": plan,
        "relay-copy.sylvia-gm-plan": plan,
        "RELAY-UPPER.SYLVIA-GM-PLAN": plan,
        "junk.sylvia-gm-plan": b"not a zip archive",
        "extra-member.sylvia-gm-plan": rezip({**good, "notes.txt": b"hello"}),
        "no-snapshot.sylvia-gm-plan": rezip({name: value for name, value in good.items() if name != snapshot}),
        "old-format.sylvia-gm-plan": rezip({**good, "record.json": wrong_version}),
        "broken-plan.sylvia-gm-plan": rezip({**good, "plan.json": b"{not json"}),
        "bzip2.sylvia-gm-plan": rezip(good, bzip2=frozenset({"plan.md"})),
        "junk-snapshot.sylvia-gm-plan": rezip({**good, snapshot: b"not a zip archive"}),
        "riichi.sylvia-gm-package": package,
        "damaged-riichi.sylvia-gm-package": damaged_package.getvalue(),
        "junk.sylvia-gm-package": b"not a zip archive",
        "folder.sylvia-gm-package": None,
    }


def runs() -> list[dict[str, object]]:
    def install(name: str, install_id: str | None, **extra: object) -> dict[str, object]:
        params: dict[str, object] = {"source": name}
        if install_id is not None:
            params["install_id"] = install_id
        return {"op": "install_package_file", "params": params, **extra}
    return [
        install("relay.sylvia-gm-plan", "plan-a"),
        install("relay-copy.sylvia-gm-plan", "plan-b"),
        install("relay.sylvia-gm-plan", None),
        install("RELAY-UPPER.SYLVIA-GM-PLAN", "plan-c"),
        install("junk.sylvia-gm-plan", "plan-junk"),
        install("extra-member.sylvia-gm-plan", "plan-extra"),
        install("extra-member.sylvia-gm-plan", "plan-extra"),
        install("extra-member.sylvia-gm-plan", "plan-extra", tamper=True),
        install("extra-member.sylvia-gm-plan", None),
        install("no-snapshot.sylvia-gm-plan", "plan-no-snapshot"),
        install("old-format.sylvia-gm-plan", "plan-old"),
        install("broken-plan.sylvia-gm-plan", "plan-broken"),
        install("bzip2.sylvia-gm-plan", "plan-bzip2"),
        install("junk-snapshot.sylvia-gm-plan", "plan-junk-snapshot"),
        install("riichi.sylvia-gm-package", "riichi"),
        install("riichi.sylvia-gm-package", "riichi"),
        install("riichi.sylvia-gm-package", None),
        install("damaged-riichi.sylvia-gm-package", "riichi-damaged"),
        install("junk.sylvia-gm-package", "riichi-junk"),
        install("missing.sylvia-gm-package", "riichi-missing"),
        install("folder.sylvia-gm-package", "riichi-folder"),
    ]


def plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def failure(error: Exception) -> dict[str, object]:
    return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def tree(storage: Path, files: Path) -> list[list[object]]:
    """Every file under the storage root but the database: each other file as its SHA-256 by
    path, then each receipt (named by a machine-specific hash) as its JSON with the selected path
    relative to the selected-files folder, by that path."""
    entries, receipts = [], []
    for path in sorted(storage.rglob("*")):
        relative = path.relative_to(storage).as_posix()
        if not path.is_file() or relative.startswith("authority.sqlite3"):
            continue
        if relative.startswith("import-repair-sources/"):
            data = path.read_bytes()
            receipt = json.loads(data)
            canonical = canonical_json_bytes(receipt) == data
            receipt["source_path"] = Path(receipt["source_path"]).relative_to(files).as_posix()
            receipts.append(["import-repair-sources/<repair>.json", {"receipt": receipt, "canonical": canonical}])
        else:
            entries.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries + sorted(receipts, key=lambda entry: entry[1]["receipt"]["source_path"])


def candidates(owner: AuthorityOwner) -> list[list[object]]:
    return sorted([item.candidate_id, item.closure_sha256, item.status.value] for item in owner.package_candidates())


def main() -> None:
    OUT.mkdir(exist_ok=True)
    recorded: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        plan = portable_plan(Path(scratch) / "source")
        (OUT / "relay.sylvia-gm-plan").write_bytes(plan)
        files = (Path(scratch) / "files").resolve()
        files.mkdir()
        kept = OUT / "selected"
        shutil.rmtree(kept, ignore_errors=True)
        kept.mkdir()
        for name, value in selected_files(plan).items():
            if value is None:
                (files / name).mkdir()
                continue
            (files / name).write_bytes(value)
            if value != RIICHI.read_bytes():
                (kept / name).write_bytes(value)
        storage = Path(scratch) / "storage"
        storage.mkdir()
        with AuthorityOwner.open(storage / "authority.sqlite3", "plan-bundle-runs") as owner:
            bus = types.SimpleNamespace(
                install_package=lambda stream, install_id: owner.install_package_archive(stream, install_id=install_id))
            for run in runs():
                params = dict(run["params"])  # type: ignore[arg-type]
                source = files / str(params.pop("source"))
                params["source_path"] = str(source)
                if run.get("tamper"):
                    for receipt in (storage / "import-repair-sources").iterdir():
                        receipt.write_bytes(receipt.read_bytes() + b" ")
                ctx = types.SimpleNamespace(storage_root=storage, application_owner=owner, bus=bus,
                                            method="install_package_file")
                try:
                    result = plain(_json_value(handlers.handle_install_package_file(ctx, params)))  # type: ignore[arg-type]
                    if isinstance(result, dict) and result.get("status") == "repairable":
                        digest = hashlib.sha256(source.read_bytes()).hexdigest()
                        expected = hashlib.sha256(
                            canonical_json_bytes([params["install_id"], digest, str(source)])).hexdigest()
                        assert result["repair_id"] == expected, (result, expected)
                        result["repair_id"] = "<repair>"
                    outcome: dict[str, object] = {"ok": result}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = failure(error)
                recorded.append({**run, "outcome": outcome, "tree": tree(storage, files),
                                 "candidates": candidates(owner)})
        imported = next(run["outcome"]["ok"] for run in recorded if "ok" in run["outcome"])  # type: ignore[index]
        exports = []
        for artifact_id, expected in (
            (imported["id"], imported["content_sha256"]),
            (imported["id"], "0" * 64),
            ("campaign-plan-reusable-" + "0" * 24 + "-" + "0" * 16, imported["content_sha256"]),
            ("not-a-plan", imported["content_sha256"]),
        ):
            output = io.BytesIO()
            try:
                receipt = plain(export_plan_bundle(storage, artifact_id, expected_content_sha256=expected,
                                                   target=output))
                outcome = {"ok": receipt, "archive_sha256": hashlib.sha256(output.getvalue()).hexdigest()}
            except Exception as error:  # noqa: BLE001
                outcome = failure(error)
            exports.append({"artifact_id": artifact_id, "expected_content_sha256": expected, "outcome": outcome})
        inspections = []
        for path in sorted(files.iterdir()):
            try:
                inspection = inspect_repair_source(path)
                outcome = {"ok": {
                    "kind": inspection.kind, "source_sha256": inspection.source_sha256,
                    "file_name": inspection.file_name, "identity": inspection.identity,
                    "diagnostics": [plain(item.as_payload()) for item in inspection.diagnostics],
                }}
            except Exception as error:  # noqa: BLE001
                outcome = failure(error)
            inspections.append({"source": path.name, "outcome": outcome})
        # The folder is the storage root's sibling; a source path's text is machine-specific.
        shutil.rmtree(files, ignore_errors=True)
    record = {
        "python": ".".join(map(str, sys.version_info[:2])),
        "plan_sha256": hashlib.sha256(plan).hexdigest(),
        "runs": recorded,
        "exports": exports,
        "inspections": inspections,
    }
    (OUT / "runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
