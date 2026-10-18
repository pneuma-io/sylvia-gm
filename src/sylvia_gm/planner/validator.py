"""Validator for the legacy planner.generator YAML output (originally T2.3).

4-layer validation for campaign plans before execution:
  1. Syntax        — YAML parse check
  2. Cross-ref     — NPCs, monsters, locations, items resolve
  3. Mechanical    — DC bounds, dice formulas, stat block validity
  4. Completeness  — Required fields, no empty sections
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import yaml

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sylvia_gm.ruleset.schema import RulesetFile


# ── Campaign Plan data model ────────────────────────────────────────────────


@dataclass
class CampaignPlan:
    """Local container for the legacy plan shape consumed by this validator.

    The validator also accepts planner.generator.CampaignPlan through the
    same attributes. Current structured plans are defined in domain.campaign_plan.
    """

    campaign: dict[str, Any] = field(default_factory=dict)
    """Top-level campaign structure: {name, acts: [{act, sessions: [...]}]}."""

    npcs: list[dict[str, Any]] = field(default_factory=list)
    """NPC roster: [{"id", "name", "motivation", ...}]."""

    encounters: list[dict[str, Any]] = field(default_factory=list)
    """Encounter definitions: [{"id", "monsters", "dc_checks", ...}]."""

    locations: list[dict[str, Any]] = field(default_factory=list)
    """World locations: [{"id", "name", ...}]."""

    items: list[dict[str, Any]] = field(default_factory=list)
    """Item catalogue: [{"id", "name", ...}]."""

    def to_yaml(self) -> str:
        """Serialize the campaign plan to YAML."""
        data: dict[str, Any] = {
            "campaign": self.campaign,
            "npcs": self.npcs,
            "encounters": self.encounters,
            "locations": self.locations,
            "items": self.items,
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ── Validation issue ───────────────────────────────────────────────────────


@dataclass
class ValidationIssue:
    """A single validation finding from any layer.

    Attributes:
        layer:     Validation layer (1-4).
        severity:  One of "critical", "high", "medium", "low".
        location:  Path into the YAML / dict structure (e.g.
                   "acts.0.sessions.1.scenes.arrival").
        message:   Human-readable description of the issue.
    """

    layer: int
    severity: str
    location: str
    message: str


# ── Validator ──────────────────────────────────────────────────────────────


class CampaignValidator:
    """Validates a CampaignPlan across four layers.

    Usage::

        validator = CampaignValidator(ruleset)
        issues = validator.validate(plan)
        if validator.is_valid(plan):
            print("Plan is ready for execution")
    """

    def __init__(self, ruleset: RulesetFile | None = None) -> None:
        """Initialise with an optional ruleset for mechanical checks.

        Args:
            ruleset: A RulesetFile providing bestiary entries, valid
                     DC ranges, and dice expression semantics.  When
                     None, some Layer 3 checks are relaxed.
        """
        self._ruleset = ruleset

    # ── Public API ──────────────────────────────────────────────────────

    def validate(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Run all four validation layers.

        Returns:
            Flat list of ValidationIssue objects (may be empty).
        """
        issues: list[ValidationIssue] = []
        issues.extend(self._validate_syntax(plan))
        issues.extend(self._validate_cross_references(plan))
        issues.extend(self._validate_mechanical_bounds(plan))
        issues.extend(self._validate_completeness(plan))

        # T5.5: Log validation result
        critical = sum(1 for i in issues if i.severity == "critical")
        high = sum(1 for i in issues if i.severity == "high")
        logger.info(
            "validation_result issues=%d critical=%d high=%d",
            len(issues), critical, high,
        )

        return issues

    def is_valid(self, plan: CampaignPlan) -> bool:
        """Return True if the plan has no critical or high-severity issues."""
        issues = self.validate(plan)
        return not any(i.severity in ("critical", "high") for i in issues)

    # ── Layer 1: Syntax ─────────────────────────────────────────────────

    def _validate_syntax(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Layer 1 — YAML parse check."""
        try:
            yaml.safe_load(plan.to_yaml())
        except yaml.YAMLError as exc:
            return [ValidationIssue(1, "critical", "", str(exc))]
        return []

    # ── Layer 2: Cross-reference integrity ───────────────────────────────

    def _validate_cross_references(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Layer 2 — Ensure all entity references resolve."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_npc_references(plan))
        issues.extend(self._check_location_references(plan))
        issues.extend(self._check_item_references(plan))
        issues.extend(self._check_bestiary_references(plan))

        return issues

    # ── 2a: NPC cross-refs ───────────────────────────────────────────────

    def _check_npc_references(self, plan: CampaignPlan) -> list[ValidationIssue]:
        npc_ids = {npc["id"] for npc in plan.npcs}
        issues: list[ValidationIssue] = []

        for act_idx, act in enumerate(plan.campaign.get("acts", [])):
            for ses_idx, session in enumerate(act.get("sessions", [])):
                for scn_idx, scene in enumerate(session.get("scenes", [])):
                    for npc_ref in scene.get("npcs_present", []):
                        if npc_ref not in npc_ids:
                            issues.append(
                                ValidationIssue(
                                    2,
                                    "high",
                                    f"acts.{act_idx}.sessions.{ses_idx}.scenes.{scn_idx}.npcs_present",
                                    f"NPC '{npc_ref}' referenced but not found in NPC roster",
                                )
                            )
        return issues

    # ── 2b: Location cross-refs ──────────────────────────────────────────

    def _check_location_references(self, plan: CampaignPlan) -> list[ValidationIssue]:
        loc_ids = {loc["id"] for loc in plan.locations}
        issues: list[ValidationIssue] = []

        for act_idx, act in enumerate(plan.campaign.get("acts", [])):
            for ses_idx, session in enumerate(act.get("sessions", [])):
                for scn_idx, scene in enumerate(session.get("scenes", [])):
                    loc_ref = scene.get("location")
                    if loc_ref and loc_ref not in loc_ids and plan.locations:
                        issues.append(
                            ValidationIssue(
                                2,
                                "high",
                                f"acts.{act_idx}.sessions.{ses_idx}.scenes.{scn_idx}.location",
                                f"Location '{loc_ref}' referenced but not found in location list",
                            )
                        )
        return issues

    # ── 2c: Item cross-refs ──────────────────────────────────────────────

    def _check_item_references(self, plan: CampaignPlan) -> list[ValidationIssue]:
        item_ids = {item["id"] for item in plan.items}
        issues: list[ValidationIssue] = []

        for table_id, table in plan.campaign.get("treasure_tables", {}).items():
            for roll_idx, roll in enumerate(table.get("rolls", [])):
                item_ref = roll.get("item")
                if item_ref and item_ref not in item_ids and plan.items:
                    issues.append(
                        ValidationIssue(
                            2,
                            "high",
                            f"treasure_tables.{table_id}.rolls.{roll_idx}",
                            f"Item '{item_ref}' referenced but not found in item catalogue",
                        )
                    )
        return issues

    # ── 2d: Bestiary / stat-block cross-refs ─────────────────────────────

    def _check_bestiary_references(self, plan: CampaignPlan) -> list[ValidationIssue]:
        known = self._known_bestiary()
        issues: list[ValidationIssue] = []

        for enc_idx, enc in enumerate(plan.encounters):
            for mon_idx, monster in enumerate(enc.get("monsters", [])):
                stat_block = monster.get("stat_block")
                if stat_block and known and stat_block not in known:
                    issues.append(
                        ValidationIssue(
                            2,
                            "high",
                            f"encounters.{enc_idx}.monsters.{mon_idx}.stat_block",
                            f"Stat block '{stat_block}' not found in bestiary",
                        )
                    )
        return issues

    # ── Layer 3: Mechanical bounds ───────────────────────────────────────

    def _validate_mechanical_bounds(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Layer 3 — DC ranges, dice formulas, stat block existence."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_dc_ranges(plan))
        issues.extend(self._check_dice_formulas(plan))
        issues.extend(self._check_stat_block_consistency(plan))

        return issues

    def _check_dc_ranges(self, plan: CampaignPlan) -> list[ValidationIssue]:
        min_dc, max_dc = self._dc_range()
        issues: list[ValidationIssue] = []

        for enc_idx, enc in enumerate(plan.encounters):
            for check_name, dc in enc.get("dc_checks", {}).items():
                if not isinstance(dc, (int, float)):
                    issues.append(
                        ValidationIssue(
                            3,
                            "medium",
                            f"encounters.{enc_idx}.dc_checks.{check_name}",
                            f"DC value '{dc}' is not numeric",
                        )
                    )
                elif dc < min_dc or dc > max_dc:
                    issues.append(
                        ValidationIssue(
                            3,
                            "medium",
                            f"encounters.{enc_idx}.dc_checks.{check_name}",
                            f"DC {dc} outside valid range ({min_dc}-{max_dc})",
                        )
                    )
        return issues

    def _check_dice_formulas(self, plan: CampaignPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for table_id, table in plan.campaign.get("treasure_tables", {}).items():
            for roll_idx, roll in enumerate(table.get("rolls", [])):
                if roll.get("type") == "gold" and "formula" in roll:
                    formula = roll["formula"]
                    if not self._is_valid_dice_formula(formula):
                        issues.append(
                            ValidationIssue(
                                3,
                                "medium",
                                f"treasure_tables.{table_id}.rolls.{roll_idx}.formula",
                                f"Invalid dice formula: '{formula}'",
                            )
                        )
        return issues

    def _check_stat_block_consistency(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Check that monster stat blocks have required mechanical fields."""
        known = self._known_bestiary()
        issues: list[ValidationIssue] = []

        for enc_idx, enc in enumerate(plan.encounters):
            for mon_idx, monster in enumerate(enc.get("monsters", [])):
                stat_block = monster.get("stat_block")
                if not stat_block:
                    issues.append(
                        ValidationIssue(
                            3,
                            "medium",
                            f"encounters.{enc_idx}.monsters.{mon_idx}",
                            "Monster has no stat_block reference",
                        )
                    )
                # If we have a ruleset, check that quantity is plausible
                qty = monster.get("quantity", 1)
                if isinstance(qty, (int, float)) and qty < 1:
                    issues.append(
                        ValidationIssue(
                            3,
                            "medium",
                            f"encounters.{enc_idx}.monsters.{mon_idx}.quantity",
                            f"Monster quantity {qty} is less than 1",
                        )
                    )
        return issues

    # ── Layer 4: Completeness gates ──────────────────────────────────────

    def _validate_completeness(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Layer 4 — Every session has scenes, every NPC has motivation, etc."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_session_scenes(plan))
        issues.extend(self._check_npc_motivations(plan))
        issues.extend(self._check_encounter_purpose(plan))
        issues.extend(self._check_no_empty_sections(plan))

        return issues

    def _check_session_scenes(self, plan: CampaignPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not plan.campaign.get("acts"):
            issues.append(
                ValidationIssue(4, "low", "campaign.acts", "Campaign has no acts defined")
            )
            return issues

        for act_idx, act in enumerate(plan.campaign.get("acts", [])):
            if not act.get("sessions"):
                issues.append(
                    ValidationIssue(
                        4,
                        "low",
                        f"acts.{act_idx}",
                        f"Act {act.get('act', act_idx)} has no sessions",
                    )
                )
                continue

            for ses_idx, session in enumerate(act.get("sessions", [])):
                if not session.get("scenes"):
                    issues.append(
                        ValidationIssue(
                            4,
                            "low",
                            f"acts.{act_idx}.sessions.{ses_idx}",
                            f"Session {session.get('session', ses_idx)} has no scenes",
                        )
                    )
        return issues

    def _check_npc_motivations(self, plan: CampaignPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for npc_idx, npc in enumerate(plan.npcs):
            if not npc.get("motivation"):
                name = npc.get("name", npc.get("id", f"index {npc_idx}"))
                issues.append(
                    ValidationIssue(
                        4,
                        "low",
                        f"npcs.{npc.get('id', npc_idx)}",
                        f"NPC '{name}' has no motivation",
                    )
                )
        return issues

    def _check_encounter_purpose(self, plan: CampaignPlan) -> list[ValidationIssue]:
        """Warn if encounters lack a description or purpose."""
        issues: list[ValidationIssue] = []

        for enc_idx, enc in enumerate(plan.encounters):
            has_purpose = enc.get("purpose") or enc.get("description") or enc.get("summary")
            if not has_purpose:
                issues.append(
                    ValidationIssue(
                        4,
                        "low",
                        f"encounters.{enc.get('id', enc_idx)}",
                        "Encounter has no purpose, description, or summary",
                    )
                )
        return issues

    def _check_no_empty_sections(self, plan: CampaignPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        # Check that acts are not empty
        if plan.campaign.get("acts") is not None and len(plan.campaign["acts"]) == 0:
            issues.append(
                ValidationIssue(4, "low", "campaign.acts", "Acts list is empty")
            )

        # Check that campaign has a name
        if not plan.campaign.get("name"):
            issues.append(
                ValidationIssue(4, "low", "campaign.name", "Campaign has no name")
            )

        return issues

    # ── Helpers ──────────────────────────────────────────────────────────

    def _known_bestiary(self) -> set[str]:
        """Return the set of known stat-block IDs from the ruleset."""
        if self._ruleset is None:
            return set()
        # Bestiary entries live in the ruleset's executable layer (T1.4)
        # or as explicit bestiary definitions.
        try:
            return set(getattr(self._ruleset, "bestiary", {}).keys())
        except Exception:
            return set()

    def _dc_range(self) -> tuple[int, int]:
        """Return (min, max) DC bounds from the ruleset or sensible defaults."""
        if self._ruleset is None:
            return (5, 30)
        try:
            combat = getattr(self._ruleset, "combat", None)
            if combat is not None:
                min_dc = getattr(combat, "min_dc", 5)
                max_dc = getattr(combat, "max_dc", 30)
                return (min_dc, max_dc)
        except Exception:
            pass
        return (5, 30)

    @staticmethod
    def _is_valid_dice_formula(formula: str) -> bool:
        """Check whether *formula* is a valid dice expression.

        Valid examples: ``2d10+5``, ``d6``, ``3d8``, ``1d4+2``,
        ``2d6-1``.
        """
        if not isinstance(formula, str):
            return False
        pattern = r"^\d*d\d+([+-]\d+)?$"
        return bool(re.match(pattern, formula.strip()))
