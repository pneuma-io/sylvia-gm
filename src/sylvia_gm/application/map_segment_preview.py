"""A declared physical segment preview; no action costs or mechanics are inferred."""
from collections.abc import Mapping
from math import dist
from sylvia_gm.application.tactical_grid import project_coordinate_map, project_tactical_grid
from sylvia_gm.application.spatial_preview import project_spatial_preview
from sylvia_gm.spatial import SpatialEngine
from sylvia_gm.domain.authority import owned_payload

def segment_preview(surface, data, selection):
    """Measure raw intent against the safe known scene through the shared spatial engine."""
    if not isinstance(selection, Mapping): return None
    action=surface['action']
    if action.get('spatial_preview')!='segment': return None
    projected=(project_coordinate_map(data) if surface['renderer']=='coordinate_map' else project_tactical_grid(data))
    actor=next((row for row in projected['entities'] if row['id']==selection.get('actor')),None)
    target=next((row for row in projected['entities'] if row['id']==selection.get('target')),None)
    endpoint=selection.get('aim_point') if 'aim_point' in action.get('bindings',{}).values() else target and target['position']
    if actor is None or endpoint is None: return None
    scene=data['scene']
    query={'kind':'ray','map_id':projected['map']['id'],'origin':actor['position'],
           'destination':endpoint,'include':['geometry'],'max_intersections':256}
    result=SpatialEngine(scene).query(query).as_payload()
    drawing=project_spatial_preview({'ray':'segment'},results={'segment':result},
        queries={'segment':query},scenes={'segment':scene})
    return owned_payload({'map_preview':drawing,'preview':{'version':1,'footprint':'compact',
        'title':action['label'],'summary':[{'label':'Distance','value':str(round(dist(actor['position'],endpoint),2))+' '+projected['map']['grid']['unit']},
                                         {'label':'Geometry contacts','value':len(result['intersections'])}]}})
