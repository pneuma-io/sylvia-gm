# SylviaBench

Status: planning only, PL-28. **Bottom priority**, as selected by Daniel. No benchmark/model run or dataset collection is delivered here.

## User outcome

Compare how reliably models operate Sylvia: use supported tools, maintain/retrieve campaign state, respect deterministic outcomes and player choices, recover from failures, and handle progression. Results should help users understand supported model/Ruleset combinations and inform existing recommendation tiers.

Do not turn this into a universal best-GM score, literary-prose judgement or claim about emotional impact/enjoyment. Factual consistency and respect for intent can be measured within explicit scenarios.

## Current baseline

Sylvia has deterministic mechanics/journey tests, model-quality evidence and a journey-performance script. Those are useful inputs, not a calibrated controlled benchmark of models operating Sylvia. Completed MQ-01 comparisons remain historical and need not be reopened or recast as comparable rankings.

## Proposed benchmark boundary

- Start with a bounded synthetic suite for the bundled SRD package, pinned to an exact host/package/scenario revision. Identify selected player profiles and intended choices. Add another Ruleset only when a supported suite exists; SRD success does not imply competence elsewhere.
- Define observable outcomes and scoring for tools, state/retrieval, committed mechanics, intent, recovery and progression. Separate model errors from engine defects, provider failures, invalid fixtures and unsupported operations.
- Record model/provider identity, configuration, relevant context/tool contract, scenario/version, runtime conditions and repeated-run policy. State uncertainty and failure categories rather than hiding variation in one number.
- Calibrate tasks and graders against known synthetic success/failure cases and human review before mapping scores to recommendation tiers. Preserve the existing option to use an unrated or below-recommendation model.
- Reuse existing owning tests/evidence where they establish the right fact. Do not duplicate every spell test, reopen completed model-quality incidents or use historical differently configured runs as controlled comparisons.
- Select authorized models, run budget, repetition and local/remote destinations before any inference call. No purchases, subscription, gameplay contributions, personal campaigns, external uploads or private Full Audit publication are authorized here.
- Retain sufficient safe provenance for reproducibility and a concise result/limits report. Keep credentials and private inputs out of public fixtures/logs; avoid collecting raw personal gameplay as benchmark data.

## Acceptance and future tests

1. Run a tiny synthetic calibration set with known correct/incorrect actions and confirm the graders and failure categories distinguish them.
2. Execute the selected bounded suite under recorded model/provider/configuration identities and a fixed supported host/Ruleset; report any nondeterminism or incomplete runs.
3. Reproduce a recorded result from its permitted scenario/configuration/lineage, and show that changed Ruleset or context versions are not silently pooled.
4. Verify attribution of engine/provider/fixture/model failures and check that a high score does not claim unsupported Ruleset competence or prose quality.
5. Review any proposed score-to-tier mapping after calibration, preserve unrated-model choice and publish only approved synthetic/report material. No live model calls occur in this draft.

## Decisions and sequencing

Keep this at the bottom of the selected queue. Select a small initial scenario/profile set, scoring/graders, model access and explicit run budget before implementation/execution. Benchmark design does not authorize unrestricted paid or remote testing.

## Full specification and implementation boundary

Planning references: PL-28 — `05b2925d-f587-4e36-bc2d-f25ec220be71`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
