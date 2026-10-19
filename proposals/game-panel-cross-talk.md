# Game Panel cross-talk and hardening

Status: selected planning scope. This draft proposes bounded SRD panel refinement, not a wholesale redesign or a runtime implementation.

## User outcome

Expand a character card in Party to inspect the full permitted information available in the Character panel. Selecting a character in Party or another relevant panel should update a consistent shared selected character, so users do not repeatedly choose the same person in independent selectors.

Keep UI selection distinct from the acting character, current combat turn and action target. Browsing another character must not grant control, change turn authority, rewrite a queued action or dispatch an action for the wrong actor.

## Current source baseline and actual overlap

At inspected main, [the frontend](../src-tauri/frontend/app.js) already has:

- `renderPartyPanel` with vitals, resources, conditions, expandable Details/Available actions, and a Party Select control when a player-controlled actor has a matching map.
- `renderCharacterPanel` with another character selector plus class/level, ready equipment, weapon-set details and aptitude/skill groups not all present in the Party card.
- `synchronizePanelActor` and map/network selection paths sharing some actor identity across Character, inventory, network and map surfaces. Character falls back through panel/map/focused-actor state.
- An encounter display with its own committed current-turn information, and action/queue paths that require preserved actor/target bindings.

Therefore neither expandable Party cards nor all cross-panel sharing should be rebuilt from scratch. The actual refinement is full permitted detail parity, fewer redundant controls, and consistent selection/lifecycle semantics around the existing partial sharing. The current Party Select handler also requests a target reset; review that behavior explicitly rather than assuming every reset is a defect or retaining it silently.

These are source observations, not a fresh installed reproduction or proof that every selection edge case is broken.

## Proposed consolidation and boundaries

1. Inventory the current SRD Party, Character, inventory/equipment, map and encounter information/actions. Identify exact duplicates and distinct uses before removing anything. Consolidate repeated character vitals/details and redundant selection affordances where that improves the journey; do not remove the standalone Character panel without examining inspection/detached-panel needs.
2. Reuse a shared permitted character-detail presentation/data boundary so an expanded Party card exposes what the Character panel would show for that same visible character. Preserve complete relevant groups, truthful missing-data states, action availability and hidden-information boundaries. Do not create a competing mechanically authoritative character cache.
3. Give the campaign/session UI one explicit selected-character identity and propagate it to relevant panels through their existing bindings. Panels opened later adopt that selection where valid. A panel that cannot display the selected entity explains its limitation rather than silently selecting someone else.
4. Preserve separate meanings for UI inspection selection, the actor authorized to take an action, committed current turn, and each action's target. Inspecting a non-controlled character must not grant control or expose GM-only data. Actor-specific commands retain their explicit owning validation, including turn/reaction permissions. Display selection, expansion and arrangement do not execute actions, spend resources or generate narration.
5. Define visible behavior for actor/target changes during a preview, draft or queued action. Retain an existing bound action, or visibly invalidate/reconfirm it as needed; never silently retarget or re-author it from a later panel selection.
6. Handle panel open/close/detach, session/campaign/Ruleset changes, late projections and roster removals/additions. Clear or replace invalid selection deliberately and consistently. Do not persist a stale entity into a different campaign or create feedback loops/duplicate subscriptions between windows.
7. Preserve layout/panel preferences and define selected-character/expanded-card persistence on reopen where useful. Add keyboard-accessible selection/expansion, clear selected/current-turn/target labels and appropriate accessible announcements.

No wholesale panel/UI framework redesign, new combat-authority model, package migration or unrelated feature work is selected. Reuse existing shared selection and owner paths; do not create duplicate selectors merely to mirror every panel.

## Acceptance and future tests

- Compare expanded Party and Character details for the same permitted SRD character, including class/level, vitals, conditions, resources, equipment and declared skill/aptitude information where available. Check unavailable data honestly.
- Select a character in Party and each selected relevant panel. Verify consistent views without moving turn authority or changing another action's actor/target. Include inspection of a visible but non-controlled character.
- Open/close/detach panels, change rosters and switch/reopen campaigns during projection updates. Verify late responses and stale selections cannot affect the wrong campaign.
- Queue or preview an action, browse another character and then confirm/cancel/retry it. Assert no lost action, unauthorized actor change, silent retargeting, duplicate dispatch or wrong-character execution.
- Complete selection and expansion using keyboard/assistive controls, preserve intended focus and announce distinct UI selection/current-turn/target states.
- Review the actual rendered SRD panel journey after implementation at normal and narrow sizes, including detached windows, readable detail/selected/acting/target cues and screenshots. Use focused renderer/selection tests plus an affected native panel/action journey; do not claim live acceptance from this source inspection or rerun unrelated mechanics suites.

## Full specification and implementation boundary

The coordinator owns Desk work item `dd228a98-0be5-406c-8057-7f4117894616` (retrieved at revision 1) and its complete specification, including the links to earlier completed panel work. Retrieve the complete current specification and mandatory references before implementation. This public proposal is not a replacement handoff.

Reuse completed shared-panel overhaul `17d55f54-ba8c-4ef3-b1df-c6f3912ad620` and TDH-04 `3e6b5811-9320-4e56-921e-0d95c9b5ee05`; do not reopen their acceptance. Preserve the separate frame simplification/Party-ordering work in PES-133 `f660f910-4d89-437e-9d40-a5cd40aa8f4a` and PES-134 `1e966982-fd4c-4d65-971f-a1c445277b69`. PL-14's generic declarations/Riichi dependency is related infrastructure, not this SRD redundancy follow-on.

This PR changes one proposal document only. No runtime implementation, third-party assets, configuration, installation, security change, merge or release is delivered. No GitHub Actions before November 2026.
