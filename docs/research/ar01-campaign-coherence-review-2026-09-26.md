# AR-01 campaign coherence and record-keeping review

Date: 2026-09-26. Advisory investigation, not implementation authority or an AR-01 verdict.

Reviewed the current dirty checkout at HEAD `5c613a6077897dadb2da4fc335d6f792580a33b5`.
This identifies the base commit, not the installed candidate's exact bytes. Existing work was
preserved. Only this report and a synthetic diagnostic test file were added. No application code,
campaign, ticket status or settings were changed. No live model calls, private campaign inspection
or AR-01 execution occurred.

## Assessment

The architecture provides a sound basis for long campaigns, but it does not yet justify saying
that all relevant systems sufficiently address coherence and record-keeping. This review found
three reproducible software defects: two omissions in automatic memory selection, and an
overestimate that prematurely blocks portable campaign export. Correct these before another
expensive endurance attempt. They do not establish that the next GM necessarily contradicts a
fact, nor that ordinary saved campaigns are corrupt.

The [Alpha Completion Plan](../plans/alpha-rebuild-readiness-plan.md) records Candidate 35's
completed focused prerequisites. That evidence remains valid for its tested cases. AR-01's
six-act, detour, natural-compaction and reopen acceptance remains outstanding. This review adds
source-level findings; it does not silently reopen tickets or change their disposition.

## Existing protections

| System | What it establishes | Limit of the claim |
| --- | --- | --- |
| Authoritative Campaign State | SQLite reconstructs current scene, latest active facts/entities, knowledge audiences and Plan Progress independently of the transcript. | Correct storage cannot establish that a model-authored claim is true. |
| Campaign File disposition | The GM must settle its durable changes or explicitly declare no change before its final response is released. Related changes are validated and committed together. | The same GM judges which facts changed and writes their contents. Structural validation is not semantic verification. |
| Mechanical state and retries | Revision fences, operation identities and atomic commits prevent partial state and repeated committed side effects. | Narrative prose can still misinterpret the correct mechanic or stored fact. |
| Automatic memory selection | Interleaves recent facts, relevant facts and entities; bounded to 128 records and 48 KiB, with omission counts and chronology. | Selective context is incomplete; the two defects below weaken relevance selection. |
| Discovery and exact reads | Searches all active records and reads exact facts outside the automatic projection. Oversized whole-state reads and invalid search limits have actionable errors. | The GM must decide to search and formulate a useful query. |
| Working-context compaction | Saves a bounded continuation note, covered identities and original protocol archive; waits for persistence before using it; keeps the old checkpoint on failure. | Summaries are lossy. They cannot guarantee recall of every old obligation or event. |
| History retrieval | After compaction, the GM can list archives, search literal text and page through original exchanges. | A recoverable archive does not guarantee proactive recollection. |
| Plan and knowledge boundaries | Current state, reusable sources and private preparation are separated; audiences are recorded explicitly. Act requirements and endings have exact retrieval and structural admission. | The GM still judges narrative completion and what characters actually learned. |

Source owners:
[continuity reconstruction](../../src/sylvia_gm/infrastructure/sqlite_continuity.py#L301),
[atomic continuity writes](../../src/sylvia_gm/infrastructure/sqlite_authority.py#L7288),
[GM contract](../../src/sylvia_gm/agent/_provider_session.py#L919),
[Campaign File duty](../../src/sylvia_gm/agent/_stateless_mcp.py#L705),
[memory selector](../../src/sylvia_gm/agent/_campaign_memory.py#L16),
[discovery](../../src/sylvia_gm/application/authority.py#L5239),
[exact reads](../../src/sylvia_gm/application/authority.py#L5547),
[compaction and archive retrieval](../../src/sylvia_gm/agent/_working_context.py#L263).

## Confirmed defect 1: scene context can suppress the player's memory cue

The provider projection combines current in-character text and serialized scene JSON into one
search query. The existing ranker rejects a body-only match unless it covers 60% of the query's
weighted terms. Scene description and JSON field names therefore count against a fact that
matches the player's question.

A synthetic campaign with an old promise about Ada's blue cloak and 300 compact newer facts
reproduces the issue through the real provider-context builder:

| Current input | Empty scene | Ordinary guildhall scene |
| --- | --- | --- |
| IC: "What about the cloak?" | Promise included | Promise omitted |
| IC: "Ada" | Promise included | Promise omitted |
| IC: full question naming promise, Ada and cloak | Promise included | Promise included |
| OOC: that identical full question | Promise omitted | Promise omitted |
| IC: "We return to town." | Promise omitted | Promise omitted |

Each projection selected 128 records. Explicit campaign search for `cloak` still finds the saved
promise; it has not been deleted. The natural-return row is a separate selective-recall limitation,
not evidence that the ranking algorithm should infer an unmentioned subject.

Recommended correction: score player-topic and scene relevance separately and merge their
candidate sets within the existing bounds. Unrelated scene terms should not veto a direct player
cue. Cover both record-count pressure and byte pressure; the existing long-filler test leaves room
for the much smaller promise after rejecting large filler records, masking this particular case.

Sources: [query construction](../../src/sylvia_gm/agent/_provider_session.py#L4619),
[selection call](../../src/sylvia_gm/agent/_campaign_memory.py#L41),
[weighted admission](../../src/sylvia_gm/application/knowledge_search.py#L140),
[existing fixture and test](../../tests/test_campaign_memory_context.py#L20).

## Confirmed defect 2: OOC reminders do not influence automatic recall

The same projection reads only `content["text"]`. Separately stored `ooc_text` never enters the
memory query. The identical full promise question selects its fact as in-character input and
omits it as OOC-only input, with or without scene context.

This does not hide the OOC instruction from the GM. It means the automatic evidence selection
does not help the GM answer a continuity correction as it would an equivalent in-character
question. Explicit search remains available.

Recommended correction: include both channels in relevance selection while preserving their
distinct roles in the actual model input. OOC guidance must not become fictional speech.

Sources: [current query](../../src/sylvia_gm/agent/_provider_session.py#L4624),
[separate input fields in the fixture](../../tests/test_alpha_f4_provider_session_adapter.py#L999).

## Confirmed defect 3: portable export rejects modest campaigns as oversized

The existing continuity/recovery test fails with
`portable_export_limit_authority_jsonl_bytes`. Instrumenting only its newly created synthetic
database measured:

- Actual serialized authority records: **1,253,124 bytes**.
- Calculated preflight bound: **139,534,772 bytes**.
- Configured authority-record limit: **67,108,864 bytes (64 MiB)**.
- `campaign_operations` alone contributes 133,001,608 estimated bytes for 136 rows whose actual
  serialized size is 338,573 bytes.

The preflight takes each column's largest value, multiplies by six for possible escaping, then
multiplies that constructed largest-row bound by every row in the table. A large construction
operation is effectively charged again for every small later operation. The actual limit was
unchanged; the real serialized data is well below it.

A minimized in-memory probe against the same preflight confirms the growth failure: with one
160,000-character value and otherwise ten-character values, 60 operation rows are accepted
(176,823 actual authority bytes), while 75 are rejected (180,993 actual authority bytes).
This reduced probe isolates size accounting, not a complete valid campaign; the existing test
above supplies the complete campaign/export reproduction.

Recommended correction: retain finite limits, but measure selected values/rows cumulatively and
use bounded incremental serialization where an upper estimate cannot decide accurately. Keep
the final exact byte check. Merely raising or removing the limit conceals the accounting defect.
Then prove export and recovery of the mature synthetic campaign and the installed affected path.

This blocks **portable campaign export**. It does not prove failure of ordinary persistence,
reopening or the separate whole-database online backup implementation, and it does not by itself
prove a narrative AR-01 failure. It is directly relevant to confidence in long-term record-keeping.

Sources: [preflight](../../src/sylvia_gm/infrastructure/sqlite_authority.py#L2011),
[inflated estimate](../../src/sylvia_gm/infrastructure/sqlite_authority.py#L2052),
[exact serialization check](../../src/sylvia_gm/infrastructure/sqlite_authority.py#L1767),
[limit](../../src/sylvia_gm/infrastructure/campaign_archive.py#L61),
[separate database backup](../../src/sylvia_gm/infrastructure/sqlite_authority.py#L2286),
[failing integration test](../../tests/test_alpha_05_rift_journey.py#L1793).

## Remaining design limitations and proportionate improvements

**Old obligations can be available without being recalled.** A fact outside the recency budget,
without a matching current cue, can be absent from both automatic context and a lossy note.
Candidate 35's recorded installed check successfully retrieved an obligation from a general
question about borrowed items; that supports the tested cue-driven case. It does not establish
unprompted recall on an ordinary return to a location.

After correcting the confirmed defects, use one focused mature-history check where an ordinary
return should make an old promise, relationship or consequence relevant, without naming it in
the input. If that fails, consider a bounded view of unresolved obligations using the existing
Campaign State. This would need explicit lifecycle semantics; do not pin every historical
`promise` record forever or add another database/memory agent by default.

**Superseded facts need a clear authoring practice.** Reusing a stable fact/entity identity updates
its current projection and preserves earlier records. Giving an old plan and its completion
different IDs leaves both active. Current batch writes always use `status="active"`; the GM-facing
fact schema has no retirement field. Revision provenance helps the GM interpret chronology but
does not semantically resolve different IDs. Clarify updating the same fact when its current
status changes, and demonstrate planned → completed → corrected behavior before introducing a
new retirement/supersession feature. Preserve historical evidence.

Sources: [active-record folding](../../src/sylvia_gm/infrastructure/sqlite_continuity.py#L339),
[fact schema](../../src/sylvia_gm/agent/_stateless_mcp.py#L188),
[batch admission](../../src/sylvia_gm/application/authority.py#L2031).

**Semantic truth remains a model responsibility.** The final response and saved claims can be
structurally accepted while the model attributes unspoken information to an NPC or incorrectly
labels private information as learned. AR-02 explicitly remains an accepted minor limitation,
not a repaired mechanism. Keep that disposition; consequential continuity, spoiler or agency
harm still falls under AR-01's serious-failure criteria. There is no basis here to promise that
every future saved narrative claim is correct or to add a speculative second-model reviewer.

Sources: [AR-02 disposition](../../.scratch/alpha-readiness/issues/02-memory-attributes-unspoken-information-to-stern.md),
[memory validation](../../src/sylvia_gm/application/authority.py#L1755),
[AR-01 acceptance](../../.scratch/alpha-readiness/issues/01-verify-long-form-campaign-continuity.md).

## Configuration and installed-evidence limits

No obvious source-level capacity misconfiguration was found. Subscription allowance comes from
the runtime's own effective catalog capacity rather than the larger API maximum. Automatic
compaction uses approximately 70% trigger and 42% target, constrained by output reserve where
applicable. Note length is `min(12000, capacity // 32)` characters.

The current plan/run record says Candidate 35 restored blank/automatic context and output
settings after its reduced-context test. Its recorded normal allowance of 258,400 implies an
8,075-character note cap, not the 2,000-character stress-test cap. These are documented earlier
observations and current source rules; this review did not independently inspect the VM's
effective settings or installed hashes. Reconcile the actual candidate, GPT-6 Sol/High,
automatic limits and Full Audit at the next selected run's preflight.

Sources: [subscription capacity facts](../../src/sylvia_gm/provider/_chatgpt_model_facts.py#L12),
[compaction budgets](../../src/sylvia_gm/agent/_working_context.py#L245),
[note limits](../../src/sylvia_gm/agent/_working_context.py#L302),
[recorded installed verification](../../.scratch/alpha-readiness/runs/pre-ar01-final-20260926/run.md#L222).

## Verification performed

All commands used the checkout's `.venv/Scripts/python.exe -m pytest -s` and synthetic fixtures.

1. `-q tests/test_campaign_memory_context.py tests/test_campaign_knowledge_search.py tests/test_working_context.py tests/test_working_context_sessions.py`: **60 passed**.
2. The read-only research agent ran memory/search/working-context plus
   `tests/test_chatgpt_model_defaults.py`: **62 passed**, overlapping the first run. These counts
   must not be added as distinct coverage.
3. `-q tests/test_alpha_05_rift_journey.py -k 'continuity_survives_restart or batch_is_validated_atomic or committed_mechanic_reference'`:
   **one passed, two failed**. Atomic/replay-safe Campaign File batch passed. Continuity export
   failed as diagnosed above. The retry/reopen test stopped at a result-card title assertion:
   actual `perception`, expected `D&D SRD D20 Test`; it did not reach its later retry/reopen
   assertions. This is not evidence of lost state, and this review did not silently update it.
4. Independent root and research-agent provider-projection probes reproduced both recall
   omissions. Root measured the integration export and minimized its preflight failure.
5. Added and ran
   [focused diagnostic tests](../../.scratch/alpha-readiness/diagnostics/test_ar01_preflight_coherence.py):
   **four failed, one passed in 4.69 seconds**, as expected on the unfixed source. Two scene-query
   cases, one OOC case and the larger export-history case fail; the smaller export control passes.
   These are ordinary failing assertions, not expected-failure markers that could conceal a fix.
   The file stays outside the normal suite until the owning corrections are selected.
6. `git diff --check` passed, with existing CRLF/LF normalization warnings.

The additional probes are source/component evidence, not genuine-model or installed acceptance.
No percentage probability of AR-01 success can be justified from these checks.

## Additional focused tests before the full campaign

The user's follow-up asks for piecemeal proof. The following is a recommendation, not a new
implementation plan or authorization to start paid/live inference. Reuse the existing Candidate 35
evidence for already proven paths. Concentrate new work on the observed gaps and the compositions
they did not exercise.

### Fast deterministic checks, added now

```powershell
.venv/Scripts/python.exe -m pytest -s -q .scratch/alpha-readiness/diagnostics/test_ar01_preflight_coherence.py
```

These five cases establish an inexpensive red/green loop for the three confirmed defects. After
correction, retain the existing memory/search/compaction suite and the authentic export/recovery
integration check. The export control ensures the minimal setup itself is supported. The latter
integration check must reach recovery and compare recovered state, not merely create a ZIP.

### Three gameplay rehearsals most relevant to the next AR-01 attempt

Clarification from Wan: the requested focused tests should predict the next full AR-01 result,
not just verify individual software fixes. Prioritize the genuine-GM scenarios below. The
deterministic regressions are supporting repair checks, not substitutes for these rehearsals.

| Scenario | Setup and actions | Pass condition |
| --- | --- | --- |
| Old consequences on an ordinary return | Separate prepared mature campaign exceeding both memory-selection boundaries, with an old loan/promise and a relationship changed by play. Retain a private fact known to only one NPC. After real compaction, return naturally to the relevant NPC/place without mentioning the obligation. | The reply acts on the obligation and changed relationship without a leading reminder; the uninformed NPC does not acquire private knowledge; saved holdings agree with what happens. Record what the GM actually does, not only whether the fact exists. |
| Completed work and corrections through repeated compaction | A planned trip has a later saved completion under a different ID. Ask a short contextual question, then give a legitimate OOC correction to another fact. Carry that accepted correction through two linked real compactions and a normal app reopen before it becomes relevant again. | No reissued completed trip or denial of its completion; short IC/OOC cues recover the right evidence; the accepted correction survives summary-of-summary and restart; committed mechanics are not repeated. Preserve the original transcript and distinguish earlier error from corrected current truth. |
| Late-act progression with detour consequences | Prepare a separate campaign immediately before a late main-quest closing event, preferably Act 5 to Act 6, with older relationships, spent resources and a meaningful outstanding side obligation. Use a short multi-scene detour, return, then play the required closing event and the first consequential next-act action. | Side work does not falsely complete the main quest; earned closure actually advances; the next act is playable without evaluator coaching or invented repairs; the detour, relationships and resources still matter. Test actual prose and choices alongside Plan Progress. |

Use the isolated Windows VM, final composed candidate, genuine selected GM model/effort, normal
automatic context limits, and Full Audit. Prepared canonical history can reach mature boundaries
cheaply, but the summary and subsequent gameplay must come from the real model. A manual
compaction is supplementary mechanism proof, not normal automatic-compaction proof. These
prepared scenarios never count toward AR-01's natural endurance requirements. Do not run them
inside any preserved failed campaign.

The evaluator knows the prepared obligations and Plan conditions; a separate player context sees
only its public briefing and ensuing play. Do not feed the answer, memory-tool instructions or
evaluation criteria into player messages. Preserve each first result rather than rerolling a failed
case until it passes. Treat minor AR-02 wording under its accepted disposition; consequential
disclosure or agency/continuity harm remains a failure.

These extend the existing AR-33 proof from a borrowed-items question to an unprompted contextual
return, extend one compaction to correction survival through repeated compaction, and address
late-act play not reached by the failed endurance run. Reuse the existing ending/after-story,
Stop/Retry and basic reopen evidence; repeat only the ending continuation affected by any new
memory change, rather than the entire prior matrix.

Separately, fix and prove mature portable export/recovery as a record-keeping check: export the
synthetic mature campaign, recover into an isolated empty destination, open it through the installed
UI and continue. Compare current scene, corrected fact, relationship, obligation, Plan Progress,
mechanical state, source history and checkpoint. This addresses the export defect; it is not a
predictor of the GM's six-act narrative quality.

Once those relevant focused checks pass on the composed installed candidate, run AR-01 unchanged.
Its blind-player detour, all six acts, five transitions, two natural GM compactions and two normal
reopens still test emergent long-form behavior that isolated cases cannot establish. Add a new
small test only for a new observed boundary or failure; avoid repeating the entire existing matrix.

### Reproduce the automatic-recall omissions

Run this Python through the checkout interpreter. It uses existing synthetic helpers and no
private campaign files:

```python
from dataclasses import replace
from tests.test_campaign_memory_context import _saved_memory
from tests.test_alpha_f4_provider_session_adapter import (
    _OwnerProjection, _provider_input, _ScriptedProvider,
)
from sylvia_gm.agent._provider_session import ProviderSessionAdapter

base = _saved_memory(filler_count=300)
facts = tuple(
    {**fact, "text": "Other observation."}
    if fact["record_id"].startswith("unrelated") else fact
    for fact in base.facts
)
scene = {
    "act_id": "act-3", "location_id": "guildhall",
    "summary": "Afternoon after the contract hearing. The party is preparing "
               "to leave the guildhall for the north road.",
}
for current_scene in ({}, scene):
    adapter = ProviderSessionAdapter(
        _OwnerProjection(continuity=replace(
            base, facts=facts, current_scene=current_scene,
        )), _ScriptedProvider([]), owner_id="owner-provider",
    )
    for ic, ooc in (
        ("What about the cloak?", None),
        ("Ada", None),
        ("What did we promise Ada? We should get that blue cloak.", None),
        ("", "What did we promise Ada? We should get that blue cloak."),
        ("We return to town.", None),
    ):
        context = adapter._current_campaign_context_payload(
            _provider_input(user_text=ic, ooc_text=ooc)
        )
        selected = (context["PLAYER_KNOWN"]["current_facts"]
                    + context["GM_PRIVATE"]["current_facts"])
        print(bool(current_scene), ic, ooc,
              any(f["record_id"] == "archive-promise" for f in selected))
```
