"""Exact Ruleset and Setting sources shared by Builder authoring and local Plan validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.plan_state_admission import validate_starting_declarations
from sylvia_gm.domain.starting_state import parse_state_schemas
from sylvia_gm.application.setting_character_requirements import (
    CHARACTER_REQUIREMENTS_PATH,
    parse_setting_character_requirements,
)
from sylvia_gm.domain.authority import PackageLock, PackageRole, SafePayload
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    ParticipantArtifactDeclaration,
    campaign_setting_entries,
    closure_sha256,
    parse_campaign_setting_knowledge_bytes,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.package_library import PackageCandidate


@dataclass(frozen=True)
class CampaignPlanToolAuthority:
    """Exact package closure and bounded Setting reader for one campaign Builder."""

    ruleset: PackageBinding
    setting: PackageBinding
    closure_sha256: str
    setting_paths: frozenset[str]
    read_setting: Callable[[str], str]
    setting_knowledge_ids: frozenset[str] = frozenset()
    participant_schemas: tuple[SafePayload, ...] = ()
    setting_character_schema: SafePayload | None = None
    state_schemas: tuple[SafePayload, ...] = ()

    @property
    def character_declarations(self) -> tuple[ParticipantArtifactDeclaration, ...]:
        """Expose the exact contained declarations for required-character draft checking."""
        return tuple(ParticipantArtifactDeclaration(
            kind=item["kind"], schema_id=item["schema_id"], schema_sha256=item["schema_sha256"],
            schema=item["schema"], entity_state_schema=item.get("entity_state_schema"),
            initial_state_calculations=tuple(item.get("initial_state_calculations", ())),
            initial_state_validation=item.get("initial_state_validation"),
        ) for item in self.participant_schemas)

    def validate(self, payload: object, readable_markdown: str) -> ValidatedCampaignPlan:
        """Validate authored content against these exact source identities and references."""
        validated = validate_campaign_plan(
            payload,
            readable_markdown=readable_markdown,
            expected_ruleset=self.ruleset,
            expected_setting=self.setting,
            expected_closure_sha256=self.closure_sha256,
            setting_paths=self.setting_paths,
            setting_knowledge_ids=self.setting_knowledge_ids,
        )
        if self.state_schemas:
            validate_starting_declarations(
                validated.payload, self.ruleset.package_id, self.ruleset.version,
                parse_state_schemas(self.state_schemas),
            )
        return validated


def campaign_plan_sources(
    owner: AuthorityOwner, lock: PackageLock, *, setting_id: str | None = None
) -> CampaignPlanToolAuthority | None:
    """Use an exact retained package closure for campaign or independent Plan authoring."""
    primary = next((entry for entry in lock.entries if entry.role is PackageRole.PRIMARY), None)
    if primary is None:
        return None
    settings = campaign_setting_entries(lock)
    if setting_id is not None:
        settings = tuple(
            setting for setting in settings if setting.package_id == setting_id
        )
    if len(settings) != 1:
        raise CommandRejected(
            "Campaign Plan construction requires exactly one Campaign Setting snapshot"
        )
    setting = settings[0]
    setting_paths = frozenset(
        owner.package_file_paths(
            setting.package_id,
            setting.version,
            setting.sha256,
            maximum_files=512,
        )
    )
    if not setting_paths:
        raise CommandRejected("Campaign Setting package has no readable source files")
    setting_knowledge_ids: frozenset[str] = frozenset()
    if CAMPAIGN_SETTING_KNOWLEDGE_PATH in setting_paths:
        knowledge_text = owner.package_text_file(
            setting.package_id,
            setting.version,
            setting.sha256,
            CAMPAIGN_SETTING_KNOWLEDGE_PATH,
        )
        try:
            setting_knowledge = parse_campaign_setting_knowledge_bytes(
                knowledge_text.encode("utf-8")
            )
        except ValueError as error:
            raise CommandRejected("Campaign Setting knowledge is invalid") from error
        setting_knowledge_ids = frozenset(item.identity for item in setting_knowledge.facts)

    def read_setting(path: str) -> str:
        if path not in setting_paths:
            raise ValueError("Campaign Setting source path is unavailable")
        return owner.package_text_file(
            setting.package_id,
            setting.version,
            setting.sha256,
            path,
        )

    return CampaignPlanToolAuthority(
        ruleset=PackageBinding(primary.package_id, primary.version, primary.sha256),
        setting=PackageBinding(setting.package_id, setting.version, setting.sha256),
        closure_sha256=closure_sha256(lock),
        setting_paths=setting_paths,
        read_setting=read_setting,
        setting_knowledge_ids=setting_knowledge_ids,
        participant_schemas=tuple(
            owner.package_record(primary.package_id, primary.version, primary.sha256).get(
                "participant_artifacts", ()
            )
        ),
        state_schemas=tuple(
            owner.package_record(primary.package_id, primary.version, primary.sha256).get(
                "state_schemas", (),
            )
        ),
        setting_character_schema=(
            parse_setting_character_requirements(read_setting(CHARACTER_REQUIREMENTS_PATH))
            if CHARACTER_REQUIREMENTS_PATH in setting_paths else None
        ),
    )


def validate_retained_campaign_plan(
    owner: AuthorityOwner, payload: object, readable_markdown: str,
    *, candidates: Sequence[PackageCandidate] | None = None,
) -> ValidatedCampaignPlan:
    """Treat saved bindings as lookup hints and validate against the exact retained packages."""
    bindings = payload.get("bindings") if isinstance(payload, Mapping) else None
    if not isinstance(bindings, Mapping):
        raise ValueError("Reusable Campaign Plan bindings are invalid")
    setting = bindings.get("campaign_setting")
    if not isinstance(setting, Mapping):
        raise ValueError("Reusable Campaign Plan Setting binding is invalid")
    closure = bindings.get("package_closure_sha256")
    available_candidates = owner.package_candidates() if candidates is None else candidates
    candidate = next(
        (item for item in available_candidates if item.closure_sha256 == closure), None
    )
    if candidate is not None:
        sources = campaign_plan_sources(
            owner, candidate.package_lock, setting_id=str(setting.get("id"))
        )
        if sources is None:
            raise ValueError("Reusable Campaign Plan Ruleset is unavailable")
        return sources.validate(payload, readable_markdown)
    else:
        sources = bundled_rift_authority()
        if closure != sources.closure_sha256:
            raise ValueError("Reusable Campaign Plan exact packages are unavailable")
    return validate_campaign_plan(
        payload,
        readable_markdown=readable_markdown,
        expected_ruleset=sources.ruleset,
        expected_setting=sources.setting,
        expected_closure_sha256=sources.closure_sha256,
        setting_paths=sources.setting_paths,
        setting_knowledge_ids=sources.setting_knowledge_ids,
    )
