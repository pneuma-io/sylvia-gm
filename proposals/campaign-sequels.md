# Campaign sequels

Status: selected planning scope, PL-24. The feature is not implemented by this draft.

## User outcome

Explicitly append more authored acts or a subsequent adventure to the same campaign. Continue with its characters, world and history rather than starting an unrelated campaign or replacing what has already happened. Preserve participants, unresolved consequences and the exact sources of both the played material and the appended adventure.

## Current baseline and actual missing outcome

At current main, [the campaign-ending handler](../src/sylvia_gm/application/authority.py) offers "Continue adventuring in this world" only when the ending permits open-world continuation. [Existing progress tests](../tests/test_fc_campaign_progress.py) describe retention of the ending choice and progress on reopen. They were inspected, not rerun here.

That freeform continuation is already supported; it does not append a new authored Plan. [Plan admission](../src/sylvia_gm/infrastructure/sqlite_authority.py) requires Builder authority, while [the GM loader](../src/sylvia_gm/interfaces/owner_process.py) loads the originally admitted Plan/handoff. Standalone Builder can publish a revised reusable library Plan without changing an existing live campaign. The missing outcome is an app-supported append/admission operation for that campaign, not another freeform Continue button or a republish command.

## Proposed behavior and boundaries

- Select the target campaign and a compatible authored addition, then preview new acts, source identities, dependencies and conflicts before explicit adoption. Cancellation or a failed preflight changes nothing.
- Preserve played acts and their order/receipts, history, participant identities, world state, inventory/resources and unresolved consequences. Retain exact original and new source bindings. Do not rewrite the original reusable library Plan or another campaign.
- Detect duplicate act/object identifiers, conflicting cast/world assumptions, missing dependencies and incompatible versions. Reject with a useful explanation or require an explicitly reviewed mapping; do not silently renumber or overwrite authoritative past facts.
- Account for any epilogue, narrated after-story or future already established by the first ending. Present conflicts for a deliberate choice instead of treating a sequel as permission to erase that future or undo played events.
- Add the new authored sequence through the owning application transaction with stale-revision and once-only/retry protection. Preserve save/reopen, backup/export and recovery of the expanded sequence and its source closure.
- Reuse [PL-01/#5](https://github.com/pneuma-io/sylvia-gm/pull/5) content-preview/adoption boundaries where applicable. Appending an adventure must not silently update a Ruleset, change mechanical state, replace the Setting or migrate unrelated packages.
- Keep the existing open-world continuation option and ordinary GM/OOC behavior. This scope does not authorize broad story rewriting, automatic new-adventure generation or arbitrary edits to past acts.

## Acceptance and future tests

1. Complete a short disposable campaign, append a compatible second authored adventure through the app, enter its first act and continue play. Verify all original played state/history and the new ordered sequence.
2. Reopen and export/recover into a clean disposable profile. Assert exact original/new source bindings, participants, unresolved consequences and appended progress survive.
3. Cancel the preview and reject stale revisions, duplicate IDs, incompatible packages, missing closure and contradictory epilogues atomically. Repeated application/retry must not duplicate acts or spend/change mechanics.
4. Compare the original library Plan and an independent campaign before/after; their bytes/state remain unchanged.
5. Check the existing freeform Continue/Finish behavior still works without claiming it delivers sequel append. Use the owning progress/Plan/adoption tests and an affected installed journey; no whole-Ruleset recertification is implied.

## Decisions for implementation

Settle the append entry point, compatible identity/conflict rules and how already narrated futures are presented. Use the complete PL-24 requirements and PL-01 boundaries before choosing storage or migration changes. The baseline findings are source evidence, not fresh installed acceptance.

## Full specification and implementation boundary

Planning references: PL-24 — `cf2cb4f9-1e13-4faf-aaba-2907302c288d`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
