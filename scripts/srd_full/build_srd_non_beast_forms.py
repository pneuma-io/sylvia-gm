"""Bind non-Beast bodies to public SRD blocks and reject unowned source clauses."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right

if __package__:
    from .build_srd_beast_forms import ROOT, STAT_BLOCK_HEADER, source_form_profile
else:
    from build_srd_beast_forms import ROOT, STAT_BLOCK_HEADER, source_form_profile

OUTPUT = ROOT / "non-beast-form-index.json"
CREATURE_TYPES = (
    "Aberration",
    "Beast",
    "Celestial",
    "Construct",
    "Dragon",
    "Elemental",
    "Fey",
    "Fiend",
    "Giant",
    "Humanoid",
    "Monstrosity",
    "Ooze",
    "Plant",
    "Undead",
)


def build_index():
    """Reuse fixed-stat extraction; admit only bodies whose complete clauses have owners."""
    forms = {}
    sources = []
    for corpus_name in ("animals", "monsters"):
        path = ROOT / "corpus" / (corpus_name + ".json")
        corpus = json.loads(path.read_text(encoding="utf-8"))
        sources.append(
            {"corpus": corpus_name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
        pages = corpus["page_records"]
        starts = []
        chunks = []
        position = 0
        for page in pages:
            starts.append(position)
            chunks.append(page["text"] + "\n")
            position += len(chunks[-1])
        text = "".join(chunks)
        headings = list(STAT_BLOCK_HEADER.finditer(text))
        for i, heading in enumerate(headings):
            name, type_line, armor_class = heading.groups()
            if re.search(r"\bBeasts?\b", type_line):
                continue
            form_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if form_id in forms:
                raise SystemExit("Duplicate source non-Beast form: " + form_id)
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            block = text[heading.start() : end]
            if i + 1 < len(headings):
                # Monster prose introduces the next creature before its repeated stat-block title.
                block = block.split("\n" + headings[i + 1][1] + "\n", 1)[0]
            page = pages[bisect_right(starts, heading.start()) - 1]
            kind = next(
                (
                    kind.lower()
                    for kind in CREATURE_TYPES
                    if re.search(r"\b" + kind + r"\b", type_line)
                ),
                None,
            )
            if kind is None:
                raise SystemExit("Unknown printed creature type: " + type_line)
            try:
                row = source_form_profile(name, type_line, armor_class, block, page)
            except SystemExit as error:
                row = {
                    "name": name,
                    "source": {"page": page["page"], "page_text_sha256": page["text_sha256"]},
                    "type_line": type_line,
                    "source_stat_block": block.rstrip(),
                    "source_stat_block_sha256": hashlib.sha256(block.rstrip().encode()).hexdigest(),
                    "unsupported_clauses": [str(error)],
                }
            row["creature_type"] = kind
            gaps = row["unsupported_clauses"]
            if " or " in type_line.split(",", 1)[0]:
                gaps.append("Alternative body sizes need an actual selected-size owner")
            if re.search(r"(?m)^Gear ", block):
                gaps.append(
                    "Printed Gear needs source-qualified body equipment and weapon ownership"
                )
            languages = re.search(r"(?ms)^Languages (.*?)(?=\nCR )", block)
            if not languages or languages[1].strip() != "None":
                gaps.append(
                    "Non-Beast spoken and understood language clauses need a "
                    "body communication owner"
                )
            if "(hover)" in block:
                gaps.append("Printed hover needs its body movement projection")
            if set(row.get("condition_immunities", [])).difference(
                {"blinded", "charmed", "deafened", "frightened", "prone"}
            ):
                gaps.append(
                    "Additional body condition immunity needs source-owned suppression "
                    "and restoration of prior conditions"
                )
            skills = re.search(r"(?m)^Skills (.*)", block)
            if skills and re.sub(
                r"(?:Acrobatics|Athletics|Perception|Stealth) [+−-]\d+|[, ]", "", skills[1]
            ):
                gaps.append("Additional printed skills need complete extraction")
            row["execution_supported"] = not gaps
            forms[form_id] = row
    return {
        "format_id": "sylvia-gm-srd-non-beast-form-index",
        "format_version": 1,
        "sources": sources,
        "form_count": len(forms),
        "forms": dict(sorted(forms.items())),
    }


if __name__ == "__main__":
    index = build_index()
    OUTPUT.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Indexed {index['form_count']} source-qualified non-Beast profiles")
