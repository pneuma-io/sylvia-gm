"""The GM's campaign knowledge reads through Python's owner: `search_campaign_knowledge` and
`read_campaign_reference`, on two bases built at test time (`installed_base.py`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_knowledge_read_runs.py

- `knowledge_read/rift_runs.json`: the bundled Rift closure (its Ruleset and the Severed Lands
  Setting) with `tests/test_campaign_knowledge_search.py`'s campaign and its two current Stern
  entities. The steps: that test's natural and unrelated queries over two source sets, each
  match read back exactly; every argument refusal; paging; and reads of every source and path
  form (engine format, Campaign State, the Plan's structured, readable, page, section and
  adventure paths, Setting documents with their default overview, sections and excerpts, and
  the bounds).
- `knowledge_read/srd_runs.json`: the bundled SRD Lite package with
  `tests/test_gm_reference_bounds.py`'s campaign, plus a Fighter chosen at setup with an
  authored Character File. The steps: the participant read with its current bookkeeping state,
  inventory, the large `classes.json` read in excerpts and searched.

Each record holds the install identity, the installed tables' digests, the rows setup wrote and
each step's arguments (exactly as a caller passes them) with its answer or failure. Reads write
nothing, so there is no final database. `uuid.uuid4` is a counter here, so the record (its
campaign identities above all) is reproducible. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import io
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(HERE))

import uuid  # noqa: E402

_counter = iter(range(1, 1 << 30))
uuid.uuid4 = lambda: uuid.UUID(int=next(_counter), version=4)  # noqa: E731

import installed_base  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_rift_archive, bundled_srd_archive  # noqa: E402
from sylvia_gm.domain.authority import PackageRole, StateValue  # noqa: E402
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_srd_second_wind import PACKAGE_ID as SRD_ID  # noqa: E402
from tests.test_srd_second_wind import _installed_fighter  # noqa: E402

OUT = HERE / "knowledge_read"
OWNER = "knowledge-owner"
ADVANCEMENT = {"advancement": {
    "method": "fixed", "starting_level": 1, "maximum_level": 1, "recipients": "whole_party",
    "timing": "Reference QA only.", "allocation": "No awards."}}


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def start(owner: AuthorityOwner, candidate, package_id: str, *, setting=None, participants=(),  # noqa: ANN001
          canonical_input=None, plan_text: str = "# Reference QA") -> tuple[str, str]:
    confirmation = owner.confirm_new_campaign(
        operation_id="confirm", display_name="Reference QA", package_lock=candidate.package_lock,
        turn_id="construction", package_candidate_id=candidate.candidate_id, builder_session_id="builder",
        builder_provider_id="fixture", builder_model_id="fixture", participants=participants,
        initial_state=(StateValue(package_id, "state", ADVANCEMENT),))
    campaign_id = confirmation.campaign_id
    owner.begin_campaign_construction(
        operation_id="begin", campaign_id=campaign_id, lane="quick_start", expected_revision=0,
        canonical_input=canonical_input or {"premise": "Reference QA"})
    lock = owner.current_package_lock(campaign_id)
    primary = next(entry for entry in lock.package_lock.entries if entry.package_id == package_id)
    binding = lambda entry: None if entry is None else {  # noqa: E731
        "id": entry.package_id, "version": entry.version, "content_sha256": entry.sha256}
    _, _, handoff = owner.finalize_campaign_construction(
        campaign_id=campaign_id, dm_provider_id="fixture", dm_model_id="fixture", plan_artifact_text=plan_text,
        model_guidance_override=True,
        structured_plan={
            "format_id": "sylvia-gm-campaign-plan", "format_version": CAMPAIGN_PLAN_FORMAT_VERSION,
            "bindings": {"ruleset": binding(primary), "campaign_setting": binding(setting),
                         "package_closure_sha256": lock.closure_sha256},
            "initial_state": {"mechanics": [{"namespace": package_id, "key": "state", "value": ADVANCEMENT}]},
        })
    if owner.current_open_turn(campaign_id) is None:
        owner.open_turn(campaign_id, "play")
    return campaign_id, handoff.dm_session_id


class Runs:
    def __init__(self, owner: AuthorityOwner, campaign_id: str) -> None:
        self.owner, self.campaign_id, self.runs = owner, campaign_id, []

    def call(self, name: str, call: str, arguments: dict[str, object]) -> object:
        value = None
        try:
            if call == "search":
                value = self.owner.search_campaign_knowledge(self.campaign_id, **arguments)
            else:
                value = self.owner.read_campaign_reference(self.campaign_id, **arguments)
            outcome: dict[str, object] = {"ok": plain(value)}
        except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
            outcome = {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
        self.runs.append({"name": name, "call": call, "arguments": arguments, "outcome": outcome})
        return value

    def search(self, name: str, read_back: bool = False, **arguments: object) -> object:
        result = self.call(name, "search", arguments)
        if read_back and isinstance(result, Mapping):
            for item in result["matches"]:
                self.call(f"{name}: read {item['path']}", "read", {"source": item["source"], "path": item["path"]})
        return result

    def read(self, name: str, **arguments: object) -> object:
        return self.call(name, "read", arguments)


NATURAL = [
    "Stern", "Stern characterization", "Tell me about Stern and her personality", "Waystation geography",
    "Publicly known documented witnessed Anchor Door opening at Waystation or elsewhere Returners records "
    "testimony Linden what evidence exists as opposed to hope",
    "Anchor Doors history public records opening",
    "Stern Returner patrol Verdant Margin characterization role voice response to someone calling from Anchor "
    "Clearing",
    "Verdant Margin Anchor Clearing nearest settlement safe route brush Waystation local geography",
]
SOURCE_SETS = [["campaign_setting"], ["campaign_setting", "campaign_plan", "campaign_state", "participant"]]


def rift_steps(runs: Runs) -> None:
    for sources in SOURCE_SETS:
        for query in NATURAL:
            runs.search(f"natural {query[:30]!r} in {len(sources)} sources", read_back=True, query=query,
                        sources=sources, limit=5, offset=0)
    for query in ["person who definitely does not exist", "ZXQnotaperson", "sternum", "quantum toaster warranty"]:
        runs.search(f"unrelated {query!r}", query=query, sources=None, limit=5, offset=0)
    runs.search("current names before the baseline", read_back=True, query="Where is Stern now?", sources=None,
                limit=5, offset=0)
    runs.search("the ruleset only", read_back=True, query="rift anchor rules", sources=["ruleset"], limit=8,
                offset=0)
    runs.search("the second page", query="Stern", sources=None, limit=2, offset=2)
    runs.search("the last offset", query="Stern", sources=["campaign_state"], limit=1, offset=2)
    good = {"query": "Stern", "sources": None, "limit": 5, "offset": 0}
    for name, changes in [
        ("limit zero", {"limit": 0}), ("limit nine", {"limit": 9}), ("limit true", {"limit": True}),
        ("limit a float", {"limit": 5.0}), ("limit text", {"limit": "5"}),
        ("a blank query", {"query": " "}), ("a padded query", {"query": " Stern"}),
        ("a long query", {"query": "s" * 501}), ("a query that is a number", {"query": 7}),
        ("no sources", {"sources": []}), ("false sources", {"sources": False}),
        ("repeated sources", {"sources": ["campaign_state", "campaign_state"]}),
        ("an unknown source", {"sources": ["bogus"]}), ("a source that is a number", {"sources": [5]}),
        ("sources as text", {"sources": "ruleset"}), ("sources as an object", {"sources": {"ruleset": 1}}),
        ("sources as a number", {"sources": 5}), ("an unhashable source", {"sources": [["ruleset"]]}),
        ("a negative offset", {"offset": -1}), ("an offset past the results", {"offset": 999}),
        ("an offset that is true", {"offset": True}), ("an offset that is text", {"offset": "1"}),
    ]:
        runs.search(name, **{**good, **changes})
    runs.read("the engine format", source="engine_format", path="spatial_scene")
    runs.read("another engine format", source="engine_format", path="combat")
    runs.read("the engine format within ten bytes", source="engine_format", path="spatial_scene", maximum_bytes=10)
    runs.read("Campaign State now", source="campaign_state", path="current")
    runs.read("the fallback inventory", source="campaign_state", path="inventory")
    runs.read("one entity", source="campaign_state", path="stern-north")
    runs.read("a missing record", source="campaign_state", path="nobody")
    runs.read("an entity past a small bound", source="campaign_state", path="stern-north", maximum_bytes=20)
    runs.read("a missing participant", source="participant", path="hero")
    for path in ["structured", "structured/bindings", "structured/bindings/ruleset", "structured/missing",
                 "structured/initial_state/mechanics/x", "readable", "readable/page/0", "readable/page/1",
                 "readable/page/x", "readable/page/00000000000", "readable#reference-qa", "readable#nope",
                 "adventures", "adventures/1/structured", "adventures/1/markdown", "adventures/1/structured/bindings",
                 "adventures/2/structured", "adventures/01/markdown", "adventures/1/other", "elsewhere"]:
        runs.read(f"the Plan at {path}", source="campaign_plan", path=path)
    runs.read("the whole Plan as its outline", source="campaign_plan", path="structured", maximum_bytes=600)
    runs.read("the whole Plan within 200 bytes", source="campaign_plan", path="structured", maximum_bytes=200)
    runs.read("the whole Plan within 20 bytes", source="campaign_plan", path="structured", maximum_bytes=20)
    for path in ["locations/waystation.md", "locations/waystation.md#overview", "locations/verdant-margin.md",
                 "npcs/patrol-veteran-stern.md", "npcs/patrol-veteran-stern.md#nope", "lore/world-overview.md",
                 "lore/world-overview.md#text/0", "lore/world-overview.md#text/100",
                 "lore/world-overview.md#text/01", "lore/world-overview.md#text/99999999",
                 "lore/world-overview.md#text/x", "missing.md", "../escape.md"]:
        runs.read(f"the Setting at {path}", source="campaign_setting", path=path)
    runs.read("a Setting document within 300 bytes", source="campaign_setting", path="lore/world-overview.md",
              maximum_bytes=300)
    runs.read("a Setting section within 50 bytes", source="campaign_setting",
              path="locations/waystation.md#overview", maximum_bytes=50)
    runs.read("an excerpt within 3 bytes", source="campaign_setting", path="lore/world-overview.md#text/0",
              maximum_bytes=3)
    for name, arguments in [
        ("an unknown source", {"source": "bogus", "path": "x"}),
        ("a source that is a number", {"source": 5, "path": "x"}),
        ("an unhashable source", {"source": ["ruleset"], "path": "x"}),
        ("a Plan path that is a number", {"source": "campaign_plan", "path": 5}),
        ("a package path that is a number", {"source": "campaign_setting", "path": 5}),
        ("a zero bound", {"source": "campaign_state", "path": "current", "maximum_bytes": 0}),
        ("a bound past 64 KiB", {"source": "campaign_state", "path": "current", "maximum_bytes": 65537}),
        ("a bound that is true", {"source": "campaign_state", "path": "current", "maximum_bytes": True}),
    ]:
        runs.read(name, **arguments)


def srd_steps(runs: Runs) -> None:
    runs.read("the Character File with current state", source="participant", path="hero")
    runs.read("a Character File past a small bound", source="participant", path="hero", maximum_bytes=100)
    runs.read("the Ruleset inventory", source="campaign_state", path="inventory")
    runs.read("Campaign State now", source="campaign_state", path="current")
    first = runs.read("the large classes file", source="ruleset", path="content/corpus/classes.json")
    if isinstance(first, Mapping) and first.get("next_path"):
        runs.read("its next excerpt", source="ruleset", path=first["next_path"])
    runs.read("classes from an exact offset", source="ruleset", path="content/corpus/classes.json#text/30000",
              maximum_bytes=1000)
    runs.search("the classes source", read_back=True, query="fighter class features second wind",
                sources=["ruleset"], limit=4, offset=0)
    runs.search("the Character File", read_back=True, query="Synthetic Hero", sources=["participant"], limit=3,
                offset=0)
    runs.search("everything about a fighter", query="fighter", sources=None, limit=8, offset=0)
    runs.read("the Setting with no binding", source="campaign_setting", path="locations/waystation.md")


def build(scratch: Path, name: str, archive: bytes, install_id: str, setup, steps) -> None:  # noqa: ANN001
    database = scratch / f"{name}.sqlite3"
    installed_base.seed_copy(database)
    with AuthorityOwner.open(database, OWNER) as owner:
        owner.install_package_archive(io.BytesIO(archive), install_id=install_id)
    installed_base.checkpoint(database)
    digests = installed_base.table_digests(database)
    installed = scratch / f"{name}-installed.sqlite3"
    shutil.copyfile(database, installed)
    with AuthorityOwner.open(database, OWNER) as owner:
        campaign_id = setup(owner)
    installed_base.checkpoint(database)
    rows = installed_base.changed_rows(installed, database)
    with AuthorityOwner.open(database, OWNER) as owner:
        runs = Runs(owner, campaign_id)
        steps(runs)
    installed_base.checkpoint(database)
    assert installed_base.changed_rows(installed, database) == rows, "a read wrote"
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "install_id": install_id,
              "campaign_id": campaign_id, "installed_table_digests": digests, "setup_tables": rows,
              "runs": runs.runs}
    (OUT / f"{name}_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


def rift_setup(owner: AuthorityOwner) -> str:
    candidate = owner.package_candidates()[0]
    primary = next(entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY)
    setting = next(entry for entry in candidate.package_lock.entries if entry.package_id == "pneuma.severed-lands")
    campaign_id, session_id = start(owner, candidate, primary.package_id, setting=setting)
    owner.append_agent_message(session_id=session_id, message_id="search-basis", kind=SessionMessageKind.USER,
                               content={"text": "Record the two unrelated scouts named Stern for this test."})
    owner.update_campaign_file(
        session_id=session_id, update_id="scouts", turn_id=owner.current_open_turn(campaign_id),
        basis_message_ids=("search-basis",),
        updates=tuple({"operation": "record_entity", "entity_id": identity, "entity_kind": "person",
                       "name": "Stern", "attributes": {"current_location": location},
                       "visibility": "player_known", "known_by": ["player"]}
                      for identity, location in (("stern-north", "North camp"), ("stern-south", "South camp"))))
    return campaign_id


def srd_setup(owner: AuthorityOwner) -> str:
    candidate = owner.package_candidates()[0]
    fighter = _installed_fighter(candidate.package_lock, fighter=True)
    selected = [{"participant_id": "hero", "summary": "A steady soldier from the border.",
                 "party_role": "player_character",
                 "character_file": {"source_id": "starter-fighter", "profile_status": "authored",
                                    "authored_profile": "Hero held the border fort for three winters.",
                                    "setting_data": {"home": "the border fort"}}}]
    campaign_id, _ = start(owner, candidate, SRD_ID, participants=(fighter,),
                           canonical_input={"premise": "Reference QA", "selected_party": selected})
    return campaign_id


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        build(Path(scratch), "rift", bundled_rift_archive(), "search", rift_setup, rift_steps)
        build(Path(scratch), "srd", bundled_srd_archive(), "lite", srd_setup, srd_steps)


if __name__ == "__main__":
    main()
