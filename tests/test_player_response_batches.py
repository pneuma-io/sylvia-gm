"""A failed direct-action response never absorbs later committed outcomes."""
import json
import pytest
from tests.test_player_actions import _create, _move
from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.agent._session_loop import CampaignSessionLoop,SessionRunRequest,SessionInputKind,ProviderEvent
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.provider._exceptions import ProviderConnectionError

def result_ids(messages):
    text=next(row['content'] for row in messages if str(row.get('content','')).startswith('SYLVIA_PLAYER_CONTROL_RESULTS'))
    return [row['action_id'] for row in json.loads(text[text.index('['):])]

@pytest.mark.parametrize('legacy,combined,partial_retry', [(False,False,False),(True,False,False),(False,True,True)])
def test_failed_response_is_frozen_and_later_action_stays_pending_after_retry(tmp_path,monkeypatch,legacy,combined,partial_retry):
    database=tmp_path/'frozen.sqlite3'
    request=SessionRunRequest(session_id='gm',turn_id='turn',attempt_id='failed',message_id='failed-input',
        user_text='',input_kind=SessionInputKind.PLAYER_ACTION_RESPONSE,player_action_id='old')
    with AuthorityOwner.open(database,'owner',disconnect_grace_seconds=1.0) as owner:
        _create(owner,controls={'response':'on_request'})
        old=_move(owner,operation_id='old')
        expected=['old']
        if combined:
            _move(owner,operation_id='same-response',session_id='gm',
                  selection={'actor':'operator','destination':[2,3],'map_revision':7},
                  expected_revision=owner.snapshot('campaign').revision)
            expected.append('same-response')
        if legacy:
            append=owner.append_agent_message
            def historical_append(**kwargs):
                if kwargs['content'].get('kind')=='player_action_response':
                    kwargs['content'].pop('player_action_ids',None)
                return append(**kwargs)
            monkeypatch.setattr(owner,'append_agent_message',historical_append)
        adapter=ProviderSessionAdapter(owner,_ScriptedProvider([]),owner_id='owner')
        def fail(value):
            assert result_ids(adapter._messages(value))==expected
            raise ProviderConnectionError('synthetic failure')
            yield
        loop=CampaignSessionLoop(owner)
        with pytest.raises(ProviderConnectionError):loop.run(request,fail)
        interruption=owner.retryable_interruption('campaign',failed_attempt_id='failed')
        new=_move(owner,operation_id='later',selection={'actor':'operator','destination':[3,3],'map_revision':7},expected_revision=owner.snapshot('campaign').revision)
        after=owner.snapshot('campaign')
    with AuthorityOwner.open(database,'owner',disconnect_grace_seconds=1.0) as owner:
        adapter=ProviderSessionAdapter(owner,_ScriptedProvider([]),owner_id='owner')
        if partial_retry:
            def partial_factory(turn):
                def partial(value):
                    assert result_ids(adapter._messages(value))==expected
                    yield ProviderEvent.talk('Only part of the original response.')
                    raise ProviderConnectionError('synthetic interrupted retry')
                return partial
            with pytest.raises(ProviderConnectionError):
                CampaignSessionLoop(owner).retry('gm',interruption_id=interruption.interruption_id,
                    retry_id='partial-retry',narrator=lambda *args:pytest.fail('provider required'),
                    provider_factory=partial_factory)
            assert [row['action_id'] for row in owner.player_action_context('campaign')]==['later']
            interruption=owner.retryable_interruption('campaign',failed_attempt_id='partial-retry')
        def factory(turn):
            def response(value):
                assert result_ids(adapter._messages(value))==expected,'Retry must retain the original outcomes'
                yield ProviderEvent.talk('The earlier action is described.')
            return response
        CampaignSessionLoop(owner).retry('gm',interruption_id=interruption.interruption_id,retry_id='retry',
            narrator=lambda *args:pytest.fail('provider retry required'),provider_factory=factory)
        assert owner.snapshot('campaign').state==after.state
        assert len(owner.snapshot('campaign').actions)==2+int(combined)
        assert [row['action_id']for row in owner.player_action_context('campaign')]==['later']
        assert owner.campaign_panels('campaign')['response_action']['action_id']=='later'
        def describe_later(value):
            assert result_ids(adapter._messages(value))==['later'], 'The second frozen request must read persisted immutable IDs'
            yield ProviderEvent.talk('The later move is now described.')
        turn=owner.current_open_turn('campaign')
        if turn is None:
            owner.open_turn('campaign','later-turn');turn=owner.current_open_turn('campaign')
        before_later=owner.snapshot('campaign')
        CampaignSessionLoop(owner).run(SessionRunRequest(session_id='gm',turn_id=turn,attempt_id='later-description',
            message_id='later-description',user_text='',input_kind=SessionInputKind.PLAYER_ACTION_RESPONSE,player_action_id='later'),describe_later)
        assert owner.snapshot('campaign').state==before_later.state
        assert owner.snapshot('campaign').actions==before_later.actions
        assert owner.player_action_context('campaign')==()
