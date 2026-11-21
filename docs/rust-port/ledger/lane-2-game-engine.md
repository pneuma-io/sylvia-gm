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
| `src/sylvia_gm/application/player_actions.py` | 343 | `sylvia-app::player_actions` | pending | |
| `src/sylvia_gm/application/result_card_admission.py` | 179 | `sylvia-app::result_card_admission` | pending | |
| `src/sylvia_gm/application/result_cards.py` | 243 | `sylvia-app::result_cards` | pending | |
| `src/sylvia_gm/application/shot_effects.py` | 376 | `sylvia-app::shot_effects` | pending | |
| `src/sylvia_gm/application/spatial_preview.py` | 118 | `sylvia-app::spatial_preview` | pending | |
| `src/sylvia_gm/application/tactical_grid.py` | 710 | `sylvia-app::tactical_grid` | pending | |
| `src/sylvia_gm/dice.py` | 920 | `sylvia-engine::dice` | pending | |
| `src/sylvia_gm/ruleset/__init__.py` | 5 | `sylvia-engine` | pending | |
| `src/sylvia_gm/ruleset/schema.py` | 273 | `sylvia-engine` | pending | |
| `src/sylvia_gm/ruleset/validator.py` | 195 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/__init__.py` | 199 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_aoe_resolver.py` | 326 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_authoring_declarations.py` | 101 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_authoring_shape.py` | 101 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_clock_manager.py` | 291 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_collections.py` | 160 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_companion_registry.py` | 105 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_condition_evaluator.py` | 481 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_content_registry.py` | 375 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_cs_loader.py` | 248 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_deferred_queue.py` | 186 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_distance_tracker.py` | 562 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_duration_tracker.py` | 189 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_effect_executor.py` | 2,351 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_exceptions.py` | 110 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_expression_parser.py` | 595 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_flag_store.py` | 97 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_frontmatter.py` | 190 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_initiative.py` | 243 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_initiative_count_triggers.py` | 165 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_parameter_declarations.py` | 154 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_procedure_executor.py` | 357 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_rng_queue.py` | 389 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_runtime.py` | 484 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_scene_awareness.py` | 499 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_schema_validator.py` | 1,734 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_simulation_mode.py` | 415 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_state_bridge.py` | 322 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_state_memo.py` | 274 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_strictness.py` | 519 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_template_resolver.py` | 75 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_time_manager.py` | 336 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_tool_executor.py` | 528 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_turn_system.py` | 174 | `sylvia-engine` | pending | |
| `src/sylvia_gm/runtime/_types.py` | 327 | `sylvia-engine` | pending | |
| `src/sylvia_gm/spatial/__init__.py` | 29 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_clearance.py` | 73 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_convex.py` | 102 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_engine.py` | 1,865 | `sylvia-engine::spatial` | pending | |
| `src/sylvia_gm/spatial/_interface.py` | 1,899 | `sylvia-engine::spatial` | pending | |
