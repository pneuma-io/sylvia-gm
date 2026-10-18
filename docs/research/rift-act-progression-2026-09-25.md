# Rift act endpoints and on-demand retrieval

Date: 2026-09-25

Status: Supporting investigation and recommendations, not implementation approval.

## Question and boundary

Wan wants more specific, session-like act endings and asks whether vague objectives explain the
AR-26/27 findings. He prefers on-demand retrieval and explicitly excludes automatically including
the next act. AR-01 remains paused after P105. This investigation reads selected source and retained
synthetic evidence; it performs no new model inference, live play, campaign repair or product change.

The main finding is a combination of weak authored closure and potentially conflicting runtime
guidance. Clearer authored milestones, useful exact references and consistent retrieval/update
instructions are a smaller first intervention than putting act details into every request.
Their effect on model behavior remains to be verified after a separately selected correction.

## 1. The plan's current scope differs from the desired session structure

Rift's source index explicitly defines six acts with flexible session count, says the GM fills in
sessions, and permits six sessions or sixty. Its acts are broad story phases. It does not currently
promise one defined evening-sized adventure per act.
[Source index](../../campaign-settings/severed-lands/campaigns/the-rift-awakening/index.md).

The bundled Plan is more substantial than the short Setting chapter. Act 1 includes specific
people/motives, Ressa's diverted copper/oil and evidence, three possible first undertakings, and
responses to confrontation, withdrawal and loss. The problem is not a complete absence of playable
material. However, Act 1 references only `arrival-and-waystation`, whose description and sole
completion condition both say to establish a chosen connection and practical first obligation.
That does not distinguish accepting an obligation from completing it, define a closing scene, or
say which preparation can remain open when Arrival ends.
[Authored situations and consequences](../../campaign-plans/the-rift-awakening/plan.md),
[canonical objective](../../campaign-plans/the-rift-awakening/plan.json).

The fixed source SHA-256 remains
`662fca34ac19ff1347d659c2ba82eace69eeedcfcad77eaff6689d0ea4687b13`.
The retained P003 successful Plan read has an Act 1 object and objectives array exactly equal to
current source. These content findings therefore apply to the evaluated candidate, not merely a
newer draft. The run record preserves the admitted identity and private evidence location.
[Run record](../../.scratch/alpha-readiness/runs/ar01-20260925-0233/run.md).

The current bundler preserves the canonical story rather than regenerating it from the Setting's
short act files. It updates bindings and renders the existing JSON. Editing only the Setting
chapter would therefore leave the copied canonical Plan situations unchanged.
[Rift entry point](../../scripts/build_rift_campaign_plan.py),
[Plan renderer/writer](../../scripts/build_severed_lands_campaigns.py).

## 2. The lookup instructions underemphasize story planning

The automatic preparation tells the GM to read the structured Plan for a new situation, consequence
or major development. The reference tool, however, says to use a reference only for a concrete
missing fact affecting present adjudication, not to decorate or expand narration. The main system
instruction similarly emphasizes a missing concrete fact. The exact P105 request contains these
instructions. This is a real difference in emphasis; whether the model treated it as a prohibition
on planning cannot be proved from the retained run.
[Preparation projection](../../src/sylvia_gm/agent/_provider_session.py),
[reference tool](../../src/sylvia_gm/agent/_stateless_mcp.py).

A GM can answer a request to hold fabric or inspect a noticeboard from current facts without
deciding it lacks a fact. Choosing what useful development to introduce next is a different reason
to consult preparation. The interface should name that reason explicitly while retaining the rule
that retrieved preparation is not already-established fiction and does not authorize player actions.
The current system already permits world events and GM-owned NPC activity; no removal of player
agency boundaries is required.

Exact small reads already exist. The reader accepts `structured/structure/act-1` and
`structured/objectives/arrival-and-waystation`. Yet the model-facing path description and invalid-
reference guidance advertise only `structured` or `readable` for Plans. Search can return item
paths, but the known current-act path need not require discovery first.
[Structured reader](../../src/sylvia_gm/application/authority.py),
[tool path guidance](../../src/sylvia_gm/agent/_stateless_mcp.py).

A read-only Python probe invoked the real `_read_structured_campaign_plan` helper against the
fixed source. Both ID-addressed reads returned the intended item. Compact UTF-8 serialization was
7,633 bytes for Act 1 and 250 for its objective, versus 57,295 for the whole source Plan. These are
source-payload byte measurements, not provider token usage or installed UI acceptance. No schema,
database or new retrieval subsystem is needed to expose these existing paths more clearly.

## 3. AR-26 is not explained away by vague completion criteria

Even an unresolved investigation must not retain a claim that its purpose or evidence is unknown
after those have been established. Likewise, an ongoing expedition objective must distinguish
planning a first scout from returning after two scouts. These corrections do not depend on choosing
a new definition of act completion.

The system says to record Plan Progress only when its real status changes. The tool is more nuanced:
omit it when both status and practical meaning are unchanged. An objective can remain `engaged`
while its factual summary changes substantially. Aligning those instructions is a concrete repair
candidate, not proof that wording alone caused the behavior.
[System instruction](../../src/sylvia_gm/agent/_provider_session.py),
[progress schema](../../src/sylvia_gm/agent/_stateless_mcp.py).

Across the three process-spanning selected Audit exports, five accepted progress updates affect
three items; all use `engaged`. Foothold and workshop records each accepted a same-status update
early in play, demonstrating that the installed host can replace such summaries. No later corrective
progress write is present. The store projects the most recently committed record for each item;
the evidence does not show it dropping a later correction.
[Persistence projection](../../src/sylvia_gm/infrastructure/sqlite_continuity.py),
[AR-26 evidence](../../.scratch/alpha-readiness/issues/26-refresh-plan-progress-after-played-consequences.md).

The last observed full structured Plan read remains P003. The correct current scene and newer facts
survive alongside old progress summaries through P105. This supports investigating update and
retrieval behavior; it does not demonstrate that compaction erased a correct progress record or
that an engine lock prevented another act.

## 4. Proposed first changes, all within on-demand preparation

1. Author a finite Arrival episode with specific outcomes and a closing consequence. Define what
   ends it on success, failure or deliberate withdrawal, and what can remain unresolved. For the
   requested Rift experience, use acts as defined playable units; do not impose a universal
   act-equals-real-world-session rule on every third-party or open-world Plan.
2. Present exact references to the current act and its objectives in the existing pointer context,
   without automatically inserting their bodies or any next-act content. Advertise the supported
   item paths accurately.
3. Clarify the existing read instruction: planning a new situation and evaluating an objective's
   outcome are valid reasons to retrieve the current preparation. At those decisions, read it if
   its relevant contents are absent from working context. A title/pointer does not mean the body
   has been supplied. No mandatory read on every turn or hidden player-text classifier is proposed.
4. Align progress instructions: update a materially changed summary even if the status stays
   `engaged`; resolve an objective when its authored observable outcome has occurred; retire or
   defer it honestly when appropriate. Do not derive progress from turn count or fabricate consent.

The exact milestone language is an authoring decision still to be selected. An illustrative Arrival
ending using existing prepared jobs is:

- A first undertaking reaches a defined outcome: cloth delivered or its loss reported; survey case
  returned or its inaccessibility established; injured patrol member brought to help or withdrawal
  and their resulting situation established. A player-created undertaking gets an equally concrete
  endpoint when offered and accepted.
- Play one debrief/aftermath that establishes the actual result, changed relationship and any
  continuing obligation. Unused jobs and unresolved restitution need not prevent closure.
- Conclude the Arrival episode at that consequence. Consult further preparation when a subsequent
  direction is being planned. Refusal can retire this route rather than lead to endless replacement
  chores; an unplayed job is never marked successful.

An alternative could make the supply investigation the defined central undertaking. That would be
a deliberate rewrite of Rift's opening, not an implication of the existing vague objective. The
important choice is the observable outcome and closure, with room for different methods and failure.
This proposal does not automatically stop the app, choose a real-world bedtime, force a particular
solution or preload the next act.

## 5. Existing authoring support and limits

The Builder skill already asks for observable objective conditions and recognisable endings. Its
worked examples describe more concrete finishes, including loss, refusal and abandonment. Rift's
objective falls short of that guidance. Strengthen the actual canonical content and its quality
review before creating a new schema or authoring system.
[Builder skill](../../src/sylvia_gm/agent/skills/build_campaign_plan/SKILL.md),
[combat example](../../src/sylvia_gm/agent/skills/build_campaign_plan/references/combat-plan.md),
[multi-act example](../../src/sylvia_gm/agent/skills/build_campaign_plan/references/multi-act-plan.md).

Existing `completion_conditions`, `situations`, `branches` and `consequences` can express the proposal.
They contain natural-language content, not machine-evaluated narrative predicates. Structural
validation cannot prove that an ending is specific enough. The generic draft tool also wraps an
objective in “The Game Master confirms: …”, which does not make an abstract goal concrete; that is
a scaffold limitation, not evidence of how this Rift objective was originally authored.
[Schema](../../src/sylvia_gm/domain/_content_schema_plan.py),
[Plan validation](../../src/sylvia_gm/domain/campaign_plan.py),
[draft construction](../../src/sylvia_gm/agent/_builder_session.py).

## Verification and remaining uncertainty

Source review, retained exact-input comparison, accepted-update audit and the two pure section reads
were completed. No controlled prompt/content comparison was run, so relative causal contributions
remain unknown. AR-25's search problem is independent and should not be assumed to fix missing
initiative; AR-02's attribution concern remains separate.

After Wan selects a correction, start with the smallest relevant proof: a same-status progress
change that persists in the next request; a focused authored undertaking that reaches its ordinary
closing consequence; and a failure/refusal branch that ends honestly. Verify a necessary exact Plan
read when relevant details are absent, including a continuation boundary where applicable. Use
separate synthetic data and preserve the paused P105 campaign. No new full run, model sweep or
memory refactor is selected by this investigation.
