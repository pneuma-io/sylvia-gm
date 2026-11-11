from __future__ import annotations

import unittest

from balance_suite.engine import EventEngine, TriggerEffect, movement_with_triggers
from balance_suite.fixtures import benchmark_actor, build_map
from balance_suite.geometry import actual_difficulty, cover_damage, directional_facing_protects, resolve_blast_snapshot, resolve_sequential_projectiles, shortest_path
from balance_suite.model import CoverObject, Position, Side, SimulationState, Stance
from balance_suite.parameters import default_manifest


class GeometryAndEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = default_manifest().seed_set()

    def test_all_seven_maps_build(self) -> None:
        ids = ("MAP_OPEN_100", "MAP_TIGHT_20", "MAP_STREET_60", "MAP_BREACH_30", "MAP_COMPOUND_80", "MAP_LONG_1000", "MAP_NETWORK_SITE_40")
        self.assertEqual(len([build_map(map_id, self.parameters) for map_id in ids]), 7)

    def test_full_blocker_stops_los(self) -> None:
        grid = build_map("MAP_BREACH_30", self.parameters)
        self.assertFalse(grid.line_of_sight(Position(10, 5), Position(20, 5)))

    def test_open_doorway_supports_los_after_door_destroyed(self) -> None:
        grid = build_map("MAP_BREACH_30", self.parameters)
        grid.covers["BREACH_DOOR"].active = False
        self.assertTrue(grid.line_of_sight(Position(10, 9.5), Position(20, 9.5)))

    def test_pathfinding_routes_around_real_blocking_geometry(self) -> None:
        grid = build_map("MAP_BREACH_30", self.parameters)
        grid.covers["BREACH_DOOR"].active = False
        path = shortest_path(Position(5, 5), Position(25, 5), grid)
        self.assertTrue(path)
        self.assertNotIn(Position(15.0, 5.0), path)
        self.assertTrue(any(position.x == 15 and position.y in {9, 10} for position in path))

    def test_pathfinding_truncates_by_walked_distance(self) -> None:
        grid = build_map("MAP_OPEN_100", self.parameters)
        path = shortest_path(Position(1, 1), Position(20, 1), grid, 4.0)
        self.assertEqual(path[-1], Position(5.0, 1.0))

    def test_stance_changes_exposure(self) -> None:
        grid = build_map("MAP_STREET_60", self.parameters)
        standing = grid.exposure(Position(10, 10), Position(30, 10), Stance.STANDING)
        crouched = grid.exposure(Position(10, 10), Position(30, 10), Stance.CROUCHED)
        self.assertNotEqual(standing, crouched)

    def test_directional_shield_protects_front_not_flank(self) -> None:
        self.assertTrue(directional_facing_protects(Position(5, 5), Position(6, 5), Position(0, 5), 180))
        self.assertFalse(directional_facing_protects(Position(5, 5), Position(6, 5), Position(5, 0), 180))

    def test_sequential_projectiles_update_geometry(self) -> None:
        cover = CoverObject("C", {(1, 0)}, 1.2, 2, 4)
        results = resolve_sequential_projectiles(cover, 3, damage=5, target_pen=2, material_pen=3)
        self.assertFalse(cover.active)
        self.assertTrue(results[-1].target_reached)
        self.assertEqual(results[-1].intersection, "CLEAR AFTER DESTRUCTION")

    def test_partial_cover_actual_difficulties_use_admitted_offsets(self) -> None:
        open_difficulty = self.parameters.values["check.difficulty.standard"]
        self.assertEqual(actual_difficulty(open_difficulty, "full", self.parameters), open_difficulty)
        self.assertEqual(actual_difficulty(open_difficulty, "half", self.parameters), open_difficulty + 2)
        self.assertEqual(actual_difficulty(open_difficulty, "quarter", self.parameters), open_difficulty + 5)
        self.assertIsNone(actual_difficulty(open_difficulty, "none", self.parameters))

    def test_cover_damage_is_damage_minus_material_resistance(self) -> None:
        self.assertEqual(cover_damage(5, 3), 2)
        self.assertEqual(cover_damage(2, 3), 0)

    def test_blast_components_share_pre_detonation_snapshot(self) -> None:
        cover = CoverObject("C", {(1, 0)}, 1.2, 2, 3)
        results = resolve_blast_snapshot(cover, (("FRAG", 5), ("FLASH", 3)))
        self.assertTrue(all(result.protected_by_snapshot for result in results))
        self.assertTrue(all(result.cover_destroyed_after for result in results))

    def test_trigger_order_explicit_then_non_active(self) -> None:
        state = SimulationState("T", "1", {})
        engine = EventEngine(state, self.parameters, 1)
        order: list[str] = []
        make = lambda name, side, explicit: TriggerEffect(name, name, side, explicit, lambda _s: True, lambda _s: order.append(name) or None)
        engine.resolve_trigger_window([make("ACTIVE", Side.A, False), make("NONACTIVE", Side.B, False), make("BEFORE", Side.A, True)])
        self.assertEqual(order, ["BEFORE", "NONACTIVE", "ACTIVE"])

    def test_trigger_revalidates_after_state_change(self) -> None:
        state = SimulationState("T", "1", {}, objective={"valid": True})
        engine = EventEngine(state, self.parameters, 1)
        order: list[str] = []
        first = TriggerEffect("FIRST", "A", Side.B, False, lambda _s: True, lambda s: s.objective.update(valid=False) or order.append("FIRST") or None)
        second = TriggerEffect("SECOND", "B", Side.B, False, lambda s: s.objective["valid"], lambda _s: order.append("SECOND") or None)
        engine.resolve_trigger_window([second, first])
        self.assertEqual(order, ["FIRST"])

    def test_nested_trigger_window_finishes_before_parent_queue(self) -> None:
        state = SimulationState("T", "1", {})
        engine = EventEngine(state, self.parameters, 1)
        order: list[str] = []
        nested = TriggerEffect("NESTED", "N", Side.B, False, lambda _s: True, lambda _s: order.append("NESTED") or None)
        first = TriggerEffect("FIRST", "A", Side.B, False, lambda _s: True, lambda _s: order.append("FIRST") or [nested])
        later = TriggerEffect("LATER", "Z", Side.A, False, lambda _s: True, lambda _s: order.append("LATER") or None)
        engine.resolve_trigger_window([later, first])
        self.assertEqual(order, ["FIRST", "NESTED", "LATER"])

    def test_movement_pauses_at_overwatch_trigger_point(self) -> None:
        actor = benchmark_actor("RIFLEMAN", 1, Side.A, Position(0, 0), self.parameters)
        state = SimulationState("OW", "1", {actor.actor_id: actor})
        engine = EventEngine(state, self.parameters, 1)
        seen: list[Position] = []
        def provider(current: SimulationState, actor_id: str) -> list[TriggerEffect]:
            moving = current.actors[actor_id]
            if moving.position.x == 2:
                return [TriggerEffect("OW", "WATCHER", Side.B, False, lambda _s: True, lambda s: seen.append(s.actors[actor_id].position) or None)]
            return []
        movement_with_triggers(engine, actor.actor_id, [Position(1,0), Position(2,0), Position(3,0)], provider)
        self.assertEqual(seen, [Position(2,0)])

    def test_event_log_hash_is_deterministic(self) -> None:
        state1 = SimulationState("E", "1", {})
        state2 = SimulationState("E", "1", {})
        engine1 = EventEngine(state1, self.parameters, 5)
        engine2 = EventEngine(state2, self.parameters, 5)
        resolver = lambda stages, windows, metrics: stages.append({"stage":"noop"}) or None
        engine1.record("A", "NOOP", {}, resolver)
        engine2.record("A", "NOOP", {}, resolver)
        self.assertEqual(engine1.log_hash(), engine2.log_hash())

    def test_rejected_event_does_not_advance_revision(self) -> None:
        state = SimulationState("E", "1", {})
        engine = EventEngine(state, self.parameters, 1)
        engine.record("A", "ILLEGAL", {}, lambda _s, _w, _m: "illegal")
        self.assertEqual(state.revision, 0)


if __name__ == "__main__":
    unittest.main()
