"""Export the Builder application tools' model definitions as builder_tool_specs.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/builder/generate_builder_tool_specs.py

Each definition is read back from the Python factory that builds the tool
(`tool_spec_to_openai(factory(...))`), never retyped. Every factory is built twice with different
stub inputs, and the two definitions must be identical: these are the tools whose definition is a
constant. A tool whose definition depends on its inputs is listed under `dynamic` with both
definitions, so the Rust side ports that part instead of copying it.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _builder_session as session  # noqa: E402
from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool  # noqa: E402
from sylvia_gm.agent._builder_source_tool import builder_setting_source_tool, builder_source_tool  # noqa: E402
from sylvia_gm.agent._character_profile_tools import character_profile_tools  # noqa: E402
from sylvia_gm.agent._content_definition_tool import content_definition_tool  # noqa: E402
from sylvia_gm.agent._import_repair_tools import import_repair_tools  # noqa: E402
from sylvia_gm.agent._library_source_copy_tool import library_source_copy_tool  # noqa: E402
from sylvia_gm.agent._library_source_tool import library_source_tool  # noqa: E402
from sylvia_gm.agent._plan_draft_check_tool import plan_draft_check_tool  # noqa: E402
from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_draft_check_tool  # noqa: E402
from sylvia_gm.agent._ruleset_test_tool import ruleset_test_tool  # noqa: E402
from sylvia_gm.tools._converter import tool_spec_to_openai  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_tool_specs.json")
NL = chr(10)


def stub(*_args, **_kwargs):
    return {}


class Anything:
    """A stand-in reader, selection or authority: every attribute is a stub."""

    def __getattr__(self, _name):
        return stub


def factories(workspace: Path, variant: int):
    """Every factory, with inputs that differ between the two variants."""
    other = workspace / f"variant-{variant}"
    yield "load_builder_skill", lambda: session._builder_skill_loader_tool()
    yield "complete_creation_subflow", lambda: session._creation_subflow_completion_tool()
    yield "get_content_definition", lambda: content_definition_tool((), None if variant == 1 else {"type": "object"})
    yield "check_ruleset_draft", lambda: ruleset_draft_check_tool(other)
    yield "test_ruleset_draft", lambda: ruleset_test_tool(other, cancelled=lambda: variant == 2)
    yield "publish_ruleset_candidate", lambda: session._ruleset_candidate_tool(
        workspace=other, session_id=f"session-{variant}", publisher=stub, cancelled=lambda: False)
    yield "publish_campaign_setting", lambda: session._campaign_setting_tool(stub)
    yield "check_campaign_setting_draft", lambda: session._campaign_setting_tool(stub, checking=True)
    yield "check_campaign_plan_draft", lambda: plan_draft_check_tool(stub)
    yield "inspect_library_sources", lambda: library_source_tool(Anything())
    yield "copy_library_source", lambda: library_source_copy_tool(Anything(), other)
    yield "inspect_builder_references", lambda: session._builder_reference_tool(Anything())
    yield "copy_builder_reference", lambda: builder_reference_copy_tool(Anything(), other)
    yield "select_builder_ruleset", lambda: builder_source_tool(Anything())
    yield "select_builder_setting", lambda: builder_setting_source_tool(Anything())
    yield "*campaign_content", lambda: session._campaign_content_tools(
        session.CampaignContentToolAuthority(items=() if variant == 1 else ({"id": "x"},)))
    yield "*character_profile", lambda: character_profile_tools(
        other, save=stub, read=stub, import_source=None if variant == 1 else stub)
    yield "*import_repair", lambda: import_repair_tools(stub, stub)


def definitions(variant: int) -> dict:
    result = {}
    with tempfile.TemporaryDirectory() as scratch:
        for label, build in factories(Path(scratch), variant):
            built = build()
            for spec in built if isinstance(built, tuple) else (built,):
                if not label.startswith("*") and spec.name != label:
                    raise ValueError(f"{label} built {spec.name}")
                result[spec.name] = tool_spec_to_openai(spec)
    return result


def main() -> None:
    first, second = definitions(1), definitions(2)
    constant = {name: value for name, value in first.items() if second.get(name) == value}
    dynamic = {name: [value, second.get(name)] for name, value in first.items() if second.get(name) != value}
    missing = sorted(set(second) - set(first))
    output = {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "constant": constant,
        "dynamic": dynamic,
        "only_in_second": missing,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=True, indent=1) + NL, encoding="utf-8", newline=NL)
    print(OUTPUT.name, len(constant), "constant;", "dynamic:", sorted(dynamic), "only in second:", missing)


if __name__ == "__main__":
    main()
