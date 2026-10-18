"""Campaign discovery ranks retained SQLite sources without changing their authority."""

import pytest

from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from tests.test_alpha_05_rift_journey import _construct_rift


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("knowledge-search")
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(root / "campaign.db", "search-owner")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    try:
        campaign_id, session_id = _construct_rift(
            LocalCommandBus(supervisor),
            owner,
            root,
            lane="quick_start",
            companion_ids=(),
        )
        yield owner, campaign_id, session_id
    finally:
        supervisor.shutdown()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Stern", ("npcs/patrol-veteran-stern.md",)),
        ("Stern characterization", ("npcs/patrol-veteran-stern.md",)),
        ("Tell me about Stern and her personality", ("npcs/patrol-veteran-stern.md",)),
        ("Waystation geography", ("locations/waystation.md",)),
        (
            "Publicly known documented witnessed Anchor Door opening at Waystation or elsewhere "
            "Returners records testimony Linden what evidence exists as opposed to hope",
            ("locations/waystation.md", "npcs/researcher-linden.md"),
        ),
        ("Anchor Doors history public records opening", ("lore/world-overview.md",)),
        (
            "Stern Returner patrol Verdant Margin characterization role voice response to someone "
            "calling from Anchor Clearing",
            ("npcs/patrol-veteran-stern.md", "locations/verdant-margin.md"),
        ),
        (
            "Verdant Margin Anchor Clearing nearest settlement safe route brush Waystation "
            "local geography",
            ("locations/verdant-margin.md", "locations/waystation.md"),
        ),
    ],
)
@pytest.mark.parametrize(
    "sources",
    [("campaign_setting",), ("campaign_setting", "campaign_plan", "campaign_state", "participant")],
)
def test_natural_queries_find_exact_sources(campaign, query, expected, sources):
    owner, campaign_id, session_id = campaign
    result = owner.search_campaign_knowledge(
        campaign_id,
        query=query,
        sources=sources,
        limit=5,
    )
    matches = result["matches"]
    assert all(any(item["path"].split("#")[0] == path for item in matches) for path in expected), [
        (item["path"], item["label"]) for item in matches
    ]
    if sources == ("campaign_setting",):
        assert matches[0]["path"].split("#")[0] in expected
    for item in matches:
        assert item["source"] in sources
        assert "content" not in item and "body" not in item and "_body" not in item
        exact = owner.read_campaign_reference(campaign_id, source=item["source"], path=item["path"])
        assert exact["content"]
        if item["source"] == "campaign_setting":
            assert item["provenance"]["package_sha256"]


@pytest.mark.parametrize(
    "query",
    [
        "person who definitely does not exist",
        "ZXQnotaperson",
        "sternum",
        "quantum toaster warranty",
    ],
)
def test_unrelated_queries_stay_empty(campaign, query):
    owner, campaign_id, session_id = campaign
    assert owner.search_campaign_knowledge(campaign_id, query=query)["matches"] == []


def test_ambiguous_current_names_precede_reusable_baseline(campaign):
    owner, campaign_id, session_id = campaign
    owner.append_agent_message(
        session_id=session_id,
        message_id="search-basis",
        kind=SessionMessageKind.USER,
        content={"text": "Record the two unrelated scouts named Stern for this test."},
    )
    owner.update_campaign_file(
        session_id=session_id,
        update_id="scouts",
        turn_id=owner.current_open_turn(campaign_id),
        basis_message_ids=("search-basis",),
        updates=tuple(
            {
                "operation": "record_entity",
                "entity_id": identity,
                "entity_kind": "person",
                "name": "Stern",
                "attributes": {"current_location": location},
                "visibility": "player_known",
                "known_by": ["player"],
            }
            for identity, location in (
                ("stern-north", "North camp"),
                ("stern-south", "South camp"),
            )
        ),
    )
    result = owner.search_campaign_knowledge(campaign_id, query="Where is Stern now?", limit=5)
    assert {item["path"] for item in result["matches"][:2]} == {"stern-north", "stern-south"}
    assert all(item["source"] == "campaign_state" for item in result["matches"][:2])
    assert any(item["source"] == "campaign_setting" for item in result["matches"])
    assert "Campaign State remains current truth" in result["authority"]
