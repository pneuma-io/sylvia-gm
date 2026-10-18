---
name: BUILD CAMPAIGN SETTING
description: Help the user create or revise a campaign setting.
---

# BUILD CAMPAIGN SETTING

Use this skill when a general Builder conversation turns into setting creation. Ask only the
questions needed to understand the desired genre, tone, scale, themes, and amount of detail.

Create ordinary persistent files describing the setting's useful facts, such as:

- places, cultures, factions, important people, history, and conflicts;
- tone, themes, boundaries, naming, and presentation conventions;
- what characters in the world commonly know;
- secrets or GM-only material when requested;
- hooks that campaign plans can use; and
- source attribution when external material was used.

Use the Ruleset in this chat's saved launch references. A Setting contains a snapshot (saved copy)
of that Ruleset; its source does not declare or depend on this world. Read the relevant Ruleset
guidance through `inspect_builder_references` before deciding how the world uses its mechanics.

Save the complete world under `setting-draft/files/`, using portable paths such as `world.md`,
`locations/harbor.md`, and `characters/guide.json`. Character data must follow the copied Ruleset's
declared schema. Keep attribution in the authored files. An existing content declaration file may
be supplied; otherwise publication creates a bounded index of these world files.

For required world-specific character fields, retrieve `get_content_definition` with
`name: setting_character_requirements` and write `setting-draft/files/character-requirements.json`.
Its schema applies to the Character File's separate `setting_data` object. Declare required fields
and allowed extensions explicitly. Ordinary lore and character prose remain free text. Mechanical
effects of world choices require an explicit edit to the contained Ruleset; initial calculations
there can read `setting_data`. Publishing rejects invalid requirements before the Setting is usable.

When the user requests a local rule change, read that file from the saved Ruleset references and
save its complete replacement or addition under `setting-draft/ruleset-files/` at the same relative path.
Publication changes only the new Setting's contained copy and validates the resulting Ruleset.
Existing Settings, Plans and the source Ruleset remain fixed. Adopting newer source changes is an
explicit editing request: retain local customizations and ask about conflicting intent.

For a requested source update, `inspect_library_sources` lists compatible installed candidates and
reads their exact files. Choose a concrete candidate ID; list order does not identify the newest
version. Compare that source with `inspect_builder_references` and the current replacement files
before editing. Reading another source leaves this chat's original references fixed. To adopt its
complete Ruleset copy and dependencies, write `setting-draft/ruleset-source.json` with exactly
`{"candidate_id": "<returned ID>", "expected_closure_sha256": "<returned closure hash>"}`.
The logical Ruleset ID must match the saved selection. Publication takes that complete source first,
then applies local rule-file edits. Reconcile those replacements with incoming changes before
publishing, retaining the user's custom intent.

For tool, character-schema, presentation or dependency changes, inspect the complete editable
record with `inspect_library_sources`, `action: record`, an exact `candidate_id`, and `path: ruleset`
or `dependencies/<package ID>` when you need a bounded preview. Use `copy_library_source` with
`source_type: editable_record` to copy that package prefix directly to
`setting-draft/package-records/<package ID>.json`, supplying the exact candidate and closure hash.
For runtime or other file changes, use `source_type: file` with an exact path from `files` and the
appropriate draft replacement path. Search the owned copy with `search_files`, read the relevant
section, then edit with its current file version. Reconcile any existing destination and supply
its observed version before replacing it. Keep `id`, `role` and `content_kind` fixed;
versions and declarations may change. File and package hashes are generated. Remove an edited
participant schema's old `schema_sha256` so it is regenerated too. Referenced dependencies must
exist in the selected copy; unchanged references follow local version edits, and unused support
packages are omitted. Keep runtime files, content declarations and records consistent. Invalid
references or rules prevent publication.

Use `check_campaign_setting_draft` with the intended package ID, version and title to check the
saved world files, character requirements and contained Ruleset edits. It uses the publication
validators and returns errors without saving a library entry, changing the draft or renaming the
chat. Correct the draft and check again as needed.

Once the intended world files are complete, call `publish_campaign_setting` with its package ID,
version and title. Report a selectable Setting only after the tool succeeds. The player can return
to any Campaign Interview, refresh its choices, and select it. If this chat has no selected Ruleset
or publication tool, preserve the draft and explain what source context is missing.
