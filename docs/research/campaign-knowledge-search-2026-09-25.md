# Campaign knowledge lookup: AR-25 design assessment

Date: 2026-09-25

Status: Supporting research, not implementation authority. Wan subsequently agreed to the bounded
approach on 2026-09-25; [AR-25](../../.scratch/alpha-readiness/issues/25-improve-natural-campaign-knowledge-discovery.md)
owns the ready implementation brief and focused acceptance. No code or candidate change, live test,
campaign mutation or AR-01 resumption occurred during this assessment or subsequent ticket update.

## Assessment

On-demand access remains appropriate for Sylvia: it preserves the GM's access to exact authored
material and saved campaign facts without placing the entire corpus in each request. The current
matching implementation is too brittle for its advertised natural-question interface. Neither a
one-line switch to any-word matching nor an unmeasured semantic-search replacement is an adequate
recommendation by itself.

Prefer direct references when the needed item is already identified, ranked lexical discovery
when its reference is unknown, and an honest browse/refine path when discovery is inconclusive.
This is a recommendation for a bounded AR-25 correction, not a new memory architecture.

## Current implementation and existing strengths

- The model-facing query description accepts a natural question. The matcher removes a short
  stopword list, requires every remaining term as a substring of one candidate, and ranks surviving
  candidates by identity matches and occurrence counts. It has no word-frequency or document-length
  normalization. [Tool description](../../src/sylvia_gm/agent/_stateless_mcp.py),
  [matching and discovery](../../src/sylvia_gm/application/authority.py).
- Discovery returns references, source kind, visibility and provenance, followed by an exact read.
  Its output is not authoritative campaign memory. Campaign State overrides reusable preparation.
  The architecture explicitly permits changing the search strategy without changing that contract.
  [Architecture, section 13](../architecture/alpha-architecture-contract.md).
- Direct reading already exists. AR-27 now supplies exact current-act and objective references;
  those reads need no search. Do not reimplement that work or inject the next act automatically.
  [Current-act projection](../../src/sylvia_gm/agent/_provider_session.py).
- Existing tests cover exact names, a short natural question, state outside the automatic context
  limit, no matches, bounded output, source precedence and private preparation sections. They do
  not establish robust longer-question ranking. [Representative tests](../../tests/test_alpha_05_rift_journey.py).

## Small source-level probe

A read-only Python probe extracted the real term, scoring and Markdown-section helpers from
`authority.py` and applied them to 218 sections from public Severed Lands NPC/location source files.
The comparison changed only the mandatory `all` match to `any`, retaining the same scoring.
Queries were illustrative, not copied from raw Audit. No provider call or installed owner/UI was
exercised; these counts describe this subset, not whole-app search results or measured latency.

| Illustrative query | Current matcher | Naive any-term variant |
| --- | --- | --- |
| `Stern` | 9 candidates; Stern first | Same |
| `Stern characterization` | No candidates | 9; Stern first |
| `Tell me about Stern and her personality` | 1; Castellan Voss, not Stern | All 218; Stern absent from the first three |
| `Waystation geography` | No candidates | 27; Waystation sections first |
| `person who definitely does not exist` | No candidates | 195 candidates |

Voss's authored profile calls him "stern" as an adjective. Literal matching does not distinguish
that mention from the named person. These observations demonstrate why exact name/title matches,
word-aware matching and relevance ranking matter together. They do not select query-specific
exceptions, claim that a name always identifies only one character, or prove a replacement.

## Alternatives and evidence

**Direct navigation.** Known references avoid a search decision entirely. Compact indexes can help
with discovery while leaving source bodies on demand. Anthropic's agent-context guidance describes
lightweight references and runtime reads, while acknowledging exploration latency and dead ends.
That is supporting engineering experience, not proof of an optimal design for Sylvia.
[Primary source](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

**Ranked lexical search.** SQLite FTS5 provides tokenized queries, field weighting and BM25 ranking,
which considers term frequency, document length and corpus frequency. It is an implementation
candidate, not a selected dependency or migration. FTS5's whitespace-separated terms default to
AND: merely adopting FTS5/BM25 would not fix AR-25 without appropriate candidate query construction.
An index would also need to track exact package versions and current campaign records correctly.
[Official documentation](https://www.sqlite.org/fts5.html).

**Semantic or hybrid retrieval.** Meaning-based retrieval could help queries whose wording differs
from the source. It also adds model/index lifecycle decisions and needs evaluation against exact
names and authoritative current records. The 2021 BEIR evaluation found BM25 a strong baseline
across its datasets; dense retrieval was not universally superior and better average reranking
methods involved higher computation. It is historical evidence, not a 2026 leaderboard or an RPG
benchmark. It supports comparing alternatives on Sylvia's actual tasks instead of assuming that
embeddings are automatically better. [Research paper](https://arxiv.org/abs/2104.08663).

## Recommended scope and proof

1. Preserve existing exact reads and current-act navigation. Search is for finding unknown
   references, not a required step before every read.
2. Improve discovery with token-aware matching, strong name/title matches, useful multi-term
   ranking and handling of weak/common-term overlaps. Prefer a conventional lexical baseline over
   an expanding collection of special cases. Choose the implementation after comparing the small
   representative query set; do not assume a persistent FTS index is required.
3. Retain readable references, source/visibility/provenance labels, legitimate no-match outcomes
   and Campaign State precedence. Search-result excerpts would change the current references-only
   contract and are not part of this recommendation.
4. Compare retained valid AR-25 misses with illustrative longer questions, exact names, ambiguous
   names, unrelated queries and conflicting current/baseline records. Check whether the intended
   source appears among the few returned candidates and whether wrong candidates crowd it out.
5. Follow any selected correction with the existing focused installed GM check. Observe whether
   it reaches the right reference with fewer failed searches; a scorer-only check cannot prove this.

No approach guarantees discovery for every natural-language question. Search also cannot ensure
the GM recognizes that it needs information or interprets a retrieved source correctly. Consider
semantic retrieval only if representative paraphrase failures remain important after a sound
lexical baseline and direct navigation; no such comparison has yet established it as necessary.


## Implementation comparison, 2026-09-25

The selected implementation uses an ephemeral Python BM25 ranker over the existing owner-collected
SQLite bytes. It adds name/path and phrase weight, conservative IDF-weighted coverage for body-only
hits, distinct-document ordering, and current named entity precedence. It retains no derived index
or additional database. Generic section headings alone are weak evidence; duplicate heading slugs
that the exact reader cannot resolve are omitted. Source restrictions, visibility and reference-only
output remain with the owner. Lexical ambiguity still exists: “stern” can also be an adjective, so
the model must read candidates rather than treating a hit as an established identity or fact.

On the same 218 public NPC/location sections, a conventional body-only BM25 OR comparison improves
recall but admits many weak results. These are helper measurements, not installed-provider proof:

| Query | Previous matcher | Body-only BM25 OR | Selected ranker |
| --- | --- | --- | --- |
| Stern | 9 hits; Stern first | 4 hits; Stern first | 4 hits; Stern first |
| Stern characterization | 0 | 4 | 2; Stern first |
| Tell me about Stern and her personality | 1; Voss | 152; Stern first | 4; Stern's Personality first |
| Waystation geography | 0 | 24; Old Finch first | 11; Waystation Overview first |
| person who definitely does not exist | 0 | 111 | 0 |

This comparison supports field-aware local ranking over a plain OR change. SQLite FTS5 would add
index lifecycle work without a demonstrated need at this boundary. The selected ranker took roughly
25–26 ms per query on this helper corpus during the build; that is not whole-owner or provider
latency. No semantic/paraphrase guarantee is claimed. The real-owner tests replay the retained
P002/P003/P058 query wording, source restrictions, exact reads, ambiguity, bounds and no-match cases.
For the broad “Anchor Doors history public records opening” query, the world overview's Anchor
Doors section is the leading Setting reference; a Waystation-specific question finds Waystation.
The run record and ticket own actual installed verification and completion status.
