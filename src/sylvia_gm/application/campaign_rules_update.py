"""Explicit, value-preserving adoption of an installed campaign snapshot.

This path never transforms saved values or resolves conflicts. Existing activation
owns the transaction, exact pins, lineage, retry receipt and failure recovery.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import yaml

from sylvia_gm.application.launch_compatibility import ARCHIVED_RULESET_IDS
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    _BoundedRuntimeLoader,
    package_tool_permanently_refused,
)
from sylvia_gm.application.plan_state_admission import validate_starting_declarations
from sylvia_gm.domain.authority import (
    JsonValue,
    LifecycleState,
    MechanicOutcome,
    PackageLock,
    PackageRole,
    ParticipantMutation,
    SafePayload,
    TurnStatus,
    WriterRole,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    CONTENT_DECLARATION_BYTE_LIMIT,
    GAMEPLAY_SUPPORT_PATH,
    ActivationCommand,
    ActivationResult,
    CampaignSettingKnowledge,
    PackageContentDeclarations,
    PackageManifest,
    ToolDeclaration,
    campaign_setting_entries,
    canonical_json_bytes,
    parse_campaign_setting_knowledge_bytes,
    parse_content_declarations_bytes,
    parse_gameplay_support_bytes,
)
from sylvia_gm.domain.session import SessionMessageKind

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner

Report = Callable[[str, str], None]


def _offer_bundled_update(owner: AuthorityOwner, campaign_id: str) -> None:
    """Offer shipped rules with the campaign's own exact Setting files, without activating."""
    from sylvia_gm.application.bundled_packages import bundled_ruleset_update_archive
    from sylvia_gm.application.package_materialization import materialize_package_archive
    from sylvia_gm.builder._package_snapshots import (
        rebind_ruleset_snapshot,
        retained_package_drafts,
    )

    current = owner._store.current_package_lock(campaign_id)
    primary, settings = _ownership(current.package_lock)
    if primary is None:
        return
    raw = bundled_ruleset_update_archive(primary)
    if raw is None:
        return
    old = _manifests(owner, current.package_lock)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        records = json.loads(archive.read("manifest.json"))["packages"]
    incoming = next(r for r in records if r["role"] == "primary")
    if (incoming["id"], incoming["version"], incoming["content_sha256"]) == old[primary].identity:
        return
    candidates = owner._store.package_candidates()
    identities = {(r["id"], r["version"], r["content_sha256"]) for r in records}
    bundled = next(
        (
            c
            for c in candidates
            if {(e.package_id, e.version, e.sha256) for e in c.package_lock.entries} == identities
        ),
        None,
    )
    if bundled is None:
        bundled = owner.install_package_archive(
            io.BytesIO(raw),
            install_id="bundled-update-" + hashlib.sha256(raw).hexdigest(),
        )
    if not settings:
        return
    install_id = (
        "rules-update-"
        + hashlib.sha256(
            f"{current.closure_sha256}:{bundled.closure_sha256}".encode("ascii")
        ).hexdigest()
    )
    if any(c.install_id == install_id for c in candidates):
        return
    source = next((c for c in candidates if c.closure_sha256 == current.closure_sha256), None)
    if source is None:
        raise CommandRejected("The campaign's retained package source is unavailable")
    original = retained_package_drafts(owner, source.candidate_id, include_setting=True)
    rules = retained_package_drafts(owner, bundled.candidate_id, include_setting=False)
    drafts = rebind_ruleset_snapshot(original, rules)
    with materialize_package_archive(drafts) as result:
        for identity in settings:
            retained = old[identity]
            replacement = next(m for m in result.manifests if m.package_id == identity)
            if {f.path: f.sha256 for f in retained.files} != {
                f.path: f.sha256 for f in replacement.files
            }:
                raise CommandRejected("The update must preserve every owned Setting file")
        owner.install_package_archive(result.archive, install_id=install_id)


def _ownership(lock: PackageLock) -> tuple[str | None, tuple[str, ...]]:
    primary = next((e.package_id for e in lock.entries if e.role is PackageRole.PRIMARY), None)
    return primary, tuple(sorted(e.package_id for e in campaign_setting_entries(lock)))


def _manifests(owner: AuthorityOwner, lock: PackageLock) -> dict[str, PackageManifest]:
    return {
        e.package_id: owner._store.exact_package_manifest(e.package_id, e.version, e.sha256)
        for e in lock.entries
    }


def _label(manifests: Mapping[str, PackageManifest]) -> str:
    return " / ".join(
        f"{m.metadata.get('label', m.package_id)} {m.version}"
        for m in manifests.values()
        if m.role is PackageRole.PRIMARY
        or any(e.kind == "ruleset_snapshot" for e in m.dependencies)
    )


def options(owner: AuthorityOwner, campaign_id: str) -> SafePayload:
    """Offer installed exact closures with the same Ruleset and Setting ownership."""
    with owner._operation():
        _offer_bundled_update(owner, campaign_id)
    with owner._operation(), owner._lane(campaign_id):
        current = owner._store.current_package_lock(campaign_id)
        current_manifests = _manifests(owner, current.package_lock)
        identity = _ownership(current.package_lock)
        candidates = []
        seen = {current.closure_sha256}
        for candidate in owner._store.package_candidates():
            if (
                identity[0] is None
                or identity[0] in ARCHIVED_RULESET_IDS
                or _ownership(candidate.package_lock) != identity
                or candidate.closure_sha256 in seen
            ):
                continue
            seen.add(candidate.closure_sha256)
            manifests = _manifests(owner, candidate.package_lock)
            if any(
                package_id in manifests
                and (manifest.role, manifest.format_version)
                != (manifests[package_id].role, manifests[package_id].format_version)
                for package_id, manifest in current_manifests.items()
            ):
                continue
            candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "label": _label(manifests),
                    "closure_sha256": candidate.closure_sha256,
                }
            )
        return owned_payload(
            {
                "campaign_id": campaign_id,
                "candidates": candidates,
                "current_label": _label(current_manifests),
                "current_closure_sha256": current.closure_sha256,
                "retained_versions": len(owner._store.package_lock_lineage(campaign_id)),
            }
        )


def _removed_keys(
    old: object,
    new: object,
    path: str,
    report: Report,
    *,
    retain_references: bool = False,
    retired_tools: frozenset[str] = frozenset(),
) -> None:
    """Conservatively flag removed runtime records/fields, without exposing their values."""
    if isinstance(old, Sequence) and not isinstance(old, (str, bytes)):
        for field in ("id", "identity"):
            if old and all(
                isinstance(item, Mapping) and isinstance(item.get(field), str) for item in old
            ):
                incoming = (
                    {
                        item[field]: item
                        for item in new
                        if isinstance(item, Mapping) and isinstance(item.get(field), str)
                    }
                    if isinstance(new, Sequence) and not isinstance(new, (str, bytes))
                    else {}
                )
                _removed_keys(
                    {item[field]: item for item in old},
                    incoming,
                    path,
                    report,
                    retain_references=retain_references,
                    retired_tools=retired_tools,
                )
                return
        references = set(old) if all(isinstance(item, str) for item in old) else set()
        if path.endswith(".links.tools"):
            references -= retired_tools
        if (
            retain_references
            and all(isinstance(item, str) for item in old)
            and references
            and (not isinstance(new, Sequence) or not references <= set(new))
        ):
            report(path, "An existing structured content reference is removed.")
        return
    if not isinstance(old, Mapping):
        return
    if not isinstance(new, Mapping):
        report(path, "An existing runtime collection changes representation.")
        return
    for key, value in old.items():
        child = f"{path}.{key}"
        if key not in new:
            report(child, "An existing content record or field is removed.")
        elif isinstance(value, (Mapping, list)):
            _removed_keys(
                value,
                new[key],
                child,
                report,
                retain_references=retain_references,
                retired_tools=retired_tools,
            )


def _compare_structured(
    owner: AuthorityOwner,
    old: PackageManifest,
    new: PackageManifest,
    report: Report,
    *,
    retired_tools: frozenset[str] = frozenset(),
) -> None:
    """Retain declared content identities, knowledge audiences and reference edges."""
    if old.content_declarations_path != new.content_declarations_path:
        report(old.package_id, "The existing content declaration binding changes.")
    contracts = (
        (
            CAMPAIGN_SETTING_KNOWLEDGE_PATH,
            CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
            parse_campaign_setting_knowledge_bytes,
        ),
        (
            old.content_declarations_path,
            CONTENT_DECLARATION_BYTE_LIMIT,
            parse_content_declarations_bytes,
        ),
        (GAMEPLAY_SUPPORT_PATH, CONTENT_DECLARATION_BYTE_LIMIT, parse_gameplay_support_bytes),
    )
    old_files = {item.path: item.sha256 for item in old.files}
    new_files = {item.path: item.sha256 for item in new.files}
    for path, bound, parse in contracts:
        if path is None or path not in old_files or old_files[path] == new_files.get(path):
            continue
        if path not in new_files:
            continue  # The ordinary manifest comparison already reports this removal.
        label = f"{old.package_id}.{path}"
        try:
            before = owner._store.package_file_bytes(*old.identity, path, maximum_bytes=bound)
            after = owner._store.package_file_bytes(*new.identity, path, maximum_bytes=bound)
            if before is None or after is None:
                raise ValueError("Declared structured content is unavailable")
            prior, incoming = parse(before), parse(after)
            _removed_keys(
                json.loads(prior.canonical_json),
                json.loads(incoming.canonical_json),
                label,
                report,
                retain_references=True,
                retired_tools=retired_tools,
            )
            if isinstance(prior, CampaignSettingKnowledge) and isinstance(
                incoming, CampaignSettingKnowledge
            ):
                facts_by_id = {fact.identity: fact for fact in incoming.facts}
                for fact in prior.facts:
                    other_fact = facts_by_id.get(fact.identity)
                    if other_fact is not None and (fact.known_by, fact.source_path) != (
                        other_fact.known_by,
                        other_fact.source_path,
                    ):
                        report(
                            f"{label}.{fact.identity}",
                            "An existing fact changes audience or source binding.",
                        )
                if (
                    set(incoming.private_references) - set(prior.private_references)
                ) & old_files.keys():
                    report(label, "An existing reference changes knowledge visibility.")
            elif isinstance(prior, PackageContentDeclarations) and isinstance(
                incoming, PackageContentDeclarations
            ):
                if (prior.scope_id, prior.scope_kind) != (incoming.scope_id, incoming.scope_kind):
                    report(label, "Existing content scope changes identity.")
                if not set(prior.source_authorities) <= set(incoming.source_authorities) or not set(
                    prior.source_boundary_refs
                ) <= set(incoming.source_boundary_refs):
                    report(
                        label, "An existing content source authority or boundary reference changes."
                    )
                inclusions_by_id = {item.identity: item for item in incoming.inclusions}
                for item in prior.inclusions:
                    other_item = inclusions_by_id.get(item.identity)
                    if other_item is not None and (
                        (item.kind, item.family, item.disposition)
                        != (other_item.kind, other_item.family, other_item.disposition)
                        or not set(item.sources) <= set(other_item.sources)
                    ):
                        report(
                            f"{label}.{item.identity}",
                            "An existing content identity or source reference changes.",
                        )
        except (ValueError, TypeError) as error:
            report(label, f"Structured content cannot be compared safely: {type(error).__name__}.")


def _tool_input_change(prior: ToolDeclaration, incoming: ToolDeclaration) -> str | None:
    """Review model guidance and string-choice changes without changing execution authority."""
    if replace(incoming, input_schema=prior.input_schema) != prior:
        return None
    before = thaw_json_value(prior.input_schema)
    after = thaw_json_value(incoming.input_schema)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    metadata = {
        "x-sylvia-gm-model-required",
        "x-sylvia-gm-castable-spells",
        "x-sylvia-gm-value-aliases",
    }
    old_properties, new_properties = before.get("properties"), after.get("properties")
    if (
        not isinstance(old_properties, dict)
        or not isinstance(new_properties, dict)
        or old_properties.keys() != new_properties.keys()
    ):
        return None
    choices_changed = False
    for name in old_properties:
        left, right = old_properties[name], new_properties[name]
        if not isinstance(left, dict) or not isinstance(right, dict):
            return None
        for child in (left, right):
            for key in metadata:
                value = child.pop(key, None)
                if value is not None and (
                    (
                        key.endswith("value-aliases")
                        and not (
                            isinstance(value, dict)
                            and all(
                                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
                            )
                        )
                    )
                    or (not key.endswith("value-aliases") and not isinstance(value, bool))
                ):
                    return None
        if left.get("enum") != right.get("enum"):
            if left.get("type") != "string" or right.get("type") != "string":
                return None
            choices: list[frozenset[str] | None] = []
            for child in (left, right):
                values = child.pop("enum", None)
                if values is None:
                    choices.append(None)
                elif (
                    isinstance(values, list) and values and all(isinstance(v, str) for v in values)
                ):
                    choices.append(frozenset(values))
                else:
                    return None
            first, second = choices
            if (
                first is not None
                and second is not None
                and not (first <= second or second <= first)
            ):
                return None
            choices_changed = True
    if before != after:
        return None
    return "changed_input_choices" if choices_changed else "changed_model_guidance"


def _compare(
    old: PackageManifest,
    new: PackageManifest,
    report: Report,
    *,
    retired_tools: frozenset[str] = frozenset(),
) -> list[str]:
    """Keep existing ownership, state/character contracts and panel bindings intact."""
    path = old.package_id
    reviewed = []
    if old.role != new.role or old.format_version != new.format_version:
        report(path, "Package role or snapshot format changes.")
    old_edges = {(e.package_id, e.kind, e.namespace) for e in old.dependencies}
    new_edges = {(e.package_id, e.kind, e.namespace) for e in new.dependencies}
    if not old_edges <= new_edges:
        report(path, "An inherited dependency is removed or changes ownership.")
    for field, key in (
        ("tools", "name"),
        ("state_schemas", "key"),
        ("participant_artifacts", "kind"),
    ):
        incoming = {getattr(x, key): x for x in getattr(new, field)}
        for prior in getattr(old, field):
            identity = getattr(prior, key)
            if incoming.get(identity) != prior:
                if field == "tools":
                    if identity in retired_tools and identity not in incoming:
                        reviewed.append("retired_unavailable_actions")
                        continue
                    other = incoming.get(identity)
                    change = _tool_input_change(prior, other) if other is not None else None
                    if change is not None:
                        reviewed.append(change)
                        continue
                report(
                    f"{path}.{field}.{identity}",
                    "An existing contract changes or is removed; explicit adaptation is required.",
                )
    for field in ("capabilities", "prompt_fragments", "files"):

        def identity(item: Any) -> str:
            return str(getattr(item, "identity", getattr(item, "path", item)))

        if not {identity(x) for x in getattr(old, field)} <= {
            identity(x) for x in getattr(new, field)
        }:
            report(f"{path}.{field}", "Existing content is removed.")
    incoming = {s.identity: s for s in new.ui_surfaces}
    for surface in old.ui_surfaces:
        other = incoming.get(surface.identity)
        if (
            other is None
            or (
                surface.title,
                surface.renderer,
                surface.source,
                surface.actions,
                surface.capability,
            )
            != (other.title, other.renderer, other.source, other.actions, other.capability)
            or not set(surface.allowed_placements) <= set(other.allowed_placements)
        ):
            report(
                f"{path}.panels.{surface.identity}",
                "An existing panel is removed or its saved view/binding becomes incompatible.",
            )
    prior_ids = {s.identity for s in old.ui_surfaces}
    if any(s.renderer == "inventory" for s in old.ui_surfaces) != any(
        s.renderer == "inventory" for s in new.ui_surfaces
    ):
        report(
            f"{path}.panels.inventory",
            "Changing the inventory panel representation requires adaptation.",
        )
    for surface in new.ui_surfaces:
        if (
            surface.renderer is not None
            and surface.identity not in prior_ids
            and "docked" not in surface.allowed_placements
        ):
            report(f"{path}.panels.{surface.identity}", "A new panel must allow sidebar placement.")
    return reviewed


def _preview(owner: AuthorityOwner, campaign_id: str, candidate_id: str) -> SafePayload:
    snapshot = owner._store.snapshot(campaign_id)
    current = owner._store.current_package_lock(campaign_id)
    candidate = owner._store.package_candidate(candidate_id)
    old, new = _manifests(owner, current.package_lock), _manifests(owner, candidate.package_lock)
    conflicts: list[dict[str, str]] = []

    def report(path: str, message: str) -> None:
        if len(conflicts) < 32:
            conflicts.append({"path": path, "message": message})

    if snapshot.lifecycle is not LifecycleState.ACTIVE or snapshot.writer is not WriterRole.DM:
        report("campaign", "Select an active, playable campaign.")
    if (
        owner._store.current_open_turn(campaign_id) is not None
        or TurnStatus.COMPLETED not in snapshot.turns.values()
    ):
        report(
            "session", "Use Stop Session to reach a completed TURN checkpoint, then preview again."
        )
    if owner._store.pending_mechanics_action(campaign_id) is not None:
        report("session", "Resolve the pending rules decision before updating.")
    for context in owner._store.campaign_session_contexts(campaign_id):
        queue = next(
            (
                m.content
                for m in reversed(context.messages)
                if m.kind is SessionMessageKind.EVIDENCE
                and m.content.get("kind") == "action_sequence"
            ),
            None,
        )
        if queue is not None:
            steps = queue.get("steps")
            if (
                not isinstance(steps, Sequence)
                or isinstance(steps, (str, bytes))
                or any(
                    not isinstance(step, Mapping) or step.get("status") != "committed"
                    for step in steps
                )
            ):
                report("session", "Clear or finish the current action sequence before updating.")
                break
    if _ownership(current.package_lock) != _ownership(candidate.package_lock):
        report("snapshot", "Select a snapshot of this campaign's Ruleset and Campaign Setting.")
    if any(e.package_id in ARCHIVED_RULESET_IDS for e in candidate.package_lock.entries):
        report("snapshot", "An archived executable Ruleset cannot be adopted.")
    if current.closure_sha256 == candidate.closure_sha256:
        report("snapshot", "This campaign already uses the selected exact version.")
    changes = []
    tool_changes = []
    added_panels: list[str] = []
    for package_id, prior in old.items():
        incoming = new.get(package_id)
        if incoming is None:
            report(package_id, "An existing package is removed.")
            continue
        old_raw = (
            owner._store.package_file_bytes(
                *prior.identity,
                PACKAGE_RUNTIME_PATH,
                maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
            )
            if any(f.path == PACKAGE_RUNTIME_PATH for f in prior.files)
            else None
        )
        incoming_names = {t.name for t in incoming.tools}
        retired = frozenset(
            t.name
            for t in prior.tools
            if t.name not in incoming_names
            and old_raw is not None
            and package_tool_permanently_refused(
                package_id=package_id,
                declaration=t,
                runtime_bytes=old_raw,
            )
        )
        reviewed = _compare(prior, incoming, report, retired_tools=retired)
        if reviewed:
            tool_changes.append({"package_id": package_id, **Counter(reviewed)})
        _compare_structured(owner, prior, incoming, report, retired_tools=retired)
        before = {f.path: f.sha256 for f in prior.files}
        after = {f.path: f.sha256 for f in incoming.files}
        if prior.content_sha256 != incoming.content_sha256:
            changes.append(
                {
                    "package_id": package_id,
                    "from_version": prior.version,
                    "to_version": incoming.version,
                    "changed_files": sorted(
                        p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
                    ),
                }
            )
        added_panels.extend(
            s.title or s.identity
            for s in incoming.ui_surfaces
            if s.renderer is not None and s.identity not in {x.identity for x in prior.ui_surfaces}
        )
        if before.get(PACKAGE_RUNTIME_PATH) != after.get(PACKAGE_RUNTIME_PATH):

            def runtime(manifest: PackageManifest) -> object:
                raw = owner._store.package_file_bytes(
                    *manifest.identity,
                    PACKAGE_RUNTIME_PATH,
                    maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
                )
                return yaml.load(raw, Loader=_BoundedRuntimeLoader) if raw else {}

            previous = runtime(prior)
            if isinstance(previous, dict) and isinstance(previous.get("tools"), dict):
                previous = {
                    **previous,
                    "tools": {
                        name: body
                        for name, body in previous["tools"].items()
                        if name not in retired
                    },
                }
            _removed_keys(previous, runtime(incoming), f"{package_id}.runtime", report)
    for package_id in new.keys() - old.keys():
        report(package_id, "Adding a package requires an explicit ownership migration.")
    for participant in snapshot.participants:
        manifest = old.get(participant.package_id)
        if manifest is None or not owner._store.mechanic_mutations_are_owned(
            campaign_id=campaign_id,
            package_id=manifest.package_id,
            package_version=manifest.version,
            package_sha256=manifest.content_sha256,
            outcome=MechanicOutcome(
                result={},
                participant_mutations=(
                    ParticipantMutation(
                        participant.participant_id,
                        participant.data,
                    ),
                ),
            ),
        ):
            report(
                f"characters.{participant.participant_id}",
                "Existing character ownership or saved data requires explicit adaptation.",
            )
    accepted = owner._store.accepted_campaign_plan(campaign_id)
    plan = accepted.content.get("campaign_plan") if accepted is not None else None
    if isinstance(plan, Mapping):
        bindings = plan.get("bindings")
        binding = bindings.get("campaign_setting") if isinstance(bindings, Mapping) else None
        setting_id = binding.get("id") if isinstance(binding, Mapping) else None
        selected = next(
            (
                e
                for e in campaign_setting_entries(candidate.package_lock)
                if e.package_id == setting_id
            ),
            None,
        )
        try:
            owner._campaign_secret_catalog(campaign_id, plan)
            facts = (
                ()
                if selected is None
                else owner._exact_setting_knowledge(
                    (selected.package_id, selected.version, selected.sha256),
                )[0]
            )
            owner._campaign_secret_catalog(campaign_id, plan, setting_facts=facts)
        except CommandRejected as error:
            report(
                "campaign.knowledge",
                f"Accepted Plan or retained adventure knowledge conflicts: {error}",
            )
    # Validate current values and references without recalculating creation-time outputs.
    for manifest in new.values():
        mechanics = [
            {
                "namespace": manifest.package_id,
                "key": declaration.key,
                "value": snapshot.state[f"{manifest.package_id}.{declaration.key}"],
            }
            for declaration in manifest.state_schemas
            if f"{manifest.package_id}.{declaration.key}" in snapshot.state
        ]
        try:
            validate_starting_declarations(
                {"initial_state": {"mechanics": mechanics}},
                manifest.package_id,
                manifest.version,
                manifest.state_schemas,
            )
        except ContentShapeError as error:
            for diagnostic in error.diagnostics:
                report(
                    f"{manifest.package_id}:{diagnostic.path}",
                    diagnostic.message.replace("Starting field", "Saved field"),
                )
    review_notice = ""
    if any(c.get("changed_input_choices") or c.get("changed_model_guidance") for c in tool_changes):
        review_notice += "Action input guidance is updated. "
    if any(c.get("retired_unavailable_actions") for c in tool_changes):
        review_notice += "Actions that always refused are removed. "
    result = {
        "campaign_id": campaign_id,
        "campaign_name": snapshot.display_name,
        "candidate_id": candidate_id,
        "expected_revision": snapshot.revision,
        "expected_lock_id": current.lock_id,
        "current_label": _label(old),
        "selected_label": _label(new),
        "closure_sha256": candidate.closure_sha256,
        "changes": changes,
        "tool_changes": tool_changes,
        "added_panels": added_panels,
        "conflicts": conflicts,
        "compatible": not conflicts,
        "recovery": review_notice
        + "Previous versions stay retained. Returning to one requires another "
        "compatibility preview and keeps your current progress.",
    }
    result["confirmation"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return owned_payload(result)


def preview(owner: AuthorityOwner, campaign_id: str, candidate_id: str) -> SafePayload:
    with owner._operation():
        owner._reserve_campaign_transition(campaign_id)
        try:
            with owner._lane(campaign_id):
                return _preview(owner, campaign_id, candidate_id)
        finally:
            owner._release_campaign_transition(campaign_id)


def adopt(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    candidate_id: str,
    activation_id: str,
    expected_revision: int,
    expected_lock_id: str,
    confirmation: str,
) -> ActivationResult:
    """Revalidate the exact reviewed selection inside the existing activation reservation."""
    if len(confirmation) != 64 or any(c not in "0123456789abcdef" for c in confirmation):
        raise CommandRejected("Preview the selected rules update before adopting it.")
    command = ActivationCommand(
        campaign_id,
        candidate_id,
        activation_id,
        expected_revision,
        expected_lock_id,
        f"compatible-update-{confirmation}",
    )

    def preserve(state: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        reviewed = _preview(owner, campaign_id, candidate_id)
        if not reviewed["compatible"]:
            raise CommandRejected(
                "This update has conflicts. Preview again; no rules changed.",
                code="rules_update_conflict",
            )
        if reviewed["confirmation"] != confirmation:
            raise CommandRejected(
                "The campaign changed. Preview the update again.", code="rules_update_stale_preview"
            )
        return state

    return owner._activate_package(command, preserve, compatible_update=True)
