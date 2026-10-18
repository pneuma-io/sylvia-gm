# SRD Full development package

This draft brings the completed Full source back into application, package,
generator and test paths on top of main `1390371`. Alpha still selects SRD Lite.
Full is available only through the explicit development adapter
`sylvia_gm.application.srd_full_packages`; it is not registered in the shipped
bundle, campaign defaults, installer or upgrade path.

The package retains its original 40 content, runtime, reference and attribution
files. The primary identity is `wotc.srd.5.2.1` version `0.3.1`; the exact reference
identity is `wotc.srd.5.2.1.reference` version `5.2.1`. The 364-page reference
corpus is included. Its source identity remains
`8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`.
See `ATTRIBUTION.md` for the retained third-party terms.

The saved inventory has 339 spells: 59 labelled `implemented` and 280 labelled
`pending`. An implemented label means some executable mechanics were mapped;
it does not claim the complete spell. The source maps' `not_yet_mapped` entries
remain authoritative limits. This draft does not finish the pending spells.

The owning generators and their imported helpers live in `scripts/srd_full/`.
The namespace and repository-root calculations were adjusted so regeneration
cannot overwrite current main's Lite generators or package. The five entrypoints
are `build_srd_5_2_1_mechanics`, `build_srd_5_2_1_advancement`,
`build_srd_5_2_1_spells`, `build_srd_beast_forms` and `build_srd_non_beast_forms`.
Use module invocation, for example
`python -m scripts.srd_full.build_srd_5_2_1_spells`, in a disposable checkout
with the project's existing dependencies. Generation replaces Full outputs.
The mechanics generator also needs its explicitly selected source PDF; this
draft does not add or fetch a source PDF.

Run the focused current-core checks with:

```text
PYTHONPATH=src python -m pytest tests/srd_full/test_full_package_drafts.py
```

They check independent source reads, exact reference binding, retained notices,
complete reference page order and hashes, deterministic Format-4 materialization,
current generic archive admission and current runtime schema acceptance. They
do not establish playable Full sessions or full rules coverage.

The 78 historical regression/helper modules and five recovered source helpers
live under `tests/srd_full/`, with Full imports isolated from Lite. The focused
checks verify that local Full test module targets exist. They do not establish
complete fixture inputs or successful legacy collection. `RECOVERY-SOURCES.json`
in that directory records the recovered helpers' source hashes and adaptations.
The historical cases remain opt-in:
`python -m pytest tests/srd_full --srd-full-legacy`. Some need historical fixture
manifests, source ancestors or frontend assumptions that have not been qualified
against current main. Rebind those fixtures to approved local inputs before
opting in; do not use old absolute-path defaults as evidence of fixture ownership.
The revised fixture needs an approved historical Format-4 package through
`SRD_REVISED_CANDIDATE` and its matching launch roster through
`SRD_REVISED_ROSTER`. The defaults are the absent
`tests/fixtures/srd_full/baseline.sylvia-gm-package` and
`tests/fixtures/srd_full/revised-launch-65.json` inside the checkout; they do not
reach into a user's old worktrees. Those data inputs are not included or replaced
with newly generated evidence. The fixture reuses current main's shared
`tests/lite_fixture_archive.py` helper.

The affected callers are `test_srd_revised_foresight_touch.py`,
`test_srd_revised_information_expiry.py`,
`test_srd_revised_information_group_expiry.py`, and
`test_srd_revised_longstrider_touch.py`. Their required source modules are
`srd_revised_native_fixture`, `srd_revised_addition_classes`,
`test_srd_revised_foresight`, `test_srd_revised_information`, and the transitive
`srd_revised_foresight_classes`, all under `tests.srd_full`.

The default selection runs focused package and import-target checks only. No
passing result is claimed for historical collection or case execution.

Remaining work before activation or merge:

- Rebind historical fixture inputs and qualify the preserved regression cases
  against current main. Repair current-core integration differences without
  rolling back Alpha's fixes or weakening admission.
- Reproduce generated outputs in an isolated checkout with the existing pinned
  dependencies and an approved, hash-verified PDF. Reconcile any saved inventory,
  support and runtime claims before advertising coverage.
- Review supported mechanics through real campaign/session journeys, including
  package selection, state changes, result cards, persistence and recovery.
- Design and approve Full registration, distribution and migration separately.
  Existing Lite campaigns and shipped defaults stay unchanged in this draft.
- Select future incomplete mechanics explicitly before implementing them.

No CI workflow is added or changed. The draft commit carries `[skip actions]`;
skipped or pending checks are not passing tests. Re-run CI only when the current
project owner authorizes it after the October Actions hold.
