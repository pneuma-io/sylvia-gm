"""Missing declared values remain null; unknown literal names remain text."""
from copy import deepcopy
import pytest
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._effect_executor import _resolve_value
from sylvia_gm.runtime._types import ExecutionContext,ToolDef,ToolParameter
from sylvia_gm.runtime._tool_executor import execute_tool

@pytest.mark.parametrize("reference", ["state.spatial_scene", "_saved.map.revision", "optional", "combat.missing", "definitions.missing"])
def test_known_nullable_references_never_become_literal_text(reference):
    ctx=ExecutionContext(params={"optional":None},vars={"_saved":None},entities={"state":{}})
    assert _resolve_value(reference,ctx) is None

@pytest.mark.parametrize("text", ["humanoid", "prone", "unknown.world", "An authored sentence."])
def test_unknown_authored_text_still_stays_literal(text):
    assert _resolve_value(text,ExecutionContext())==text

def test_first_scene_arrives_through_public_parameter_and_preserves_integer_revision():
    scene={"format":1,"map":{"id":"qa","revision":1,"bounds":{"min":[0,0,0],"max":[20,20,20]},
        "grid":{"cell_size":5,"unit":"ft","origin":[0,0]},"layers":[{"id":"ground","min_elevation":0,"max_elevation":20}]},
        "entities":[],"geometry":[],"zones":[],"terrain":[]}
    original=deepcopy(scene)
    bridge=AuthorityMemoryStateBridge("qa.test",{"qa.test.state":{"advancement":{}}})
    tool=ToolDef(name="enter",parameters=[ToolParameter("lasting_state","string",resolve="entity"),ToolParameter("scenes","array")],
        effects=[{"set":{"field":"_saved","value":"state.spatial_scene"}},
                 {"condition":"_saved == null","on_true":[{"set":{"field":"state.spatial_scene","value":"scenes[0]"}}]},
                 {"spatial_query":{"scene":"state.spatial_scene","operation":"snapshot"},"store_as":"_snapshot"}])
    result=execute_tool("enter",{"lasting_state":"state","scenes":[scene]},{"enter":tool},bridge,{},{},{})
    assert result.success,result.error
    assert result.context["_saved"] is None
    assert result.context["_snapshot"]["geometry_revision"]==1
    assert type(result.context["_snapshot"]["geometry_revision"]) is int
    assert bridge.read_field("state","spatial_scene")==original and scene==original


@pytest.mark.parametrize("supplied,expected", [({},None), ({"optional":False},False), ({"optional":True},True)])
def test_omitted_declared_optional_boolean_remains_typed_through_nested_replay(supplied,expected):
    outer=ToolDef(name="outer",parameters=[ToolParameter("optional","boolean",required=False)],
        effects=[{"set":{"field":"_saved","value":"optional"}},
                 {"call":"inner","params":{"optional":"{_saved}"},"export_context":{"_replayed":"_replayed"}}])
    inner=ToolDef(name="inner",parameters=[ToolParameter("optional","boolean",required=False)],
        effects=[{"set":{"field":"_replayed","value":"optional"}}])
    def nested(name,parameters,context):
        return execute_tool(name,parameters,{"inner":inner},None,{},{},{})
    result=execute_tool("outer",supplied,{"outer":outer,"inner":inner},None,{},{},{},tool_executor=nested)
    assert result.success,result.error
    assert result.context["_saved"] is expected
    assert result.context["_replayed"] is expected


def test_optional_boolean_still_rejects_text():
    tool=ToolDef(name="outer",parameters=[ToolParameter("optional","boolean",required=False)])
    result=execute_tool("outer",{"optional":"optional"},{"outer":tool},None,{},{},{})
    assert not result.success and result.failure_kind=="parameter_validation"
