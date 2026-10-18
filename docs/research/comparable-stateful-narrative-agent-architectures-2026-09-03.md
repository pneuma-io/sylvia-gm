# Comparable stateful narrative-agent architectures

Status: research note, not product or architecture authority.

## Short answer

Yes. No public system found here does the whole Sylvia job, but the component ideas are well
established. The closest comparison is a combination:

- Hermes Agent or Letta supplies the repeated model/tool loop and agent-managed recall.
- Inform supplies the authoritative world model and the separation between checking an action,
  changing the world, and reporting what happened.
- Failbetter's quality-based narrative and *Façade* supply ways to keep authored story direction as
  eligible opportunities rather than a fixed script.
- AI Dungeon supplies a practical split between always-present context, retrieved memories, recent
  history, and lore.
- LangGraph supplies durable execution and inspectable application state, while LangSmith shows how
  a trace can retain structure and errors without retaining sensitive inputs and outputs.

The important finding is that these systems do not try to solve every concern in one system prompt.
They divide instructions, current state, long-term reference material, narrative selection,
mechanical execution, and presentation.

## What each system contributes

| System | What maps to Sylvia | Where the comparison stops |
|---|---|---|
| **Hermes Agent** | Its documented turn lifecycle is the conventional agent loop: call the model, execute any tools, append their results, and call the model again until it returns text. Tool failures are returned as error results so the model can react. Hermes also separates stable prompt material from volatile and call-time context, and can load non-core tool schemas on demand. ([agent loop](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop), [tool runtime](https://hermes-agent.nousresearch.com/docs/developer-guide/tools-runtime), [prompt assembly](https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly), [tool search](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search)) | Hermes memory is two small, agent-curated files injected as a frozen session snapshot, plus searchable session history. That is useful personal memory, but it is not an authoritative simulation database. Hermes also treats visible tool calls as an observability principle, which does not account for player-facing campaign spoilers. ([memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory), [architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)) |
| **Letta / MemGPT** | A Letta agent persists its messages, reasoning, tool calls, memory, and tools. One run may contain many model/tool steps. Small memory blocks remain in context and can be updated; larger archival memory is queried on demand. The underlying MemGPT paper describes this as tiered virtual memory under model control. ([stateful agents](https://docs.letta.com/v1-sdk/concepts/stateful-agents), [memory blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks), [archival memory](https://docs.letta.com/v1-sdk/memory/archival-memory), [MemGPT paper](https://arxiv.org/abs/2310.08560)) | Agent-edited prose blocks do not by themselves provide typed game invariants, provenance, or a single current truth. Letta also recommends blocks rather than archival memory for frequently changing state. Its semantic archive is useful for discovery, but an already-known Character File or Campaign Entity still wants exact lookup. |
| **LangGraph** | State has an explicit schema; model and tool nodes can loop over it. Checkpoints preserve each graph step, allow recovery after failure, and separate thread state from longer-lived namespaced stores. ([graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [agents and workflows](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) | It is an orchestration runtime, not a narrative design. It does not decide what a Campaign Plan means, which fact is canon, or what a player should be told. Copying the graph machinery would not fix those boundaries by itself. |
| **Inform** | Inform's world model owns objects, places, containment, visibility, time, and actions. Its action machinery separates `check`, `carry out`, and `report`: establish whether the action is possible, mutate the world, then describe the result. It also treats `look`, `examine`, `search`, and `look under` as different information-seeking actions; `look` only describes what is visible and omits concealed objects. ([world model](https://www.inform-fiction.org/manual/html/s24.html), [action rulebooks](https://ganelson.github.io/inform/standard_rules/var.html), [new actions](https://ganelson.github.io/inform-website/book/RB_6_2.html)) | Inform is a deterministic parser-fiction engine with authored objects and responses. Sylvia deliberately leaves much non-combat judgment to the GM model, so it cannot simply turn every prose action into an Inform command. The action boundaries remain a useful contract even when their implementation is free-form. |
| **Failbetter quality-based narrative** | Storylets are small authored opportunities whose availability depends on mutable qualities. This lets current state decide which prepared material is eligible. Failbetter also advises using a small set of qualities widely and leaving some connective detail to the player's imagination. ([StoryNexus diary](https://www.failbettergames.com/news/storynexus-developer-diary-2-fewer-spreadsheets-less-swearing), [storylets and qualities](https://www.failbettergames.com/news/echo-bazaar-narrative-structures-part-two), [parsimony and "fires in the desert"](https://www.failbettergames.com/news/narrative-snippets-parsimony-2)) | Storylets are prewritten content units and qualities are explicit gates. A Sylvia Campaign Plan is broader and the GM improvises its connective material. Treating every plan beat as a mandatory or immediately eligible storylet would recreate railroading. |
| **Façade** | Its creators split the system into a story world, story memory, believable characters, and a drama manager. The drama manager selects eligible beats in response to what the player has done while pursuing desired dramatic values. This is the closest match for a private plan that says where the GM hopes play will go without declaring the next beat to be current fiction. ([Mateas and Stern paper](https://cdn.aaai.org/Symposia/Fall/2000/FS-00-04/FS00-04-021.pdf)) | *Façade* is a tightly authored one-act drama. Its beats guide coordinated character performances and only a subset appears in one run. Sylvia must also support open campaigns, arbitrary Rulesets, and events no campaign author anticipated. |
| **AI Dungeon** | It keeps AI Instructions, Plot Essentials, Story Summary, Author's Note, and the latest action in a required context budget. Story Cards, retrieved Memory Bank entries, and older history use a dynamic budget. Story Cards describe characters, places, and concepts and enter context when triggered; generated memories compress past actions and are retrieved by relevance. ([context composition](https://help.aidungeon.com/faq/what-goes-into-the-context-sent-to-the-ai), [memory system](https://help.aidungeon.com/faq/the-memory-system), [Story Cards](https://help.aidungeon.com/faq/story-cards)) | AI Dungeon is primarily context-managed story generation. Its docs do not describe a typed authoritative Campaign State or a Ruleset Engine that validates mechanics before narration. Keyword-triggered cards can also be activated by the model's own output, so they are a weak authority mechanism for secrets or current facts. |
| **ReAct and Generative Agents** | ReAct demonstrates the basic reasoning/action/observation loop over external tools. Generative Agents stores a complete natural-language experience stream, retrieves memories by relevance, recency, and importance, and feeds higher-level reflections and plans back into later behavior. ([ReAct paper](https://arxiv.org/abs/2210.03629), [Generative Agents paper](https://arxiv.org/abs/2304.03442)) | ReAct does not define durable commit semantics, and its visible reasoning trajectories conflict with Sylvia's need to conceal private GM deliberation. Generative Agents is aimed at believable autonomous people; model-written memories and reflections are not a safe substitute for the Campaign Aggregate's current truth. |

## The common architecture underneath them

The comparisons point to four different kinds of information. Sylvia already has names for them:

| Concern | Sylvia source | Comparable pattern |
|---|---|---|
| Reusable facts about the world and participants | Campaign Setting and Character Files | Inform source definitions; AI Dungeon Story Cards; Letta files or archival memory |
| What is true in this campaign now | Campaign State and Campaign Chronology inside the Campaign Aggregate | Inform runtime world model; LangGraph thread state and checkpoints |
| Private story opportunities and their progress | Campaign Plan and Plan Progress | Failbetter storylets and qualities; *Façade* beats and story memory |
| How actions are resolved | Ruleset and Ruleset Engine | Inform action rulebooks and runtime |

This separation matters more than the exact storage technology. A Character File can say where Arya
usually lives while Campaign State says she is currently in hospital in the capital. A Plan can say
that her history may become relevant without making that history known to the player or declaring
that its reveal scene is happening now.

## A provisional reference loop for Sylvia

The simplest synthesis worth testing is one GM model in a durable loop:

1. Read the player's exact input and the compact current Campaign State projection.
2. Decide whether the present decision requires more information. Use exact identity lookup for a
   known Campaign Entity, Character File, location, Plan item, or rule; use semantic retrieval to
   discover an unknown relevant record.
3. Interpret the Campaign Plan privately as a set of possible pressures, leads, and later reveals.
   It may influence the decision, but does not become a fact merely because it was retrieved.
4. If the Ruleset assigns the action to deterministic resolution, call the Ruleset Engine and use
   its committed result. Otherwise, the GM adjudicates it in prose.
5. Submit the settled narration with the Campaign File disposition. The owner validates and commits
   any Campaign State, Chronology, knowledge, or Plan Progress changes before showing the narration
   to the player.
6. Continue the same model/tool loop when a read, engine result, or rejected write changes what the
   GM needs to decide.

Hermes and Letta show that a multi-call turn is normal. Inform supplies the missing discipline:
understand the action, determine its effects, then report those effects. For `I look around`, the
useful analogue is Inform's `look`, not `search`. It should expose the immediately visible scene and
leave close inspection, provenance, age estimates, and hidden clues for later actions unless the
Ruleset or established fiction says otherwise.

## Observability without spoilers

None of the narrative systems surveyed provides Sylvia's exact two-audience requirement. Hermes
normally exposes every tool call. AI Dungeon lets the player inspect the assembled model context
through its [context viewer](https://help.aidungeon.com/how-do-i-manage-context).
Either can reveal private preparation in a GM-led campaign.

LangSmith demonstrates a lower-level pattern: a trace may keep structure, timing, metadata, and
errors while replacing its inputs and outputs with empty or redacted values, including on a
per-request basis. ([sensitive-data masking](https://docs.langchain.com/langsmith/mask-inputs-outputs),
[conditional tracing](https://docs.langchain.com/langsmith/conditional-tracing)) This suggests two
separate views for Sylvia: a player-safe activity feed that identifies the kind of work and whether
it succeeded, and owner-only Admin Full Audit with exact requests, arguments, results, and model
responses. The public layer needs meaningful distinctions such as reference lookup, Ruleset
resolution, and Campaign File review; eight identical `Action > Completed` rows carry no useful
information.

## What this changes about the current diagnosis

The recent prompt iterations may be asking prose instructions to carry too much architectural
weight. A single model response has been expected to infer action scope, distinguish current facts
from background lore, choose whether a planned lead should surface, simulate perception, update
memory, and write polished prose. Comparable systems put several of those decisions behind explicit
state and action boundaries.

That does not prove Sylvia needs a larger non-combat engine. It does suggest a smaller question for
the next design discussion: what compact, authoritative present-scene information does the GM need
before it can freely narrate, and what must it deliberately query or establish before revealing
more? Prompt wording can then teach the model how to use those boundaries instead of trying to
create them by itself.
