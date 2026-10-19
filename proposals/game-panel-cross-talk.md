# Game Panel cross-talk and hardening

Party cards now expose the same permitted details as the standalone Character view. One inspection selection drives Character and equipment views. Inspection never chooses the acting character, clears a target, rewrites a queued intention, or executes mechanics.

## Interaction

- Party offers an accessible Inspect button for every visible member, including members the player cannot control. Compact names, HP, conditions and resources remain readable. Details adds class/level, equipment, weapon-set information, readiness and declared aptitude/skill groups through the same renderer used by Character.
- Character remains independently arrangeable. Its redundant selector becomes Choose in Party, which reveals Party. Rulesets without a Party panel retain a shared accessible inspection picker. Inventory follows that selection and explains unavailable records without displaying another person.
- Map and Network retain explicit action-character controls. An explicit action-character choice also updates inspection; inspection cannot change the action character or target. Map labels distinguish the acting character. Encounter still displays authoritative turn ownership.
- Existing Party actions retain their declared sources and actor bindings. Character-only action names link to their owning panel, avoiding duplicate dispatchers in detached Party windows.

Daniel approved the layout on 19 October 2026: retain Character as an optional dockable view and allow several Party cards expanded at once. Details expands a card without changing inspection; Inspect selects the character shown by Character and equipment views. No whole-shell redesign or automatic layout reset is included.

## State and safety

Inspection and outer expansion use the existing panel host, native presentation sequence and saved campaign workspace. Ruleset defaults never copy another campaign's inspected identity. Roster removal clears inspection to an explicit unavailable state; reintroducing the same ID does not silently restore it. Campaign and Ruleset changes validate against their own admitted records. There is no additional engine authority or private campaign cache.

Docking, tabs, hide/show, detached windows and reopening use the existing host lifecycle. The renderer preserves disclosure state if a redraw races a pending browser toggle event, and restores nested stat-group focus within the same campaign/session. Children adopt the owner's current presentation and reject obsolete contexts/sequences.

Manual requests capture actor/target inputs before awaiting. A pending intention prevents replacement DOM or a moved child panel from starting another copy. Delayed previews and confirmations recheck campaign/session, declared action, revision, actor control, visible actor/target and map revision. The authoritative owner still validates permissions, resources, revision and idempotency. Queued intentions retain their original explicit bindings.

## Verification

Focused source coverage includes detail parity, noncontrolled inspection, equipment, removal/reappearance, Ruleset replacement, delayed previews/confirmation, captured queued actors, duplicate suppression, shared window transport and workspace persistence.

```powershell
node --test tests/frontend_character_selection.test.cjs tests/frontend_character_equipment.test.cjs tests/frontend_party_panel_actions.test.cjs tests/frontend_panel_host.test.cjs tests/frontend_panel_workspace.test.cjs
node --test tests/frontend_character_inspection.browser.cjs tests/frontend_panel_startup.browser.cjs tests/frontend_map_header.browser.cjs
```

Real SRD Lite owner checks exercise single spending, identical receipt replay, save/reopen, and unchanged state after stale, unauthorized and out-of-range requests:

```powershell
.venv/Scripts/python.exe -m pytest -s tests/test_lite_panel_combat.py -k "actual_spell_control_spends_once_and_reopens or stale_unauthorized_and_out_of_range_controls_mutate_nothing" --basetemp <owned-fixture-root>/attempts/<fresh-run>
```

Use the repository's current Lite fixture environment and archive rules. Native evidence is separate from component evidence. Testing uses disposable SRD campaigns, isolated WebView data and the packaged owner. Browser tests do not establish real-provider or physical-input acceptance.

Two network-preview assertions also fail on unchanged base e618d4ff7998cd1f436e43c2a767609c9fe506e0. They remain existing failures; their assertions were not weakened.

## Scope and integration

Desk work item dd228a98-0be5-406c-8057-7f4117894616 was read at revision 4. The complete specification and relevant unabridged original tickets were retrieved before implementation. Today's explicit implementation selection supersedes historical planning-only status.

This reuses the completed panel overhaul, TDH-04 lifecycle, PES-133 compact frames and PES-134 player-first ordering. Riichi's piece_table extension is independent: preserve its panelContent case and CSS block during integration. This branch adds no display renderer, package mechanics, credentials, security change, installer, merge or release. GitHub Actions remain deferred until November 2026.
