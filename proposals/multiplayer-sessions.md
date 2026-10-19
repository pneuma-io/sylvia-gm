# Multiplayer sessions

Status: planning draft only. WebUI delivery comes first; multiplayer follows. No networking implementation or additional worker starts from this proposal, and the four selected feature implementations retain their slots.

Desk work item: `ed0cd898-438a-4077-aeb5-6daf84232f78`, **PL-03: Multiplayer sessions** (complete description read at revision 6). The original unabridged PL-03 ticket was also read through authorized parent transport from the retained intake archive. Its source identity is SHA-256 `0e70930899ba8474f992f1579d403d7c77217b16ae3e2cddf3f3cf1284ec2bb0`. Retrieve the complete current Desk item and original mandatory references again before implementation; this proposal is not a replacement handoff. The coordinator owns the reciprocal PR link.

## Intended outcome and preserved original scope

Several players participate in one campaign with one authoritative campaign writer and one host retaining Out-Of-Character control. Sylvia can address one player or a selected subset, wait for the requested responses, and continue according to agreed response and host-override rules. Every exchange must not require every player to respond.

The original backlog's range of 2–6 player slots, selective prompts, waiting for all prompted players, host override, and player access to public chat plus their own character sheet remain design requirements to resolve. Confirm supported counts and precise visibility before implementation. The four-human Riichi and two-participant D&D acceptance journeys below are explicit future checks; they do not declare that a general six-player implementation already exists.

Multiplayer remains post-Alpha work. Human-GM assistance and mobile UI have separate tickets and are not prerequisites silently added to the first multiplayer journey. Original related items PL-02 and PL-05 retain their identities and dispositions.

## Delivery order and existing foundations

Deliver the [local WebUI outcome in PR #14](https://github.com/pneuma-io/sylvia-gm/pull/14) before multiplayer so players have a browser entry point. Its existing scope remains user-hosted and localhost by default, with any remote hosting/proxy/tunnel arranged explicitly by the user. This dependency does not configure managed hosting, public binding, a tunnel, accounts or firewall/security settings.

A browser UI alone does not establish multiplayer. PL-03 adds the joining/session identity, authenticated participant permissions, authoritative shared-state synchronization and private participant views. Select the host/client and remote-connection design before implementation; do not assume a hosted service, account system, peer-to-peer topology or remote desktop exposure. Planning can proceed while PR #14 remains a delivery dependency.

[PR #15, character inspection and Party details](https://github.com/pneuma-io/sylvia-gm/pull/15), provides a relevant panel foundation: inspection is separate from acting character, targets and mechanics. At inspected head `2d26974ca889b59fba552d126904bec1ff8eac79`, [`panel-workspace.js`](https://github.com/pneuma-io/sylvia-gm/blob/2d26974ca889b59fba552d126904bec1ff8eac79/src-tauri/frontend/panel-workspace.js#L311-L331) uses campaign/ruleset keys without participant identity in those keys. Its current single-user viewer-profile storage is not proof of future network isolation. Multiplayer persistence and transport must isolate each authenticated participant's presentation state, through a correctly scoped store or equivalent owning boundary. This observation is not a claim of a current cross-player leak.

[PR #10, the Riichi shared-table foundation](https://github.com/pneuma-io/sylvia-gm/pull/10), supplies a Ruleset-declared table/projection path. Its inspected head was `9748e09478c10b6b69a1ca025e8499992665b058`. Current selected Riichi scope remains one player plus three GM-controlled opponents. This multiplayer plan does not change that implementation, settle its unselected rules/scoring profile or claim four-human play is ready. Both foundations must be rechecked on their final integrated bytes; neither draft proves multiplayer acceptance.

## Shared game authority and private views

Keep one authoritative shared campaign/game state. Each authenticated participant/seat receives an allowed projection and its own instance of the Ruleset-declared panel. Reuse mechanics and panel declarations; do not give each window a separately authoritative game copy.

Bind viewer/seat identity and action ownership at the authoritative boundary. A client's asserted seat or selected character cannot grant permission to inspect another player's data or act for that seat. Define character ownership, host-versus-player actions and any host override before implementation. Enforce those choices before projection and execution, including queued actions and retries. Viewing or selecting content is not action authority.

For four-human Riichi, each participant receives their own concealed hand and permitted public table information. Other concealed hands have no transmitted tile identities. Discards, revealed melds and other rule-permitted public information remain visible. Remove concealed identities before transmission to unauthorized clients; visually hiding a full table payload is insufficient.

Apply the same visibility contract to initial snapshots, incremental updates, reconnect/save-reopen responses, errors, player-accessible logs and replay/export paths. GM-private preparation and another player's restricted character data must also remain inaccessible through the underlying interface. If spectators or replay views are later selected, they must preserve restrictions on still-private information; this proposal does not commission those additional features.

## Independent inspection, selection and panels

A shared D&D map does not make inspection, expanded cards or window arrangement global. Each participant can inspect permitted characters, choose view-only content, expand cards, and arrange/dock/pop out their panels independently. Synchronization between a participant's own windows must not overwrite another participant's inspection, selection, expansion or layout. Persist and restore those views under the authenticated participant's scope; reusing campaign/ruleset identity alone must not collapse viewers into one shared preference record.

Keep presentation state separate from shared acting character, committed targets, turn state and queued-action bindings. Inspection must not change who acts, change a target, submit an intention or run mechanics. Preserve explicit actor/target authorization and confirmation. Delayed previews, confirmations and reconnects must retain the correct captured bindings rather than adopting whichever character was inspected later.

## Response coordination and recovery

Design explicit addressed-player and response identities. Decide what constitutes a completed response, how edits are represented, and what happens when a prompted player leaves or fails to answer. Resolve the host's waiting/override behaviour with the owner. Duplicate, stale or late submissions and resumed connections must not advance a resolved action twice or bypass the requested response set.

Define what survives disconnect, reconnect and application restart, including pending responses and current game/view bindings. Revalidate identity/permissions on restored access and deliver only the participant's permitted state. Keep single-writer ownership and authoritative committed results; clients must not reconcile competing campaign copies into truth.

## Future acceptance

1. **Original coordinated-play journey:** Use the selected host/client arrangement with at least two distinct authenticated players. Prompt one player, then a subset requiring several responses. Verify agreed waiting, edits, missing/departed-player and host-override behaviour, one execution of the resolved action, and proper public/private visibility. Exercise duplicate and late responses plus reconnect/restart without double advancement.
2. **Four-client Riichi privacy:** Use four separate authenticated clients/seats. Inspect underlying payloads and client-accessible state as well as rendered panels. Each receives only its own concealed identities plus permitted public information; other concealed identities are absent from snapshots, deltas, reconnect responses, errors and player-visible logs. Exercise a legitimate reveal without widening unrelated visibility.
3. **Authorization failures:** Attempt another seat/view/action, stale bindings and invalid response identities. Reject without disclosing restricted values or mutating authoritative state. Test the real boundary rather than only disabling buttons. Confirm the agreed host permissions do not implicitly become every player's permissions.
4. **Two-client D&D independence:** Share one map while the participants inspect different characters, expand different cards and arrange/dock/pop out panels differently. Exercise same-viewer child synchronization and independent reconnect/restore. One participant's inspection/layout must not change the other's, shared actors/targets or mechanics; explicit actions retain their authorized bindings.
5. **All delivery paths:** Verify equivalent filtering and identity on snapshots, updates, save/reopen and every implemented player-facing log/export/replay surface. Test spectator/replay access only if separately selected; otherwise report it unimplemented. UI screenshots and an opaque table identifier alone do not prove private data stayed off the client.
6. **Actual application and claims:** When implementation is selected, test the agreed WebUI/owner transport with real distinct client sessions and synthetic campaign data, then the representative deployed/installed path actually claimed. Preserve normal single-player, real SRD Lite and panel behaviour. Record exact source/package/client bytes, commands, pass/fail/not-run results and remaining native/provider/hosting limits. No multiplayer behaviour is tested by this planning PR.

## Decisions before implementation

Set the smallest playable host/client arrangement, join/authentication mechanism, supported counts, character/seat ownership, projection contract, host override and response/recovery semantics. Keep package rules at their owning boundary and the core ruleset-neutral. Record approved interfaces and focused failure cases, then break the broad feature into bounded implementation work before dispatch. The current Alpha contracts do not become a networking specification merely because this plan exists.

This draft is based on `main` at `e618d4ff7998cd1f436e43c2a767609c9fe506e0` and adds one proposal document only. It starts no multiplayer or other implementation, imports no private archive, changes no existing branch/settings, and performs no build, network exposure, credentials, merge, tag or release. No GitHub Actions before November 2026. All feature acceptance remains open.
