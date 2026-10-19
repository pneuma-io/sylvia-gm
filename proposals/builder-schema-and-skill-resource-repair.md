# Focused Builder schema and skill-resource repair

Status: planning draft only. Keep implementation queued behind the four selected feature implementations. This proposal assigns no new implementation worker.

Desk work item: `f32250e3-84e8-4d30-9108-6f011b0cbf4f`, **Repair Builder schema references** (retrieved at revision 1). The coordinator owns the reciprocal PR link. Retrieve the complete current Desk specification and mandatory references before implementation; this proposal records the bounded repair and is not a replacement handoff.

## Outcome and scope

Repair three connected gaps in Builder's existing authoring guidance and tools: align compact Ruleset effect names with the live runtime, make selected-skill reference resources reachable, and point the Ruleset skill to the complete executable reference already installed in Sylvia.

The intended result is that an author can use an existing supported effect, retrieve the worked examples required by the active skill, and inspect the exact runtime grammar before writing executable mechanics. Unsupported names must fail clearly. Keep the current validators, package admission and authority boundaries.

No character/profile skill expansion, new Ruleset, schema-system replacement or new runtime effect is selected. Sylvia already exposes 24 static content definitions and contextual `character_creation`; this repair uses that surface.

## Verified source baseline

The planning baseline is `main` at `e618d4ff7998cd1f436e43c2a767609c9fe506e0`. Its comparison with shipped source `f998e4805d391246b17603c45a6736c90585c7b1` changes only `README.md`; the relevant runtime source is therefore shared with that shipped source. This is static source evidence, not a new runtime reproduction or installed test.

The live PR inventory was checked for overlap before creating this draft. The four selected implementations remain separate and keep their existing branches.

| Finding | Pinned evidence |
| --- | --- |
| Compact authoring admits stale effect names and omits supported ones. | [`_content_schema_ruleset.py`, `EFFECT_FIELDS` and effect `propertyNames`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/domain/_content_schema_ruleset.py#L25-L69), with the effect admission at lines 222–229. |
| The compiler does not translate those aliases. | [`builder/_ruleset_source.py`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/builder/_ruleset_source.py#L382) validates compact source, then passes expanded preconditions/effects/narrative through at lines 454–456. |
| Live dispatch uses different literal names. | [`runtime/_effect_executor.py`, `_EFFECT_HANDLERS`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/runtime/_effect_executor.py#L2206-L2256). |
| Both turn paths hide the reference loader when a tracked skill is selected. | [`_builder_session.py`, synchronous gate](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/agent/_builder_session.py#L2235-L2236) and the streaming gate at lines 2413–2414. |
| The Plan skill explicitly requires that loader. | [`build_campaign_plan/SKILL.md`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/agent/skills/build_campaign_plan/SKILL.md#L15-L28) directs the author to `load_builder_skill` and `references/worked-examples.md`. |
| The complete executable reference already has an owning retrieval path. | [`content_definitions.py`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/domain/content_definitions.py#L287-L313) attaches `procedural_definition` to `package_runtime`; [`_content_schema_runtime.py`](https://github.com/pneuma-io/sylvia-gm/blob/e618d4ff7998cd1f436e43c2a767609c9fe506e0/src/sylvia_gm/domain/_content_schema_runtime.py#L192-L200) exposes its `executable` member and validation owners. |

## Repair 1: align compact effect admission

Use the existing live authoring declarations in `runtime/_authoring_declarations.py` and the executable reference envelope, with the live handler registry as the execution owner. Remove the separate stale effect-name list as an independent source of accepted names. Keep the dependency arrangement simple and avoid introducing a second registry or a new schema framework.

The compact source must admit supported `call`, `trigger`, `set_participant`, `create_entity` and `require` forms. It must reject unregistered `modify_field`, `conditional`, `nested_call` and `trigger_procedure` names instead of silently converting them or advertising them as working aliases.

Distinguish handler names from legal envelope/sibling fields. Preserve the documented `condition`/`store_as` roles, flat conditional branches, and the runtime's `call` and `trigger` siblings such as `params`, `export_context` and `context`. Keep family-template substitution valid. Full body grammar, expressions, references and compiled admission remain with the existing runtime validators; broadening a name list does not prove an executable body is valid.

## Repair 2: retain skill-resource access after selection

Make the current workflow's `load_builder_skill` reference access available in both general and selected-skill Builder turns, through the synchronous and streaming paths. Selecting a skill must not make the references required by that skill disappear.

Retain the existing advertised skill names, exact packaged-reference catalog, bounded path checks, lazy loading and selection/authority behavior. A reference request must not silently switch the active workflow or grant unrelated instructions authority. Unknown names, missing resources, traversal and non-catalog paths must still return clear rejection. Preserve saved session selection and ordinary tool behavior.

## Repair 3: point the Ruleset skill to executable grammar

Keep `get_content_definition` with `name: ruleset_source` as the compact outer-format reference. Add an explicit instruction in `build_ruleset/SKILL.md` to retrieve `name: package_runtime` before authoring executable effects, expressions, procedures and calls, and inspect `definition.procedural_definition.executable`. The tool also supports the precise section `/procedural_definition/executable` for a focused reread.

Name the distinction plainly: the compact definition owns the authoring container; the executable reference explains the accepted runtime language. Use the existing `_runtime_executable_reference.py` resource and its validation owners rather than copying the whole grammar into skill prose. Keep full schemas lazy and on demand; do not inject all 24 definitions into every turn. Continue to require `check_ruleset_draft` and normal package/runtime admission. Reference retrieval alone does not establish validity, publication or gameplay acceptance.

## Future acceptance and proportionate verification

1. **Effect parity:** Use table-driven literal compact-effect examples for supported runtime handlers, with validation, compilation and ordinary admission. Check literal expected handler names against the actual dispatch registry and compact authoring declarations. Account explicitly for envelope/sibling fields so the test does not confuse them with handlers. Do not compare two values derived from the same shared constant and call that independent parity. Cover the five formerly omitted handlers, the four invalid aliases and an unknown name.
2. **Compilation and validation:** Exercise representative compact families with template expansion, a nested call with its legal siblings, a trigger and a conditional branch. Verify the intended expanded effects reach the existing runtime validator unchanged and valid examples pass ordinary admission. Malformed bodies, unknown names and invalid aliases must fail with useful source paths; no new runtime aliases are introduced.
3. **Selected/general access:** Use a small synchronous/streaming test matrix with a general Builder turn and an explicitly selected `BUILD CAMPAIGN PLAN` turn. Verify the offered loader can return its catalog and the requested worked example through the real tool dispatch. Check selection remains intact and invalid/non-catalog paths are rejected. Cover the affected saved-session behavior without resetting user sessions.
4. **Executable pointer:** Verify the Ruleset skill names `package_runtime` and the exact executable section. Retrieve it through `get_content_definition`, check the live grammar/validator ownership is present, and retain the existing compact definition. Preserve the 24 static definitions and contextual character surface without adding character/profile skills.
5. **Packaged resources:** Add a focused smoke check using the packaged owner/resource layout, not only a source-tree read. Confirm the skill, its reference catalog/worked example and the executable definition are available at the same tool paths. `src-tauri/sidecar-data-files.json` already lists the current skills and Plan references; change that inventory only if a real resource change requires it. A source fixture does not prove installed bytes.
6. **Compatibility and claims:** Use the actual selected SRD Lite package for affected admission/compatibility checks. Preserve its content, saved bindings and defaults, keep Full inactive, and retain multi-ruleset behavior. Reuse existing owner tests and a representative composition check; no whole-spell replay or repository-wide suite is selected by this planning document. Return the focused implementation PR, exact final revision, tested package/resource identities, commands and pass/fail/not-run results for each criterion, including any later installed/native/provider limitations. Planning text does not satisfy these criteria.

Existing focused test modules such as `tests/test_content_definitions.py`, `tests/test_runtime_content_definitions.py`, `tests/test_builder_content_draft_checks.py` and `tests/test_campaign_plan_skill_examples.py` are starting points to inspect when implementation begins. Place regressions at the actual owning boundary; do not add a parallel test harness or execute tests as part of this draft.

## Scheduling and limits

Keep the repair behind the four selected feature implementations. Before starting, retrieve current Desk decisions and recheck the affected owner paths against the then-current integration base. The implementation owner must coordinate any overlap in `_builder_session.py`; this proposal does not transfer ownership or write another branch.

This PR adds one planning document only. It changes no schema, skill, executable, package or shipped bytes; performs no runtime test, build, merge, tag or release; and changes no repository settings. No GitHub Actions before November 2026. The three repairs and their future acceptance remain open until a separately selected implementation is completed and verified.
