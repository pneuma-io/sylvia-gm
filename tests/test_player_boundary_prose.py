"""Prose at a Ruleset player boundary is held for the Campaign File, not discarded (#215).

On the ChatGPT route the Codex runtime manages tool_choice, so "required" reaches GPT only as
text. Live, a fight that had just started waited for the player's choice, GPT narrated as the
player-boundary control asks, and the loop discarded that narration three times and ended the
turn. These sessions replay that sequence through the real session loop with a scripted GM.
"""
from __future__ import annotations

import shutil
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import CanonicalEventType, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from tests.test_gm_context_contract import settle
from tests.test_gm_fixed_tool_list import FixedGM
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign

START_TEXT = ("The guard draws steel and attacks us. Start the fight: hero, guard and other are "
              "in it.")
CAST_TEXT = ("On my turn I cast Summon Dragon: put the dragon 30 feet north of me in the bright "
             "daylight, fire damage.")
FIGHT_PROSE = "Steel flashes as the guard closes in. Initiative falls your way. What do you do?"
CAST_PROSE = "A fire dragon unfurls thirty feet to the north, wings blazing in the daylight."
HELD = "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_REQUIRED"
DISCARDED = "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION"
UNCHANGED = ("update_campaign_file", {"state_change": "unchanged",
                                      "state_change_summary": "The fight is under way."})


def _seed(tmp_path_factory, label, overrides):
    database = tmp_path_factory.mktemp(label) / "seed.db"
    with AuthorityOwner.open(database, label) as owner:
        _state, _data, parameters = _create_spell_campaign(
            owner, "summon_dragon", participant_overrides=overrides)
        owner.create_agent_session(campaign_id="casting", session_id="prose-session",
                                   role=WriterRole.DM, provider_id="test.transport",
                                   model_id="test.transport/model0")
    return database, parameters


@pytest.fixture(scope="module")
def player_seed(tmp_path_factory):
    # A created player character carries the Ruleset's player-control field, as in real play.
    return _seed(tmp_path_factory, "prose-player", {"party_role": "player_character"})


@pytest.fixture(scope="module")
def gm_owned_seed(tmp_path_factory):
    return _seed(tmp_path_factory, "prose-gm-owned", None)


class Session:
    """The live script's turns on a scratch copy, with ChatGPT's transport and fixed dice."""

    def __init__(self, seed, tmp_path, monkeypatch):
        monkeypatch.setattr(dice, "_rng", Random(0))
        monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
        # The stable list, so the scripted GM calls mechanics by their own names. The ChatGPT
        # route ships the tool loader (tests/test_chatgpt_deferred_tool_list.py); its live
        # runs crossed this same player boundary without a discarded reply.
        monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", "stable")
        database = tmp_path / "session.db"
        shutil.copy2(seed[0], database)
        self.parameters = seed[1]
        self.gm = FixedGM()
        self.owner = AuthorityOwner.open(database, "prose-owner", disconnect_grace_seconds=1.0)

    def turn(self, text, steps):
        self.gm.steps.extend(steps)
        identity = f"turn-{len(self.gm.calls)}"
        first = len(self.gm.calls)
        result = CampaignSessionLoop(self.owner).run(SessionRunRequest(
            session_id="prose-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(self.owner, self.gm, owner_id="prose-owner")
            .for_turn("casting-turn"))
        assert result.interruption is None, result.interruption
        assert self.gm.position == len(self.gm.steps), (self.gm.position, self.gm.steps)
        shown = [event.safe_payload["text"] for event in result.canonical
                 if event.event_type is CanonicalEventType.TALK]
        return self.gm.calls[first:], shown

    def start_step(self):
        scene = _plain_json(self.owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        return ("start_group_combat_encounter",
                {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene],
                 "player_intent_after_preparation": "await_player_choice"})

    def close(self):
        self.owner.close()


class LoaderSession(Session):
    """The same turns on the ChatGPT route's shipped default: Sylvia's tool loader.

    The loader-aware scripted GM calls each mechanic through ``call_mechanic``, and repeats a
    first call the loader refuses with the mechanic's definition (``mechanic_arguments_unread``).
    """

    def __init__(self, seed, tmp_path, monkeypatch):
        from tests.test_gm_context_contract import _chatgpt_loader_gm

        super().__init__(seed, tmp_path, monkeypatch)
        monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)
        self.gm = _chatgpt_loader_gm()


def _text(call):
    return "\n".join(str(message.get("content", "")) for message in call["messages"])


def test_loader_holds_fight_start_and_cast_prose_for_the_campaign_file(
    player_seed, tmp_path, monkeypatch,
):
    """#215's fix on the ChatGPT route's shipped default, the tool loader."""
    from sylvia_gm.agent._tool_loader import MECHANIC_TOOL_NAME

    session = LoaderSession(player_seed, tmp_path, monkeypatch)
    try:
        session.turn("We look around the clearing. What do we notice?",
                     [settle("The clearing is quiet.")])
        fight, shown = session.turn(START_TEXT, [session.start_step(), ("prose", FIGHT_PROSE),
                                          UNCHANGED])
        # The loader is in use: one small declared list with its generic mechanic tool (the
        # request as sent; the scripted GM's own view adds the mechanics it has read).
        declared = [tool["function"]["name"] for tool in session.gm.requests[-1]["tools"]]
        assert MECHANIC_TOOL_NAME in declared and len(declared) < 16
        # The prose reply is held for one Campaign File disposition, never discarded.
        assert not any(DISCARDED in _text(call) for call in fight)
        held = [call for call in fight if HELD in _text(call)]
        assert len(held) == 1 and FIGHT_PROSE in _text(held[0])
        combat = session.owner.snapshot("casting").state[f"{SRD}.combat"]
        assert combat["encounter_active"] is True and combat["active_actor"] == "hero"
        assert shown == [FIGHT_PROSE]

        summon = dict(session.parameters)
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion():
            pending = session.owner.snapshot("casting").state[f"{SRD}.state"][
                "pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        cast, shown = session.turn(CAST_TEXT, [("begin_srd_spell_action", summon),
                                        ("complete_srd_pending_action", completion),
                                        ("prose", CAST_PROSE), UNCHANGED])
        assert not any(DISCARDED in _text(call) for call in cast)
        # The last request of the turn is the Campaign File disposition for the cast's prose
        # (each request carries the history, so the fight's held record is in all of them).
        assert CAST_PROSE not in _text(cast[-2]) or HELD not in _text(cast[-2]).split(
            CAST_PROSE, 1)[-1]
        assert HELD in _text(cast[-1]).split(CAST_PROSE, 1)[-1]
        entities = session.owner.snapshot("casting").state[f"{SRD}.entities"]
        assert entities["dragon_one"]["summon_active"] is True
        assert shown == [CAST_PROSE]
    finally:
        session.close()
    assert session.gm.failure is None


def test_fight_start_and_cast_narrated_in_prose_are_held_for_the_campaign_file(
    player_seed, tmp_path, monkeypatch,
):
    session = Session(player_seed, tmp_path, monkeypatch)
    try:
        session.turn("We look around the clearing. What do we notice?",
                     [settle("The clearing is quiet.")])
        fight, shown = session.turn(START_TEXT, [session.start_step(), ("prose", FIGHT_PROSE),
                                          UNCHANGED])
        # Start, the prose reply, then one Campaign File disposition for the held narration.
        assert len(fight) == 3
        assert HELD not in _text(fight[1]) and DISCARDED not in _text(fight[1])
        assert HELD in _text(fight[2]) and DISCARDED not in _text(fight[2])
        assert FIGHT_PROSE in _text(fight[2])
        assert [name.split("_", 2)[-1] for name in fight[2]["tools"]].count(
            "update_campaign_file") == 1
        combat = session.owner.snapshot("casting").state[f"{SRD}.combat"]
        assert combat["encounter_active"] is True and combat["active_actor"] == "hero"
        assert shown == [FIGHT_PROSE]

        summon = dict(session.parameters)
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion():
            pending = session.owner.snapshot("casting").state[f"{SRD}.state"][
                "pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        cast, shown = session.turn(CAST_TEXT, [("begin_srd_spell_action", summon),
                                        ("complete_srd_pending_action", completion),
                                        ("prose", CAST_PROSE), UNCHANGED])
        assert len(cast) == 4
        assert HELD in _text(cast[3]) and DISCARDED not in _text(cast[3])
        entities = session.owner.snapshot("casting").state[f"{SRD}.entities"]
        assert entities["dragon_one"]["summon_active"] is True
        assert shown == [CAST_PROSE]
    finally:
        session.close()
    assert session.gm.failure is None


def test_prose_that_never_settles_the_campaign_file_still_ends_the_turn(
    player_seed, tmp_path, monkeypatch,
):
    session = Session(player_seed, tmp_path, monkeypatch)
    try:
        session.turn("We look around the clearing. What do we notice?",
                     [settle("The clearing is quiet.")])
        session.gm.steps.extend([session.start_step(), *[("prose", FIGHT_PROSE)] * 4])
        with pytest.raises(CommandRejected) as caught:
            CampaignSessionLoop(session.owner).run(SessionRunRequest(
                session_id="prose-session", turn_id="casting-turn", attempt_id="fight",
                message_id="fight", user_text=START_TEXT,
            ), ProviderSessionAdapter(session.owner, session.gm, owner_id="prose-owner")
                .for_turn("casting-turn"))
        assert caught.value.code == "campaign_file_disposition_required"
        # One held reply, then the bounded Campaign File corrections; the turn shows nothing.
        assert sum(HELD in _text(call) for call in session.gm.calls) == 4
    finally:
        session.close()


def test_prose_at_a_gm_owned_actor_decision_is_still_discarded(
    gm_owned_seed, tmp_path, monkeypatch,
):
    # Without the player-control field the Ruleset gives the hero's turn to the GM, so the GM
    # must act for it; prose cannot end that turn.
    session = Session(gm_owned_seed, tmp_path, monkeypatch)
    try:
        session.turn("We look around the clearing. What do we notice?",
                     [settle("The clearing is quiet.")])
        session.gm.steps.extend([session.start_step(), *[("prose", FIGHT_PROSE)] * 4])
        with pytest.raises(CommandRejected) as caught:
            CampaignSessionLoop(session.owner).run(SessionRunRequest(
                session_id="prose-session", turn_id="casting-turn", attempt_id="fight",
                message_id="fight", user_text=START_TEXT,
            ), ProviderSessionAdapter(session.owner, session.gm, owner_id="prose-owner")
                .for_turn("casting-turn"))
        assert caught.value.code == "provider_required_tool_call_missing"
        assert not any(HELD in _text(call) for call in session.gm.calls)
        assert FIGHT_PROSE not in _text(session.gm.calls[-1])
    finally:
        session.close()
