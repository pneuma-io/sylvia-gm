# Campaign-authoring variations

These are small variations on the complete worked examples. Retrieve the current definition for
each artifact before adopting a fragment. Replace only the fields relevant to the user's request.

## A fixed protagonist

Use this when an adventure depends on a particular authored past. In A Debt in Iron, trainee
Tavin Bell pledged a borrowed relic; recovering it joins a personal obligation to a ruin expedition.

```json
{
  "player_character": {
    "mode": "fixed",
    "character": {"id": "tavin-bell", "path": "characters/tavin-bell.json"}
  },
  "required_companions": [],
  "custom_companions": {"minimum": 0, "maximum": 2}
}
```

This is a `starting_party` fragment. The path must refer to a real complete Character File in this
Plan's Setting copy. Read `get_content_definition` for `character_file` and `character_creation`.
Write its identity, full profile, exact schema binding, participant, calculated complete state and
Setting data. Check the authored state with `check_participant_draft`; put the returned complete
state into the file. Check the whole Plan against the effective source copies. Its
`required_characters_ready` result establishes whether setup can use the character.

For a source repository, the same operations are `materialize_initial_character_state` (or
`verify_initial_character_state` for complete data), `parse_setting_character` and the Plan's
snapshot validator. Read their signatures rather than inventing an alternate character format.

An optional starter keeps `mode: compatible`. A recommended profile does not make that character
mandatory, and a Markdown biography does not create a selectable mechanical Character File.

## An earned secret

```json
{
  "id": "altered-route-report",
  "fact": "The contractor replaced the northern survey's date to claim a missed delivery was completed.",
  "known_by": ["contractor", "survey-clerk"],
  "reveal": {
    "from_act": "act-1",
    "condition": "Compare the original dated field sheet with the paid invoice, or obtain the clerk's supported testimony."
  }
}
```

This is a local `secrets` entry. Author the field sheet, invoice and clerk in the act preparation
and make them reachable. Mark Act 1 as the earliest possible discovery if an unusually direct
approach could expose it then. The GM preserves a real truth while adapting how the player finds it.
For an existing Setting secret, use its exact `setting_fact_id` instead of defining a second truth.

## A combat opening or map

Prepared opponents are encounter possibilities until the initial facts or play establish their
presence. Narrative `entities` alone do not instantiate combat actors. If the opening starts
before combat, give the GM an exact encounter brief and let it call the admitted encounter tools
when engagement occurs. When combat or a map must already exist at Start, inspect the Ruleset's
actual state schema and tools, author `initial_state.mechanics`, and provide party placement slots.

Each possible player/companion slot needs complete compatible state updates and explicit appends
to the Ruleset-owned actor lists. Use whole-value `{"$participant":"id"}` and
`{"$participant":"name"}` references for selected identities. A token described only in prose will
not appear on the map. Validate a representative campaign start in a disposable synthetic context
when you introduce mechanical starting state; do not claim a map is playable from JSON shape alone.

For GM-adjudicated objectives such as holding a winch or keeping a wagon moving, state the trigger,
progress, interruption and outcomes concretely. Put that limitation in `unsupported_mechanics`.
Resolve encoded attacks and damage through the engine before narrating them.

## A rejected draft

Suppose validation reports an unknown `clues` member at `$.structure[0].clues`. Retrieve the
installed Plan definition. Put the authored clues into that act's `situations` prose, where this
format accepts them, and remove the undeclared member. Preserve the clues themselves. Recheck
the corrected files, including any related references. Do not repeatedly resubmit the unchanged
payload or claim validation succeeded because the story sounds finished.

If a referenced NPC file does not exist, use an actual matching Setting source or author a new
entry in this Plan's inherited Setting copy and declare it. When the NPC only belongs to this
adventure, it can instead live directly in the Plan's cast preparation without a fabricated path.

## Two acts, three acts and long campaigns

Expand the causal story, not just the number of headings. A second act can pay off a promise or
reframe the original job. A third can make allies and resources earned earlier matter at the
resolution. A long campaign also needs places to rest, relationships that develop between crises,
optional detours and ways to preserve continuity across reordered material. Keep lasting facts
separate from speculative opportunities. Provide enough prepared situations for the intended scope
while allowing the GM to retire optional material that no longer fits the player's choices.
Give each act declared objective IDs and all-required completion conditions, including its named
main problem, required discoveries and exact closing event. An alternative belongs within the
condition it satisfies. If the player abandons the campaign, play the specified departure or loss
report; keep unplayed required objectives unfinished rather than marking future acts complete.

## An ongoing world after a finite opening

An indefinite campaign can end its arrival act at a specific completed rescue/report, then enter
an ongoing second act. Keep that first act's declared required objective. Give the final act
`"objectives": []` and explain its open situations in `situations`; optional jobs remain optional.
Declare the final-act footnote at Plan level:

```json
{
  "ending": {
    "mode": "ongoing",
    "note": "After the arrival rescue and report, life in the river towns remains open. The final act has no intended conclusion; the player chooses which people and problems to pursue."
  }
}
```

This is an excerpt, not a complete Plan. A one-act ongoing world can have both its final act's
`objectives` and the Plan's `objectives` empty. Do not add an endless list of required side quests,
a fabricated ending outcome, or a command to resolve that ongoing act.

## A finite ending with a longer future

After a lighthouse repair, the required immediate closing event could be the keeper verifying its
regular beam and acknowledging the rescue headcount. The ending's concrete conditions repeat
those actual facts; `immediate_aftermath` specifies the keeper's response and the rescued crew's
care. Set `open_world: true` if adventures remain viable. An `after_story` premise such as
"The character settles in the harbour and builds a peaceful life among the people met in play"
is expanded only after Finish the story. The GM supplies the actual friendships and romances;
the premise must not invent them. For a short job with no longer future, use `after_story: null`.
