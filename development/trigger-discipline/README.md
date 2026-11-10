# Trigger Discipline integration

This directory contains the public Trigger Discipline compiler, compiled contracts and retained
mechanics integrated through [Sylvia #6](https://github.com/pneuma-io/sylvia-gm/pull/6).
Version 0.1.29 is the unbalanced integration baseline. Merge, balance qualification and release
remain separate steps.

## Public build and import

From a clean committed Sylvia checkout:

```powershell
uv run --locked --group dev --extra dev python scripts/build_trigger_discipline_package.py --output ../trigger-discipline-0.1.29.sylvia-gm-package
```

The default build works without the private vault and omits every `references/canon/` file.
It uses `source/sylvia_ruleset/compiled-inputs.json` for retained specialty contracts,
eligibility and note path/hash identities. Raw note excerpts and canonical spans are excluded
from that data. Its parameter hash must match the current seed; regenerate the inputs after
intentional rule changes. The command runs normal compilation and admission, then writes a standalone
Ruleset and a JSON identity receipt. The receipt names the source commit, package content,
runtime and parameter hashes. An existing output is never overwritten. `--allow-worktree`
explicitly labels a development build. The identity receipt also names the distribution mode.
Only public builds may be committed to this repository or attached to a public release.

For private local play, supply the private vault separately and write outside this checkout:

```powershell
uv run --locked --group dev --extra dev python scripts/build_trigger_discipline_package.py --private --notes-directory D:/Private/trigger-discipline --output ../trigger-discipline-private.sylvia-gm-package
```

This recompiles note-derived contracts and includes the indexed canonical notes. Testing notes
and canvases are not packaged. Private packages have an explicit label and receipt; never
commit or attach them publicly. `--notes-directory` without `--private` regenerates contracts
from those notes while still omitting their text from the public package.

To refresh the retained public compiler data, run from `source/`, then review and commit it:

```powershell
python -m sylvia_ruleset compile-inputs --notes-directory D:/Private/trigger-discipline
```

Import the archive through Settings / Packages. A compatible Campaign Setting and Campaign Plan
remain separate content. Siren Protocol continues privately and is not included in public builds.
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

Daniel's 10 November 2026 decision supersedes the earlier vault visibility approval. The compiler
may remain public; Obsidian notes and canvases stay in the private `pneuma-io/trigger-discipline`
repository. PR6 must not merge before this privacy cleanup. PR28 precedes PR6, and the later
`app.js` refactor waits for both. No new GitHub Actions are selected; checks run locally.

## Public gameplay limits

The public GM and Builder can read the compiled guides under `sylvia_ruleset/references/`,
specialty eligibility, runtime contracts and support declarations. Canonical note paths in
the guides and provenance identify private sources; those files are unavailable in a public
build. The packaged character and campaign guides state that limitation.

Supported deterministic controls can execute against authored state. These compiled contracts
do not replace the full rules text, provide unresolved HP/RES/AP/equipment allocations, or prove
that the GM and Builder can run a complete session from the public package alone. That broader
public play boundary and the future public TTRPG Rulebook remain a product decision for Daniel.
No replacement rules text or invented defaults are introduced by this privacy separation.

## Source and continuation

The original preservation imported 319 selected files from upstream commit
`95d95c435f6cccd399a862ee44735d77ab07ed89`, with inspected upstream main basis
`e89a428b9d539689ff8f32e9171810105202cc41`. `SOURCE-MANIFEST.json` records that historical
import, including original paths, Git blobs and hashes. Removed vault entries are metadata only.
Existing historical exposures are not purged and no history is rewritten. Later implementation is recorded in
Sylvia's Git history; the original manifest does not describe the current compiler bytes.

Use the current Sylvia host and the live inclusion proposal to select later work. Historical
upstream notes and their indexed identities are provenance, not current instructions or new
qualification. The complete note bodies remain private.

The 9 November owner selection authorizes completing and checking this integration before
balancing, with merge held. Retained Overwatch and runtime changes are reconciled with the
current host. Source and browser checks do not establish installed-app or genuine-provider
acceptance; current results and remaining qualification belong in the active completion plan.
The 10 November goal additionally selects native Windows qualification with isolated synthetic
data; the PR records the exact tested candidate and remaining limits. Numerical balancing stays
separate. The newer Opus production port is tracked in [Sylvia #28](https://github.com/pneuma-io/sylvia-gm/pull/28);
the final combined frontend must match the approved mock-up and receive the later Opus pass.

## Limits and notices

Read [licensing status](LICENSING-STATUS.md) before reuse. Source visibility does not change existing source ownership or proprietary metadata, and this snapshot is not silently relicensed under Sylvia's root terms.

The credential-named canonical note `Hacking/CREDENTIAL.md` was outside the approved source-read route and remains excluded; this is not a claim it contains a real credential. One engineering guide with a machine-local source locator was also excluded. The complete source closure is therefore not claimed. The manifest lists these gaps and other excluded categories.

The original preservation commit implemented no mechanics and released no package. The
integration extends that branch's history without rewriting the upstream repository.
