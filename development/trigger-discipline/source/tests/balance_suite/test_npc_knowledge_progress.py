"""Information counterfactuals and real policy/executor progress regressions."""
from copy import deepcopy
from dataclasses import asdict, replace
import math
import unittest
from unittest.mock import patch

from balance_suite import geometry, objective_tactical, overwatch, simulation, tactical
from balance_suite.engine import EventEngine
from balance_suite.fixtures import benchmark_actor, material_cover
from balance_suite.model import KnowledgeState, Position, Side, SimulationState, Stance
from balance_suite.parameters import default_manifest
from balance_suite.policies import OverwatchSelection, PolicyDecision, choose_action


class KnowledgeProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = default_manifest().seed_set()

    def team(self):
        state, _, policies = tactical._build_control_state(tactical.TeamScenarioSpec(level=1,
            classes_a=('RIFLEMAN',) * 4, classes_b=('RIFLEMAN',) * 4), self.parameters)
        grid = geometry.GridMap('REPAIR-80X60', 80, 60)
        state.covers = grid.covers
        for i, actor in enumerate(state.actors.values()):
            actor.position = Position(10 + i * 4, 10)
            actor.target_knowledge.clear()
            actor.target_last_known.clear()
        return state, grid, policies

    def observe(self, state, grid, actor_id):
        engine = EventEngine(state, self.parameters, 88)
        rng = deepcopy(engine.rng.provenance())
        observation = tactical._policy_observation(actor_id, state, grid)
        decisions = tuple(choose_action(p, observation) for p in
            ('POLICY_DIRECT', 'POLICY_COVER_FIRST', 'POLICY_FLANK', 'POLICY_OBJECTIVE', 'POLICY_STRONGPOINT'))
        self.assertEqual(rng, engine.rng.provenance())
        return observation, decisions

    def test_duel_unaware_position_and_downed_do_not_change_input_or_choice(self):
        a = benchmark_actor('RIFLEMAN', 1, Side.A, Position(10, 15), self.parameters)
        b = benchmark_actor('RIFLEMAN', 1, Side.B, Position(90, 15), self.parameters)
        state = SimulationState('REPAIR-DUEL', '1', {a.actor_id: a, b.actor_id: b})
        before = simulation._observation(state, a.actor_id, b.actor_id, 'POLICY_DIRECT')
        b.position, b.downed = Position(80, 10), True
        after = simulation._observation(state, a.actor_id, b.actor_id, 'POLICY_DIRECT')
        self.assertEqual(before, after)
        self.assertIsNone(after.objective_position)
        self.assertEqual(choose_action('POLICY_DIRECT', before), choose_action('POLICY_DIRECT', after))

    def test_duel_stale_and_confirmed_positive_controls(self):
        a = benchmark_actor('RIFLEMAN', 1, Side.A, Position(10, 15), self.parameters)
        b = benchmark_actor('RIFLEMAN', 1, Side.B, Position(90, 15), self.parameters)
        state = SimulationState('REPAIR-DUEL', '1', {a.actor_id: a, b.actor_id: b})
        a.target_knowledge[b.actor_id] = KnowledgeState.LAST_KNOWN
        a.target_last_known[b.actor_id] = Position(50, 15)
        before = simulation._observation(state, a.actor_id, b.actor_id, 'POLICY_DIRECT')
        self.assertEqual(Position(50, 15), before.objective_position)
        b.position, b.downed = Position(80, 10), True
        self.assertEqual(before, simulation._observation(state, a.actor_id, b.actor_id, 'POLICY_DIRECT'))
        a.target_knowledge[b.actor_id] = KnowledgeState.CONFIRMED
        after = simulation._observation(state, a.actor_id, b.actor_id, 'POLICY_DIRECT')
        self.assertEqual(b.position, after.objective_position)
        self.assertTrue(after.known_targets[0].downed)
        self.assertNotEqual(before, after)

    def test_team_hidden_cover_counterfactual(self):
        # Exact accepted COVER-PREPARED cover and observer configuration.
        state, grid, _ = self.team()
        observer = state.actors['A_VESPER_2']
        observer.position = Position(30, 27)
        grid.covers['one'] = material_cover('one', {(36, 26), (36, 27), (36, 28)}, 'light_masonry', 1.25, self.parameters)
        grid.covers['two'] = material_cover('two', {(43, 31), (43, 32), (43, 33)}, 'light_masonry', 1.25, self.parameters)
        visible = state.actors['B_VESPER_2']
        visible.position = Position(49, 40)
        observer.target_knowledge[visible.actor_id] = KnowledgeState.CONFIRMED
        hidden = [state.actors[i] for i in ('B_VESPER_1', 'B_VESPER_3', 'B_VESPER_4')]
        for enemy, p in zip(hidden, ((47, 26), (48, 27), (49, 28))):
            enemy.position = Position(*p)
        before = self.observe(state, grid, observer.actor_id)
        for enemy, p in zip(hidden, ((30, 12), (31, 12), (32, 12))):
            enemy.position = Position(*p)
        self.assertEqual(before, self.observe(state, grid, observer.actor_id))

    def test_team_unaware_and_stale_downed_and_positions_are_masked(self):
        for knowledge in (KnowledgeState.UNAWARE, KnowledgeState.SUSPICIOUS, KnowledgeState.APPROXIMATE, KnowledgeState.LAST_KNOWN):
            with self.subTest(knowledge=knowledge):
                state, grid, _ = self.team()
                actor, target = state.actors['A_VESPER_1'], state.actors['B_VESPER_1']
                actor.target_knowledge[target.actor_id] = knowledge
                if knowledge is KnowledgeState.LAST_KNOWN:
                    actor.target_last_known[target.actor_id] = Position(25, 12)
                before = self.observe(state, grid, actor.actor_id)
                target.position, target.downed = Position(60, 40), True
                self.assertEqual(before, self.observe(state, grid, actor.actor_id))

    def test_stale_cover_uses_last_known_and_confirmed_change_is_visible(self):
        state, grid, _ = self.team()
        actor, target = state.actors['A_VESPER_1'], state.actors['B_VESPER_1']
        actor.position = Position(10, 10)
        grid.covers['wall'] = material_cover('wall', {(15, y) for y in range(8, 13)}, 'light_masonry', 1.25, self.parameters)
        actor.target_knowledge[target.actor_id] = KnowledgeState.LAST_KNOWN
        actor.target_last_known[target.actor_id] = Position(20, 10)
        before = self.observe(state, grid, actor.actor_id)
        self.assertTrue(before[0].behind_cover)
        target.position = Position(10, 15)
        self.assertEqual(before, self.observe(state, grid, actor.actor_id))
        actor.target_knowledge[target.actor_id] = KnowledgeState.CONFIRMED
        after = self.observe(state, grid, actor.actor_id)
        self.assertFalse(after[0].behind_cover)
        self.assertNotEqual(before, after)
        target.downed = True
        downed = tactical._policy_observation(actor.actor_id, state, grid)
        self.assertTrue(next(t for t in downed.known_targets if t.actor_id == target.actor_id).downed)

    def test_actual_refresh_loses_and_reacquires_contact(self):
        state, grid, _ = self.team()
        actor, target = state.actors['A_VESPER_1'], state.actors['B_VESPER_1']
        actor.position, target.position = Position(10, 10), Position(20, 10)
        tactical._refresh_actor_knowledge(actor.actor_id, state, grid)
        self.assertEqual(KnowledgeState.CONFIRMED, actor.target_knowledge[target.actor_id])
        grid = geometry.GridMap('REPAIR-BLOCKED', 80, 60, {(15, y) for y in range(60)})
        target.position = Position(25, 10)
        tactical._refresh_actor_knowledge(actor.actor_id, state, grid)
        self.assertEqual(KnowledgeState.LAST_KNOWN, actor.target_knowledge[target.actor_id])
        self.assertEqual(Position(20, 10), actor.target_last_known[target.actor_id])
        grid = geometry.GridMap('REPAIR-REOPENED', 80, 60)
        tactical._refresh_actor_knowledge(actor.actor_id, state, grid)
        self.assertEqual(KnowledgeState.CONFIRMED, actor.target_knowledge[target.actor_id])
        self.assertEqual(target.position, actor.target_last_known[target.actor_id])

    def test_policy_movement_carries_available_ap_ceiling(self):
        state, grid, _ = self.team()
        actor = state.actors['A_VESPER_3']
        observation = tactical._policy_observation(actor.actor_id, state, grid)
        decision = choose_action('POLICY_OBJECTIVE', observation)
        self.assertEqual('MOVE_OBJECTIVE', decision.action)
        self.assertEqual(observation.ap, decision.movement_ap_budget)

    def pipeline_scene(self, shared):
        state, _, _ = self.team()
        observer, target = state.actors['A_VESPER_1'], state.actors['B_VESPER_1']
        for index, actor in enumerate(state.actors.values()):
            actor.position = Position(40 + index * 2, 40)
        observer.position, target.position = Position(10, 10), Position(20, 10)
        state.objective['overwatch_policy'] = {
            observer.actor_id: asdict(OverwatchSelection('lane', 'east', 24, 3, 'REVIEW-REFRESH'))
        } if shared else {}
        return state, observer.actor_id, target.actor_id

    def refreshed_choices(self, state, grid, actor_id):
        engine = EventEngine(state, self.parameters, 88)
        rng_before = deepcopy(engine.rng.provenance())
        actor = state.actors[actor_id]
        resources = (actor.ap, actor.hp, actor.res, actor.weapon.total_rounds())
        tactical._refresh_actor_knowledge(actor_id, state, grid)
        observation = tactical._policy_observation(actor_id, state, grid)
        decisions = tuple(choose_action(policy, observation) for policy in (
            'POLICY_DIRECT', 'POLICY_COVER_FIRST', 'POLICY_FIRE_DISCIPLINE', 'POLICY_VOLUME',
            'POLICY_PRESSURE', 'POLICY_FLANK', 'POLICY_OBJECTIVE', 'POLICY_SUPPORT',
            'POLICY_PREPARED_FIRE', 'POLICY_STRONGPOINT'))
        self.assertEqual(rng_before, engine.rng.provenance())
        self.assertEqual(resources, (actor.ap, actor.hp, actor.res, actor.weapon.total_rounds()))
        return observation, decisions

    def test_refresh_hidden_alive_downed_invariance_after_confirmed_contact(self):
        for shared in (False, True):
            with self.subTest(shared=shared):
                state, observer_id, target_id = self.pipeline_scene(shared)
                self.refreshed_choices(state, geometry.GridMap('REVIEW-OPEN', 80, 60), observer_id)
                self.assertEqual(KnowledgeState.CONFIRMED, state.actors[observer_id].target_knowledge[target_id])
                blocked = geometry.GridMap('REVIEW-BLOCKED', 80, 60, {(15, y) for y in range(60)})
                reference = None
                for position in (Position(25, 10), Position(35, 12)):
                    for downed in (False, True):
                        current = deepcopy(state)
                        current.actors[target_id].position, current.actors[target_id].downed = position, downed
                        result = self.refreshed_choices(current, blocked, observer_id)
                        observed = next(t for t in result[0].known_targets if t.actor_id == target_id)
                        self.assertEqual(KnowledgeState.LAST_KNOWN, observed.knowledge)
                        self.assertEqual(Position(20, 10), observed.position)
                        self.assertFalse(observed.downed)
                        self.assertFalse(observed.exposed)
                        self.assertEqual(Position(20, 10), current.actors[observer_id].target_last_known[target_id])
                        if reference is not None:
                            self.assertEqual(reference, result)
                        reference = result

    def test_refresh_visible_downed_target_is_legitimately_observed(self):
        for shared in (False, True):
            for initially_confirmed in (False, True):
                with self.subTest(shared=shared, initially_confirmed=initially_confirmed):
                    state, observer_id, target_id = self.pipeline_scene(shared)
                    grid = geometry.GridMap('REVIEW-VISIBLE', 80, 60)
                    if initially_confirmed:
                        self.refreshed_choices(state, grid, observer_id)
                    state.actors[target_id].position = Position(25, 10)
                    state.actors[target_id].downed = True
                    observation, _ = self.refreshed_choices(state, grid, observer_id)
                    observed = next(t for t in observation.known_targets if t.actor_id == target_id)
                    self.assertEqual(KnowledgeState.CONFIRMED, observed.knowledge)
                    self.assertEqual(Position(25, 10), observed.position)
                    self.assertTrue(observed.downed)
                    self.assertTrue(observed.exposed)
                    self.assertEqual(Position(25, 10), state.actors[observer_id].target_last_known[target_id])

    def test_refresh_lawful_reacquisition_updates_alive_and_downed_facts(self):
        for shared in (False, True):
            for downed in (False, True):
                with self.subTest(shared=shared, downed=downed):
                    state, observer_id, target_id = self.pipeline_scene(shared)
                    open_grid = geometry.GridMap('REVIEW-REACQUIRE', 80, 60)
                    self.refreshed_choices(state, open_grid, observer_id)
                    state.actors[target_id].position = Position(25, 10)
                    blocked = geometry.GridMap('REVIEW-REACQUIRE-BLOCKED', 80, 60, {(15, y) for y in range(60)})
                    stale = self.refreshed_choices(state, blocked, observer_id)
                    self.assertEqual(KnowledgeState.LAST_KNOWN, state.actors[observer_id].target_knowledge[target_id])
                    state.actors[target_id].downed = downed
                    current = self.refreshed_choices(state, open_grid, observer_id)
                    observed = next(t for t in current[0].known_targets if t.actor_id == target_id)
                    self.assertEqual(KnowledgeState.CONFIRMED, observed.knowledge)
                    self.assertEqual(Position(25, 10), observed.position)
                    self.assertEqual(downed, observed.downed)
                    self.assertTrue(observed.exposed)
                    self.assertNotEqual(stale[0], current[0])
                    self.assertEqual(Position(25, 10), state.actors[observer_id].target_last_known[target_id])

    def test_one_ap_cardinal_alternative_for_both_path_adapters(self):
        state, grid, _ = self.team()
        actor = state.actors['A_VESPER_3']
        actor.position = Position(30, 32)
        goal = Position(39, 16)
        for path in (geometry.shortest_path(actor.position, goal, grid, 1),
                     overwatch.movement_path(state, grid, actor.actor_id, goal, 1)):
            self.assertTrue(path)
            self.assertEqual(1, actor.position.distance_to(path[0]))
            self.assertLess(path[-1].distance_to(goal), actor.position.distance_to(goal))

    def test_cardinal_diagonal_stance_costs_and_state(self):
        for shared in (False, True):
            for stance, goal, budget, expected_cost in ((Stance.STANDING, Position(11, 10), 1, 1),
                    (Stance.STANDING, Position(11, 11), 2, 2),
                    (Stance.CROUCHED, Position(11, 10), 2, 2),
                    (Stance.PRONE, Position(11, 10), 4, 4)):
                with self.subTest(shared=shared, stance=stance, goal=goal):
                    state, grid, policies = self.team()
                    actor = state.actors['A_VESPER_1']
                    actor.position, actor.stance = Position(10, 10), stance
                    engine = EventEngine(state, self.parameters, 88)
                    metrics = tactical._blank_metrics(state)
                    before_ap, before_rng = actor.ap, deepcopy(engine.rng.provenance())
                    decision = PolicyDecision('MOVE_OBJECTIVE', None, None, 0, goal, 'cost regression', movement_ap_budget=budget)
                    # Exercise the host path/move boundary without manufacturing a watch.
                    with patch.object(tactical.overwatch, 'movement_path', wraps=overwatch.movement_path) as host_path:
                        if shared:
                            from balance_suite.policies import OverwatchSelection
                            # Real setup makes the executor choose its shared spatial movement branch.
                            setup = PolicyDecision('OVERWATCH', None, None, 0, None, 'setup', OverwatchSelection('lane', 'east', 24, 3, 'REPAIR'))
                            entry = tactical._execute_decision(engine, grid, policies, actor.actor_id, setup, metrics)
                            self.assertIsNone(entry.rejected_reason)
                            before_ap = actor.ap
                        entry = tactical._execute_decision(engine, grid, policies, actor.actor_id, decision, metrics)
                        if shared:
                            self.assertTrue(host_path.called)
                    self.assertIsNone(entry.rejected_reason)
                    self.assertEqual(goal, actor.position)
                    self.assertEqual(expected_cost, before_ap - actor.ap)
                    self.assertEqual(expected_cost, entry.metrics['ap_spent'])
                    self.assertGreaterEqual(actor.ap, 0)
                    self.assertEqual(before_rng['master_seed'], engine.rng.provenance()['master_seed'])
                    self.assertFalse(entry.random_draws)

    def test_insufficient_ap_unreachable_and_noop_are_honest(self):
        for kind in ('insufficient', 'unreachable', 'noop'):
            with self.subTest(kind=kind):
                state, grid, policies = self.team()
                actor = state.actors['A_VESPER_1']
                actor.position = Position(10, 10)
                actor.ap = 1
                if kind == 'insufficient':
                    actor.stance = Stance.PRONE
                if kind == 'unreachable':
                    grid.full_blockers.update((x, y) for x in (9, 10, 11) for y in (9, 10, 11) if (x, y) != (10, 10))
                goal = actor.position if kind == 'noop' else Position(12, 10)
                engine, metrics = EventEngine(state, self.parameters, 88), tactical._blank_metrics(state)
                before = actor.position
                entry = tactical._execute_decision(engine, grid, policies, actor.actor_id,
                    PolicyDecision('MOVE_OBJECTIVE', None, None, 0, goal, kind, movement_ap_budget=6), metrics)
                self.assertIsNotNone(entry.rejected_reason)
                self.assertEqual(before, actor.position)
                self.assertNotIn('ap_spent', entry.metrics)
                self.assertEqual(0, metrics['by_actor'][actor.actor_id]['movement_m'])

    def test_exact_flank_repro_moves_without_free_ap(self):
        state, grid, policies = self.team()
        actor = state.actors['A_VESPER_3']
        actor.position = Position(30, 32)
        observation = tactical._policy_observation(actor.actor_id, state, grid)
        decision = choose_action('POLICY_FLANK', replace(observation, objective_position=tactical._flank_destination(actor.actor_id, actor.side)))
        engine, metrics = EventEngine(state, self.parameters, 88), tactical._blank_metrics(state)
        entry = tactical._execute_decision(engine, grid, policies, actor.actor_id, decision, metrics)
        self.assertIsNone(entry.rejected_reason)
        self.assertNotEqual(Position(30, 32), actor.position)
        distance = metrics['by_actor'][actor.actor_id]['movement_m']
        self.assertEqual(math.ceil(distance - 1e-9), 6 - actor.ap)

    def test_repeated_rejections_yield_all_actors_and_other_side(self):
        original = tactical._execute_decision
        attempted = []
        def reject(engine, grid, policies, actor_id, decision, metrics, **kwargs):
            attempted.append((engine.state.active_side, actor_id, decision.action))
            if decision.action == 'END':
                return original(engine, grid, policies, actor_id, decision, metrics, **kwargs)
            return engine.record(actor_id, decision.action, {}, lambda *_: 'REPAIR deliberate finite rejection')
        with patch.object(tactical, '_execute_decision', reject), patch.object(tactical, 'choose_action',
                lambda *_: PolicyDecision('MOVE_OBJECTIVE', None, None, 0, Position(39, 30), 'reject probe')):
            result = tactical.run_team_scenario(tactical.TeamScenarioSpec(level=1, max_team_turns=2), self.parameters, 88, capture_events=True)
        self.assertEqual({Side.A, Side.B}, {side for side, _, _ in attempted})
        self.assertEqual(8, sum(action == 'MOVE_OBJECTIVE' for _, _, action in attempted))
        self.assertEqual(8, sum(action == 'END' for _, _, action in attempted))
        self.assertEqual('max team turns', result['terminal_reason'])
        self.assertFalse(result['metrics']['invariant_failures'])
        self.assertEqual(8, sum(e['rejected_reason'] is not None for e in result['events']))

    def test_objective_consumer_cardinal_progress_boundary(self):
        state, grid, _ = self.team()
        actor = state.actors['A_VESPER_3']
        actor.position = Position(30, 32)
        metrics = tactical._blank_metrics(state)
        for actor_id, values in metrics['by_actor'].items():
            values['side'] = state.actors[actor_id].side.value
            values['ap_by_action'] = {}
        engine = EventEngine(state, self.parameters, 88)
        self.assertTrue(objective_tactical._record_move(engine, grid, actor, Position(39, 16), metrics))
        self.assertEqual(5, actor.ap)
        self.assertEqual(1, metrics['by_actor'][actor.actor_id]['movement_m'])
