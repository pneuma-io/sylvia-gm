# Sylvia context limits and conversation compaction

The latest failure was the seventh model request during a new Builder's automatic opening.
Six requests had succeeded while the agent loaded its skill and reference material. The chat had
not yet received the editing brief. Sylvia rejected the next request locally; Codex had not reported
that this request exceeded its actual context window (the amount the model can work with at once).

The recommended solution has two parts: correct request budgeting, then add explicit management of
the model's working context. Preserve the saved conversation and authored files throughout. A
smaller reply setting alone does not fix the accounting, and enabling Codex compaction alone does
not fit the current adapter's lifecycle.

This is advisory research, checked on 13 September 2026 against checkout `b251b1d` plus the current
uncommitted PES changes, the selected synthetic installed-test audit, and current official OpenAI
documentation. It selects no implementation or contract change. No live inference was needed.

**What happened in the three fresh chats?**

| Builder | When it stopped | Successful model requests before the stop |
|---|---|---:|
| Companion | Automatic opening, before asking for or receiving the player's concept | 5 |
| Player Character | After its opening and the first character-creation brief | 18 across both stages |
| Greyhaven Campaign Plan | Automatic opening, before the source-adoption brief | 6 |

One player-facing response can involve many model requests: the model asks for a file, Sylvia
returns the result, and the model requests something else. Thus this was a continuation of the
opening's tool loop (the repeated model/tool exchanges), not a long-established conversation.
The separate older Greyhaven chat also hit the local guard, but that is a different history size.
[Installed findings](../../.scratch/player-experience-strategy-2026-09-06/issues/104-fresh-sol-companion-exhausts-context-during-opening.md).

The fresh Plan opening loaded a roughly 16,654-character skill, read a 19,630-character Ruleset
identity document, obtained a 22,409-character content definition, listed references, and requested
32,000 characters of the saved Plan. Each result increased the next input. Large reference reads
are a contributor; the evidence does not show that they filled the actual model window.

| Measurement | Value | Meaning |
|---|---:|---|
| Sylvia's configured context cap | 262,144 | Local operating setting |
| Codex-reported context window | 258,400 | Runtime metadata from the sixth completed request |
| Sixth request's local input bound | 109,549 | Serialized bytes treated as tokens |
| Sixth request's reported input usage | 27,724 | Codex's measured input-token report |
| Sixth request's reported output usage | 172 | Includes the reported reasoning usage; not a forecast for later requests |
| Seventh request's local input bound | 149,210 | No actual token count, because Sylvia blocked it |
| Reply reserve added by Sylvia | 128,000 | Local reservation, not an enforced Codex output control |
| Seventh request's calculated requirement | 277,210 | 149,210 + 128,000; exceeds Sylvia's cap |

The local bound was about 3.95 times reported input usage on the sixth request. This comparison
does not provide a tokenizer for the seventh request or a conversion ratio that is safe for every
language, file, tool schema, or model. It does establish that the local rejection should not be
reported as observed model exhaustion. The runtime window is also not a report of the active
automatic-compaction threshold. [Run record](../../.scratch/player-experience-strategy-2026-09-06/live-greyhaven-adoption-2026-09-13.md).

**Is 128k too much for a reply?**

As an automatic reservation for every Builder request, my assessment is yes. As a model capability
ceiling, it can be correct. Official Sol API documentation lists a 1,050,000-token context window
and 128,000 maximum output tokens. These published API limits do not establish the effective
window available through Sylvia's subscription connection. Raising Sylvia's setting to the API
maximum would not prove that Codex accepted that operating window.
[GPT-5.6 Sol specifications](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Sylvia currently combines three different ideas in its output setting:

- the model's maximum supported output;
- the output limit requested for this particular response;
- the headroom used to decide whether the input should be sent.

The [request guard](../../src/sylvia_gm/provider/_context_fit.py) defaults the second and third to
the model's maximum. The [subscription adapter](../../src/sylvia_gm/provider/_chatgpt.py) labels
`max_tokens` as runtime-managed and does not forward it to Codex. The installed app-server schema
has no direct per-turn output-limit parameter. An arbitrary configuration dictionary is not proof
that a particular output-limit option exists or is enforced.

For providers with a verified output-limit control, I recommend testing a **32,000-token Builder
response budget** as an initial default, with an explicit larger setting available when needed.
That is a proposed starting point, not a measured optimum or a change to existing user settings.
This budget includes thinking and tool arguments as well as visible prose. OpenAI's reasoning
guide recommends starting experiments with at least 25,000 tokens for reasoning and output, then
adjusting from observed usage. A 172-token file-read request is not evidence that all future work
needs only a few hundred tokens.
[Reasoning and output budgets](https://developers.openai.com/api/docs/guides/reasoning).

For Codex subscription models, display **Output budget: managed by Codex** unless a supported
control is verified. Sylvia may use planning headroom for estimates and compaction scheduling,
but must label it as a heuristic, not an enforced maximum. Do not automatically reduce an explicit
GM output setting: that behavior has a separate current contract.

**What context management exists today?**

| Area | Current behavior | Limitation |
|---|---|---|
| Saved Builder chat | Reconstructs model history from SQLite session records | No durable semantic checkpoint for long conversations |
| Codex subscription adapter | Creates an ephemeral thread for each request, injects history, stops at the first Sylvia tool call | Runtime conversation state is discarded between requests |
| Codex compaction | Adapter stops when a `contextCompaction` item starts | No accepted native compaction continuation |
| Builder file reads | Ordinary `read_file` bodies are omitted from durable tool receipts, with a content hash retained | This is selective result projection, not conversation summarization; other reference results remain large |
| Older Builder runner | Clips its in-memory history to the last 50 messages after a text response | Message-count clipping has no semantic preservation or token budget |
| Active GM path | Uses current campaign data, durable Campaign File notes, and bounded recent dialogue | This is a separate context projection, not a general Builder compactor |

The 50-message behavior is reachable through the current Builder runner. A synthetic streaming
probe supplied 60 history messages and received one fake completion: all 60 were sent, then the
runner's snapshot contained 50 entries. It used no tools, inference service, or persistent history
file. This proves the clipping behavior; it does not prove deletion from the saved SQLite chat or
an installed loss-of-work incident. Its interaction with incremental persistence and steering
needs focused verification before relying on long Builder sessions.
[Builder runner](../../src/sylvia_gm/agent/_runner.py),
[saved Builder adapter](../../src/sylvia_gm/agent/_builder_session.py).

There is older summarization and overflow-retry code, but the canonical Builder stream explicitly
sets `retry_on_length=False`, and the old DM runner is retired. The active GM projection selects
recent dialogue with a 24-message bound alongside current state and other evidence. Neither fact
establishes a supported, general-purpose automatic memory system.
[Current GM provider adapter](../../src/sylvia_gm/agent/_provider_session.py).

**Why not just turn on Codex's compaction?**

Codex documents an automatic-compaction threshold and a separate context-window setting. It also
offers `thread/compact/start`, with completion reported through events. Those controls operate on
a Codex thread. Sylvia currently throws that thread away at each tool handoff and reconstructs the
next one from its own history. Allowing compaction during one request would therefore not retain
the resulting working context for the next request.
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[app-server thread lifecycle](https://learn.chatgpt.com/docs/app-server).

There are two coherent ownership choices:

| Choice | Benefit | Cost for Sylvia |
|---|---|---|
| Keep a continuing Codex thread | Native compaction and provider conversation continuity | Synchronize tool results, current state, instructions, interruption, reconnects and model switching between Sylvia and Codex |
| Keep a continuing working-context record in Sylvia | One memory policy across subscription, API and local models | Implement bounded checkpoints, retrieval and explicit compaction recovery |

I recommend **Sylvia-owned working context** for the current product. Sylvia already owns saved
sessions, content snapshots, tool execution and provider switching. A persistent Codex adapter is
a reasonable later optimization, especially for reasoning continuity, but it would not solve
long conversations on the other providers. Its benefits should be measured before selecting that
larger lifecycle change. Ordinary text summaries cannot reproduce opaque provider reasoning.

OpenAI's Responses API has a separate compaction interface that returns encrypted continuation
state. That state must be carried into later requests; explicit compaction also requires input
that still fits. This is not evidence that Sylvia can use API endpoints with subscription login,
or move their opaque state between unrelated providers.
[Responses compaction](https://developers.openai.com/api/docs/guides/compaction).

**The proposed memory design**

Keep three things distinct:

| Record | Purpose | Authority |
|---|---|---|
| Saved conversation | What the player and agent actually said, plus durable completed-operation records | Retained chronology; compaction does not replace it |
| Current files and campaign data | What has actually been authored, validated, saved or resolved | Existing file versions, package snapshots and engine records remain authoritative |
| Working context | What the model receives for its next request | A smaller, replaceable view assembled from the first two |

A **checkpoint** (a saved continuation note) would record the current task, accepted decisions,
unresolved questions, source identities, relevant file paths and versions, validation findings,
and the next unfinished step. It would identify which completed portion of the conversation it
covers. Recent exact messages and complete tool exchanges stay beside it. Old conversation remains
available through bounded search/read tools, so a disputed detail can be checked against its source.

The application should carry exact structured selections, package identities, and file versions
directly. The summarizing model supplies explanatory notes, not replacements for those facts.
Record accepted corrections with their source messages and preserve superseded decisions as
history. Do not turn guesses, quoted document instructions, rejected tool calls, or future plans
into trusted instructions or completed events. A checkpoint remains fallible: validate its
structure and source references, retain the original, and test the decisions it must preserve.
Use conversation content and operation evidence for this note; do not add private model reasoning
to saved history as part of compaction.

For Builder, large authored content belongs in files. Use the existing copy/read/edit tools so
the model can change a small part without reproducing a whole Plan in every response. Keep recent
read results while they are useful, then retain a retrievable reference to the exact version.
If an older version is not retained, say so instead of presenting a reread of the current file as
the old result. Neither a smaller page size alone nor repeated maximum-size pages solves cumulative
growth. The initial improvement should use existing readers and copy operations, without adding
a vector database or a general retrieval platform.

For GM, refreshed engine state and committed Campaign File notes must override conflicting memory
notes. HP, AP, stance, items, action outcomes and character ownership never come from a summary.
Private preparation stays separate from player-known history. The deterministic Campaign Interview
can continue using its exact questions and answers; it does not need the same policy as a long
free-form Builder chat merely because both appear in the sidebar.

**When should compaction run?**

Check size before every model request, including requests inside one player-facing response.
Waiting for the next player message would miss the opening failure seen here. Compact only at a
completed exchange boundary: never split a tool call from its result or replay an already committed
operation. Within a long tool loop, the entire unfinished player intent, current decisions and
committed results remain available even when older completed exchanges are summarized.

The trigger must leave room for the next response, an allowed large tool result, measurement
uncertainty, and the compaction operation itself. As a prototype setting for the observed 258,400
window, starting near **180,000 estimated input tokens** and shrinking toward **100,000–120,000**
would be a reasonable experiment. These are my proposed trial values, not Codex defaults or a
guarantee of fit. Recalculate them for the selected model and its actual supported output control;
do not copy a universal 128k reserve or universal message count across providers.

Build the checkpoint from a bounded completed segment that itself fits. Commit the new working
context only after a successful checkpoint result and revision check. If the player steers or
files change during preparation, retain that newer information rather than replacing it with an
older note. If compaction fails, keep the previous working context and show a recoverable stop.
For an already oversized saved chat, a bounded recovery pass can process old segments progressively;
do not submit the whole oversized history to the same summarizer or create an endless retry loop.

Once approved as normal behavior, automatic compaction should show a brief progress message and a
retained marker such as “Working context shortened; saved conversation retained.” It should not
require permission every time. A manual Compact action can use the same operation. Any summary
display must respect the session's visibility rules; GM-private memory must not appear in a public
chat card.

**How to correct admission now**

Prefer an authoritative request counter when the selected transport actually supports one.
OpenAI documents `POST /v1/responses/input_tokens` for Responses payloads, including tools.
That does not establish a counter available through this Codex subscription adapter. A local
model-aware tokenizer can improve estimates, but cannot certify unseen runtime additions.
[Token-counting documentation](https://developers.openai.com/api/docs/guides/token-counting).

For subscriptions, I recommend making Codex the final authority on request acceptance. Keep local
measurement for warnings, diagnostics and compaction scheduling. Distinguish a measured over-limit
input, an estimated risk, and a runtime rejection; do not turn failure to prove fit into a claim
that the input is too large. A conservative byte bound is especially unsuitable as a rejection
test: exceeding an upper bound's budget does not prove the actual count exceeds that budget.
The current measurement also precedes Codex's final input construction, so it cannot prove the
size of hidden runtime additions.

This still needs defined recovery when Codex rejects or requests compaction. Preserve completed
work, stop the current generation, prepare a smaller working context through the chosen mechanism,
then continue only from unresolved work. Until Sylvia has that recovery, delegating acceptance is
an immediate admission correction with a known remaining limit, not the full long-term solution.
Do not disable or raise Codex's own compaction threshold to manufacture a pass.

**Recommended delivery boundary**

1. **PES-104:** separate capacity, enforced output limit and estimated headroom; correct subscription
   admission and error classification; retain runtime-window observations; make targeted improvements
   to large reference responses; verify the existing Companion, Character and Plan failures.
2. **Saved Builder context management:** add the checkpoint and retrieval path, replace unsafe
   message-count clipping, handle continuation/restart/model switching, and exercise compaction
   inside a tool loop. Give this a focused child ticket under PES-02 when the design is selected,
   linked to PES-104. It is separate from PES-08's deferred ruleset-testing sandbox.
3. **GM integration:** reuse the working-context mechanism where needed while preserving campaign
   state, private-knowledge boundaries and the GM's existing output-setting contract. Verify one
   representative long campaign continuation rather than reopening completed mechanic matrices.

A proportional acceptance set should prove that the fresh openings finish, true overflow is
reported, output controls behave as labelled, a long Builder survives compaction and restart,
an early player correction survives multiple checkpoints, old source text can be retrieved, and
tool operations are neither repeated nor marked successful by a summary. Cancellation, failed
checkpoint generation and a smaller-model switch should preserve the saved work. Then exercise
one installed Sol journey through a real compaction and one existing non-Codex provider path.

Basic compaction is appropriate before promising unrestricted long-running Builder sessions.
It is not the explanation for this premature fresh-chat rejection. The current product contract
requires pre-send proof and forbids silent construction-history compaction; approval would need
to distinguish preserved saved history from explicitly compacted model context and select the
subscription admission exception. The broader design above remains a proposal.
[Product contract](../product/alpha-launch-contract.md),
[architecture contract](../architecture/alpha-architecture-contract.md).
