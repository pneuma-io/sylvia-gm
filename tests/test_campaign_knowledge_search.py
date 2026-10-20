"""Campaign discovery ranks retained SQLite sources without changing their authority."""

import io

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.domain.authority import PackageRole, StateValue
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION
from sylvia_gm.domain.session import SessionMessageKind


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("knowledge-search")
    with AuthorityOwner.open(root / "campaign.db", "search-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="search"
        )
        primary = next(p for p in candidate.package_lock.entries if p.role is PackageRole.PRIMARY)
        setting = next(
            p for p in candidate.package_lock.entries if p.package_id == "pneuma.severed-lands"
        )
        state = {
            "advancement": {
                "method": "fixed",
                "starting_level": 1,
                "maximum_level": 1,
                "recipients": "whole_party",
                "timing": "Reference QA only.",
                "allocation": "No awards.",
            }
        }
        confirmation = owner.confirm_new_campaign(
            operation_id="search-confirm",
            display_name="Search QA",
            package_lock=candidate.package_lock,
            turn_id="construction",
            package_candidate_id=candidate.candidate_id,
            builder_session_id="search-builder",
            builder_provider_id="fixture",
            builder_model_id="fixture",
            participants=(),
            initial_state=(StateValue(primary.package_id, "state", state),),
        )
        campaign_id = confirmation.campaign_id
        owner.begin_campaign_construction(
            operation_id="search-begin",
            campaign_id=campaign_id,
            lane="quick_start",
            expected_revision=0,
            canonical_input={"premise": "Search QA"},
        )
        lock = owner.current_package_lock(campaign_id)
        _, _, handoff = owner.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id="fixture",
            dm_model_id="fixture",
            plan_artifact_text="# Search QA",
            model_guidance_override=True,
            structured_plan={
                "format_id": "sylvia-gm-campaign-plan",
                "format_version": CAMPAIGN_PLAN_FORMAT_VERSION,
                "bindings": {
                    "ruleset": {
                        "id": primary.package_id,
                        "version": primary.version,
                        "content_sha256": primary.sha256,
                    },
                    "campaign_setting": {
                        "id": setting.package_id,
                        "version": setting.version,
                        "content_sha256": setting.sha256,
                    },
                    "package_closure_sha256": lock.closure_sha256,
                },
                "initial_state": {
                    "mechanics": [{"namespace": primary.package_id, "key": "state", "value": state}]
                },
            },
        )
        owner.open_turn(campaign_id, "search-turn")
        yield owner, campaign_id, handoff.dm_session_id


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
