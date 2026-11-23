"""Write ruleless_mechanics_vectors.json: Python's No Ruleset mechanics check on held replies.

Run from the checkout root with the snapshot's interpreter:
    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_ruleless_mechanics_vectors.py

Each case is a held response (blocks, or plain text when there are none) with Python's
`mechanics_in_response` findings and `ruleless_mechanics_rejection` for them. The texts reach
every pattern, near misses, out-of-character negation, and the characters where Rust's regular
expression tables differ from Python's (case folding, combining marks, other digits).
Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.ruleless_play import (  # noqa: E402
    mechanics_in_response,
    ruleless_mechanics_rejection,
)

TEXTS = [
    # Each pattern, then a near miss.
    "Roll a d20.", "She rolls 2d6 and grins.", "The d20-shaped die", "A word like add20 or d100x",
    "Roll for initiative!", "rolling to see if it works", "Roll your dexterity save now.",
    "Make a quick check of the windows.", "Make a Perception check.", "attempt a DC 15 save",
    "give me an opposed check", "make a save of your progress", "make a hard save on it",
    "a Stealth check", "saving throws are hard", "a wisdom saving throw", "a luck roll",
    "Beat DC12 to pass", "difficulty class", "target number 7", "armour class", "AC 15", "AC15",
    "You have 12 hit points.", "health point", "HP is low", "The hp bar", "takes 7 fire damage",
    "takes 7 points of damage", "a storm damages the roof", "initiative order", "rolled initiative",
    "combat rounds", "round 3 of combat", "attack rolls", "to-hit", "a bonus action", "spell slots",
    "experience points", "300 XP", "you level up", "you leveled up", "with advantage",
    "has disadvantage", "with advantage of numbers", "gains advantage over them", "roll with advantage",
    "an advantage to us", "She gambles with dice in the tavern.", "Nothing mechanical here.",
    # Case and Unicode: Python's folding of i, s and k, combining marks, other digits.
    "ROLL A D20", "Make a Perceptıon check", "Make a PERCEPTİON check", "Roll ſor initiative",
    "Make a sKill check", "café roll for", "éhit points", "x́AC 15",
    "Roll a ١d20", "Roll a １d20", "Roll a 1d20́", "hit points", "hit\x1cpoints",
    "Kill check", "make a ſave", "", "   ",
]

OOC = [
    "We don't roll for anything here.", "There is no saving throw in this campaign.",
    "Instead of a Perception check, I'll describe it.", "Without dice, roll for nothing.",
    "No rolls. Make a Stealth check anyway, you said? " + "x" * 40 + " make a stealth check",
    "never ask for hit points", "Roll for initiative, not literally.",
    # The negation window is 32 characters: these sit at about 28 and 20 before the match.
    "No dice here at all, friend: roll for it in prose.", "Not this time, my friend: roll a d20? No.",
]


def case(blocks, text):
    findings = mechanics_in_response(blocks, text)
    return {"blocks": blocks, "text": text, "findings": list(findings),
            "rejection": ruleless_mechanics_rejection(findings) if findings else None}


def main() -> None:
    cases = [case(None, text) for text in TEXTS]
    cases.append(case(None, None))
    for text in OOC:
        cases.append(case([{"kind": "ooc", "text": text}], None))
        cases.append(case([{"kind": "narration", "text": text}], None))
    cases.append(case([
        {"kind": "narration", "text": "You step inside."},
        {"kind": "npc_dialogue", "speaker_id": "e-1", "spoken_words": "Roll a d20, stranger."},
        {"kind": "npc_utterance", "spoken_words": "You have 3 hit points left."},
        {"kind": "ooc", "text": "No dice: no d20 here."},
        {"kind": "narration", "text": 7},
        {"kind": "narration", "spoken_words": "takes 4 damage", "text": "calm"},
    ], "ignored when blocks are given: roll a d20"))
    cases.append(case([], "Roll a d20."))
    out = Path(__file__).with_name("ruleless_mechanics_vectors.json")
    out.write_bytes((json.dumps({"cases": cases}, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {out.name}: {len(cases)} cases")


if __name__ == "__main__":
    main()
