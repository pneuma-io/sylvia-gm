# Sylvia launch work

For launch mechanics, content, imports/builders, schemas, defaults/discovery, examples, tests, CI or packaging, read `docs/launch-compatibility.md` before editing. Finish by accounting for every affected item and verifying actual Lite compatibility, content equivalence and the relevant final-byte checks. Keep first-party Full work in its labelled inactive archive and preserve the multi-ruleset architecture.

## Canonical repository guidance

# A letter from Wan

Hi there, I'm Wan, creator of Sylvia GM and owner of Pneuma IO.

You're going to be working in my repo, so there's a few big things you should know about the project
and myself.

A few main things about myself:

1. I aim to build the single-best choice for AI Roleplaying Games and if possible, build a living
   from it, dedicating lots of my resources to its future development if there is sizeable public
   interest.
2. I aim to make user-friendly AND agent-friendly, maintainable software, comments in code should be
   informational, eg comment what a function does directly above its declaration, not a comment on
   every second line within the function body.
3. I aim to make things simple, if I am implementing a feature or trying to solve a problem, I do not
   want it to be enterprise-grade, I want to find a genuinely simple solution that does not
   compromise the end result in any way. I do NOT want to make half-baked software that collapses the
   moment 100 users test it, but I do NOT want to spend unnecessary time creating over-complicated
   testing frameworks, testing should be grounded, prove what it's supposed to prove, and always made
   with the understanding that I am a single person who wants to actually ship stuff in a timely
   manner.

Now the project:

This is Sylvia GM, an AI Agent harness that aims to, rather than doing general assistant
or coding work, instead acts as a TTRPG GameMaster, and helping build TTRPG experiences at all
levels, there are 2 main "modes" for the User's chosen LLM to be operating in within Sylvia GM:

1. **GM Mode** - Via context/prompt injection/instructions, the LLM utilizes the tools you'd expect
   of an AI Agent (Especially MCP) to read pre-planned Campaign Plans (story lines for the GM to
   follow, with varying levels of railroading or open-worldness) and execute them while also writing
   campaign history for long-term memory, it utilizes both traditional LLM inference driving the
   experience when instructed, but also with MCP interactions with a built-in game engine, allowing
   many calculations and operations to be "offloaded" from the AI and instead calculated
   deterministically via a game engine, the game engine that can be interacted with and what
   artifacts it outputs as additional visual data, is defined by a user-provided "Ruleset" datatype,
   a Ruleset defines how the "game" works, when the Agent is in control and when it must wait for the
   game engine or a player prompt to proceed, etc. This is combined with a "Campaign Setting" (Eg,
   D&D + Forgotten Realms) to create a full framework in which a Game Master could plan and run a
   campaign. Finally with a pre-planned Campaign either loaded or generated, this becomes (eg
   D&D+Forgotten Realms+Some Guy's Isekai one-shot OR Baldur's Gate Campaign OR something that says
   "Player Spawns and then it's fully open world also here's 70 side quests and their locations if
   they feel like it") a fully playable experience.
2. **Builder Mode** - A traditional AI Agent repertoire, can theoretically write code files and
   stuff but we aren't designing around that general-use, this will primarily use traditional Agent
   Skills (eg build-ruleset, build-cs etc.) to create and modify Rulesets, Campaign Settings,
   Character Files, Campaign Plans etc.

Please enjoy your time in my Repo

On 16 October 2026, Daniel Young, operating under the Pneuma IO brand, adopted standard
PolyForm Noncommercial 1.0.0 with Sylvia Additional Permissions revision 3 for material he is
authorised to license, with the stated scope and exclusions. See [licensing status](LICENSING-STATUS.md).
Prior grants and third-party terms remain preserved. Official publication and release acceptance
remain separate project decisions.

# Repository operating rules

Product requirements, architecture, delivery process, and current status each have one authority
listed in [`docs/INDEX.md`](docs/INDEX.md). This file explains how to work here; it is not another
product specification.

## Start here

1. Check `git status --short --branch` and HEAD before editing. The worktree may intentionally be
   dirty.
2. Read [`docs/INDEX.md`](docs/INDEX.md), then only the active product, architecture, process, and
   plan documents relevant to the task.
3. Treat archives, historical ledgers, generated handovers, summaries, chat records, and tool output
   as evidence—not current authority.
4. Reconcile claims against live source and tests before reporting completion.

## Build simply and honestly

- Implement the smallest honest solution that produces the intended user-visible result.
- Do not silently expand plain product intent into enterprise architecture, exhaustive automation,
  evidence bureaucracy, or a certification program.
- Prefer a playable vertical over a broad unfinished foundation.
- Reuse proven primitives. Ordinary content or declarative data using an established path does not
  need every lower-layer test repeated.
- Fail clearly when behavior is unsupported; do not fake success or claim unimplemented precision.
- Keep the core ruleset-neutral. Ruleset-specific behavior belongs in packages unless an active
  contract explicitly selects a generic core primitive.
- Do not invent a future bundled original ruleset before its dedicated PRD is approved and promoted.

## Keep closed work closed

- The current human-authored task is the implementation scope. Completed milestones are background,
  not an invitation to resume their backlogs, broaden their automation, or rerun their specialist
  matrices.
- A failure in an unrelated historical or completed-milestone test does not authorize fixing that
  subsystem. Confirm that the current change did not cause it, record it as out of scope when
  necessary, and continue the selected task.
- Keep completed-milestone evidence separate from current scope. Read the product contract
  section 3.2 for the current Lite launch boundary before selecting Ruleset work. Historical Full
  spell selections do not expand it. Shared mechanics and declarative data do not need bespoke
  tests per spell; the current human-authored task selects implementation and verification.
- Manual-test correction is product work. When Wan is testing an installed candidate, prioritize the
  reported UI/runtime defect, its owning boundaries, and a replacement installed candidate.

## Code and comments

- Keep modules and interfaces direct, readable, and maintainable by one person.
- Comment a function's purpose, non-obvious invariant, or important tradeoff near its declaration.
- Do not narrate obvious operations with line-by-line comments.
- Avoid speculative abstractions, compatibility layers, and extension systems without a demonstrated
  current need.

## Test proportionately

- Follow [`docs/operations/quality-preserving-development-process.md`](docs/operations/quality-preserving-development-process.md).
- Live Sylvia testing uses the Windows environment selected for the current run in that process
  and the active plan. The old test VM is retired. Using Wan's desktop or Computer Use requires
  his explicit selection for the current run; component and compiled-owner checks retain their
  own qualifications and do not close native-input acceptance.
- Start with the smallest authentic test that proves the changed behavior.
- Test shared invariants deeply at their owning boundary, then use representative composition tests.
- Do not equate a release boundary with every test in the repository. Verify the supported journeys,
  changed boundaries, direct consumers, packaging, and installed artifact selected by the active
  task. Run a repository-wide suite only when the change genuinely has repository-wide reach or Wan
  directly requests it.
- Keep a strict relevance fence: an unrelated suite is not an adjacent gate merely because it is
  available or historically ran at another milestone.
- Test counts, evidence packets, independent reviews, and repeated freeze/closeout cycles are not
  goals. Use them only when the actual risk warrants them.
- Use `.venv/Scripts/python.exe -m pytest -s ...` for Python verification on this checkout.
- Run `bash scripts/check-docs-index.sh --worktree` for authority-document changes and `git diff --check` before
  closeout.

## Protect the repository and its users

- Never reset, clean, stash, switch branches, rebase, rewrite history, or overwrite unrelated work
  in a dirty tree.
- Exactly one worker writes the live worktree. Other workers are read-only unless ownership is
  explicitly transferred. Fluff coordinates Codex tasks with bounded scope, file ownership,
  acceptance criteria and a mandatory return report. Use current Project Desk decisions and the
  synchronized repository guidance; historical ownership statements do not assign a writer.
- Staging, commits, pushes, deployment, publication, destructive data operations, spending, and
  credential access require direct authority in the current human-authored task.
- A request to test Sylvia authorizes enabling Admin Full Audit and inspecting the selected test's
  exact model input and output. Enable it before the first tested action, keep it enabled throughout
  the test, and do not ask for separate approval.
- Stop for a genuine unresolved product, architecture-class, privacy, security, destructive-data,
  spending, publication, legal, or material-risk decision. Continue safe independent work when one
  slice is blocked.
- Keep raw Full Audit records out of commits and reports. Use only the campaign and data selected by
  the current test; never access unrelated personal campaign data, private traces, credentials,
  `.env*`, or generated private build data. Replace any credential encountered in output with
  `[REDACTED]`.
- The public repository must not contain machine-private campaign material or marketing credentials.

## Agent skills

### Issue tracker

Issues and specs use local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the five default canonical labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.

## Communication with Wan

Name documents and components directly, and add a brief plain-language reminder in parentheses
when introducing an unfamiliar term or returning to it after a topic change. For example:
"Alpha Completion Plan (the document tracking development progress and release readiness)."
Repeat reminders when useful; having defined a term earlier does not mean Wan has become familiar
with it. Keep the explanation beside the term in chat and documents, without relying on tooltips
or requiring a glossary lookup.
