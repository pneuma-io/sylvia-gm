"""Generate reference vectors for sylvia_app::campaign_library_selection::participant_catalog_payload.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_participant_catalog_vectors.py

Each case is one runtime served by a fake owner (`package_file_paths` and `package_text_file`, the
two reads `_character_state_reference` makes):
- SRD Lite's runtime as shipped, and edited so that an empty `model_decision_scope` falls back
  to the boundary, a scope supplies the reference, or `actor_entities` is renamed away;
- Riichi's packaged runtime;
- a runtime that is not YAML, and a package with no runtime file.
For each, `participant_catalog_payload` on the first participant, then `_participant_choice` with
that runtime's reference for participants with and without state, an authored profile, and the
built-in starter flag.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.campaign_library_selection import (  # noqa: E402
    _character_state_reference,
    _participant_choice,
    participant_catalog_payload,
)
from sylvia_gm.application.reusable_participants import ReusableParticipant  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402

OUTPUT = Path(__file__).with_name("participant_catalog_vectors.json")
SRD = "rulesets/srd-lite/runtime/ruleset.yaml"
RIICHI = ["rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package",
          "packages/pneuma.riichi-mahjong/runtime/ruleset.yaml"]
NL = chr(10)
SCOPE = "model_decision_scope:" + NL + "".join(
    f"  {field}:" + NL + "    namespace: sylvia.srd-lite.bg3" + NL + f"    key: scope.{field}" + NL
    for field in ("decision_owner", "active_side", "eligible_actors", "focused_actor", "actor_entities"))
EDITS = {
    "srd": [],
    "srd_empty_scope": [["replace", "model_decision_boundary:" + NL, "model_decision_scope: {}" + NL + "model_decision_boundary:" + NL]],
    "srd_no_actors": [["replace", "  actor_entities:" + NL, "  actor_records:" + NL]],
    "srd_scope": [["replace", "model_decision_boundary:" + NL, SCOPE + "model_decision_boundary:" + NL]],
}
PARTICIPANTS = [
    {"entity_state": {"max_hp": 9}, "authored_profile": "Profile", "builtin_starter": False},
    {"entity_state": {}, "authored_profile": "Profile", "builtin_starter": False},
    {"entity_state": {"max_hp": 9}, "authored_profile": None, "builtin_starter": True},
    {"entity_state": {}, "authored_profile": None, "builtin_starter": False},
]


def apply(text, edits):
    for _, old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


class Owner:
    def __init__(self, runtime):
        self.runtime = runtime

    def package_file_paths(self, package_id, version, sha256, *, maximum_files=512):
        return ("package.json",) if self.runtime is None else ("package.json", "runtime/ruleset.yaml")

    def package_text_file(self, package_id, version, sha256, path, *, maximum_bytes=65536):
        if self.runtime is None or path != "runtime/ruleset.yaml":
            raise CommandRejected("Package source path is unavailable")
        return self.runtime


def participant(spec, index):
    return ReusableParticipant(
        artifact_id=f"participant-{index:024x}-{index:016x}", role="player_character", name="Ada", summary="A hero.",
        authored_profile=spec["authored_profile"], content_sha256="c" * 64, package_id="sylvia.srd-lite.bg3",
        package_version="0.3.29", package_sha256="a" * 64, schema_id="schema", schema_sha256="b" * 64,
        campaign_setting_id="pneuma.severed-lands", campaign_setting_version="1.0.0", campaign_setting_sha256="d" * 64,
        participant={"name": "Ada"}, entity_state=spec["entity_state"], builtin_starter=spec["builtin_starter"])


def outcome(call):
    try:
        return ["ok", thaw_json_value(call())]
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics]]


def main() -> None:
    srd = (ROOT / SRD).read_text(encoding="utf-8")
    with zipfile.ZipFile(ROOT / RIICHI[0]) as archive:
        riichi = archive.read(RIICHI[1]).decode("utf-8")
    runtimes = {name: apply(srd, edits) for name, edits in EDITS.items()}
    runtimes["riichi"] = riichi
    runtimes["broken"] = "sylvia_gm_runtime: [" + NL
    runtimes["absent"] = None
    items = [participant(spec, index) for index, spec in enumerate(PARTICIPANTS)]
    cases = []
    for name, runtime in runtimes.items():
        owner = Owner(runtime)
        package_id = "pneuma.riichi-mahjong" if name == "riichi" else "sylvia.srd-lite.bg3"
        reference = outcome(lambda: _character_state_reference(owner, package_id, "1.0.0", "a" * 64))
        item = items[0]
        if package_id != item.package_id:
            item = participant(PARTICIPANTS[0], 0)
            object.__setattr__(item, "package_id", package_id)
        case = {"runtime": name, "package_id": package_id, "reference": reference,
                "payload": outcome(lambda: participant_catalog_payload(owner, item))}
        if reference[0] == "ok":
            case["choices"] = [thaw_json_value(_participant_choice(other, reference[1])) for other in items]
        cases.append(case)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD, "riichi": RIICHI,
               "edits": EDITS, "participants": PARTICIPANTS, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8", newline=NL)
    for case in cases:
        print(case["runtime"], case["reference"][0], ascii(str(case["reference"][1])[:80]), case["payload"][0])


if __name__ == "__main__":
    main()
