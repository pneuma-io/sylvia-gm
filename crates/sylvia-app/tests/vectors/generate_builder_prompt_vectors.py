"""Generate reference vectors for sylvia_app::builder_prompts (builder/_prompts.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_builder_prompt_vectors.py

Each case calls `build_builder_system_prompt` with one override and mode and one set of
`available_*` arguments: each option kind present, missing, empty, mis-shaped (Python's
`AttributeError` and `TypeError`), with defaults, non-text values, the plan-title suffix and
act counts. Outcomes are the prompt's SHA-256 and length, or the exception class and text;
the default prompt (what the live caller builds) is also stored whole.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.builder._prompts import build_builder_system_prompt  # noqa: E402

OUTPUT = Path(__file__).with_name("builder_prompt_vectors.json")
NEWLINE = chr(10)
SUFFIX = " " + chr(0x2014) + " Campaign Plan"

RULESETS = [
    None, [], [{"id": "a", "name": "A"}], [{"id": "a"}], [{"id": "a", "name": "A", "description": "D"}],
    [{"id": 1, "name": None, "description": 0}], [{}], [{"id": "a", "description": ["x"]}],
    [{"id": "sylvia.srd-lite.bg3", "name": "D&D SRD 5.2.1", "description": "Lite " + chr(0xe9)},
     {"id": "b", "name": "B"}],
    ["x"], "ab", {"a": 1}, 0, [None],
]
GROUPED = {
    "settings": [
        None, {}, {"r": [{"slug": "s", "name": "S", "description": "D"}], "e": []}, {"r": [{"slug": "s"}]},
        {"r": [{"slug": 2, "name": True, "description": ""}]}, {"r": None, "q": [{"name": "N"}]}, [1], {"r": "ab"},
        {"r": [1]}, "x",
    ],
    "plans": [
        None, {"s": [{"slug": "p", "name": "Thing" + SUFFIX, "act_count": 3}, {"slug": "q", "act_count": 0}]},
        {"s": [{"slug": "p", "name": "A" + SUFFIX + SUFFIX}, {"slug": "r", "act_count": "3"}]},
        {"s": [{"slug": "p", "act_count": []}, {"slug": "q", "act_count": 1.5, "name": "Q"}]},
        {"s": [{"slug": "p", "name": [SUFFIX]}]}, {"s": [{"slug": "p", "name": ["x"]}]},
        {"s": [{"slug": "p", "name": 5}]}, {"s": [{"slug": "p", "name": {"k": 1}}]}, {"s": []}, [{"slug": "p"}],
    ],
    "characters": [
        None, {"s": [{"slug": "c", "name": "C"}, {"slug": "d"}]}, {"s": [{"name": "Only"}], "t": []}, {"s": ["x"]},
        ["x"],
    ],
    "companions": [
        None, {"s": [{"slug": "m", "name": "M"}]}, {"s": [{}], "u": [{"slug": "n", "name": None}]}, {"s": [5]},
    ],
}
MODES = [(None, "append"), ("", "append"), ("Extra.", "append"), ("Extra.", "replace"), (None, "replace"),
         ("", "replace"), ("Full.", "other"), ("x", "Append")]
FULL = {"rulesets": RULESETS[8], "settings": GROUPED["settings"][2], "plans": GROUPED["plans"][1],
        "characters": GROUPED["characters"][1], "companions": GROUPED["companions"][1]}


def outcome(override, mode, options):
    try:
        text = build_builder_system_prompt(
            "Ruleset", "/project", override=override, prompt_mode=mode,
            available_rulesets=options.get("rulesets"), available_settings=options.get("settings"),
            available_plans=options.get("plans"), available_characters=options.get("characters"),
            available_companions=options.get("companions"),
        )
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error)]
    return ["ok", hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)]


def main() -> None:
    cases = []

    def add(override, mode, options):
        cases.append({"override": override, "mode": mode, "options": options,
                      "outcome": outcome(override, mode, options)})

    for override, mode in MODES:
        add(override, mode, {})
        add(override, mode, FULL)
    for rulesets in RULESETS:
        add(None, "append", {"rulesets": rulesets})
    for kind, values in GROUPED.items():
        for value in values:
            add(None, "append", {**FULL, kind: value})
            add(None, "append", {kind: value})
    default = build_builder_system_prompt("Ruleset", "/project")
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "default_prompt": default,
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, indent=0) + NEWLINE, encoding="utf-8",
                      newline=NEWLINE)
    kinds = {}
    for case in cases:
        kinds[case["outcome"][0]] = kinds.get(case["outcome"][0], 0) + 1
    print(len(cases), "cases", kinds)


if __name__ == "__main__":
    main()
