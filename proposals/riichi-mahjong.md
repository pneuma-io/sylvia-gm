# Riichi Mahjong

Status: implementation selected on 19 October 2026. The shared table foundation is implemented. Four seats, GM-controlled opponents, per-match East-only/East–South selection and one complete table panel are confirmed. The exact Riichi rules/scoring profile still needs an owner decision before game mechanics are implemented. This draft does not yet provide a playable Riichi game.

## User outcome

Play Riichi Mahjong in Sylvia with a readable table alongside ordinary GM narration and roleplaying. The Ruleset owns legal actions, state, scoring and match progression. Normal in-game/gameplay and out-of-game/OOC transitions still apply. There is no special adventuring mechanics layer around Mahjong.

The feature is called **Riichi Mahjong**. A clear table and coherent game loop are the aim; a commercial Mahjong client's feature set, effects, accounts or visual assets are not requirements.

Each match has one player and three GM-controlled opponents. The ordinary `start_game` tool must let the caller choose East-only or East–South for that match. One Game Panel contains the entire table, including all four seats and shared match state. These are confirmed product requirements; the shared display checkpoint does not yet implement the match-start tool or opponents.

## Implemented shared display

The new `piece_table` renderer presents Ruleset-declared play areas, groups, pieces and status values through the existing Game Panel host. Piece controls reference ordinary admitted panel actions. The owner projects permitted pieces, retains the viewer's identity independently of whose turn it is, and removes the original source from player-facing table output. A synthetic Signal console demonstrates reuse without another game implementation.

The declaration format is documented in [content formats](../docs/reference/content-formats.md#grouped-piece-tables). Focused checks cover concealed-piece isolation, malformed data, literal and state sources, save/reopen, existing layouts and actual child HTML startup in a browser fixture. Browser fixtures do not establish native desktop acceptance.

## Existing baseline

Sylvia has generic Ruleset/package, authoritative command, result/projection and panel-host paths. Those are foundations to inspect and reuse, not evidence that Mahjong is already implemented. Existing tactical panels do not establish a complete reusable tile/table display. The three historical tasks describe related mechanics, reusable presentation and authored content; this is one coherent feature proposal rather than three duplicate Mahjong PRs.

## Proposed scope and boundaries

- Select the exact Riichi rules/scoring profile, optional rules and authoritative reference before implementing mechanics. Preserve the confirmed four-seat control, match-format choice and complete-table panel requirements above. Record licences for any adapted text, code or assets; do not import proprietary game material.
- Cover setup, turn and response actions, hand resolution, scoring and match progression for that selected profile. Ruleset/owner paths enforce legal actions and outcomes. Narration and UI do not maintain a competing score or authoritative game state.
- Declare one readable panel containing the entire table: the player's hand, permitted opponent information, discards, exposed groups, seat/turn/round, scores and available actions. Keep concealed information out of unauthorized projections and public result cards.
- Use the existing panel host for placement, resizing, docking/detaching where supported, theme/layout persistence and accessible controls. Choose direct table controls for usefulness; not every game action must operate exclusively by clicking tiles.
- PL-14 supplies only the smallest reusable declaration/rendering primitives needed for a good table and representative reuse. No fixed Mahjong renderer hidden in core, universal UI framework or additional unrelated game implementation.
- Use normal gameplay/OOC mode transitions and the existing campaign session/action boundaries around matches. Preserve roleplaying continuity through committed match results, without inventing an adventurer stat system or a special RPG Ruleset layer.
- PL-15's older Setting/Plan ideas remain accessible as context. No unapproved copyrighted Setting, borrowed cast, third-party tile art or promised campaign collection is selected. Use original/synthetic demonstration content unless a particular authored scope and rights are approved.
- Multiplayer networking is separate. This proposal does not silently implement PL-03 or require paid external services.

## Acceptance and future tests

1. Start either East-only or East–South through the ordinary `start_game` tool and complete the selected match with one player and three GM-controlled opponents. Assert legal/illegal responses, scoring, round progression and saved authoritative results at the owning boundary.
2. Exercise narration, ordinary OOC/gameplay transitions and return to the table without losing turn/state or replaying an action.
3. Verify each seat/view receives only permitted information, including results, reopen and diagnostics. Use disposable deterministic fixtures for hidden-hand checks.
4. Inspect the actual rendered table for readable state, selected actions, keyboard/accessibility behavior and panel layout persistence. Reopen a campaign mid-hand and after resolution.
5. Demonstrate the selected generic panel primitives with a minimal non-Mahjong synthetic declaration, without committing to another game. Reuse existing owner/package tests and representative composition.
6. Check package/content/notice boundaries and any selected original demonstration Plan. Structural validity alone does not establish enjoyable or complete gameplay.

## Decisions still required

The remaining product dependency is the exact rules/scoring profile and its optional rules, including any profile-specific match-end conditions. The earlier Tenhou-style profile suggestion remains unapproved. It is not an implementation default or an owner selection. No third-party code, rules prose or art has been imported.

Daniel has confirmed four seats with one player and three GM-controlled opponents, East-only or East–South chosen per match through the ordinary `start_game` tool, and one panel containing the entire table. These choices supersede the corresponding open questions in the historical specifications. They do not select a scoring profile.

The latest selection removes any requirement for a separate adventuring layer. The three complete specifications and all retained source versions were retrieved from the authorized private archive before implementation. The archive is kept outside this public repository.

## Full specification and implementation boundary

Planning references: PL-13 — `bfade435-b977-4436-aefa-732e34744dd1`; PL-14 — `be27d182-07b3-4154-b4b7-08458341a085`; PL-15 — `190951c3-1d82-41a1-bd83-13e7ebfcbc96`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR includes the shared display foundation and its focused checks. Complete hand/match progression, scoring/legal-action proof, campaign gameplay/OOC integration, native normal-launch screenshots and Riichi save/reopen remain incomplete. No merge or release is selected. No GitHub Actions before November 2026.
