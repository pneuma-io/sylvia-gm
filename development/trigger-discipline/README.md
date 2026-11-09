# Trigger Discipline integration

This directory contains the Trigger Discipline compiler, canonical rules notes and retained
mechanics integrated through [Sylvia #6](https://github.com/pneuma-io/sylvia-gm/pull/6).
Version 0.1.29 is the unbalanced integration baseline. Merge, balance qualification and release
remain separate steps.

## Build and import

From a clean committed Sylvia checkout:

```powershell
uv run --locked --group dev --extra dev python scripts/build_trigger_discipline_package.py --output ../trigger-discipline-0.1.29.sylvia-gm-package
```

The command runs the normal compiler and package admission, then writes an importable standalone
Ruleset and a JSON identity receipt. The receipt names the source commit, package content,
runtime and parameter hashes. An existing output is never overwritten. `--allow-worktree`
explicitly labels a development build.

Import the archive through Settings / Packages. A compatible Campaign Setting and Campaign Plan
remain separate content; Siren composition belongs in [#2](https://github.com/pneuma-io/sylvia-gm/pull/2).
The package's conformance scene is synthetic test data, not an approved starting party or adventure.
The current Alpha defaults are unchanged.

## Mechanics baseline

The retained implementation includes firearm and chamber state, authored manipulation costs,
movement and physical cover, grid-edge wall editing, shooter-origin Overwatch, and supported
specialty hooks. Preview and execution use the shared spatial owner. Existing saved packages
retain their exact pins; a new lane declaration does not reinterpret an older fixed zone.

No numerical balancing is selected by this integration. Lane dimensions are explicit inputs;
test extents and widths do not establish class allowances or accuracy penalties. The runtime
requires a positive authored cycling cost; the balance lab's separate hard-coded cost is not
adopted here. Consult the generated `support/gameplay-resolution.json` for deterministic,
hybrid and unsupported boundaries. Test-only specialty counterfactual tools are excluded from
ordinary packages.

The owner approved public source visibility on 19 October 2026. The upstream repository remains private and unchanged; no private commit history, generated package archives, editor state, binary assets or captured installed-test material is imported.

## Source and continuation

The original preservation imported 319 selected files from upstream commit
`95d95c435f6cccd399a862ee44735d77ab07ed89`, with inspected upstream main basis
`e89a428b9d539689ff8f32e9171810105202cc41`. `SOURCE-MANIFEST.json` records that historical
import, including original paths, Git blobs and hashes. Later implementation is recorded in
Sylvia's Git history; the original manifest does not describe the current compiler bytes.

Use the current Sylvia host and the live inclusion proposal to select later work. Preserved upstream notes may mention older hosts, private checkouts or historical acceptance; those statements are provenance, not current instructions or new qualification. The source's private-vault wording is retained as historical text.

The 9 November owner selection authorizes completing and checking this integration before
balancing, with merge held. Retained Overwatch and runtime changes are reconciled with the
current host. Source and browser checks do not establish installed-app or genuine-provider
acceptance; current results and remaining qualification belong in the active completion plan.

## Limits and notices

Read [licensing status](LICENSING-STATUS.md) before reuse. Source visibility does not change existing source ownership or proprietary metadata, and this snapshot is not silently relicensed under Sylvia's root terms.

The credential-named canonical note `Hacking/CREDENTIAL.md` was outside the approved source-read route and remains excluded; this is not a claim it contains a real credential. One engineering guide with a machine-local source locator was also excluded. The complete source closure is therefore not claimed. The manifest lists these gaps and other excluded categories.

The original preservation commit implemented no mechanics and released no package. The
integration extends that branch's history without rewriting the upstream repository.
