# Riichi Mahjong

Status: implementation selected on 19 October 2026. Four seats, GM-controlled opponents, per-match East-only/East–South selection and one complete table panel are confirmed. The declared engine now implements legal actions, scoring and complete hand/match progression. Native UI acceptance and the remaining profile checks below keep this PR in draft.

## User outcome

Play Riichi Mahjong in Sylvia with a readable table alongside ordinary GM narration and roleplaying. The Ruleset owns legal actions, state, scoring and match progression. Normal in-game/gameplay and out-of-game/OOC transitions still apply. There is no special adventuring mechanics layer around Mahjong.

The feature is called **Riichi Mahjong**. A clear table and coherent game loop are the aim; a commercial Mahjong client's feature set, effects, accounts or visual assets are not requirements.

Each match has one player and three NPC opponents, each making game decisions in a separate actor context. The GM keeps its continuous narration/campaign context. The ordinary `start_game` tool selects East-only or East–South for that match. One Game Panel contains the entire table, including all four seats and shared match state.

The Ruleset declares each actor's permitted observations, legal actions and relevant play/character instructions through a reusable mechanism. NPC contexts use the configured provider/model and remain isolated from other NPCs and GM narration, including native thinking, tool history and saved continuation. The engine alone validates and commits proposals. Same-actor context can continue when valid; state revisions, seat identity and package identity constrain resume and stale proposals. Validated public results return to the GM without replacing its campaign context. No additional credentials, parallel decisions, management UI or general autonomous-agent platform are selected.

The implementation now uses the explicit `isolated_participants.v1` contract. A synthetic hidden-token Ruleset exercises three NPCs, successive same-actor decisions, a human turn and return to the same NPC through production Anthropic, chat, Responses and Gemini request formatters. Focused checks also cover retained GM history, private inspection, public-result reconstruction, save/reopen, interruption, stale and illegal proposals, model changes and owned compaction notes. Native desktop acceptance remains separate.

## Implemented shared display

The new `piece_table` renderer presents Ruleset-declared play areas, groups, pieces and status values through the existing Game Panel host. Piece controls reference ordinary admitted panel actions. A `piece` selection binding supplies the clicked opaque identity only after the owner verifies that the current permitted piece advertises that action. Availability is checked per piece, including in the ordinary queued-action flow. The owner projects permitted pieces, retains the viewer's identity independently of whose turn it is, and removes the original source from player-facing table output. A synthetic Signal console demonstrates reuse without another game implementation.

The declaration format is documented in [content formats](../docs/reference/content-formats.md#grouped-piece-tables). Focused checks cover concealed-piece isolation, malformed data, literal and state sources, save/reopen, existing layouts and actual child HTML startup in a browser fixture. Browser fixtures do not establish native desktop acceptance.

## Existing baseline

Sylvia has generic Ruleset/package, authoritative command, result/projection and panel-host paths. Those are foundations to inspect and reuse, not evidence that Mahjong is already implemented. Existing tactical panels do not establish a complete reusable tile/table display. The three historical tasks describe related mechanics, reusable presentation and authored content; this is one coherent feature proposal rather than three duplicate Mahjong PRs.

## Proposed scope and boundaries

- Use the selected Mahjong Soul-like four-player profile, including one red five per suit. Document variant boundaries and verify subtle payments and progression before acceptance. No third-party code, rules prose or art is imported.
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

## Selected profile and remaining verification

The selected profile uses open tanyao and atozuke, forbids kuikae, requires one yaku, uses no kiriage mangan, double-wind pair 4 fu, chiitoitsu 25 fu, kazoe yakuman and the selected double yakuman variants. Triple ron pays every winner; kokushi can rob a concealed kan. Pao applies only to the liable yakuman component.

Matches start at 25,000 with a 30,000 target, end below zero, allow one extension wind, repeat the dealer on a win or tenpai, and end when the final dealer leads at target. Ties follow original seat order. This follows the selected familiar ranked profile, without a claim of Tenhou identity or Mahjong Soul feature parity. The tournament PDF describes different competition settings and does not determine these defaults.

The [official ranked rules](https://mahjongsoul.com/news/46) establish nearest-winner honba/deposit priority, dealer continuation in multi-ron, pao honba responsibility, unchanged-wait riichi kans, concealed-kan robbery by kokushi, and indicator timing. A previous delayed indicator reveals before the next kan declaration/robbery window; a robbed added kan creates no new indicator. Concealed-kan indicators appear before kokushi responses. These are rule-derived sequences; the rare consecutive-kan/chankan case has not been observed in the actual client during this work.

Nagashi replaces tenpai payments, keeps deposits and uses dealer tenpai for continuation. The player's own calls remain eligible, supported by the secondary [Mahjong Soul guide](https://nekomimi.ws/~nikukyu/jantama/jt_yakumain.html) and [Game8 conditions](https://game8.jp/jantama/661221); all discards must remain unclaimed terminals/honors. This secondary basis is distinct from the official settlement rule. Prior concealed dragon/wind kan counting for pao is still being investigated; no unsupported change is selected.

The latest selection removes any requirement for a separate adventuring layer. The three complete specifications and all retained source versions were retrieved from the authorized private archive before implementation. The archive is kept outside this public repository.

## Full specification and implementation boundary

Planning references: PL-13 — `bfade435-b977-4436-aefa-732e34744dd1`; PL-14 — `be27d182-07b3-4154-b4b7-08458341a085`; PL-15 — `190951c3-1d82-41a1-bd83-13e7ebfcbc96`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR now includes a bounded declarative package, scoring and hand/match progression, private-state publication, current-generation legal actions, per-seat views and save/reopen checks. Normal-start fixtures complete both match formats through one extension. Native acceptance and current-game rule verification are still in progress. No merge or release is selected. No GitHub Actions before November 2026.
