# Narrative AI prompt and context comparison

Status: research note, not product or architecture authority.

This note answers the prompt-audit question: what useful patterns exist in Hermes/AI Dungeon and
other narrative-agent projects, and which should Sylvia GM adopt or deliberately avoid? Product
decisions remain in the active product and architecture contracts.

## Hermes in AI Dungeon

Hermes 3 is a model option inside AI Dungeon, not a complete game-state architecture. AI Dungeon's
official model guide describes Hermes 3 as highly capable at nuance, subterfuge, dialogue, and
genre writing but unusually sensitive to instruction wording. Its model-specific advice sometimes
changes the role label from “dungeon master” to “storytelling assistant” and recommends explicit
player-agency wording. That is evidence for a small provider/model compatibility layer, not for
putting provider-specific folklore into Sylvia's core GM contract.

AI Dungeon's more important architectural lesson is its context composition. Its documented order
separates instructions, Plot Essentials, triggered Story Cards, story summary, retrieved memories,
recent history, Author's Note, the last action, and script-owned front memory. Required and dynamic
sections receive explicit context budgets. Its scripting API also gives the opening a distinct
`start` action type and exposes a short-lived, owner-only context inspector.

Sylvia should keep the same useful separations while preserving its stronger authority model:

- a stable GM operating contract rather than a campaign-sized system prompt;
- a semantic campaign-start event rather than fabricated player speech;
- structured current scene and durable facts distinct from recent verbatim dialogue;
- bounded relevant source material rather than an unconditional corpus dump;
- exact owner-private audit captures;
- provider-specific formatting only at the transport boundary, without changing gameplay meaning.

Sources: [AI Dungeon model differences](https://help.aidungeon.com/ai-model-differences),
[AI Dungeon context composition](https://help.aidungeon.com/faq/what-goes-into-the-context-sent-to-the-ai),
and [AI Dungeon scripting hooks and inspector](https://help.aidungeon.com/faq/how-do-i-write-scripts-and-use-scripting).

## NarrativeEngine-P

NarrativeEngine-P is the closest public comparison found for long-form GM memory. Its README says
that it keeps the most recent eight messages verbatim, preserves named/mechanical details during
compression, supports pinned memories, and maintains a structured “Divergence Register” for
locations, NPC events, promises, debts, world state, party facts, lore, and rules. Most importantly
for Sylvia's spoiler concern, facts carry `knownBy` permissions and scene witness information is
used to prevent NPC metagaming.

This strongly supports Sylvia's explicit `PLAYER_KNOWN`/`GM_PRIVATE` split, per-character knowledge,
provenance-backed durable memories, bounded recent dialogue, and persistent current-scene state.
Sylvia should not copy the claim that an LLM-facing instruction can *guarantee* secrecy; its engine
should own reveal state and tests should inspect both context and player-visible output.

Source: [NarrativeEngine-P repository](https://github.com/Sagesheep/NarrativeEngine-P).

## SillyTavern and AI Dungeon lore retrieval

SillyTavern World Info dynamically inserts lore entries based largely on keyword activation and
offers ordering, recursion, per-character/persona/chat sources, and context budgets. AI Dungeon
similarly treats Story Cards and Memory Bank entries as dynamic context selected within a token
budget. These systems demonstrate why a full lore dump is wasteful and why source entries need to
be independently addressable.

Sylvia should adopt bounded, provenance-labelled, context-specific retrieval. It should not copy
keyword matching as the authority for mechanic availability, nor rely on player wording alone to
choose crucial current-scene lore. The Campaign Plan's structured scene references and a single
read-only MCP retrieval operation are a better fit: plan/state establish relevance, while the GM
can request additional exact admitted Setting, Plan, Ruleset, or participant material.

Source: [SillyTavern World Info documentation](https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/worldinfo.md).

## DMCP and game-specific function calling

DMCP demonstrates a broad MCP-native GM service with stored secrets, relationships, notes, recaps,
pause/resume state, and reusable setup/resume prompts. It validates MCP as a natural delivery
boundary for deterministic GM operations and reference access. Its broad feature catalogue is not,
by itself, a reason to advertise every possible operation on every turn; Sylvia's Ruleset-driven
current-state projection remains the more precise design.

Research on the *Labyrinth* tabletop game likewise reports that game-specific function calls
improved narrative quality and state consistency. This supports Sylvia's split in which the model
chooses among currently legal operations while the engine owns calculations, commits, and tool
availability.

Sources: [DMCP repository](https://github.com/shawnrushefsky/dmcp) and
[“You Have Thirteen Hours in Which to Solve the Labyrinth”](https://arxiv.org/abs/2409.06949).

## Resulting Sylvia design judgment

The original gut concern was correct. A generic “be a good GM” prompt plus a whole plan and a broad
tool catalogue is weaker than the public patterns above. The right correction is not a much larger
system prompt. It is a compact stable contract backed by explicit data boundaries:

1. Ruleset declarations and authoritative engine state determine the exact mechanic surface.
2. MCP delivers that surface afresh after every commit and provides one bounded reference reader.
3. The accepted Plan supplies current beats and contingencies; the Setting remains retrievable as
   the world bible; neither overrides committed events or mechanics.
4. Recent dialogue, durable memory, current scene, canonical mechanics, and knowledge/reveal state
   remain separate projections.
5. Secrets carry identities, reveal conditions, and `known_by` state; player knowledge changes only
   through a committed reveal.
6. Full Audit shows the exact application request and MCP surface, with transport-specific changes
   separately identified.

The stable prompt should explain these sources and precedence once. Volatile scene facts, lore,
memory, and tool schemas belong in their bounded per-inference channels, not in the system prompt.
