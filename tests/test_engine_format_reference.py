"""GM format discovery reads installed definitions without changing campaign authority."""

import hashlib
import json

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_alpha_f4_generic_sessions import _campaign


def test_gm_can_discover_exact_spatial_format_without_plan_or_campaign_write(tmp_path):
    with AuthorityOwner.open(tmp_path / "format.db", "format-owner") as owner:
        _campaign(owner, campaign_id="format", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="format", role=WriterRole.DM, provider_id="synthetic",
            model_id="synthetic", session_id="session",
        )
        before = owner.snapshot("format")
        catalog = StatelessMcpMechanics(owner).list_tools("format", session_id="session")
        read = next(tool for tool in catalog if tool.name == "read_campaign_reference")
        search = next(tool for tool in catalog if tool.name == "search_campaign_knowledge")
        assert "engine_format" in read.input_schema["properties"]["source"]["enum"]
        assert "spatial_scene" in read.input_schema["properties"]["path"]["description"]
        sources = search.input_schema["properties"]["sources"]["items"]["enum"]
        assert "engine_format" not in sources
        result = owner.read_campaign_reference(
            "format", source="engine_format", path="spatial_scene"
        )
        expected = get_content_definition("spatial_scene")
        assert json.loads(result["content"]) == expected
        expected_hash = hashlib.sha256(canonical_json_bytes(expected)).hexdigest()
        assert result["content_sha256"] == expected_hash
        assert result["provenance"]["owner"] == "sylvia.application"
        assert owner.snapshot("format") == before
        with pytest.raises(CommandRejected, match="read bound"):
            owner.read_campaign_reference(
                "format", source="engine_format", path="spatial_scene", maximum_bytes=1
            )
        with pytest.raises(CommandRejected, match="unavailable"):
            owner.read_campaign_reference("format", source="engine_format", path="../auth.json")
        assert owner.snapshot("format") == before
