"""Versioned structured Campaign Plan artifacts used by Builder and campaign handoff."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from sylvia_gm.domain.authority import (
    SafePayload,
    StateValue,
    canonical_payload_is_safe,
    owned_payload,
)
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.errors import RulesetDiagnostic
from sylvia_gm.domain.package_library import canonical_json_bytes, validate_portable_path

CAMPAIGN_PLAN_FORMAT_ID = "sylvia-gm-campaign-plan"
CAMPAIGN_PLAN_FORMAT_VERSION = 2
CAMPAIGN_PLAN_BYTE_LIMIT = 512 * 1024
CAMPAIGN_PLAN_REFERENCE_LIMIT = 512

_ABSOLUTE_PATH = re.compile(r"(?:^[A-Za-z]:[\\/]|^/|^\\\\)")
_PRIVATE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "password",
        "private_reasoning",
        "refresh_token",
        "secret_key",
    }
)


@dataclass(frozen=True)
class PackageBinding:
    """One exact package identity embedded in a Campaign Plan artifact."""

    package_id: str
    version: str
    content_sha256: str

    def as_payload(self) -> dict[str, str]:
        return {
            "id": self.package_id,
            "version": self.version,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class ValidatedCampaignPlan:
    """Canonical validated plan bytes plus its deterministic identity."""

    payload: SafePayload
    content_sha256: str
    artifact_id: str
    title: str
    readable_markdown: str
    referenced_setting_paths: tuple[str, ...]


@dataclass(frozen=True)
class CampaignPlanCharacterReference:
    """One exact Campaign Setting character selected by a Campaign Plan."""

    character_id: str
    path: str

    def as_payload(self) -> dict[str, str]:
        return {"id": self.character_id, "path": self.path}


@dataclass(frozen=True)
class StartingPartyPolicy:
    """Campaign Plan policy that narrows the Ruleset's compatible party envelope."""

    player_character_mode: str
    fixed_player_character: CampaignPlanCharacterReference | None
    required_companions: tuple[CampaignPlanCharacterReference, ...]
    custom_companion_minimum: int
    custom_companion_maximum: int

    def as_payload(self) -> dict[str, object]:
        player_character: dict[str, object] = {"mode": self.player_character_mode}
        if self.fixed_player_character is not None:
            player_character["character"] = self.fixed_player_character.as_payload()
        return {
            "player_character": player_character,
            "required_companions": [item.as_payload() for item in self.required_companions],
            "custom_companions": {
                "minimum": self.custom_companion_minimum,
                "maximum": self.custom_companion_maximum,
            },
        }


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


class CampaignPlanFieldsError(ValueError):
    """Identify closed-object field mistakes without returning submitted field values."""

    def __init__(
        self,
        value: Mapping[str, object] | None,
        keys: set[str],
        label: str,
        *,
        optional: frozenset[str] = frozenset(),
        path: str | None = None,
    ) -> None:
        actual = set(value) if value is not None else set()
        details = []
        for kind, names in (("missing", keys - actual), ("unexpected", actual - keys - optional)):
            if names:
                safe_names = sorted(
                    name
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
                    else "[invalid field name]"
                    for name in names
                )
                details.append(f"{kind}: {', '.join(safe_names[:8])}")
        message = f"{label} fields are invalid ({'; '.join(details)})"
        super().__init__(message)
        self.diagnostics = (
            RulesetDiagnostic(
                code="campaign_plan_fields_invalid",
                phase="campaign_plan",
                message=message,
                path=path,
                guidance="Correct the named fields using the Campaign Plan reference. "
                "Preserve unrelated draft content and the original source bindings.",
            ),
        )


class CampaignPlanReferenceError(ValueError):
    """Locate a malformed Setting reference without echoing its submitted value."""

    def __init__(self, label: str, *, path: str | None) -> None:
        message = f"{label} must be an object with id and path fields"
        super().__init__(message)
        self.diagnostics = (
            RulesetDiagnostic(
                code="campaign_plan_reference_invalid",
                phase="campaign_plan",
                message=message,
                path=path,
                guidance="Use the {id, path} reference object shown in the Campaign Plan "
                "reference. Correct the reference at this JSON path and preserve unrelated data.",
            ),
        )


def _exact(
    value: Mapping[str, object],
    keys: set[str],
    label: str,
    *,
    optional: frozenset[str] = frozenset(),
    path: str | None = None,
) -> None:
    """Check required and optional fields of a closed Plan object."""
    if keys - value.keys() or value.keys() - keys - optional:
        raise CampaignPlanFieldsError(value, keys, label, optional=optional, path=path)


def _text(value: object, label: str, *, maximum: int = 20_000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _texts(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 256,
) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not minimum <= len(value) <= maximum
    ):
        raise ValueError(f"{label} is invalid")
    return tuple(_text(item, label, maximum=4_000) for item in value)


def _identity(value: object, label: str) -> str:
    text = _text(value, label, maximum=160)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text):
        raise ValueError(f"{label} is invalid")
    return text


def _validate_party_placements(value: object, ruleset_id: str) -> None:
    """Validate ordered party slots whose data belongs entirely to the selected Ruleset."""
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not 1 <= len(value) <= 64
    ):
        raise ValueError("Party placements must contain one to 64 ordered slots")

    def template(item: object) -> None:
        if isinstance(item, Mapping):
            if "$participant" in item:
                if (
                    set(item) != {"$participant"}
                    or not isinstance(item["$participant"], str)
                    or item["$participant"] not in {"id", "name"}
                ):
                    raise ValueError("Party placement participant reference is invalid")
            else:
                for child in item.values():
                    template(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                template(child)

    for raw in value:
        slot = _object(raw, "Party placement")
        _exact(slot, {"entity_state", "append"}, "Party placement")
        entity_state = _object(slot["entity_state"], "Party placement entity state")
        if "$participant" in entity_state:
            raise ValueError("Party placement entity state must remain an object")
        appends = slot["append"]
        if (
            not isinstance(appends, Sequence)
            or isinstance(appends, (str, bytes, bytearray))
            or len(appends) > 16
        ):
            raise ValueError("Party placement append list is invalid")
        for raw_append in appends:
            entry = _object(raw_append, "Party placement append")
            _exact(entry, {"namespace", "key", "value"}, "Party placement append")
            state = StateValue(entry["namespace"], entry["key"], entry["value"])
            if not (state.namespace == ruleset_id or state.namespace.startswith(ruleset_id + ".")):
                raise ValueError("Party placement state must belong to the Plan's Ruleset")
            if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", state.key):
                raise ValueError("Party placement state path is invalid")
            nested = state.value
            for field in reversed(state.key.split(".")):
                nested = {field: nested}
            if not canonical_payload_is_safe(nested):
                raise ValueError("Party placement contains a reserved canonical channel")
        if not canonical_payload_is_safe(slot):
            raise ValueError("Party placement contains a reserved canonical channel")
        template(slot)


def _package_binding(value: object, label: str) -> PackageBinding:
    item = _object(value, label)
    _exact(item, {"id", "version", "content_sha256"}, label)
    content_sha256 = _text(item["content_sha256"], f"{label} content hash", maximum=64)
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise ValueError(f"{label} content hash is invalid")
    return PackageBinding(
        _identity(item["id"], f"{label} identity"),
        _identity(item["version"], f"{label} version"),
        content_sha256,
    )


def _setting_reference(value: object, label: str, *, path: str | None = None) -> str:
    if not isinstance(value, Mapping):
        raise CampaignPlanReferenceError(label, path=path)
    item = _object(value, label)
    _exact(item, {"id", "path"}, label, path=path)
    _identity(item["id"], f"{label} identity")
    return validate_portable_path(_text(item["path"], f"{label} path", maximum=512))


def _character_reference(
    value: object,
    label: str,
    *,
    path: str,
) -> CampaignPlanCharacterReference:
    item = _object(value, label)
    _exact(item, {"id", "path"}, label, path=path)
    return CampaignPlanCharacterReference(
        character_id=_identity(item["id"], f"{label} identity"),
        path=validate_portable_path(_text(item["path"], f"{label} path", maximum=512)),
    )


def parse_starting_party_policy(value: object) -> StartingPartyPolicy:
    """Validate and return the portable Campaign Plan starting-party policy."""

    party = _object(value, "Starting party")
    _exact(
        party,
        {"custom_companions", "player_character", "required_companions"},
        "Starting party",
        path="$.starting_party",
    )
    player = _object(party["player_character"], "Starting player character")
    mode = _text(player.get("mode"), "Starting player character mode", maximum=20)
    if mode == "compatible":
        _exact(player, {"mode"}, "Starting player character")
        fixed = None
    elif mode == "fixed":
        _exact(player, {"character", "mode"}, "Starting player character")
        fixed = _character_reference(
            player["character"],
            "Fixed player character",
            path="$.starting_party.player_character.character",
        )
    else:
        raise ValueError("Starting player character mode is invalid")

    required = party["required_companions"]
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes, bytearray))
        or len(required) > 64
    ):
        raise ValueError("Required companion references are invalid")
    required_companions = tuple(
        _character_reference(
            item,
            "Required companion reference",
            path=f"$.starting_party.required_companions[{index}]",
        )
        for index, item in enumerate(required)
    )
    required_identities = [(item.character_id, item.path) for item in required_companions]
    if len(set(required_identities)) != len(required_identities):
        raise ValueError("Required companion references must be unique")

    custom = _object(party["custom_companions"], "Custom companion policy")
    _exact(custom, {"maximum", "minimum"}, "Custom companion policy")
    minimum = custom["minimum"]
    maximum = custom["maximum"]
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 0 <= minimum <= maximum <= 64
    ):
        raise ValueError("Custom companion limits are invalid")
    return StartingPartyPolicy(
        player_character_mode=mode,
        fixed_player_character=fixed,
        required_companions=required_companions,
        custom_companion_minimum=minimum,
        custom_companion_maximum=maximum,
    )


def _scan_private(value: object, path: str = "plan") -> None:
    """Reject credential-shaped fields and machine-private absolute paths."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.casefold() in _PRIVATE_KEYS:
                raise ValueError(f"{path} contains private credential material")
            _scan_private(item, f"{path}.{key_text}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _scan_private(item, f"{path}[{index}]")
    elif isinstance(value, str) and _ABSOLUTE_PATH.match(value):
        raise ValueError(f"{path} contains an absolute path")


def validate_campaign_plan(
    payload: object,
    *,
    readable_markdown: str,
    expected_ruleset: PackageBinding,
    expected_setting: PackageBinding,
    expected_closure_sha256: str,
    setting_paths: frozenset[str],
    setting_knowledge_ids: frozenset[str] = frozenset(),
) -> ValidatedCampaignPlan:
    """Validate one closed plan and return canonical deterministic publication data."""

    plan = _object(payload, "Campaign Plan")
    required_plan_fields = {
        "bindings",
        "boundaries",
        "completion_conditions",
        "continuation_conditions",
        "failure_conditions",
        "format_id",
        "format_version",
        "gm_guidance",
        "id",
        "initial_state",
        "intended_player_experience",
        "objectives",
        "premise",
        "revision",
        "secrets",
        "setting_references",
        "side_content",
        "starting_location",
        "starting_party",
        "structure",
        "title",
        "tone",
        "unsupported_mechanics",
    }
    _exact(
        plan,
        required_plan_fields,
        "Campaign Plan",
        optional=frozenset({"opening_direction", "ending"}),
        path="$",
    )
    if (
        plan["format_id"] != CAMPAIGN_PLAN_FORMAT_ID
        or plan["format_version"] != CAMPAIGN_PLAN_FORMAT_VERSION
    ):
        raise ValueError("Campaign Plan format is unsupported")
    plan_id = _identity(plan["id"], "Campaign Plan identity")
    if (
        not isinstance(plan["revision"], int)
        or isinstance(plan["revision"], bool)
        or plan["revision"] < 1
    ):
        raise ValueError("Campaign Plan revision is invalid")
    title = _text(plan["title"], "Campaign Plan title", maximum=160)
    _text(plan["premise"], "Campaign Plan premise")
    _text(plan["tone"], "Campaign Plan tone", maximum=500)
    _texts(plan["boundaries"], "Campaign Plan boundaries")
    _text(plan["intended_player_experience"], "Campaign Plan intended experience")
    if "opening_direction" in plan:
        _text(plan["opening_direction"], "Campaign Plan opening direction", maximum=4_000)

    bindings = _object(plan["bindings"], "Campaign Plan bindings")
    _exact(
        bindings,
        {"campaign_setting", "package_closure_sha256", "ruleset"},
        "Campaign Plan bindings",
        path="$.bindings",
    )
    ruleset = _package_binding(bindings["ruleset"], "Ruleset binding")
    setting = _package_binding(bindings["campaign_setting"], "Campaign Setting binding")
    closure = _text(bindings["package_closure_sha256"], "Package closure hash", maximum=64)
    if (
        ruleset != expected_ruleset
        or setting != expected_setting
        or closure != expected_closure_sha256
    ):
        raise ValueError("Campaign Plan package bindings do not match the admitted closure")

    party = parse_starting_party_policy(plan["starting_party"])
    player_refs = (
        () if party.fixed_player_character is None else (party.fixed_player_character.path,)
    )
    companion_refs = tuple(item.path for item in party.required_companions)
    starting_location = _setting_reference(plan["starting_location"], "Starting location")

    initial_state = _object(plan["initial_state"], "Initial state")
    _exact(
        initial_state,
        {"entities", "facts"},
        "Initial state",
        optional=frozenset({"mechanics", "party_placements"}),
        path="$.initial_state",
    )
    if "party_placements" in initial_state:
        _validate_party_placements(initial_state["party_placements"], ruleset.package_id)
    mechanics = initial_state.get("mechanics", ())
    if (
        not isinstance(mechanics, Sequence)
        or isinstance(mechanics, (str, bytes, bytearray))
        or len(mechanics) > 256
    ):
        raise ValueError("Initial mechanical state is invalid")
    state_keys = set()
    for raw in mechanics:
        state = _object(raw, "Initial mechanical state entry")
        _exact(state, {"namespace", "key", "value"}, "Initial mechanical state entry")
        value = StateValue(state["namespace"], state["key"], state["value"])
        if not (
            value.namespace == ruleset.package_id
            or value.namespace.startswith(ruleset.package_id + ".")
        ):
            raise ValueError("Initial mechanical state must belong to the Plan's Ruleset")
        if not canonical_payload_is_safe({value.namespace: {value.key: value.value}}):
            raise ValueError("Initial mechanical state contains a reserved canonical channel")
        identity = (value.namespace, value.key)
        if identity in state_keys:
            raise ValueError("Initial mechanical state keys must be unique")
        state_keys.add(identity)
    entities = initial_state["entities"]
    if (
        not isinstance(entities, Sequence)
        or isinstance(entities, (str, bytes, bytearray))
        or len(entities) > 128
    ):
        raise ValueError("Initial state entities are invalid")
    entity_ids: list[str] = []
    entity_refs: list[str] = []
    for index, raw in enumerate(entities):
        entity_path = f"$.initial_state.entities[{index}]"
        entity = _object(raw, "Initial state entity")
        _exact(
            entity,
            {"id", "kind", "source_ref", "state"},
            "Initial state entity",
            path=entity_path,
        )
        entity_ids.append(_identity(entity["id"], "Initial state entity identity"))
        _identity(entity["kind"], "Initial state entity kind")
        entity_refs.append(
            _setting_reference(
                entity["source_ref"],
                "Initial state entity source_ref",
                path=f"{entity_path}.source_ref",
            )
        )
        _object(entity["state"], "Initial state entity state")
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("Initial state entity identities must be unique")
    facts = initial_state["facts"]
    if (
        not isinstance(facts, Sequence)
        or isinstance(facts, (str, bytes, bytearray))
        or len(facts) > 256
    ):
        raise ValueError("Initial state facts are invalid")
    fact_ids: list[str] = []
    for raw in facts:
        if isinstance(raw, str):
            _text(raw, "Initial state fact")
            continue
        fact = _object(raw, "Initial state fact")
        _exact(fact, {"fact", "id", "known_by"}, "Initial state fact")
        fact_ids.append(_identity(fact["id"], "Initial state fact identity"))
        _text(fact["fact"], "Initial state fact text")
        _texts(fact["known_by"], "Initial state fact knowledge", maximum=128)
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("Initial state fact identities must be unique")

    reference_groups = _object(plan["setting_references"], "Setting references")
    expected_groups = {"creatures", "factions", "items", "locations", "lore", "npcs"}
    _exact(reference_groups, expected_groups, "Setting references")
    references: list[str] = [*player_refs, *companion_refs, starting_location, *entity_refs]
    for group in sorted(expected_groups):
        values = reference_groups[group]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise ValueError(f"Setting reference group {group} is invalid")
        references.extend(_setting_reference(item, f"{group} reference") for item in values)
    if len(references) > CAMPAIGN_PLAN_REFERENCE_LIMIT:
        raise ValueError("Campaign Plan has too many Setting references")
    missing = sorted(set(references) - setting_paths)
    if missing:
        raise ValueError(f"Campaign Plan references missing Setting content: {missing[0]}")

    structure = plan["structure"]
    if (
        not isinstance(structure, Sequence)
        or isinstance(structure, (str, bytes, bytearray))
        or not 1 <= len(structure) <= 64
    ):
        raise ValueError("Campaign Plan structure is invalid")
    act_ids: list[str] = []
    for raw in structure:
        act = _object(raw, "Campaign act")
        _exact(
            act,
            {
                "branches",
                "consequences",
                "dynamic_encounter_guidance",
                "id",
                "objectives",
                "planned_encounters",
                "setting_refs",
                "situations",
                "summary",
                "title",
            },
            "Campaign act",
        )
        act_ids.append(_identity(act["id"], "Campaign act identity"))
        _text(act["title"], "Campaign act title", maximum=160)
        _text(act["summary"], "Campaign act summary")
        for field in (
            "branches",
            "consequences",
            "dynamic_encounter_guidance",
            "objectives",
            "planned_encounters",
            "situations",
        ):
            _texts(act[field], f"Campaign act {field}", maximum=128)
        act_refs = _texts(act["setting_refs"], "Campaign act Setting references", maximum=128)
        for path in act_refs:
            validate_portable_path(path)
            if path not in setting_paths:
                raise ValueError(f"Campaign act references missing Setting content: {path}")
    if len(set(act_ids)) != len(act_ids):
        raise ValueError("Campaign act identities must be unique")

    secrets = plan["secrets"]
    if (
        not isinstance(secrets, Sequence)
        or isinstance(secrets, (str, bytes, bytearray))
        or len(secrets) > 256
    ):
        raise ValueError("Campaign Plan secrets are invalid")
    secret_ids: list[str] = []
    for raw in secrets:
        if isinstance(raw, str):
            _text(raw, "Campaign Plan secret")
            continue
        secret = _object(raw, "Campaign Plan secret")
        fields = set(secret)
        if fields == {"fact", "id", "known_by", "reveal"}:
            secret_id = _identity(secret["id"], "Campaign Plan secret identity")
            if secret_id in setting_knowledge_ids:
                raise ValueError("Existing Campaign Setting secrets must use setting_fact_id")
            _text(secret["fact"], "Campaign Plan secret fact")
            _texts(secret["known_by"], "Campaign Plan secret knowledge", maximum=128)
        elif fields == {"id", "setting_fact_id", "reveal"}:
            secret_id = _identity(secret["id"], "Campaign Plan secret identity")
            setting_fact_id = _identity(
                secret["setting_fact_id"], "Campaign Setting knowledge fact identity"
            )
            if secret_id != setting_fact_id:
                raise ValueError("Campaign Plan Setting secret identities must match")
            if setting_fact_id not in setting_knowledge_ids:
                raise ValueError("Campaign Plan references missing Campaign Setting knowledge")
        else:
            raise ValueError("Campaign Plan secret fields do not match the portable format")
        secret_ids.append(secret_id)
        reveal = _object(secret["reveal"], "Campaign Plan secret reveal")
        _exact(reveal, {"condition", "from_act"}, "Campaign Plan secret reveal")
        _text(reveal["condition"], "Campaign Plan secret reveal condition")
        from_act = _identity(reveal["from_act"], "Campaign Plan secret reveal act")
        if from_act not in act_ids:
            raise ValueError("Campaign Plan secret reveal act is not declared")
    if len(set(secret_ids)) != len(secret_ids):
        raise ValueError("Campaign Plan secret identities must be unique")

    objectives = plan["objectives"]
    ending = plan.get("ending")
    ongoing = isinstance(ending, Mapping) and ending.get("mode") == "ongoing"
    if (
        not isinstance(objectives, Sequence)
        or isinstance(objectives, (str, bytes, bytearray))
        or (not objectives and not ongoing)
    ):
        raise ValueError("Campaign Plan objectives are invalid")
    objective_ids: list[str] = []
    for raw in objectives:
        objective = _object(raw, "Campaign objective")
        _exact(
            objective, {"completion_conditions", "description", "id", "title"}, "Campaign objective"
        )
        objective_ids.append(_identity(objective["id"], "Campaign objective identity"))
        _text(objective["title"], "Campaign objective title", maximum=160)
        _text(objective["description"], "Campaign objective description")
        _texts(
            objective["completion_conditions"],
            "Campaign objective conditions",
            minimum=1,
            maximum=64,
        )
    if len(set(objective_ids)) != len(objective_ids):
        raise ValueError("Campaign objective identities must be unique")
    for raw in structure:
        act = _object(raw, "Campaign act")
        ending = plan.get("ending")
        ongoing_final = (
            isinstance(ending, Mapping) and ending.get("mode") == "ongoing"
            and raw is structure[-1]
        )
        required_objectives = _texts(
            act["objectives"], f"Campaign act {act['id']} objectives",
            minimum=0 if ongoing_final else 1, maximum=128,
        )
        if len(set(required_objectives)) != len(required_objectives):
            raise ValueError(f"Campaign act {act['id']} repeats an objective reference")
        for objective_id in required_objectives:
            if objective_id not in objective_ids:
                raise ValueError(
                    f"Campaign act {act['id']} references missing objective {objective_id}"
                )

    guidance = _object(plan["gm_guidance"], "GM guidance")
    _exact(
        guidance,
        {"instructions", "player_freedom", "preparation_detail", "railroading"},
        "GM guidance",
    )
    for field in sorted(guidance):
        _text(guidance[field], f"GM guidance {field}")
    for field in (
        "completion_conditions",
        "continuation_conditions",
        "failure_conditions",
        "side_content",
        "unsupported_mechanics",
    ):
        _texts(plan[field], f"Campaign Plan {field}", maximum=256)

    _scan_private(plan)
    markdown = _text(
        readable_markdown.strip(),
        "Campaign Plan readable rendering",
        maximum=CAMPAIGN_PLAN_BYTE_LIMIT,
    )
    if len(markdown.encode("utf-8")) > CAMPAIGN_PLAN_BYTE_LIMIT:
        raise ValueError("Campaign Plan readable rendering is too large")
    validate_content_shape("campaign_plan", plan, file="plan.json")
    from sylvia_gm.domain.campaign_ending import validate_ending

    validate_ending(plan)
    captured = owned_payload(plan)
    encoded = canonical_json_bytes(captured)
    if len(encoded) > CAMPAIGN_PLAN_BYTE_LIMIT:
        raise ValueError("Campaign Plan is too large")
    content_sha256 = hashlib.sha256(encoded).hexdigest()
    return ValidatedCampaignPlan(
        payload=captured,
        content_sha256=content_sha256,
        artifact_id=f"campaign-plan-{plan_id}-{content_sha256[:16]}",
        title=title,
        readable_markdown=markdown,
        referenced_setting_paths=tuple(sorted(set(references))),
    )
