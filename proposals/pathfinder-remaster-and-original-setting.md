# Pathfinder Second Edition Remaster Ruleset and original Campaign Setting

Status: planning draft only. Implementation is queued behind the four currently selected work items; no implementation owner or new worker is assigned by this proposal.

Desk work item: `9e69fe1f-cb3d-4d73-a17e-a1ad295343c4`, **Plan Pathfinder Ruleset and Campaign Setting** (retrieved at revision 1). The coordinator owns its reciprocal PR link. Retrieve the complete current Desk specification and mandatory references before later implementation; this public proposal is not a replacement handoff.

## Intended outcome and current baseline

Add a supported Ruleset based on the selected Pathfinder Second Edition Remaster mechanics, together with an original Campaign Setting that declares compatible dependencies. Users should be able to select the agreed package pair, create a campaign, play the supported slice through Sylvia's normal game/out-of-game paths, and retain its state and exact content bindings.

Sylvia already has generic Ruleset/content admission, authoritative mechanics, saved campaigns and native game panels to reuse. Existing SRD Full, Trigger Discipline and Riichi work do not implement this Ruleset. The live PR inventory contained no Pathfinder proposal at planning time. This is a separate concrete outcome from the broader future-Rulesets discussion, PL-18 `cdc9c82a-e3f9-4654-b8f0-395e7647583b`.

**Setting theme and name are undecided and remain Daniel's choices.** Do not invent them, copy an existing Paizo setting or start creative implementation until those choices are made.

## Proposed source whitelist

Only the ORC-licensed mechanical portions of these exact Remaster sources are candidates:

| Source | Selected edition/year | Intake requirement |
| --- | --- | --- |
| Player Core | Remaster, 2023 | Exact printing/errata and notices |
| GM Core | Remaster, 2023 | Exact printing/errata and notices |
| Monster Core | Remaster, 2024 | Exact printing/errata and notices |
| Player Core 2 | Remaster, 2024 | Exact printing/errata and notices |

This whitelist is a source boundary, not a statement that every page is licensed or implemented. Do not silently mix First Edition, legacy/OGL Second Edition, later supplements, online aggregates or another printing into this scope.

Before adapting an item, record its title, edition/printing, errata/revision, authorized source identity, precise location, applicable notice/designation, adapted unit, exclusions and upstream attribution. Maintain immutable source/package identities and an item-level mapping from source to adapted rules. Use only authorized access; no source-book upload, whole-book copying or extraction is part of this planning PR.

## Rights, notices and naming boundary

The [final registered ORC licence](https://downloads.paizo.com/ORC_LicenseFINAL.pdf) ([drafter-hosted final copy](https://azoralaw.com/wp-content/uploads/2023/09/ORC-License.FINAL_.pdf)) distinguishes Licensed and Reserved Material and requires notices, upstream attribution and downstream rights in adapted rules. Keep those rules obligations separate from the application's existing licence; do not impose incompatible application restrictions on ORC rules content.

The [final ORC AxE, digital applications section](https://azoralaw.com/wp-content/uploads/2023/09/ORC-AxE.FINAL_.pdf), linked by the licence's drafter, explains digital use and does not require release of implementation source code. This does not remove the obligations on adapted licensed mechanics.

Exclude Paizo lore, Golarion, distinctive characters, setting names, art, logos, trade dress and the action-symbol font unless separately verified permission is deliberately selected. Use original setting names, lore and art; retain any necessary licensed mechanical terms only under their verified source boundary. Do not assume a right to copy another publisher's software or assets.

The [compatibility licence](https://downloads.paizo.com/paizo_compatibility_license_1.0.pdf) limits its logo/font grant to printed/electronic books. Do not assume it authorizes app branding. Review any eventual plain compatibility wording/product naming and avoid official-endorsement claims; this planning label is not a released product brand.

Include the applicable ORC licence and exact required notices/attributions with adapted rules and their selected distributions. Identify original setting material and application code separately without silently changing their terms. No legal agreement, logo/font use or licence change is accepted by this planning pass.

## Practical first implementation slice

Propose one playable vertical after source intake and owner selection:

1. Core check/outcome resolution with truthful declared support, a bounded action economy and selected conditions, implemented through ordinary Ruleset/owner paths.
2. A small explicit roster of supported character builds, equipment and creatures, verified against the selected sources. Choose and list the exact entries before implementation; no implied full class, spell, item or monster coverage.
3. Existing native projections/panels that expose the supported actions, resources, conditions and committed results with appropriate player/GM visibility.
4. A minimal coherent original Setting package, once its theme/name are selected, with explicit Ruleset/version dependencies and enough original demonstration content to exercise the agreed slice.

Keep Ruleset semantics in the package/shared owning boundaries. Add a generic engine primitive only for a demonstrated missing need; do not add Pathfinder-specific branches throughout the app or copy an SRD implementation and assume equivalence.

The eventual goal is declared coverage of the selected four-book mechanical scope, including applicable character/progression, encounter/action/condition, spell/equipment/creature and GM procedures. Maintain a coverage ledger distinguishing implemented, unsupported, unmapped and rights-excluded content. The first slice does not falsely deliver that complete goal.

## Future acceptance and tests

- **Provenance and notices:** Verify each included rule/roster entry against the exact edition manifest and notices, all required upstream creator credits, downstream ORC treatment and reserved-material exclusions. Check package/source/distribution notices; no unapproved book text, lore, icon/font, art or endorsement.
- **Deterministic mechanics:** Test supported outcomes and boundary cases, costs/action availability, conditions, invalid choices and once-only/retry behavior at their owning boundary. Use controlled fixtures and real committed state; reject unsupported cases clearly rather than inventing resolution.
- **Declared content:** Validate the selected character/equipment/creature roster and original Setting, including incompatible/missing dependencies and package tampering. Do not infer all four books from a few passing cases.
- **Native play:** In the actual selected Sylvia build, create a compatible campaign, exercise representative supported actions and results, move between game/out-of-game modes and return without unauthorized mechanical changes or repeated actions. Verify useful panels, actor/target bindings and visibility.
- **Saved work:** Save/reopen and export/recover the campaign with the same Ruleset/Setting versions, state and exact dependency closure. Leave other campaigns, library sources and shipped defaults unchanged.
- **Claims:** Record exact final bytes and what was actually tested. A valid package or deterministic fixture does not establish every mechanic, model narration or native journey. Include the original setting in working package acceptance, not merely a placeholder name.

## Decisions and source checks before implementation

Confirm the first roster/slice, Setting theme/name and final compatibility wording. Resolve ambiguous licensed/reserved portions item by item, without importing them first.

The final-text URL distinction is resolved. Paizo's final PDF uses `ORC_LicenseFINAL.pdf`, with no underscore between `License` and `FINAL`; it and the drafter-hosted final copy identify the final licence and registration `TX 9-307-067` in Sections III.a and III.e. The earlier [underscored Paizo URL](https://downloads.paizo.com/ORC_License_FINAL.pdf) serves interim text with registration placeholders and is not this proposal's final licence reference. The final licence and AxE linked above were readable.

Still verify each selected book's exact edition/printing, errata, legal notices, licensed/reserved designations and upstream attribution at intake. Identifying the final licence does not establish rights in every book item or accept a legal agreement.

Primary reference entry points remain [Paizo ORC](https://paizo.com/orclicense), [Paizo licences](https://paizo.com/licenses), the [drafter's final licence/AxE/registration page](https://azoralaw.com/orclicense/), the linked final licence/AxE and compatibility PDFs, and each selected book's exact legal notices. Do not rely on a search snippet or a generic site-wide licence claim in place of those notices.

## Scheduling and limits of this PR

Keep implementation behind the current four tracks. Do not edit their branches, run a new Pathfinder implementation worker, import books, build packages or displace active writers. The existing application licence and shipped selection remain unchanged.

This draft changes one proposal document only. No rules/book-text import, implementation, build, feature test, installation, legal agreement acceptance, merge, tag, release or security change occurs. No GitHub Actions before November 2026.
