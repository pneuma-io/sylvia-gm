# Siren Protocol — Sylvia Campaign Setting

Wan named this Setting **Siren Protocol** on 2026-09-23. It was previously called Harem Run.
The historical repository and internal character/equipment IDs remain provenance identifiers.
The Setting chooses no protagonist, opening mission, compulsory party or romance.

The 44 authored source files came from `pneuma-io/haremrun` at
`c7a9587b94f8e5125ea5818a9f708ec32d0bab01`. Before copying, each file was verified against
`upstream-source-manifest.json`. The port changes the visible Setting name and reconciles
implementation guidance. `source-manifest.json` identifies the resulting current files;
the upstream manifest identifies their historical originals. No images or generated outputs
are included in this adapter.

`scripts/build_siren_protocol_package.py` builds format 4: this Setting contains an exact
Trigger Discipline Ruleset snapshot supplied explicitly by the caller. It never downloads
or chooses a different Ruleset. The Setting has no runtime and grants no new mechanics.
Source construction and automated admission are distinct from installed acceptance.

The eleven authored Level-5 cast builds and their equipment remain editable templates.
Starting/max HP, RES, AP, full starting inventory, and numerical progression remain pending
Wan's decision (TDH-10/12). They are not complete Character Files and must not be described
as ready to play. Existing fiction, allocations and loadout references are preserved.

Use the contained Ruleset's `references/character-authoring.md` and
`support/gameplay-resolution.json`. In a Character Builder launched for a selected Setting,
`get_content_definition` exposes the contextual `character_creation` definition. A general Builder
instead has the `character_file` envelope and the exact participant declarations in
`inspect_campaign_sources` → `ruleset/identity.json`. Schema validity does not establish specialty
eligibility or a live mechanic. Test-only counterfactuals are not gameplay support.

The desktop's bundled Severed Lands collection does not include this separate TD-containing
archive. Local construction/import does not authorize publication or redistribution of TD.
