# Campaign sequels

Status: PL-24 implementation in PR #8. Native desktop acceptance remains open.

## Player outcome

The campaign GM menu offers **Add another adventure** after the current authored adventure
has earned its ending. Choose a saved or bundled Campaign Plan, inspect its preparation,
source bindings and identity mapping, describe its connection to the retained story, and
explicitly confirm the review before appending. **Author with Builder** opens an independent
draft containing selected campaign context for the player to review before sending.

The existing **Continue adventuring in this world** remains the freeform option. Publishing
a Plan in Builder still does not change a live campaign. Appending is an application-owned
operation with its own review receipt.

## Preserved campaign and sources

The append changes accepted preparation and the campaign revision. It does not initialize
characters, locations, resources, inventory, mechanics, knowledge or world facts from the new
source. The old Plan and its played progress, history, ending choice and unresolved preparation
remain available. The GM reads the new active Plan and can inspect earlier preparation through
`campaign_plan/adventures` and `adventures/N/structured/...` or `adventures/N/markdown`.

The existing and new Plans must use the same exact Setting, Ruleset and dependency closure.
Missing snapshots, incompatible fixed characters or new required companions fail preflight.
New story IDs receive a deterministic, visible mapping; world and participant identities stay
unchanged. Ambiguous overlapping act/objective identities and redeclared private facts require
author repair. The original reusable Plan and other campaigns are never rewritten.

Source starting assumptions and prose are shown for narrative review. The player must adapt
contradictory preparation in Builder before adoption. This does not attempt to determine prose
compatibility automatically. The supported connection follows all established events and any
already narrated future; it cannot rewind or erase them. Broader timeline placement remains a
product decision. The current eligibility guard requires a continuable ending; allowing a
reviewed authored sequel after an ending that disables freeform continuation is also pending
the owner's product decision.

## Transaction and recovery

Review captures the current Plan, exact source, campaign revision and latest GM message
sequence. Adoption revalidates them under the existing campaign work gate and commits source,
review evidence and revision together in one SQLite transaction. Active work, interrupted
replies and pending mechanics choices block adoption. Stale or changed reviews fail without
partial writes. Replaying an identical request returns its saved receipt, including after
reopen or recovery without the original library. Reusing the request ID with changed content
fails. Two competing append requests cannot both commit from one review basis.

The accepted Plan lineage and its source closures travel through existing save, backup and
campaign export/recovery. Earlier ending cards remain quiet history while the new adventure
has its own progress and completion gates. An early old ending does not complete unplayed
acts or require them to finish the sequel.

## Verification and limits

- `tests/test_campaign_sequels.py` exercises real saved Plan and package snapshots, retained
  state/history, unchanged library and other campaign, unresolved preparation, identity conflicts,
  stale narration, rollback, concurrent admission, interrupted work, export/recovery and backup.
- The app-owner transport journey uses controlled provider responses and the real GM read/write
  tools to preserve a narrated after-story, append through the new commands, enter the new act and
  reopen. This is source/transport evidence, not native UI acceptance.
- `tests/frontend_campaign_sequel.browser.cjs` exercises actual dialog code and wiring with an
  explicitly substituted desktop bridge: review, cancellation, conflicts and exact retry after
  uncertain acknowledgement. Component screenshots are kept privately.
- `tests/frontend_campaign_endings.test.cjs` covers old quiet ending cards beside the active
  adventure. Existing ending and early-ending regressions remain applicable.
- The unchanged pre-implementation Lite two-act Finish/Continue journey passed. The separate
  legacy Rift source-binding regression already failed at the original head and is outside this
  feature. No source content, rules package, spell roster or dependency migration is selected.
- The desktop and frozen owner compiled from implementation head `02e408a`. Twenty-four Rust
  unit/bridge checks passed, including the explicitly selected frozen-owner handshake/reopen test.
  The frozen owner discovered both source-validated SRD Lite QA adventures and enabled Full Audit
  in the disposable profile. This is engineering-candidate evidence, not installed release approval.

Ordinary desktop launch/input and the complete native disposable-campaign journey still require
the coordinated desktop slot. Browser and owner proofs do not close them. Formal release packaging
also requires the existing private identity-check input. No merge, release or GitHub Actions run is
included. The coordinator owns reconciliation of the complete PL-24, PL-01 and AR-30 Desk
requirements and the original archive entries; this implementation note is not their replacement.
