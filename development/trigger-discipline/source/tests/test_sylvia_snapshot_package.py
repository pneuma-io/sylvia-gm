"""Verify current TD package ownership without rebuilding unrelated specialty fixtures."""

import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balance_suite.parameters import default_manifest
from sylvia_ruleset import builder
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.package_library import campaign_setting_entries


def test_ruleset_installs_alone_and_setting_retains_its_exact_rules(tmp_path):
    parameters = default_manifest().seed_set()
    runtime = builder._runtime(parameters.values, {})
    runtime["tools"].pop("exercise_specialty_counterfactual")
    specialties = builder._conformance_character()["artifact"]["specialties"]
    descriptor = builder._manifest("1" * 64, specialties, ["not-executed"])
    descriptor["tools"] = [
        tool for tool in descriptor["tools"] if tool["name"] in runtime["tools"]
    ]
    files = {"runtime/ruleset.yaml": builder._canonical_json(runtime)}
    guide_path = runtime["definitions"]["authoring_defaults"]["campaign_authoring_reference"]
    files[guide_path] = (Path(builder.__file__).parent / guide_path).read_bytes()
    files["content/content-declarations.json"] = builder._canonical_json(
        builder._content_declarations("1" * 64, "2" * 64, sorted(files), descriptor, [])
    )
    ruleset_record = builder._complete_package_record(descriptor, files)
    setting, setting_files = builder._conformance_setting_package(ruleset_record["content_sha256"])
    setting_record = builder._complete_package_record(setting, setting_files)
    standalone = builder._archive_bytes([(ruleset_record, files)])
    combined = builder._archive_bytes([(ruleset_record, files), (setting_record, setting_files)])
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        rules = owner.install_package_archive(io.BytesIO(standalone), install_id="rules-only")
        assert campaign_setting_entries(rules.package_lock) == ()
        world = owner.install_package_archive(io.BytesIO(combined), install_id="world")
        assert campaign_setting_entries(world.package_lock)[0].package_id == setting_record["id"]
        assert next(
            entry for entry in world.package_lock.entries if entry.package_id == builder.PACKAGE_ID
        ).sha256 == ruleset_record["content_sha256"]
        retained = io.BytesIO()
        owner.export_package_candidate(world.candidate_id, retained)
    with AuthorityOwner.open(tmp_path / "fresh.db", "fresh") as owner:
        restored = owner.install_package_archive(io.BytesIO(retained.getvalue()), install_id="world")
        assert restored.package_lock == world.package_lock
        assert len(owner.package_candidates()) == 1
        assert owner.package_text_file(
            builder.PACKAGE_ID, builder.PACKAGE_VERSION,
            ruleset_record["content_sha256"], guide_path,
        ) == files[guide_path].decode("utf-8")
