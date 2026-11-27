"""Export the MCP tool listing's model-read texts and schemas as listing_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/stateless_mcp/generate_listing_texts.py

Read back from `agent/_stateless_mcp.py`, never retyped:

- `knowledge_search` and `reference_read`: the two reference tools `list_tools` lists, their
  descriptions and input schemas read from the source with `ast`. In the schemas the request's
  reference sources are written as `"__SOURCES__"` (the list), `"__SOURCE_COUNT__"` (its length)
  and `"__SOURCES_AND_ENGINE__"` (the list with `"engine_format"` appended), which the Rust side
  fills.
- `campaign_file`: the Campaign File update tool, from the functions themselves:
  `_campaign_file_update_description()`, the two assessment properties and the collection
  wrapper of `_campaign_file_update_schema` (read with every operation offered; its `items` is
  `"__ITEMS__"`), the operations
  and their collections (`_CAMPAIGN_FILE_COLLECTIONS`), and `_narrative_input_schema` for each
  note type with no campaign-specific choices (`items`), plus the `act_id` property a scene gets
  when the Plan has acts, and `inventory_update_schema()`.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _stateless_mcp as mcp  # noqa: E402

SOURCE = ROOT / "src" / "sylvia_gm" / "agent" / "_stateless_mcp.py"
OUTPUT = Path(__file__).with_name("listing_texts.json")


class Placeholders(ast.NodeTransformer):
    """Replace the request's reference sources with marker strings."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == "reference_sources":
            return ast.Constant("__SOURCES__")
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (isinstance(node.func, ast.Name) and node.func.id == "len"
                and len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "reference_sources"):
            return ast.Constant("__SOURCE_COUNT__")
        return self.generic_visit(node)

    def visit_List(self, node: ast.List) -> ast.AST:
        if (len(node.elts) == 2 and isinstance(node.elts[0], ast.Starred)
                and isinstance(node.elts[0].value, ast.Name)
                and node.elts[0].value.id == "reference_sources"
                and isinstance(node.elts[1], ast.Constant) and node.elts[1].value == "engine_format"):
            return ast.Constant("__SOURCES_AND_ENGINE__")
        return self.generic_visit(node)


def reference_tools() -> dict[str, dict[str, object]]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    server = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "build_stateless_mcp_server")
    listing = next(node for node in ast.walk(server)
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_tools")
    found: dict[str, dict[str, object]] = {}
    for node in ast.walk(listing):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Tool"):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        name = keywords["name"]
        if not isinstance(name, ast.Name) or name.id not in {"_KNOWLEDGE_SEARCH_TOOL_NAME",
                                                             "_REFERENCE_TOOL_NAME"}:
            continue
        key = "knowledge_search" if name.id == "_KNOWLEDGE_SEARCH_TOOL_NAME" else "reference_read"
        schema = ast.literal_eval(Placeholders().visit(keywords["input_schema"]))
        found[key] = {"name": getattr(mcp, name.id),
                      "description": ast.literal_eval(keywords["description"]),
                      "input_schema": schema}
    assert set(found) == {"knowledge_search", "reference_read"}, found.keys()
    return found


def campaign_file() -> dict[str, object]:
    operations = [operation for operation, _ in mcp._CAMPAIGN_FILE_COLLECTIONS]
    narrative_operations = [operation for operation in operations
                            if operation not in {"update_character", "update_inventory"}]
    full = mcp._campaign_file_update_schema({
        "operations": operations, "character_update_schema": {"type": "object"},
    })
    properties = full["properties"]
    first_collection = mcp._CAMPAIGN_FILE_COLLECTIONS[0][1]
    wrapper = {key: "__ITEMS__" if key == "items" else value
               for key, value in properties[first_collection].items()}
    scene = mcp._narrative_input_schema("update_scene", {"scene_act_ids": ["__ACT__"]})
    act = dict(scene["properties"]["act_id"])
    assert act["enum"] == ["__ACT__"]
    act["enum"] = "__ACTS__"
    return {
        "name": mcp._CAMPAIGN_FILE_UPDATE_TOOL_NAME,
        "description": mcp._campaign_file_update_description(),
        "state_change": mcp._CAMPAIGN_FILE_STATE_CHANGE,
        "state_change_summary": mcp._CAMPAIGN_FILE_STATE_CHANGE_SUMMARY,
        "assessment_properties": {
            mcp._CAMPAIGN_FILE_STATE_CHANGE: properties[mcp._CAMPAIGN_FILE_STATE_CHANGE],
            mcp._CAMPAIGN_FILE_STATE_CHANGE_SUMMARY:
                properties[mcp._CAMPAIGN_FILE_STATE_CHANGE_SUMMARY],
        },
        "collections": [list(pair) for pair in mcp._CAMPAIGN_FILE_COLLECTIONS],
        "collection": wrapper,
        "required": full["required"],
        "items": {operation: mcp._narrative_input_schema(operation, {})
                  for operation in narrative_operations},
        "scene_act": act,
        "inventory": mcp.inventory_update_schema(),
    }


def main() -> None:
    data = {**reference_tools(), "campaign_file": campaign_file()}
    OUTPUT.write_bytes((json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {OUTPUT.name}")


if __name__ == "__main__":
    main()
