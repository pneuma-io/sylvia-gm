"""First-run character authoring can inspect the same Lite runtime admitted for play."""

import io

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.agent._builder_session import _builder_reference_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import reader_for_references
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.domain.authority import PackageRole


def test_actual_lite_runtime_is_available_to_character_builder_find_read_and_copy(tmp_path):
    with AuthorityOwner.open(tmp_path / "character.db", "character-builder") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="lite"
        )
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        assert primary.package_id == "sylvia.srd-lite.bg3"
        references = {
            "candidate_id": candidate.candidate_id,
            "packages": [
                {
                    "prefix": "ruleset",
                    "id": primary.package_id,
                    "version": primary.version,
                    "content_sha256": primary.sha256,
                }
            ],
        }
        reader = reader_for_references(owner, references)
        path = "ruleset/runtime/ruleset.yaml"
        assert path in reader.paths
        tool = _builder_reference_tool(reader)
        found = tool.handler(
            {"action": "find", "path": path, "query": "starting_level_queue", "limit": 1}, None
        )
        assert found["success"] is True, found
        # This was the actual failed lookup; an absent literal is a successful search.
        found = tool.handler(
            {"action": "find", "path": path, "query": "martial_arts", "limit": 1}, None
        )
        assert found["success"] is True, found
        assert found["matches"]
        match = found["matches"][0]
        page = tool.handler(
            {
                "action": "read",
                "path": path,
                "offset": match["excerpt_offset"],
                "limit": len(match["excerpt"]),
            },
            None,
        )
        assert page["success"] is True and page["content"] == match["excerpt"]
        assert len(page["content"]) <= 32000
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        copied = builder_reference_copy_tool(reader, workspace).handler(
            {
                "source_path": path,
                "destination_path": "references/runtime.yaml",
            },
            None,
        )
        assert copied["success"] is True, copied
        assert (workspace / "references/runtime.yaml").read_text() == reader.read(path)
