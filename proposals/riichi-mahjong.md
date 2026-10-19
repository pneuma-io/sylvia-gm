# Riichi Mahjong

Status: selected planning scope combining PL-13, PL-14 and the applicable PL-15 content boundary. This draft does not implement a game.

## User outcome

Play Riichi Mahjong in Sylvia with a readable table alongside ordinary GM narration and roleplaying. The Ruleset owns legal actions, state, scoring and match progression. Normal in-game/gameplay and out-of-game/OOC transitions still apply. There is no special adventuring mechanics layer around Mahjong.

The feature is called **Riichi Mahjong**. A clear table and coherent game loop are the aim; a commercial Mahjong client's feature set, effects, accounts or visual assets are not requirements.

## Current baseline

Sylvia has generic Ruleset/package, authoritative command, result/projection and panel-host paths. Those are foundations to inspect and reuse, not evidence that Mahjong is already implemented. Existing tactical panels do not establish a complete reusable tile/table display. The three historical tasks describe related mechanics, reusable presentation and authored content; this is one coherent feature proposal rather than three duplicate Mahjong PRs.

## Proposed scope and boundaries

- Select the supported Riichi rules profile and authoritative reference before implementing: player count, match structure, scoring, optional rules and who controls the other seats. Record licences for any adapted text, code or assets; do not import proprietary game material.
- Cover setup, turn and response actions, hand resolution, scoring and match progression for that selected profile. Ruleset/owner paths enforce legal actions and outcomes. Narration and UI do not maintain a competing score or authoritative game state.
- Declare a readable table: the player's hand, permitted opponent information, discards, exposed groups, seat/turn/round, scores and available actions. Keep concealed information out of unauthorized projections and public result cards.
- Use the existing panel host for placement, resizing, docking/detaching where supported, theme/layout persistence and accessible controls. Choose direct table controls for usefulness; not every game action must operate exclusively by clicking tiles.
- PL-14 supplies only the smallest reusable declaration/rendering primitives needed for a good table and representative reuse. No fixed Mahjong renderer hidden in core, universal UI framework or additional unrelated game implementation.
- Use normal gameplay/OOC mode transitions and the existing campaign session/action boundaries around matches. Preserve roleplaying continuity through committed match results, without inventing an adventurer stat system or a special RPG Ruleset layer.
- PL-15's older Setting/Plan ideas remain accessible as context. No unapproved copyrighted Setting, borrowed cast, third-party tile art or promised campaign collection is selected. Use original/synthetic demonstration content unless a particular authored scope and rights are approved.
- Multiplayer networking is separate. This proposal does not silently implement PL-03 or require paid external services.

## Acceptance and future tests

1. Complete a small match for the selected rules profile through normal supported actions. Assert legal/illegal responses, scoring, round progression and saved authoritative results at the owning boundary.
2. Exercise narration, ordinary OOC/gameplay transitions and return to the table without losing turn/state or replaying an action.
3. Verify each seat/view receives only permitted information, including results, reopen and diagnostics. Use disposable deterministic fixtures for hidden-hand checks.
4. Inspect the actual rendered table for readable state, selected actions, keyboard/accessibility behavior and panel layout persistence. Reopen a campaign mid-hand and after resolution.
5. Demonstrate the selected generic panel primitives with a minimal non-Mahjong synthetic declaration, without committing to another game. Reuse existing owner/package tests and representative composition.
6. Check package/content/notice boundaries and any selected original demonstration Plan. Structural validity alone does not establish enjoyable or complete gameplay.

## Decisions for implementation

Choose the exact rules profile, other-seat control and useful visual/action examples, then reconcile mechanics/display declarations and permitted demonstration content. Preserve the complete three specifications; the latest selection removes any requirement for a separate adventuring layer.

## Full specification and implementation boundary

Planning references: PL-13 — `bfade435-b977-4436-aefa-732e34744dd1`; PL-14 — `be27d182-07b3-4154-b4b7-08458341a085`; PL-15 — `190951c3-1d82-41a1-bd83-13e7ebfcbc96`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
