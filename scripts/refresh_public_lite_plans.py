"""Adopt current Lite in the 16 public Plans through the ordinary Builder snapshot path.

Read the retained collection from --source-root and write replacements to --output-root.
The source collection and existing saves are never modified. --check regenerates in memory
and compares final bytes; --work-root holds only the selected synthetic library and drafts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import read_plan_catalogue
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.builder._plan_snapshot_materializer import materialize_plan_snapshot_draft
from sylvia_gm.domain.package_library import canonical_json_bytes, closure_sha256
from sylvia_gm.infrastructure.package_archive import admit_package_archive

LITE = "sylvia.srd-lite.bg3"


def _identity(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _setting(snapshot: bytes) -> tuple[dict, dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        record = next(
            row
            for row in json.loads(archive.read("manifest.json"))["packages"]
            if row["content_kind"] == "campaign_setting"
        )
        return record, {
            row["path"]: archive.read(f"packages/{record['id']}/{row['path']}")
            for row in record["files"]
        }


def _install_source(owner: AuthorityOwner, snapshot: bytes):
    """Reuse an exact admitted candidate so repeated checks do not replay a terminal install."""
    with admit_package_archive(io.BytesIO(snapshot)) as admitted:
        closure = closure_sha256(admitted.package_lock)
    retained = next((c for c in owner.package_candidates() if c.closure_sha256 == closure), None)
    return (
        retained
        if retained is not None
        else owner.install_package_archive(
            io.BytesIO(snapshot), install_id="source-" + hashlib.sha256(snapshot).hexdigest()
        )
    )


def refresh(
    source_root: Path,
    output_root: Path,
    work_root: Path,
    *,
    check: bool,
    retained_rift_only: bool = False,
) -> dict:
    """Preserve each owned world and preparation while explicitly adopting current rules."""
    source_root, output_root, work_root = (
        path.resolve() for path in (source_root, output_root, work_root)
    )
    if output_root == source_root or output_root.is_relative_to(source_root):
        raise ValueError("Descendants require a separate replacement directory")
    if work_root == source_root or work_root.is_relative_to(source_root):
        raise ValueError("Draft work requires a separate directory")
    public = read_plan_catalogue(source_root)
    selected = []
    for row in public:
        directory = source_root / row["id"]
        payload = json.loads((directory / "plan.json").read_bytes())
        if payload["bindings"]["ruleset"]["id"] == LITE:
            selected.append((row["id"], directory, payload))
    if len(selected) != 16 or len(public) != 17:
        raise ValueError("Expected the existing 16 Lite Plans and one other-ruleset Plan")
    if retained_rift_only:
        directory = source_root / "the-rift-awakening"
        selected = [(directory.name, directory, json.loads((directory / "plan.json").read_bytes()))]
    work_root.mkdir(parents=True, exist_ok=True)
    rows = []
    with AuthorityOwner.open(work_root / "sources.sqlite3", "public-lite-descendants") as owner:
        owner.set_admin_full_audit(True)
        current = _install_source(owner, bundled_srd_archive())
        for plan_id, directory, original in selected:
            old_snapshot = (directory / "setting.sylvia-gm-package").read_bytes()
            readable = (directory / "plan.md").read_bytes()
            original_rejection = None
            try:
                old_plan = validate_plan_snapshot(old_snapshot, original, readable.decode("utf-8"))
            except ValueError as error:
                if not retained_rift_only or str(error) != (
                    "Campaign Plan package bindings do not match the admitted closure"
                ):
                    raise
                # Create a new revision from the available owned world. This does not
                # restore or reinterpret the unavailable historical declared closure.
                old_plan = None
                original_rejection = str(error)
            retained = _install_source(owner, old_snapshot)
            authority = campaign_plan_sources(
                owner,
                retained.package_lock,
                setting_id=original["bindings"]["campaign_setting"]["id"],
            )
            if authority is None:
                raise ValueError("The retained Plan has no exact Setting authority")
            workspace = work_root / "drafts" / plan_id
            draft = workspace / "campaign-plan-draft"
            draft.mkdir(parents=True, exist_ok=True)
            payload = deepcopy(original)
            payload["revision"] += 1
            if retained_rift_only:
                payload["bindings"] = {
                    "ruleset": authority.ruleset.as_payload(),
                    "campaign_setting": authority.setting.as_payload(),
                    "package_closure_sha256": authority.closure_sha256,
                }
            (draft / "plan.json").write_bytes(canonical_json_bytes(payload))
            (draft / "plan.md").write_bytes(readable)
            (draft / "ruleset-source.json").write_bytes(
                canonical_json_bytes(
                    {
                        "candidate_id": current.candidate_id,
                        "expected_closure_sha256": current.closure_sha256,
                    }
                )
            )
            with materialize_plan_snapshot_draft(
                owner,
                workspace,
                candidate_id=retained.candidate_id,
                expected_authority=authority,
            ) as result:
                snapshot = result.archive.read()
                effective = json.loads(canonical_json_bytes(result.plan.payload))
                preserved = deepcopy(effective)
                preserved["bindings"] = original["bindings"]
                preserved["revision"] = original["revision"]
                if (
                    preserved != original
                    or result.plan.readable_markdown != readable.decode("utf-8").strip()
                ):
                    raise ValueError(f"Authored Plan content changed: {plan_id}")
                old_record, old_files = _setting(old_snapshot)
                new_record, new_files = _setting(snapshot)
                if old_files != new_files:
                    raise ValueError(
                        f"Owned Setting content requires explicit adaptation: {plan_id}"
                    )
                normalized = deepcopy(new_record)
                normalized["content_sha256"] = old_record["content_sha256"]
                normalized["dependencies"] = old_record["dependencies"]
                if normalized != old_record:
                    raise ValueError(f"Owned Setting declarations changed: {plan_id}")
                replacements = {
                    "plan.json": canonical_json_bytes(result.plan.payload),
                    "setting.sylvia-gm-package": snapshot,
                }
                for name, raw in replacements.items():
                    target = output_root / plan_id / name
                    if check:
                        if not target.is_file() or target.read_bytes() != raw:
                            raise ValueError(f"Descendant bytes differ: {plan_id}/{name}")
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw)
                rows.append(
                    {
                        "id": plan_id,
                        "before_plan_content_sha256": (
                            old_plan.content_sha256 if old_plan is not None else None
                        ),
                        "before_plan_file": _identity((directory / "plan.json").read_bytes()),
                        "original_pair_rejection": original_rejection,
                        "after_plan_content_sha256": result.plan.content_sha256,
                        "before_revision": original["revision"],
                        "after_revision": effective["revision"],
                        "before_bindings": original["bindings"],
                        "after_bindings": effective["bindings"],
                        "before_snapshot": _identity(old_snapshot),
                        "after_snapshot": _identity(snapshot),
                        "changed_plan_paths": [
                            "$.revision",
                            "$.bindings.ruleset.content_sha256",
                            "$.bindings.campaign_setting.content_sha256",
                            "$.bindings.package_closure_sha256",
                        ],
                        "setting_payload_files_identical": len(old_files),
                        "plan_markdown_identical": _identity(readable),
                        "authored_semantics_identical": True,
                    }
                )
            print(f"{'Checked' if check else 'Composed'} {plan_id}", flush=True)
    return {
        "format": (
            "retained-rift-lite-descendant-v1"
            if retained_rift_only else "public-lite-plan-descendants-v1"
        ),
        "current_ruleset_closure_sha256": current.closure_sha256,
        "plans": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--retained-rift-only", action="store_true",
        help="Adopt current Lite in a new retained Rift revision, preserving its owned world.",
    )
    args = parser.parse_args()
    result = refresh(
        args.source_root, args.output_root, args.work_root,
        check=args.check, retained_rift_only=args.retained_rift_only,
    )
    args.receipt.write_bytes(canonical_json_bytes(result))


if __name__ == "__main__":
    main()
