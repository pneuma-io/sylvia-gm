"""Refusals preserve resources; the runner records a separate participation yield."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from balance_suite import checkpoint, tactical
from balance_suite.engine import EventEngine
from balance_suite.geometry import GridMap
from balance_suite.model import ConditionState, LifeThreateningCondition, Position, Side
from balance_suite.parameters import default_manifest
from balance_suite.policies import PolicyDecision, choose_action


class RejectionAPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = default_manifest().seed_set()

    def scene(self, ap=1, policy='POLICY_DIRECT', medical=False):
        state, _, _ = tactical._build_control_state(tactical.TeamScenarioSpec(level=1,
            classes_a=('CORPSMAN' if medical else 'RIFLEMAN', 'RIFLEMAN', 'RIFLEMAN', 'RIFLEMAN'),
            classes_b=('RIFLEMAN',) * 4), self.parameters)
        actor = next(a for a in state.actors.values() if a.side is Side.A and a.actor_id.endswith('_1'))
        target, ally = (state.actors[k] for k in ('B_VESPER_1', 'A_VESPER_2'))
        state.actors = {a.actor_id: a for a in ((actor, target, ally) if medical else (actor, target))}
        actor.position, target.position, ally.position = Position(10, 10), Position(20, 10), Position(11, 10)
        actor.ap = ap
        actor.weapon.magazine_rounds, actor.weapon.chamber, actor.weapon.reserve_rounds = 0, 'EMPTY', 20
        state.objective['actor_objectives'] = {a: None for a in state.actors}
        state.objective['cover_destination'] = {a: None for a in state.actors}
        state.objective['overwatch_policy'] = {}
        if medical:
            actor.weapon.magazine_rounds, actor.weapon.chamber = 30, 'LIVE'
            actor.supplies = {'medical': 6}
            ally.hp, ally.res, ally.downed = 0, 0, True
            ally.conditions = [LifeThreateningCondition('BLEED', 'hemorrhage', ConditionState.ACTIVE, 60, 'MEDKIT')]
        grid = GridMap('REJECTION-AP-80X60', 80, 60)
        state.covers = grid.covers
        policies = {a: policy for a in state.actors}
        engine = EventEngine(state, self.parameters, 88)
        tactical._refresh_actor_knowledge(actor.actor_id, state, grid)
        observation = tactical._policy_observation(actor.actor_id, state, grid)
        return engine, grid, policies, actor, observation

    def assert_refusal(self, engine, grid, policies, actor, decision):
        engine.rng.stream(f'event:{engine.event_count}')
        before = deepcopy(engine.state.to_dict())
        state_hash = engine.state.semantic_hash()
        rng = checkpoint.encode(engine.rng)
        entry = tactical._execute_decision(engine, grid, policies, actor.actor_id, decision,
            tactical._blank_metrics(engine.state))
        self.assertIsNotNone(entry.rejected_reason)
        self.assertEqual(decision.action, entry.action)
        self.assertEqual(before, engine.state.to_dict())
        self.assertEqual(state_hash, entry.pre_state_hash)
        self.assertEqual(state_hash, entry.post_state_hash)
        self.assertEqual(entry.post_state_hash, engine.state.semantic_hash())
        self.assertEqual(rng, checkpoint.encode(engine.rng))
        self.assertEqual([], entry.random_draws)
        self.assertEqual([], entry.procedure_stages)
        self.assertEqual({}, entry.metrics)
        self.assertEqual([entry], engine.log)

    def test_one_ap_reload_refusal_preserves_state_for_all_ammunition_policies(self):
        for policy in ('DIRECT', 'VOLUME', 'FIRE_DISCIPLINE', 'PRESSURE'):
            with self.subTest(policy=policy):
                engine, grid, policies, actor, observation = self.scene(policy='POLICY_' + policy)
                decision = choose_action(policies[actor.actor_id], observation)
                self.assertEqual('RELOAD', decision.action)
                self.assert_refusal(engine, grid, policies, actor, decision)

    def test_unsupported_medical_refusal_preserves_state_without_treatment(self):
        for policy in ('POLICY_SUPPORT', 'POLICY_OBJECTIVE'):
            with self.subTest(policy=policy):
                engine, grid, policies, actor, observation = self.scene(6, policy, medical=True)
                decision = choose_action(policy, observation)
                self.assertEqual('MEDICAL', decision.action)
                self.assert_refusal(engine, grid, policies, actor, decision)

    def test_other_unsupported_actions_preserve_state(self):
        for action in ('HACK', 'DEPLOY'):
            with self.subTest(action=action):
                engine, grid, policies, actor, _ = self.scene(6)
                self.assert_refusal(engine, grid, policies, actor,
                    PolicyDecision(action, None, None, 0, None, 'unsupported boundary'))

    def test_at_cost_service_rifle_reload_is_charged_once_and_chambers(self):
        engine, grid, policies, actor, observation = self.scene(2)
        before_rounds = actor.weapon.total_rounds()
        entry = tactical._execute_decision(engine, grid, policies, actor.actor_id,
            choose_action('POLICY_DIRECT', observation), tactical._blank_metrics(engine.state))
        self.assertIsNone(entry.rejected_reason)
        self.assertEqual((0, 2), (actor.ap, entry.metrics['ap_spent']))
        self.assertEqual(('LIVE', 19, 0), (actor.weapon.chamber, actor.weapon.magazine_rounds, actor.weapon.reserve_rounds))
        self.assertEqual(before_rounds, actor.weapon.total_rounds())
        self.assertEqual(entry.post_state_hash, engine.state.semantic_hash())
        self.assertEqual([], entry.random_draws)

    def test_ready_observation_separates_chamber_feed_and_reserves(self):
        for chamber, magazine, reserve, needs_cycle, expected in (
                ('LIVE', 1, 20, False, 2), ('LIVE', 0, 20, False, 1),
                ('EMPTY', 20, 0, False, 0), ('SPENT', 20, 20, True, 0),
                ('EMPTY', 0, 0, False, 0)):
            with self.subTest(chamber=chamber, magazine=magazine):
                engine, grid, _, actor, _ = self.scene(6)
                actor.weapon.chamber, actor.weapon.magazine_rounds = chamber, magazine
                actor.weapon.reserve_rounds, actor.weapon.needs_cycle = reserve, needs_cycle
                actor.weapon.retained_magazines = [7]
                before = engine.state.to_dict()
                observation = tactical._policy_observation(actor.actor_id, engine.state, grid)
                self.assertEqual(expected, observation.ammunition)
                self.assertEqual(chamber, observation.metadata['chamber'])
                self.assertEqual(reserve, observation.metadata['reserve_rounds'])
                self.assertEqual(actor.weapon.total_rounds(), observation.metadata['total_rounds'])
                self.assertEqual(before, engine.state.to_dict())

    def test_sequential_service_rifle_reload_then_actual_fire(self):
        for policy in ('DIRECT', 'VOLUME', 'FIRE_DISCIPLINE', 'PRESSURE'):
            with self.subTest(policy=policy):
                engine, grid, policies, actor, observation = self.scene(6, 'POLICY_' + policy)
                metrics = tactical._blank_metrics(engine.state)
                before_rounds = actor.weapon.total_rounds()
                reload_event = tactical._execute_decision(engine, grid, policies, actor.actor_id,
                    choose_action(policies[actor.actor_id], observation), metrics)
                self.assertIsNone(reload_event.rejected_reason)
                self.assertEqual(2, reload_event.metrics['ap_spent'])
                tactical._refresh_actor_knowledge(actor.actor_id, engine.state, grid)
                observation = tactical._policy_observation(actor.actor_id, engine.state, grid)
                self.assertEqual(20, observation.ammunition)
                event = tactical._execute_decision(engine, grid, policies, actor.actor_id,
                    choose_action(policies[actor.actor_id], observation), metrics)
                self.assertIsNone(event.rejected_reason)
                firing = next(s['result'] for s in event.procedure_stages
                    if s['stage'] in {'ATTACK ROLL', 'SUPPRESSION COMMITMENT'})
                self.assertGreater(firing['rounds_fired'], 0)
                self.assertEqual(before_rounds - actor.weapon.total_rounds(), firing['rounds_fired'])
                self.assertEqual(event.post_state_hash, engine.state.semantic_hash())

    def test_magazine_primitive_and_manual_reload_do_not_gain_free_chambering(self):
        from balance_suite.fixtures import weapon_state
        from balance_suite.rules import reload_magazine
        engine, grid, policies, actor, _ = self.scene(2)
        reload_magazine(actor.weapon, 20)
        self.assertEqual('EMPTY', actor.weapon.chamber)
        actor.weapon = weapon_state('manual_precision_rifle', self.parameters)
        actor.weapon.chamber, actor.weapon.magazine_rounds, actor.weapon.needs_cycle = 'EMPTY', 0, True
        entry = tactical._execute_decision(engine, grid, policies, actor.actor_id,
            PolicyDecision('RELOAD', None, None, 0, None, 'manual mechanism boundary'),
            tactical._blank_metrics(engine.state))
        self.assertIsNone(entry.rejected_reason)
        self.assertEqual('EMPTY', actor.weapon.chamber)
        self.assertTrue(actor.weapon.needs_cycle)
        observation = tactical._policy_observation(actor.actor_id, engine.state, grid)
        self.assertEqual(0, observation.ammunition)

    def test_legitimate_end_records_its_own_ap_change(self):
        for ap in (0, 1, 6):
            with self.subTest(ap=ap):
                engine, grid, policies, actor, _ = self.scene(ap)
                entry = tactical._execute_decision(engine, grid, policies, actor.actor_id,
                    PolicyDecision('END', None, None, 0, None, 'declared participation end'),
                    tactical._blank_metrics(engine.state))
                self.assertIsNone(entry.rejected_reason)
                self.assertEqual((0, ap), (actor.ap, entry.metrics['ap_spent']))
                self.assertEqual('END TEAM PARTICIPATION', entry.procedure_stages[0]['stage'])
                self.assertEqual(entry.post_state_hash, engine.state.semantic_hash())
                self.assertEqual([], entry.random_draws)

    def test_real_unsupported_refusals_yield_separately_and_both_sides_progress(self):
        def unsupported_choice(*_):
            return PolicyDecision('MEDICAL', None, None, 0, None, 'unsupported executor boundary')
        with patch.object(tactical, 'choose_action', unsupported_choice):
            result = tactical.run_team_scenario(tactical.TeamScenarioSpec(level=1, max_team_turns=2),
                self.parameters, 88, capture_events=True)
        events = result['events']
        self.assertEqual(16, len(events))
        self.assertEqual({'A', 'B'}, {e['actor_id'][0] for e in events})
        self.assertEqual('max team turns', result['terminal_reason'])
        self.assertFalse(result['metrics']['invariant_failures'])
        for failed, ended in zip(events[::2], events[1::2]):
            self.assertEqual('MEDICAL', failed['action'])
            self.assertEqual('unsupported policy action MEDICAL', failed['rejected_reason'])
            self.assertEqual(failed['pre_state_hash'], failed['post_state_hash'])
            self.assertEqual(failed['post_state_hash'], ended['pre_state_hash'])
            self.assertEqual(failed['actor_id'], ended['actor_id'])
            self.assertEqual('END', ended['action'])
            self.assertIsNone(ended['rejected_reason'])
            self.assertEqual(6, ended['metrics']['ap_spent'])
            self.assertEqual([], failed['random_draws'])


if __name__ == '__main__':
    unittest.main()
