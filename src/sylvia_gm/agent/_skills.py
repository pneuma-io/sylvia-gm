"""Explicit file-backed construction skills for ordinary Builder sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sylvia_gm.domain.session import BUILDER_SKILLS


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    text: str


_SKILL_DIRECTORIES = {
    "BUILD RULESET": "build_ruleset",
    "BUILD CAMPAIGN SETTING": "build_campaign_setting",
    "BUILD CAMPAIGN PLAN": "build_campaign_plan",
}


def builder_skills() -> tuple[str, ...]:
    """Advertise the exact, frozen Builder construction surface."""

    return BUILDER_SKILLS


def builder_skill_references(name: str) -> tuple[str, ...]:
    """List only packaged example/reference files belonging to a tracked skill."""

    if name not in BUILDER_SKILLS:
        raise ValueError("Unknown Builder construction skill")
    root = Path(__file__).with_name("skills") / _SKILL_DIRECTORIES[name]
    references = root / "references"
    if not references.is_dir() or references.is_symlink():
        return ()
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(references.rglob("*"))
        if path.is_file()
        and path.suffix in {".md", ".json"}
        and not path.is_symlink()
        and path.resolve().is_relative_to(references.resolve())
    )


def load_builder_skill(name: str, *, reference: str | None = None) -> LoadedSkill:
    """Load a tracked skill or an exact member of its packaged reference catalog."""

    if name not in BUILDER_SKILLS:
        raise ValueError("Unknown Builder construction skill")
    if reference is not None and reference not in builder_skill_references(name):
        raise ValueError("Unknown Builder skill reference")
    path = Path(__file__).with_name("skills") / _SKILL_DIRECTORIES[name] / (
        reference or "SKILL.md"
    )
    return LoadedSkill(name=name, text=path.read_text(encoding="utf-8"))
