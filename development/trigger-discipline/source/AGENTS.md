# Trigger Discipline Vault Instructions

This repository is a Canvas-first Obsidian rules vault.

## Single source of truth

The atomic Markdown notes are the canonical rules corpus.

Do not create a second rules summary, parallel canon, decision ledger, or agent-only rules source that duplicates them. Canvas files are navigation only and must point at the canonical Markdown notes rather than restating rules.

This file explains repository structure and editing conventions only. It is not a rules source.

## Human-first navigation

The creator primarily navigates the ruleset visually and does not want to read long documents.

The intended human interface is:

1. `Trigger Discipline.canvas` — visual home page.
2. `Maps/*.canvas` — curated domain maps.
3. Canonical Markdown cards — click a card when detail is needed.
4. Local Graph — optional relationship exploration around one active note.
5. Global Graph — optional diagnostic/overview tool, not the primary UI.

Canvas layout should be deliberately curated. Do not rely on force-directed graph layout for primary navigation.

## Canonical note design

Each canonical Markdown note should:
- represent one meaningful mechanic, state, action, class, specialty, skill, aptitude, or system concept;
- have a short `At a glance` summary immediately below the title;
- contain the fine detail needed by agents and rules execution;
- use frontmatter/tags for type, domain, and design status;
- preserve uppercase mechanics labels where appropriate;
- link only to genuine mechanical/conceptual neighbors.

Long detail is allowed when the concept genuinely needs it. Do not split rules merely to make files tiny.

## Canvas design

Canvas files may:
- group concepts spatially;
- show parent/child relationships and process flows;
- link to other Canvas maps for zoomed-in exploration;
- use short navigation labels or non-canonical orientation text;
- use file nodes that reference canonical Markdown notes.

Canvas files must not become a second rules source. If a Canvas statement conflicts with a Markdown note, the Markdown note wins and the Canvas should be corrected.

Prefer multiple focused maps over one enormous board.

Current atlas:
- `Trigger Discipline.canvas`
- `Maps/Character & Classes.canvas`
- `Maps/Aptitudes & Skills.canvas`
- `Maps/Classes.canvas`
- `Maps/Combat Classes.canvas`
- `Maps/Bulwark.canvas`
- `Maps/Corpsman.canvas`
- `Maps/Influencer.canvas`
- `Maps/Combat.canvas`
- `Maps/Firearms.canvas`
- `Maps/Technology.canvas`
- `Maps/Hacking.canvas`
- `Maps/Drones.canvas`
- `Maps/Social & Investigation.canvas`
- `Maps/Equipment & World.canvas`
- `Maps/CQC.canvas`
- `Maps/Explosives.canvas`

## Canon status

Use status metadata such as `locked`, `provisional`, `parked`, or `review`.

- `locked` — deliberately accepted canon.
- `provisional` — current working direction, not final.
- `parked` — intentionally deferred.
- `review` — previously recorded material whose authority or boundary now needs explicit reconfirmation.

Do not silently convert unresolved ideas into locked rules. Open questions belong in the relevant canonical note. Git history is the historical decision record.

## Agent workflow

Before changing a rule:
1. Read the relevant canonical note and direct neighbors.
2. Update the canonical Markdown note itself.
3. Update wikilinks only when the conceptual relationship actually changes.
4. Update any affected Canvas map when the human visual model should change.

Do not maintain derived long-form rulebooks in the repository unless explicitly requested. Generate temporary summaries from canonical notes when an agent or chat session needs them.
