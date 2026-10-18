# New Campaign Selector Failure: Postmortem and Binding Fix Specification

> **Status:** Corrected, installed, and live-proven on Wan's host; direct acceptance pending
>
> **Scope:** The installed New Campaign Ruleset -> Campaign Setting -> Campaign Plan journey
>
> **Rejected candidate:** source tree `5ff3acb1c494d7c23d8d71710ae2b16fce59e7fd`
>
> **Completion boundary:** every unchecked requirement below, followed by the complete live Windows
> screenshot proof sequence in the normally installed app on Wan's host machine

This document is the complete task-specific postmortem, implementation checklist, recurrence
control, and live-proof specification for the rejected New Campaign selector. The active Alpha plan
links here for status. Do not reconstruct this correction from chat history, summaries, test counts,
or the earlier rejected candidate.

## 1. Incident summary

Wan opened the newly installed Sylvia GM candidate and immediately found that New Campaign offered
a nonexistent **The Rift Ruleset**, displayed multiple D&D SRD package candidates as separate
Ruleset choices, and then reported that every real SRD choice had no compatible Campaign Setting.

This was not an obscure edge case. It was the first selector in the primary campaign-creation
journey. The candidate was incorrectly described as ready after extensive internal testing. It is
rejected, and none of its passing test counts or earlier isolated happy-path exercises constitute
acceptance evidence.

## 2. Exact observed failure

The New Campaign frontend combined three incompatible behaviors:

1. It prepended a fake Ruleset option labelled **The Rift Ruleset (bundled)** whose value was the
   Campaign Plan ID `the-rift-awakening`.
2. It appended every exact package-store candidate independently, including historical, duplicate,
   or bare candidates for the same D&D SRD package.
3. It implemented Setting compatibility as a special case that showed a Setting only when the
   selected Ruleset value was the fake `the-rift-awakening` value. Every real SRD candidate
   necessarily fell through to **No compatible installed Setting**.

The effective frontend behavior was:

```text
if selected Ruleset value is "the-rift-awakening":
    show the hard-coded Rift Setting
else:
    show "No compatible installed Setting"
```

The package candidates were real storage records, but the player-facing choices and compatibility
behavior were false.

## 3. Canonical content identities

The real identities are and always were:

| Artifact role | Exact logical identity | Player-facing name |
|---|---|---|
| Ruleset | `wotc.srd.5.2.1` | D&D 5e SRD 5.2.1 |
| Campaign Setting | `pneuma.severed-lands` | Severed Lands |
| Campaign Plan | `the-rift-awakening` | The Rift Awakening |

`the-rift-awakening` is never a Ruleset identity and never a Campaign Setting identity.

The accepted Rift Campaign Plan already declares exact Ruleset and Setting bindings. Compatibility
must be calculated from those bindings and exact package closure, not inferred from the word
"Rift," a campaign name, a UI mode, or a scenario-specific sentinel.

## 4. Where “The Rift Ruleset” came from

The fake Ruleset was introduced by the uncommitted New Campaign correction in this task. It was not
a real package and was not present in the checked-in frontend at `HEAD`.

The implementation reused the older specialized Rift startup helper. Instead of adapting that
helper to receive the real Ruleset, Setting, and Plan identities, the correction used the Plan ID
`the-rift-awakening` as one synthetic shortcut for all three selections. The owner command was then
extended to accept that shortcut, and tests were written to submit it as expected behavior.

This directly contradicted the selected domain model:

```text
Ruleset -> compatible Campaign Setting -> compatible Campaign Plan
```

It also violated the rule that a field may not carry an identity belonging to another artifact
role. The shortcut crossed the UI, exact player answer, Interview State, resume behavior, owner
command, and tests; it was not a harmless display alias.

## 5. Why several SRD entries appeared

The package store correctly retains exact package candidates. Different candidate records may
represent:

- different SRD versions;
- a standalone or bare SRD archive;
- an exact SRD closure that includes Severed Lands;
- prior installed package identities; or
- the same logical Ruleset installed through different package operations.

Those records are appropriate in package management and exact recovery. They are not automatically
useful campaign choices.

The frontend projected `package_candidates()` directly into the New Campaign `<select>` without:

- grouping by logical Ruleset identity;
- choosing a compatible exact closure;
- checking for a declared Campaign Setting dependency;
- filtering stale or bare candidates;
- matching Campaign Plans against exact Ruleset and Setting bindings; or
- giving package-management history a separate presentation from campaign creation.

The result exposed storage implementation details as three apparently equivalent SRD choices.

## 6. Why the tests passed

The test oracle repeated the implementation mistake.

Guided-interview and Rift journey fixtures explicitly recorded answers equivalent to:

```python
("ruleset", "The Rift Ruleset", {"candidate_id": "the-rift-awakening"})
("setting", "The Rift Setting", {"setting_id": "the-rift-awakening"})
```

That made the wrong behavior the expected behavior.

The other test layers missed the defect for specific reasons:

- provider tests proved role projection, context fit, tool continuations, and Audit behavior;
- persistence tests proved Draft, transcript, export, resume, and handoff records;
- frontend tests mostly searched source for controls and bridge calls rather than executing the
  selector against realistic catalog data;
- the isolated desktop exercise followed the preselected fake option rather than opening and
  inspecting every selector;
- the isolated exercise used cleaner data than Wan's upgraded normal installation, so it did not
  reproduce accumulated package candidates; and
- no acceptance gate required observing all visible choices on the first screen.

The reported passing-test count proved complicated internal behavior while the first ordinary user
interaction remained wrong. Passing tests are not useful evidence when their oracle encodes the
defect.

## 7. Why the implementation process failed

The failure was methodological as well as technical:

1. The work optimized for making the full vertical green instead of proving the first visible
   choice honestly.
2. Reusing the specialized Rift helper was treated as more important than preserving the real
   Ruleset/Setting/Plan identities.
3. A scenario sentinel was incorrectly treated as a harmless implementation detail.
4. Tests were written from the implementation outward instead of from the player's observed
   journey inward.
5. Broad provider, persistence, transaction, documentation, and export coverage was mistaken for
   confidence in the UI.
6. The manual exercise accepted default values and did not inspect the contents of each selector.
7. Only a clean isolated store was exercised, not a populated upgrade state.
8. The candidate was called ready before Wan accepted its first interaction.

## 8. Binding implementation requirements

A passing substitute path does not satisfy these requirements.

- [x] **FIX-01 — Remove every cross-artifact alias.**
  Ruleset answers contain a real Ruleset identity or an opaque exact candidate identity. Setting
  answers contain a real Setting identity. Plan answers contain a real Plan identity. Neither UI,
  transcript, Interview State, resume, nor owner command may use `the-rift-awakening` as a Ruleset
  or Setting.

- [x] **FIX-02 — Compute compatibility from exact bindings.**
  The construction catalog and selector must follow:

  ```text
  Ruleset exact identity
      -> exact declared Campaign Setting dependency
      -> Campaign Plans whose Ruleset and Setting bindings both match
  ```

  Campaign names, UI modes, and scenario-specific `if` statements do not own compatibility.

- [x] **FIX-03 — Project logical campaign choices, not raw package candidates.**
  Historical, duplicate, bare, or incompatible candidates must not appear as several apparently
  equivalent D&D choices. The supported bundled journey shows one **D&D 5e SRD 5.2.1** choice.
  Other installed Rulesets appear only as one useful logical choice backed by a closure containing
  a Campaign Setting the flow can use. Package history remains in package management.

- [x] **FIX-04 — Show the real compatible content.**
  Selecting D&D 5e SRD 5.2.1 shows **Severed Lands**. Selecting Severed Lands shows
  **The Rift Awakening**. Review displays the same three names. **The Rift Ruleset** and
  **The Rift Awakening setting** do not appear in runtime source, fixtures, or installed UI.

- [x] **FIX-05 — Keep the generic frontend ruleset-neutral.**
  The owner projects player-ready logical choices, exact identities, and compatible Setting
  bindings. Generic frontend code renders that projection. It must not contain D&D/SRD constants
  or decide compatibility using Rift-specific constants.

- [x] **FIX-06 — Enforce artifact roles in the owner.**
  The owner rejects a Plan identity supplied as a Ruleset or Setting. The bundled Rift materializer
  may install or resolve its exact closure, but it receives and validates the real selected
  Ruleset, Setting, and Plan identities.

- [x] **FIX-07 — Preserve honest No Ruleset behavior.**
  **No Ruleset — narrative play** remains an explicit distinct choice. It does not silently acquire
  D&D mechanics or Severed Lands. If no compatible Setting/Plan exists, the UI follows the selected
  Create New/Back behavior without inventing compatibility.

- [x] **FIX-08 — Preserve exact identities through resume and handoff.**
  Interview answers, Draft resume, review, Start, package lock, accepted Plan, and GM handoff agree
  on the same exact Ruleset, Setting, and Plan. Resume never restores a legacy fake option or
  silently substitutes another candidate.

## 9. Regression and qualification requirements

- [x] **TEST-01 — Populated logical-catalog test.**
  Install multiple historical/bare SRD candidates, the exact SRD + Severed Lands closure, and an
  incompatible Ruleset closure in a temporary owner. Prove the construction catalog projects one
  usable D&D logical choice with Severed Lands plus one distinct compatible logical Ruleset—not raw
  candidate duplication.

- [x] **TEST-02 — Frontend compatibility behavior test.**
  Execute the real generic compatibility function with an owner-shaped catalog. Prove the chosen
  logical Ruleset resolves only its exact Settings and Plans, and the D&D path produces Severed
  Lands then The Rift Awakening.

- [x] **TEST-03 — Cross-type rejection test.**
  Prove the owner rejects `the-rift-awakening` when submitted as a Ruleset or Setting and accepts
  `wotc.srd.5.2.1`, `pneuma.severed-lands`, and `the-rift-awakening` in their correct fields.

- [x] **TEST-04 — Forbidden shortcut regression.**
  Runtime/frontend/test-fixture checks reject **The Rift Ruleset**, **The Rift Awakening setting**,
  `ruleset_selection = the-rift-awakening`, and `setting_id = the-rift-awakening` outside this
  postmortem's quoted historical evidence.

- [x] **TEST-05 — Clean and populated desktop state.**
  Exercise the selector once with clean isolated data and once with populated synthetic upgrade
  data containing multiple stored SRD candidates. Both must show the same single usable D&D choice
  and exact compatible Setting/Plan.

- [x] **BUILD-01 — Build and install one new exact Windows candidate.**
  Record source-tree identity, installer path and SHA-256, installed desktop and owner hashes,
  registered version/location, and successful launch. The rejected `5ff3acb` installer cannot be
  reused as evidence.

## 10. Mandatory live Windows proof

- [x] **LIVE-01 — Run the whole journey in the normally installed app on Wan's host.**

This must use the normal Windows Sylvia GM installation on this physical host—not the VM, not a
debug-only identity, not source-only HTML, and not only an automated bridge. Create one clearly
named synthetic acceptance campaign. Capture and post a screenshot for every visible step below.

### Required screenshot sequence

All screenshots must identify the same installed candidate:

1. Home screen before **New Campaign**.
2. Ruleset step with its dropdown open, proving one D&D 5e SRD choice and no fake or duplicate
   Rift/SRD choices.
3. D&D 5e SRD selected.
4. Campaign Setting step with **Severed Lands** visibly selected.
5. Campaign Plan step with **The Rift Awakening** visibly selected.
6. Every artifact-contributed question screen active for the selected content.
7. Editable campaign-name step.
8. Player Character and Companion selection step.
9. Boundaries and Preferences step.
10. Specific Requests step using the deterministic no-additional-requests path.
11. Final spoiler-safe review showing D&D 5e SRD 5.2.1, Severed Lands, and The Rift Awakening.
12. The visible **Ready to Begin** action.
13. The resulting GM screen/opening state after the same Campaign Aggregate hands off.

The proof is incomplete if a step is inferred from logs, omitted because a default happened to be
selected, captured only from a debug app, or exercised only through an automated bridge.

Screenshot files remain owner-local when the installed journey contains owner-specific party or
campaign data. They must be rendered in the active Codex task for Wan's inspection, but must not be
committed merely to preserve acceptance evidence.

## 11. Recurrence controls

These controls apply to the correction and its acceptance:

1. A field may never carry an identity from another artifact role.
2. Scenario-specific IDs may not decide generic compatibility.
3. Package-store rows may not be dumped directly into player selectors.
4. Test fixtures use canonical artifact identities from the real catalog.
5. Installed UI qualification opens and inspects every selector rather than accepting defaults.
6. Both clean and populated upgrade state are exercised.
7. Candidate readiness is based on the observed supported journey, not suite size.
8. A hard-coded special case that crosses a domain boundary is treated as a defect, not a shortcut.
9. Wan's first-interaction acceptance remains open until Wan directly accepts the exact installed
   candidate.

## 12. Execution evidence

### 12.1 Implemented correction

The owner now projects a player-ready logical construction catalog. It groups exact package-store
records by logical Ruleset identity, admits only useful validated/activated closures with a real
Campaign Setting dependency, and returns exact compatible Settings. Campaign Plans retain separate
exact Ruleset and Setting bindings. The frontend only renders and filters this generic projection.

The bundled path now uses these exact values throughout construction and handoff:

```text
Ruleset: wotc.srd.5.2.1
Setting: pneuma.severed-lands
Plan:    the-rift-awakening
```

The owner rejects `the-rift-awakening` in either the Ruleset or Setting field. The fake Rift
Ruleset/Setting labels and cross-role fixtures are gone. No Ruleset remains an explicit separate
narrative choice.

### 12.2 Automated verification

The focused changed-boundary suite passed on the corrected source tree:

```text
.venv/Scripts/python.exe -m pytest -s
  tests/test_alpha_manual_campaign_plan.py
  tests/test_guided_campaign_draft.py
  tests/test_alpha_05_rift_journey.py
  tests/test_alpha_f6c_canonical_frontend.py
  tests/test_alpha_f6e_owner_session_transport.py

112 passed in 101.08s
```

This includes clean and populated owner catalogs, two independently installed bare SRD candidates,
the exact Rift closure, a distinct compatible Signal Ruleset, the real frontend compatibility
function running under Node, forbidden-shortcut checks, cross-type owner rejection, exact resume,
and the complete accepted handoff path. The clean/populated projection and the frontend resolver
together prove the selector receives the same single usable D&D choice instead of raw package rows;
the populated normal installation was then exercised directly in LIVE-01.

Additional checks passed:

```text
node --check src-tauri/frontend/app.js
cargo check                         # src-tauri
.venv/Scripts/python.exe -m compileall -q src tests
bash scripts/check-docs-index.sh
git diff --check                    # no errors; existing CRLF warnings only
```

### 12.3 Exact Windows candidate

- Base commit: `835b4537e7caa6b243efafff6337d1c0f9b35030`
- Runtime build-input identity: `90aac938dbc8cab773b4303fd541773a8a352afe062ebd9c1cc6a29810ddb8a0`
- Product version: `0.3.0-1`
- Installer:
  `dist/sylvia-gm-0.3.0-alpha.1-windows-x86_64-90aac938/Sylvia GM_0.3.0-1_x64-setup.exe`
- Installer bytes: `36,954,877`
- Installer SHA-256: `bad77cf41b216d3d94da0de0ebc54adc9570b6960cf5d53db39df79a04fdad3d`
- Normal registered location: `%USERPROFILE%\AppData\Local\Sylvia GM`
- Installed desktop SHA-256:
  `f869a191147c5d8bff30eabe853d8c2a114c2ee9b85c491267a653de7d366cb9`
- Installed owner SHA-256:
  `e44c5c0f22e0757cea4f3f8295342bcce1a205c27cc921ef113e2a425266a71b`

The installer completed normally with exit code 0, registered version `0.3.0-1`, and launched the
installed per-user binary from that location. This was not the VM or a debug identity.

### 12.4 Physical-host live proof

The installed app used Wan's populated normal owner store and created the synthetic campaign
**Codex New Campaign Acceptance 2026-08-20**. The open Ruleset selector visibly contained:

- No Ruleset — narrative play;
- one D&D 5e SRD 5.2.1 choice; and
- one distinct compatible Signal Static Ruleset.

There was no fake Rift Ruleset and no duplicate SRD choice. The journey then selected Severed
Lands, The Rift Awakening, the Plan contribution **Mystery and discovery**, and Player Character
**Mira Vale**. The final review displayed the same exact Ruleset, Setting, and Plan.

Ready-to-Begin committed the same Campaign Aggregate and changed writer authority to DM. The first
opening attempt was honestly blocked before provider admission because the configured Gemma request
needed 33,366 conservative tokens while its configured context was 32,768. For the live proof only,
the configured maximum reply reserve was reduced from 4,096 to 3,000, Manual Retry produced the
opening narration successfully, and the original 4,096 value was then restored. The capacity block
and the successful opening are both preserved as screenshots; no required context was trimmed.

The complete thirteen-step sequence plus the model-capability and capacity-block diagnostics were
captured and reviewed during the live task. Those files remain owner-local and are intentionally
excluded from the repository because the installed journey includes owner-specific party data.

## 13. Execution status

- [x] Complete FIX-01 through FIX-08.
- [x] Complete TEST-01 through TEST-05.
- [x] Complete BUILD-01 and install the exact candidate on this host.
- [x] Complete LIVE-01 and post all 13 screenshot stages in the Codex task.
- [ ] Record Wan's direct acceptance. Publication remains separately approval-gated.

**Current candidate:** runtime identity `90aac938dbc8cab773b4303fd541773a8a352afe062ebd9c1cc6a29810ddb8a0`;
installed and live-proven on this host; pending Wan's direct acceptance.

**Next action:** Wan inspects the rendered screenshot sequence and accepts or reports the first
remaining defect. Publication remains separately approval-gated.
