"""Final declarations retain every public tool within the existing per-inclusion bound."""

import json
from copy import deepcopy
from pathlib import Path

from scripts.srd_full.build_srd_5_2_1_mechanics import _split_spell_runtime_tool_links

from sylvia_gm.domain.package_library import (
    CONTENT_DECLARATION_LINK_FAMILY_LIMIT,
    canonical_json_bytes,
    parse_content_declarations_bytes,
)


def test_actual289_final_links_fit_without_losing_any_public_tool():
    package = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-runtime-content-links/dnd-srd-5.2.1")
    )
    declarations = json.loads((package / "content/content-declarations.json").read_bytes())
    tools = json.loads((package / "package-record.json").read_bytes())["tools"]
    public = {row["name"] for row in tools}
    original = deepcopy(declarations)
    gameplay = next(
        row for row in declarations["inclusions"] if row["id"] == "srd-5.2.1-gameplay-runtime"
    )
    assert len(gameplay["links"]["tools"]) > CONTENT_DECLARATION_LINK_FAMILY_LIMIT
    _split_spell_runtime_tool_links(declarations, tools)
    assert all(
        len(row["links"]["tools"]) <= CONTENT_DECLARATION_LINK_FAMILY_LIMIT
        for row in declarations["inclusions"]
    )
    linked = {
        name for row in declarations["inclusions"] for name in row.get("links", {}).get("tools", [])
    }
    assert public == linked
    parse_content_declarations_bytes(canonical_json_bytes(declarations))
    repeated = deepcopy(declarations)
    _split_spell_runtime_tool_links(declarations, tools)
    assert declarations == repeated
    original_gameplay = next(
        row for row in original["inclusions"] if row["id"] == "srd-5.2.1-gameplay-runtime"
    )
    original_gameplay["links"]["tools"] = gameplay["links"]["tools"]
    original["inclusions"].extend(
        row for row in declarations["inclusions"] if row["id"] != original_gameplay["id"]
    )
    assert declarations == original


def test_spell_links_split_at_the_existing_bound_without_losing_names():
    package = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-runtime-content-links/content-declarations.json")
    )
    declarations = json.loads(package.read_bytes())
    tools = [
        {"name": f"spell_{i:03d}", "capability": "dnd.srd.5.2.1.spells"}
        for i in range(CONTENT_DECLARATION_LINK_FAMILY_LIMIT + 1)
    ]
    _split_spell_runtime_tool_links(declarations, tools)
    groups = [row for row in declarations["inclusions"] if row["family"] == "spells"]
    assert [len(row["links"]["tools"]) for row in groups] == [256, 1]
    assert {name for row in groups for name in row["links"]["tools"]} == {
        row["name"] for row in tools
    }
