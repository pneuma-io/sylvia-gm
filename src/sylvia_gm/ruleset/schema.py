"""Pydantic models for ruleset configuration files.

Two-layer schema:
  - CONFIG LAYER (T1.2): meta, features, abilities, skills, modes,
    character_creation, progression, equipment, magic, conditions.
    Validated by T1.2 Pydantic models.
  - EXECUTABLE LAYER (T1.4): tools, procedures, definitions, narrative.
    Accepted as raw dict passthrough. T1.4 validates these at runtime.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Meta ──────────────────────────────────────────────────────────────────────


class Meta(BaseModel):
    """Ruleset metadata — id is the canonical identifier (C3 fix)."""

    id: str  # REQUIRED — canonical ruleset identifier, lowercase kebab-case
    name: str
    version: str
    author: str
    license: str
    license_url: str | None = None
    description: str | None = None
    source_url: str | None = None
    attribution: str | None = None

    @field_validator("id")
    @classmethod
    def id_must_be_kebab_case(cls, v: str) -> str:
        """Canonical ID must be lowercase kebab-case."""
        if not re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", v):
            raise ValueError(
                f"meta.id must be lowercase kebab-case, got '{v}'. "
                f"Examples: 'cairn-2e', 'dnd-5e-2014', 'pf-2e'."
            )
        return v


# ── Features ──────────────────────────────────────────────────────────────────


class Features(BaseModel):
    """Legacy D&D/Cairn compatibility toggles.

    Rulesets do not need this object. Unknown explicit compatibility metadata is
    preserved rather than silently discarded, but package-owned capabilities
    remain the neutral extension contract.
    """

    model_config = ConfigDict(extra="allow")

    death_saves_enabled: bool = False
    spell_slots_enabled: bool = False
    concentration_enabled: bool = False
    proficiency_bonus_enabled: bool = False
    hit_dice_enabled: bool = False
    initiative_enabled: bool = True  # Cairn uses side-based initiative
    critical_hits_enabled: bool = False
    critical_misses_enabled: bool = False
    advantage_disadvantage_enabled: bool = False
    multi_attack_enabled: bool = False
    reactions_enabled: bool = False
    bonus_actions_enabled: bool = False
    skills_enabled: bool = False
    backgrounds_enabled: bool = True
    species_enabled: bool = False
    classes_enabled: bool = False
    feats_enabled: bool = False
    multiclassing_enabled: bool = False


# ── Abilities ─────────────────────────────────────────────────────────────────


class AbilityConfig(BaseModel):
    """Configuration for a single ability score.

    Free-form keys — no fixed ability superset. Each ruleset declares its own
    ability keys using native naming. The parser is a pure conduit.
    """

    active: bool = True
    name: str
    description: str | None = None
    default_min: int | None = Field(default=None, ge=1)
    default_max: int | None = Field(default=None, le=100)
    generation: str | None = None
    modifier_formula: str | None = None
    used_for: list[str] = Field(default_factory=list)
    save_label: str | None = None

    @field_validator("default_max")
    @classmethod
    def min_less_than_max(cls, v: int | None, info: Any) -> int | None:
        minimum = info.data.get("default_min")
        if v is not None and minimum is not None and minimum >= v:
            raise ValueError(f"default_min ({minimum}) must be < default_max ({v})")
        return v


# ── Skills ────────────────────────────────────────────────────────────────────


class SkillDef(BaseModel):
    """A single skill definition."""

    name: str
    ability: str | None = None  # optional package-owned relationship
    description: str | None = None
    trained_only: bool = False


# ── Turn System Models ────────────────────────────────────────────────────────


class ResourcePool(BaseModel):
    """A typed resource that depletes during a turn (action, movement, etc.)."""

    count: int | float | None = None  # fixed count, or None when `source` is used
    refresh: str  # "turn_start", "round_start", "phase_start"
    unit: str | None = None  # "feet", "slots", etc.
    condition: str | None = None  # e.g. "granted_by_feature" for bonus actions
    source: str | None = None  # e.g. "entity.speed" — resolved at runtime


class Actor(BaseModel):
    """Who can act during a turn and when."""

    entity: str | None = None  # entity ID, or None for "the engine"
    timing: str  # "on_turn_start", "during", "on_turn_end"
    resources: dict[str, ResourcePool] = Field(default_factory=dict)


class TurnSystemConfig(BaseModel):
    """Turn structure for a mode."""

    type: str  # "round", "phase", etc.
    label: str | None = None  # "morning", "round 3", "down 2"
    actors: list[Actor] = Field(default_factory=list)
    order: str | None = None  # "initiative" (for sequential resolution)
    resolution: str | None = None  # "simultaneous" (for parallel resolution)
    exit_condition: str | None = None


class ModeConfig(BaseModel):
    """A game mode declaration (combat, out-of-combat, day, drive, etc.)."""

    model_config = ConfigDict(extra="allow")  # Preserve mode-specific data

    turn_system: TurnSystemConfig


# ── Conditions ────────────────────────────────────────────────────────────────


class ConditionLevel(BaseModel):
    level: int = Field(ge=1)
    effect: str


class Condition(BaseModel):
    name: str
    description: str
    mechanical_effect: str
    stackable: bool = False
    max_stacks: int | None = None
    levels: list[ConditionLevel] = Field(default_factory=list)
    cleared_by: list[str] = Field(default_factory=list)


# ── Top-Level Ruleset ─────────────────────────────────────────────────────────


class ValidationIssue(BaseModel):
    """A single validation issue found during cross-reference checking."""

    severity: str  # "error", "warning", "info"
    location: str  # dotted path to the field, e.g. "skills[0]"
    message: str


class RulesetFile(BaseModel):
    """Complete ruleset configuration file.

    Two-layer schema:
      - CONFIG LAYER (T1.2): meta, features, abilities, skills, modes,
        character_creation, progression, equipment, magic, conditions.
        Validated by T1.2 Pydantic models.
      - EXECUTABLE LAYER (T1.4): tools, procedures, definitions, narrative.
        Accepted as raw dict passthrough. T1.4 validates these at runtime.
    """

    # ── T1.2 Configuration Layer ──
    meta: Meta | None = None
    features: Features | None = None
    abilities: dict[str, AbilityConfig] = Field(default_factory=dict)
    skills: list[SkillDef] = Field(default_factory=list)
    modes: dict[str, ModeConfig] = Field(default_factory=dict)
    magic: dict[str, Any] = Field(default_factory=dict)
    equipment: dict[str, Any] = Field(default_factory=dict)
    character_creation: dict[str, Any] = Field(default_factory=dict)
    progression: dict[str, Any] = Field(default_factory=dict)
    # combat: DELETED — replaced by modes
    conditions: list[Condition] = Field(default_factory=list)

    # ── T1.4 Executable Layer (passthrough) ──
    tools: dict[str, Any] | None = None
    procedures: dict[str, Any] | None = None
    definitions: dict[str, Any] | None = None
    definition_row_schemas: dict[str, Any] = Field(default_factory=dict)
    narrative: dict[str, Any] | None = None

    # ── Convenience Methods ──

    def active_abilities(self) -> list[str]:
        """Return ability keys marked active: true."""
        return [abbr for abbr, cfg in self.abilities.items() if cfg.active]

    def get_ability(self, abbr: str) -> AbilityConfig:
        """Get ability config by key. Raises KeyError if not found."""
        if abbr not in self.abilities:
            raise KeyError(f"Ability '{abbr}' not found. Available: {list(self.abilities.keys())}")
        return self.abilities[abbr]

    def modifier_for(self, ability_key: str, score: int) -> int:
        """Evaluate a declared modifier through the runtime's bounded expression language."""
        from sylvia_gm.runtime._condition_evaluator import evaluate_expression
        from sylvia_gm.runtime._types import ExecutionContext

        cfg = self.get_ability(ability_key)
        if cfg.modifier_formula is None:
            raise ValueError(f"Ability '{ability_key}' does not declare a modifier formula.")
        return int(evaluate_expression(cfg.modifier_formula, ExecutionContext(params={"score": score})))

    def get_skill(self, name: str) -> SkillDef | None:
        """Find a skill by name (case-insensitive)."""
        lower = name.lower()
        for s in self.skills:
            if s.name.lower() == lower:
                return s
        return None

    def has_skills(self) -> bool:
        if self.features is None:
            return len(self.skills) > 0
        return self.features.skills_enabled and len(self.skills) > 0

    def has_conditions(self) -> bool:
        return len(self.conditions) > 0

    def get_condition(self, name: str) -> Condition | None:
        """Find a condition by name (case-insensitive)."""
        lower = name.lower()
        for c in self.conditions:
            if c.name.lower() == lower:
                return c
        return None

    def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
        """Run cross-reference validation. Auto-called by RulesetParser.load()."""
        from sylvia_gm.ruleset.validator import validate_ruleset_consistency

        return validate_ruleset_consistency(self)
