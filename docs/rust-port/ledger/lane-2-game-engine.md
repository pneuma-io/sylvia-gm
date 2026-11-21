# Lane 2 ledger: Game engine

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 57 files, 24,338 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/application/_runtime_cache.py` | 82 | `sylvia-app::_runtime_cache` | pending | |
| `src/sylvia_gm/application/action_sequences.py` | 784 | `sylvia-app::action_sequences` | pending | |
| `src/sylvia_gm/application/actor_decisions.py` | 190 | `sylvia-app::actor_decisions` | pending | |
| `src/sylvia_gm/application/decision_scope.py` | 314 | `sylvia-app::decision_scope` | pending | |
| `src/sylvia_gm/application/map_segment_preview.py` | 59 | `sylvia-app::map_segment_preview` | pending | |
| `src/sylvia_gm/application/package_runtime.py` | 1,643 | `sylvia-app::package_runtime` | pending | |
| `src/sylvia_gm/application/piece_table.py` | 274 | `sylvia-app::piece_table` | pending | |
| `src/sylvia_gm/application/player_actions.py` | 343 | `sylvia-domain::player_actions` (marker checks), `sylvia-app::player_actions` (the rest) | in progress | Ported to `sylvia-domain::player_actions`, below the store so its verifier can call them: `PLAYER_ACTION_ORIGIN`, `player_control_origin`, `valid_player_action_consequence` (`sylvia-domain` `player_action_vectors`). Lane 3's stored-authority verifier (PR 315) needs the last one, so its call switches over to close that cutover gap. Still to port, in `sylvia-app`: `parse_player_controls` (with PR B), `player_action_request_matches`, `validate_player_control`, `resolve_party_action_parameters`, `retain_player_action_origin`, `player_action_origins`, `player_action_records`, `resolve_inventory_action_parameters`. |
| `src/sylvia_gm/application/result_card_admission.py` | 179 | `sylvia-app::result_card_admission` | pending | |
| `src/sylvia_gm/application/result_cards.py` | 243 | `sylvia-app::result_cards` | pending | |
| `src/sylvia_gm/application/shot_effects.py` | 376 | `sylvia-app::shot_effects` | pending | |
| `src/sylvia_gm/application/spatial_preview.py` | 118 | `sylvia-app::spatial_preview` | pending | |
| `src/sylvia_gm/application/tactical_grid.py` | 710 | `sylvia-app::tactical_grid` | pending | |
| `src/sylvia_gm/dice.py` | 920 | `sylvia-engine::dice` | ported | `sylvia-engine::dice`, drawing from `sylvia-engine::pyrandom`. The three context-variable streams (`use_roll_source`, `seeded_rolls`, the module default) are a `Dice` value the caller passes down; a scripted source implements `RollSource` (Python's `ScenarioDice` overrides `randint` the same way). `weighted_pick` returns the picked row's index. Python's `TypeError` branches for non-`int` arguments cannot arise from typed Rust callers; the dynamic ones (`weighted_pick` weights, `sum()`) are kept. Sums, totals plus modifiers, margins and success counts beyond `i64` are refused (`differences.md` row 5). Vectors: `tests/vectors/dice_vectors.json`. |
| `src/sylvia_gm/ruleset/__init__.py` | 5 | `sylvia-engine` | pending | |
| `src/sylvia_gm/ruleset/schema.py` | 273 | `sylvia-engine` | pending | |
| `src/sylvia_gm/ruleset/validator.py` | 195 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/__init__.py` | 199 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_aoe_resolver.py` | 326 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_authoring_declarations.py` | 101 | `sylvia-engine` | ported | Constants in `sylvia-engine::ruleset::authoring_shape`, checked against Python by the vectors. |
| `src/sylvia_gm/runtime/_authoring_shape.py` | 101 | `sylvia-engine` | ported | `sylvia-engine::ruleset::authoring_shape::runtime_authoring_shape_errors`. |
| `src/sylvia_gm/runtime/_clock_manager.py` | 291 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_collections.py` | 160 | `sylvia-engine` | ported | `sylvia-engine::expr::collections`, including `partitions`/`completions` with Counter order and the budget. Proven by both expression evaluation vector files. |
| `src/sylvia_gm/runtime/_companion_registry.py` | 105 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_condition_evaluator.py` | 481 | `sylvia-engine` | ported | `sylvia-engine::expr::{eval, ops, compare, value}`: field resolution and traversal (dicts, lists, tuples, strings, dice result dataclasses, `int`/`float`/`str`/`bool` attributes from a table generated from 3.12), every operator with CPython 3.12's type rules, messages and float algorithms, comparisons, membership and the builtins. Proven by `expression_eval_vectors` (30,101 generated cases in one rich context) and `expression_recorded_vectors` (every evaluation recorded while the snapshot's SRD Lite and Riichi tests run). Integers are `i64` with the row 5 refusal; rows 16 to 20 list the other declared differences. |
| `src/sylvia_gm/runtime/_content_registry.py` | 375 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_cs_loader.py` | 248 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_deferred_queue.py` | 186 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_distance_tracker.py` | 562 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_duration_tracker.py` | 189 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_effect_executor.py` | 2,351 | `sylvia-engine` | in progress | `_parse_dice_expr` is `sylvia-engine::dice_expr::parse_dice_expr` (vectors over 3,070 expressions). `_handle_shuffle` draws from `secrets.SystemRandom` (OS entropy), not the seeded tool stream, so its order differs between any two runs, Python or Rust; it is ported as OS-random. Shipped tools that reach it: Riichi Mahjong `start_game` (through `_new_hand`, `shuffle indices(136)`) and every Riichi `take_action` that runs `refresh` (`shuffle indices(256)`). SRD Lite has none. Comparison sessions must avoid those tools or declare a rule for the shuffled values. |
| `src/sylvia_gm/runtime/_exceptions.py` | 110 | `sylvia-engine` | in progress | `ExpressionError` and `FieldNotFoundError` are `sylvia-engine::expr::error` (message rules, class names). The other runtime exceptions come with the code that raises them. |
| `src/sylvia_gm/runtime/_expression_parser.py` | 595 | `sylvia-engine` | ported | Tokenizer, limits, AST and parser are `sylvia-engine::expr::{token, parser}` (`expression_parse_vectors`, 65,848 texts); the sandboxed functions (`len`, `merge`, the builtins and their arity errors) are in `expr::eval` (`expression_eval_vectors`). An integer literal beyond `i64` parses as in Python and is refused when evaluated (row 5). |
| `src/sylvia_gm/runtime/_flag_store.py` | 97 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_frontmatter.py` | 190 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_initiative.py` | 243 | `sylvia-engine` | in progress | `_roll_initiative_expr` is `sylvia-engine::dice_expr::roll_initiative_expr` (vectors over 3,070 expressions). The rest is pending. |
| `src/sylvia_gm/runtime/_initiative_count_triggers.py` | 165 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_parameter_declarations.py` | 154 | `sylvia-engine` | ported | `sylvia-engine::ruleset::parameter_declarations`: `resolve_parameter_declarations` (and `_with` for the shape callback), the descriptor schema checked by lane 1's `content::schema::Validator`. |
| `src/sylvia_gm/runtime/_procedure_executor.py` | 357 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_rng_queue.py` | 389 | `sylvia-engine` | ported | `sylvia-engine::rng_queue`. No product code calls it (only exported). `from_dict` returns `None` for data `to_dict` would not write, where Python raises `KeyError`/`TypeError`. |
| `src/sylvia_gm/runtime/_runtime.py` | 484 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_scene_awareness.py` | 499 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_schema_validator.py` | 1,734 | `sylvia-engine` | ported | `sylvia-engine::ruleset::schema_validator`: `validate_ruleset` (YAML text, `Loader::Pure`), `validate_ruleset_data`, `validate_parameter_declarations`, `validate_effects` and every private check, returning Python's messages or its `TypeError`. Python's object model is in `ruleset::pydata` (equality, membership, `sorted`, `repr`, `str`, `isalnum`). Suggestion ties, handler order, aliased branches and recursive YAML: `differences.md` rows 22 to 25. |
| `src/sylvia_gm/runtime/_simulation_mode.py` | 415 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_state_bridge.py` | 322 | `sylvia-engine` | retired | Retired: no live caller in the 0.4.1 snapshot (manager's decision, 21 Nov 2026). Nothing constructs the legacy `StateAPI` it wraps (`state/` is retired), and `RulesetRuntime.__init__`'s `StateBridge(state_api)`, its `state_api` parameter and `set_state_api` have no caller in src/, tests/ or scripts/. The `state_bridge` parameter the runtime managers take (`_clock_manager`, `_distance_tracker`, `_duration_tracker`, `_flag_store`, `_simulation_mode`, `_state_memo`) stays: the live path fills it with `AuthorityMemoryStateBridge` (`application/package_runtime.py`), and that interface is ported as the managers use it, None checks included. |
| `src/sylvia_gm/runtime/_state_memo.py` | 274 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_strictness.py` | 519 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_template_resolver.py` | 75 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_time_manager.py` | 336 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_tool_executor.py` | 528 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_turn_system.py` | 174 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_types.py` | 327 | `sylvia-engine` | in progress | `ExecutionBudget` is `sylvia-engine::expr::ExecutionBudget`; `ExecutionContext`'s expression-facing fields are `EvalContext`. The rest comes with the effects. |
| `src/sylvia_gm/spatial/__init__.py` | 29 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_clearance.py` | 73 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_convex.py` | 102 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_engine.py` | 1,865 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_interface.py` | 1,899 | `sylvia-engine::spatial` | pending | |
