"""First-run character authoring can inspect the same Lite runtime admitted for play."""

import io
import json
from copy import deepcopy
from pathlib import Path

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.agent._builder_session import _builder_reference_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import reader_for_references
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.tools._builder_handlers import tool_read_file, tool_search_files


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
        assert "content" not in copied
        assert (workspace / "references/runtime.yaml").read_text() == reader.read(path)


def test_admitted_reference_file_and_index_sizes_share_package_limits_and_page_model_results(
    tmp_path,
):
    source = Path(__file__).parent / "fixtures/rulesets/signal-and-static"
    record = json.loads((source / "package-record.json").read_text())
    payloads = {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in source.rglob("*")
        if p.is_file() and p.name != "package-record.json"
    }
    # Both are supported by package admission; neither fits the older Builder-only caps.
    text = ("Reference data.\n" * 1200000) + "END-OF-ADMITTED-REFERENCE"
    assert len(text.encode()) > 16 * 1024 * 1024
    payloads["lore/large-reference.md"] = text.encode()
    payloads.update({f"lore/choice-{i:04}.md": b"Choice" for i in range(513)})
    declaration_path = record["content_declarations"]
    declarations = json.loads(payloads[declaration_path])
    paths = sorted(path for path in payloads if path.startswith("lore/"))
    for offset in range(0, len(paths), 256):
        inclusion = deepcopy(declarations["inclusions"][0])
        inclusion["id"] = f"admitted-reference-{offset}"
        inclusion["links"] = {key: [] for key in inclusion["links"]}
        inclusion["links"]["artifacts"] = paths[offset : offset + 256]
        declarations["inclusions"].append(inclusion)
    payloads[declaration_path] = canonical_json_bytes(declarations)
    with (
        materialize_package_archive((NormalizedPackageDraft(record, payloads),)) as archive,
        AuthorityOwner.open(tmp_path / "admitted.db", "admitted-reference") as owner,
    ):
        candidate = owner.install_package_archive(archive.archive, install_id="admitted")
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        reader = reader_for_references(
            owner,
            {
                "packages": [
                    {
                        "prefix": "ruleset",
                        "id": primary.package_id,
                        "version": primary.version,
                        "content_sha256": primary.sha256,
                    }
                ]
            },
        )
        assert len(reader.paths) > 512
        tool = _builder_reference_tool(reader)
        listed = tool.handler({"action": "list", "limit": 100000}, None)
        assert listed["success"] and len(listed["paths"]) == 100
        assert listed["next_offset"] == 100
        path = "ruleset/lore/large-reference.md"
        page = tool.handler({"action": "read", "path": path, "limit": 1000000}, None)
        assert page["success"] and len(page["content"]) == 32000
        assert page["next_offset"] == 32000
        found = tool.handler(
            {"action": "find", "path": path, "query": "END-OF-ADMITTED-REFERENCE"}, None
        )
        assert found["success"] and len(found["matches"]) == 1
        assert found["matches"][0]["offset"] > 16 * 1024 * 1024
        workspace = tmp_path / "copy"
        workspace.mkdir()
        copied = builder_reference_copy_tool(reader, workspace).handler(
            {
                "source_path": path,
                "destination_path": "references/large.md",
            },
            None,
        )
        assert copied["success"] and "content" not in copied
        assert (workspace / "references/large.md").read_bytes() == payloads[
            "lore/large-reference.md"
        ]
        copied_path = workspace / "references/large.md"
        searched = tool_search_files(
            {
                "_resolved_path": copied_path,
                "_project_root": workspace,
                "target": "content",
                "pattern": "END-OF-ADMITTED-REFERENCE",
            },
            None,
        )
        assert searched["success"] and len(searched["matches"]) == 1
        assert len(searched["matches"][0]["content"]) <= 200
        assert searched["skipped_oversized_files"] == 0
        read = tool_read_file({"_resolved_path": copied_path, "limit": 1}, None)
        assert read["success"] and read["lines"] == 1
        assert len(read["content"]) < 100
