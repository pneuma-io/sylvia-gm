"""Sunburst derives its chosen-point Sphere from actual admitted creature volumes."""

import re
from copy import deepcopy

SOLID_HIT = 'qualify_srd_area_solid_hit'
AREA_ORIGIN = 'project_srd_area_origin'
BODY_NEAREST = 'derive_srd_point_area_body_nearest'


def _solid_hit():
    """Report an interior crossing separately from whether its start is inside the solid."""
    return _tool_scene('Qualify the actual solid interior crossed by an area ray.',
        [_parameter('geometry_id'), _parameter('plane'), _array('start'), _array('end')], [
            _set('_area_solid_crossed', False),
            _set('_area_solid_start_inside', False),
            _set('_area_solid_rows', 'state.spatial_scene.geometry'),
            _loop('_area_solid_rows', '_area_solid', [
                _when("_area_solid.id == geometry_id and _area_solid.active == true and "
                      "_area_solid.movement == 'blocking' and "
                      "(_area_solid.metadata.plane == plane or (_area_solid.metadata.plane == null and plane == 'material'))", [
                    _call('srd_information_segment_interval', {
                        'start': '{start}', 'end': '{end}', 'volume': '{_area_solid.volume}'},
                        ('_segment_entry', '_segment_exit')),
                    _set('_area_solid_mid', [
                        f'start[{i}] + (end[{i}] - start[{i}]) * (_segment_entry + _segment_exit) * 0.5'
                        for i in range(3)]),
                    _when('_segment_exit - _segment_entry > 0.000000001', [
                        _when("_area_solid.volume.kind == 'box'", [
                            _set('_area_solid_start_inside', ' and '.join(
                                f'start[{i}] > _area_solid.volume.min[{i}] + 0.000000001 and '
                                f'start[{i}] < _area_solid.volume.max[{i}] - 0.000000001'
                                for i in range(3))),
                            _set('_area_solid_crossed', ' and '.join(
                                f'_area_solid_mid[{i}] > _area_solid.volume.min[{i}] + 0.000000001 and '
                                f'_area_solid_mid[{i}] < _area_solid.volume.max[{i}] - 0.000000001'
                                for i in range(3)))]),
                    ])
                ])
            ])
        ])

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene, _array
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push
    from .srd_lite_mass_cure_geometry import _nearest_body
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene, _array
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push
    from srd_lite_mass_cure_geometry import _nearest_body


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _intent():
    return [
        _parameter(
            "sunburst_origin",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
            description="Choose the actual area origin. When target Cover varies by origin, admit target scene "
                        "metadata.cover_from_points rows with point [x,y,z], plane, and cover "
                        "(none, half, three_quarters, or total).",
        ),
        _parameter("sunburst_plane", required=False, default=""),
    ]


def _contains(frame, point="origin"):
    """Use true Box or circular body surfaces within the existing arithmetic tolerance."""
    return (
        f"max({frame}.position[2]-{point}[2],{point}[2]-{frame}.position[2]-{frame}.height)<=0.000000001 and "
        f"(({frame}.footprint.kind=='box' and max(abs({point}[0]-{frame}.position[0])-{frame}.footprint.half_extents[0],"
        f"abs({point}[1]-{frame}.position[1])-{frame}.footprint.half_extents[1])<=0.000000001) or "
        f"({frame}.footprint.kind=='circle' and (({point}[0]-{frame}.position[0])**2+"
        f"({point}[1]-{frame}.position[1])**2)**0.5<={frame}.footprint.radius+0.000000001))"
    )


def _body_nearest_tool():
    """Reuse Mass Cure Wounds' proven nearest occupied point without state writes."""
    effects = _nearest_body('body', 'point')
    effects[0]['require']['error'] = 'Point-area placement needs an actual admitted box or circular body.'
    return _tool_scene('Find the nearest occupied point on an actual point-area body.',
        [_parameter('body', 'object', from_state='state.spatial_scene'), _array('point')], effects)


def _body_nearest_call(frame, point, result='_mass_cure_nearest'):
    """Keep every point-area consumer on the same actual body distance calculation."""
    return {'call': BODY_NEAREST, 'params': {'body': '{' + frame + '}', 'point': '{' + point + '}'},
            'export_context': {result: '_mass_cure_nearest', '_mass_cure_distance_squared': '_mass_cure_distance_squared'}}


def _nearest(frame, point):
    """Retain the existing aligned-box expression interface for its other area consumers."""
    return [
        f"max({frame}.position[0] - {frame}.footprint.half_extents[0], "
        f"min({point}[0], {frame}.position[0] + {frame}.footprint.half_extents[0]))",
        f"max({frame}.position[1] - {frame}.footprint.half_extents[1], "
        f"min({point}[1], {frame}.position[1] + {frame}.footprint.half_extents[1]))",
        f"max({frame}.position[2], min({point}[2], {frame}.position[2] + {frame}.height))",
    ]


def _area_origin():
    """Project an area to the first real solid interior crossed by its origin ray."""
    return _tool_scene('Project the actual first solid boundary on an area-origin path.',
        [_array('start'), _array('origin'), _parameter('plane')], [
            _set('_area_origin', 'origin'), _set('_area_origin_blocked', False),
            _set('_area_origin_start_inside', False),
            {'spatial_query': {'scene': 'state.spatial_scene', 'operation': 'ray',
                'snapshot': '{_info_snapshot}', 'query': {
                    'map_id': '{state.spatial_scene.map.id}', 'origin': '{start}',
                    'destination': '{origin}', 'include': ['geometry'], 'max_intersections': 1024}},
             'store_as': '_area_origin_ray'},
            _require('_area_origin_ray.truncated != true', 'Area-origin placement exceeded the actual ray bound.'),
            _set('_area_origin_hits', '_area_origin_ray.intersections'),
            _loop('_area_origin_hits', '_area_origin_hit', [
                _when('_area_origin_blocked != true', [
                    _call(SOLID_HIT, {'geometry_id': '{_area_origin_hit.id}', 'plane': '{plane}',
                          'start': '{start}', 'end': '{origin}'},
                          ('_area_solid_crossed', '_area_solid_start_inside')),
                    _when('_area_solid_crossed == true', [
                        _set('_area_origin', '_area_origin_hit.point'),
                        _set('_area_origin_blocked', True),
                        _set('_area_origin_start_inside', '_area_solid_start_inside'),
                    ]),
                ]),
            ]),
        ])


def _origin_unseen():
    return _parameter('origin_unseen', 'boolean', required=False, default=False)


def _origin_solid_path(origin="sunburst_origin", plane="_sunburst_plane",
                       error="A solid barrier blocks Sunburst's chosen point.",
                       formed="_sunburst_origin"):
    """Place the actual origin and enforce its visibility and solid-boundary guards."""
    return [
        _call(AREA_ORIGIN, {
            'start': '{_sunburst_range_start}', 'origin': '{' + origin + '}', 'plane': '{' + plane + '}'},
            ('_area_origin', '_area_origin_blocked', '_area_origin_start_inside')),
        _require('_area_origin_blocked != true or origin_unseen == true', error),
        _require('_area_origin_start_inside != true', 'An area cannot form from inside a blocking solid.'),
        _set(formed, '_area_origin'),
        _set('_sunburst_origin_relocated', '_area_origin_blocked'),
    ]


def bind_sunburst_origin_interior(runtime):
    """Wire chosen-point placement, public visibility and formatted area feedback."""
    tools = runtime['tools']
    changed = []
    for name, origin, plane, formed in (
        ('collect_srd_sunburst_creature_targets', 'sunburst_origin', '_sunburst_plane', '_sunburst_origin'),
        ('qualify_srd_delayed_blast_point', 'delayed_blast_origin', '_dbf_plane', '_dbf_origin'),
    ):
        if name not in tools:
            continue
        owners = [(rows, index) for rows in _lists(tools[name]['effects'])
                  for index, effect in enumerate(rows)
                  if effect.get('call') == 'srd_information_barriers'
                  and effect.get('params', {}).get('rule') == 'solid']
        if len(owners) != 1:
            raise ValueError('Keep the sole retained chosen-origin barrier check: ' + name)
        rows, index = owners[0]
        expected = {'start': '{_sunburst_range_start}', 'end': '{' + origin + '}',
                    'rule': 'solid', 'plane': '{' + plane + '}'}
        guard = rows[index + 1].get('require', {})
        if rows[index]['params'] != expected or guard.get('condition') != '_information_blocked != true':
            raise ValueError('Keep the retained chosen-origin barrier and guard: ' + name)
        rows[index:index + 2] = _origin_solid_path(origin, plane, guard['error'], formed)
        rows[index + 5:] = _replace_names(rows[index + 5:], {origin: formed})
        tools[name]['parameters'].append(_origin_unseen())
        changed.append(name)
    if not changed:
        raise ValueError('The selected Sunburst origin owner is missing')
    # The preserved worker runtime predates this module's current body calculation.
    tools['collect_srd_sunburst_creature_targets'] = _sphere_tool()
    tools[BODY_NEAREST] = _body_nearest_tool()
    tools[SOLID_HIT] = _solid_hit()
    tools[AREA_ORIGIN] = _area_origin()
    family = tools['cast_srd_sunburst']
    family['parameters'].append(_origin_unseen())
    collectors = [effect for rows in _lists(family['effects']) for effect in rows
                  if effect.get('call') == 'collect_srd_sunburst_creature_targets']
    if len(collectors) != 1:
        raise ValueError('Keep the sole Sunburst recipient collector')
    collectors[0]['params']['origin_unseen'] = '{origin_unseen}'
    collectors[0].setdefault('export_context', {})['_sunburst_origin_relocated'] = '_sunburst_origin_relocated'
    card_owners = set()
    for name, tool in tools.items():
        for rows in list(_lists(tool['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index].get('call') != 'cast_srd_sunburst':
                    continue
                if not any(p['name'] == 'caster_can_see_origin' for p in tool['parameters']):
                    raise ValueError('Keep the public origin visibility transport: ' + name)
                rows[index]['params']['origin_unseen'] = '{_sunburst_cast_origin_unseen}'
                rows[index].setdefault('export_context', {}).update({
                    '_sunburst_cast_rows': '_sunburst_cast_rows',
                    '_sunburst_cast_details': '_sunburst_cast_details',
                })
                rows.insert(index, _set('_sunburst_cast_origin_unseen', 'caster_can_see_origin == false'))
                card_owners.add(name)
    family['effects'].append(_when('_spell_damage_dice != null', [
        _set('_sunburst_cast_rows', []),
        _set('_sunburst_cast_details', []),
        _call('format_srd_information_projection', {'message': 'Point {_sunburst_origin}'},
              ('_info_projection_message',)),
        _push('_sunburst_cast_rows', {'label': "'Origin'", 'value': '_info_projection_message'}),
        _push('_sunburst_cast_rows', {'label': "'Radius (feet)'", 'value': 60}),
        _call('format_srd_information_projection', {'message':
              'Sphere origin {_sunburst_origin}; plane {_sunburst_plane}; radius 60 feet.'},
              ('_info_projection_message',)),
        _push('_sunburst_cast_details', {'label': "'Sunburst area'", 'value': '_info_projection_message'}),
        _when('_sunburst_origin_relocated == true', [
            _push('_sunburst_cast_rows', {'label': "'Origin relocated'", 'value': "'Near side of obstruction'"}),
            _call('format_srd_information_projection', {'message':
                  'Requested {sunburst_origin}; formed {_sunburst_origin} on the near side of the obstruction.'},
                  ('_info_projection_message',)),
            _push('_sunburst_cast_details', {'label': "'Origin relocated'", 'value': '_info_projection_message'}),
        ]),
    ]))
    # Append bounded area rows after the generic recipient summary has applied its own display cap.
    for name in card_owners:
        tools[name]['effects'].append(_when('_sunburst_cast_rows != null', [
            _loop('_sunburst_cast_rows', '_sunburst_cast_row', [
                _push('_spell_card_summary', '_sunburst_cast_row')]),
            _loop('_sunburst_cast_details', '_sunburst_cast_detail', [
                _push('_spell_detail_rows', '_sunburst_cast_detail')]),
        ]))
    return changed


def _sphere_tool():
    """Closed Sphere intersection uses the nearest actual Box or circular body point."""
    return _tool_scene(
        "Validate Sunburst's actual chosen point and collect every supported living creature.",
        [_entity("caster_state"), *_intent(), _origin_unseen()],
        [
            _require(
                "len(sunburst_origin) == 3", "Choose Sunburst's actual three-dimensional point."
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_sunburst_source", "_info_scene_entity"),
            _require(
                "_sunburst_source.metadata.plane != null",
                "Sunburst needs its actual caster body and plane.",
            ),
            _require(
                "sunburst_plane == '' or sunburst_plane == _sunburst_source.metadata.plane",
                "Sunburst's chosen point must be on the caster's actual plane.",
            ),
            _set("_sunburst_plane", "_sunburst_source.metadata.plane"),
            _set("_sunburst_origin", "sunburst_origin"),
            _require(
                "sunburst_origin[0] >= state.spatial_scene.map.bounds.min[0] and "
                "sunburst_origin[0] <= state.spatial_scene.map.bounds.max[0] and "
                "sunburst_origin[1] >= state.spatial_scene.map.bounds.min[1] and "
                "sunburst_origin[1] <= state.spatial_scene.map.bounds.max[1] and "
                "sunburst_origin[2] >= state.spatial_scene.map.bounds.min[2] and "
                "sunburst_origin[2] <= state.spatial_scene.map.bounds.max[2]",
                "Sunburst's point must be inside the admitted scene.",
            ),
            _body_nearest_call('_sunburst_source', 'sunburst_origin', '_sunburst_range_start'),
            _require(
                "(sunburst_origin[0] - _sunburst_range_start[0]) ** 2 + "
                "(sunburst_origin[1] - _sunburst_range_start[1]) ** 2 + "
                "(sunburst_origin[2] - _sunburst_range_start[2]) ** 2 <= 150 ** 2",
                "Sunburst's actual chosen point is beyond150 feet.",
            ),
            *_origin_solid_path(),
            *_snapshot(),
            _set("_sunburst_sphere_targets", []),
            _set("_sunburst_seen_actors", []),
            _set("_sunburst_scene_rows", "state.spatial_scene.entities"),
            _loop(
                "_sunburst_scene_rows",
                "_sunburst_target",
                [
                    _when(
                        "_sunburst_target.active == true and "
                        "_sunburst_target.metadata.participant_ref != null and "
                        "_sunburst_target.metadata.is_object != true and "
                        "_sunburst_target.metadata.creature_type != 'spell_force' and "
                        "(_sunburst_target.metadata.plane == _sunburst_plane or "
                        "_sunburst_target.metadata.plane == null)",
                        [
                            _call(
                                "check_srd_cone_creature_actor",
                                {"target_state": "{_sunburst_target.metadata.participant_ref}"},
                                ("_cone_actual_creature",),
                            ),
                            _when(
                                "_cone_actual_creature == true",
                                [
                                    _require(
                                        "_sunburst_target.metadata.plane != null",
                                        "Sunburst needs each actual creature's admitted plane.",
                                    ),
                                    _require(
                                        "_sunburst_target.metadata.participant_ref not in "
                                        "_sunburst_seen_actors",
                                        "Sunburst creature has ambiguous scene proxies.",
                                    ),
                                    _push(
                                        "_sunburst_seen_actors",
                                        "_sunburst_target.metadata.participant_ref",
                                    ),
                                    _body_nearest_call('_sunburst_target', '_sunburst_origin', '_sunburst_near'),
                                    _when(
                                        "(_sunburst_near[0] - _sunburst_origin[0]) ** 2 + "
                                        "(_sunburst_near[1] - _sunburst_origin[1]) ** 2 + "
                                        "(_sunburst_near[2] - _sunburst_origin[2]) ** 2 <= 60 ** 2",
                                        [
                                            _require(
                                                "_sunburst_target.metadata.participant_ref not in "
                                                "_sunburst_sphere_targets",
                                                "Sunburst creature has ambiguous scene proxies.",
                                            ),
                                            _require(
                                                "len(_sunburst_sphere_targets) < 64",
                                                "Sunburst exceeds64 supported creatures.",
                                            ),
                                            _push(
                                                "_sunburst_sphere_targets",
                                                "_sunburst_target.metadata.participant_ref",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _point_cover_facts():
    """Select a single admitted Cover fact for this actual origin and plane, preserving source-only defaults."""
    return _when(
        "target_scene.metadata.cover_from_points != null",
        [
            _require("len(target_scene.metadata.cover_from_points) <= 64",
                     "Point-area Cover accepts at most 64 admitted origin facts per body."),
            _set("_point_cover_seen", False),
            _set("_point_cover_rows", "target_scene.metadata.cover_from_points"),
            _loop("_point_cover_rows", "_point_cover_row", [
                _require("_point_cover_row.point != null and len(_point_cover_row.point) == 3 and "
                         "_point_cover_row.plane != null",
                         "Each point-area Cover fact needs a three-dimensional point and actual plane."),
                _require("_point_cover_row.cover == 'none' or _point_cover_row.cover == 'half' or "
                         "_point_cover_row.cover == 'three_quarters' or _point_cover_row.cover == 'total'",
                         "Admit categorical Cover for each recorded area origin."),
                _when("_point_cover_row.point[0] == origin[0] and _point_cover_row.point[1] == origin[1] and "
                      "_point_cover_row.point[2] == origin[2] and "
                      "_point_cover_row.plane == source_frame.metadata.plane", [
                    _require("_point_cover_seen != true",
                             "Point-area Cover facts are ambiguous for this actual origin and plane."),
                    _set("_point_cover_seen", True),
                    _set("_breath_target_cover", "_point_cover_row.cover"),
                ]),
            ]),
        ],
    )


def _point_cover(tools):
    """Reuse physical Cover, excluding the caster body only when it actually contains the origin."""
    collect = deepcopy(tools["collect_srd_spell_area_cover_targets"])
    cover = deepcopy(tools["qualify_srd_spell_area_target_cover"])
    # A source ID equal to the resolved target ID otherwise becomes an entity object.
    for parameter in cover['parameters']:
        if parameter['name'] == 'source_id':
            parameter['type'] = 'object'
            parameter['from_state'] = 'state.spatial_scene'
    cover['effects'] = _replace_names(cover['effects'], {'source_id': 'source_id.id'})
    cover["parameters"].append(
        _parameter("source_frame", "object", from_state="state.spatial_scene")
    )
    target_checks = 0
    cover.setdefault("pre_conditions", []).append(
        {
            "condition": "source_frame.id == source_id.id and (source_frame.footprint.kind == 'box' or source_frame.footprint.kind == 'circle')",
            "error": "Sunburst Cover needs this action's actual caster scene frame.",
        }
    )
    # Compute once so mixed-body containment fits the expression limit at both consumers.
    inside = '_point_origin_inside_source'
    cover['effects'][:0] = [_set('_area_body', 'source_frame'), _set(inside, _contains('_area_body'))]
    category_checks = 0
    for effects in list(_lists(cover["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get('require', {}).get('error') == 'Area physical qualification requires actual target boxes.':
                effect['require'] = {
                    'condition': "target_scene.footprint.kind == 'box' or target_scene.footprint.kind == 'circle'",
                    'error': 'Point-area Cover needs an actual admitted box or circle target.'}
                target_checks += 1
            if effect.get('set', {}).get('field') == '_area_h':
                effects[index] = {
                    'condition': "target_scene.footprint.kind == 'box'",
                    'on_true': [effect],
                    'on_false': [_set('_area_h', ['target_scene.footprint.radius', 'target_scene.footprint.radius'])]}
            if effect.get('set', {}).get('field') == '_breath_fast_clear':
                effects[index] = {
                    'condition': "target_scene.footprint.kind == 'box'", 'on_true': [effect],
                    'on_false': [_set('_breath_fast_clear', False)]}
            if effect.get("require", {}).get("error") == "Admit categorical Cover from the actual area source.":
                effects.insert(index, _point_cover_facts())
                category_checks += 1
            condition = effect.get("condition", "")
            if "_breath_path_hit.id != source_id.id" in condition:
                effect["condition"] = condition.replace(
                    "_breath_path_hit.id != source_id.id",
                    f"(_breath_path_hit.id != source_id.id or not ({inside}))",
                )
            if condition == "_breath_path_hit.kind == 'geometry' and _breath_path_hit.id in geometry_blockers":
                effect.pop('set', None)
                effect['on_true'] = [
                    _call(SOLID_HIT, {
                        'geometry_id': '{_breath_path_hit.id}', 'plane': '{source_frame.metadata.plane}',
                        'start': '{origin}', 'end': '{_breath_sample.point}'}, ('_area_solid_crossed',)),
                    _when('_area_solid_crossed == true', [_set('_breath_path_obstructed', True)])
                ]
    if target_checks != 1:
        raise ValueError('Keep the single actual point-area target body guard')
    if category_checks != 1:
        raise ValueError("Keep the single actual area Cover category owner")
    # This private consumer selects the generic point algorithm; Cone's optimized path is untouched.
    collect["parameters"] = [
        p for p in collect["parameters"] if p["name"] not in {"axis", "source_frames"}
    ]
    collect = _replace_names(
        collect, {"axis": "_sunburst_cover_axis", "source_frames": "_sunburst_cover_frames"}
    )
    collect["effects"][:0] = [
        _set("_sunburst_cover_axis", []),
        _set("_sunburst_cover_frames", []),
    ]
    candidate_checks = 0
    for effects in list(_lists(collect["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get('require', {}).get('error') == 'Area physical qualification requires actual target boxes.':
                effect['require'] = {
                    'condition': "_breath_candidate_scene.footprint.kind == 'box' or _breath_candidate_scene.footprint.kind == 'circle'",
                    'error': 'Point-area Cover needs an actual admitted box or circle candidate.'}
                candidate_checks += 1
            if effect.get('set', {}).get('field') == '_area_h':
                effects[index] = {
                    'condition': "_breath_candidate_scene.footprint.kind == 'box'",
                    'on_true': [effect],
                    'on_false': [_set('_area_h', ['_breath_candidate_scene.footprint.radius', '_breath_candidate_scene.footprint.radius'])]}
            if effect.get('set', {}).get('field') == '_breath_candidate_clear':
                effects[index] = {
                    'condition': "_breath_candidate_scene.footprint.kind == 'box'", 'on_true': [effect],
                    'on_false': [_set('_breath_candidate_clear', False)]}
            if (
                effect.get("set", {}).get("field") == "_breath_source_scene_id"
                and effect["set"]["value"] == "_info_scene_entity.id"
            ):
                effects[index + 1:index + 1] = [
                    _set('_sunburst_cover_source', '_info_scene_entity'),
                    _set('_area_body', '_info_scene_entity'),
                    _set(inside, _contains('_area_body'))]
            if effect.get("call") == "qualify_srd_spell_area_target_cover":
                effect["call"] = "qualify_srd_sunburst_target_cover"
                effect['params']['source_id'] = '{_sunburst_cover_source}'
                effect["params"]["source_frame"] = "{_sunburst_cover_source}"
            condition = effect.get("condition", "")
            if condition.startswith("_breath_candidate_clear == true and "):
                effect["condition"] = (
                    "(" + condition + ") and _breath_candidate_scene.metadata.cover_from_points == null"
                )
            if "_breath_body.id != _breath_source_scene_id" in condition:
                effect["condition"] = condition.replace(
                    "_breath_body.id != _breath_source_scene_id",
                    "(_breath_body.id != _breath_source_scene_id or not ("
                    + inside
                    + "))",
                )
    if candidate_checks != 2:
        raise ValueError('Keep the generic and axis candidate body guards')
    return {
        BODY_NEAREST: _body_nearest_tool(),
        SOLID_HIT: _solid_hit(),
        "collect_srd_sunburst_cover_targets": collect,
        "qualify_srd_sunburst_target_cover": cover,
    }


def _replace_names(value, replacements):
    if isinstance(value, str):
        for old, actual in replacements.items():
            value = re.sub(r"\b" + old + r"\b", actual, value)
        return value
    if isinstance(value, list):
        return [_replace_names(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_names(item, replacements) for key, item in value.items()}
    return value


def compose_sunburst_geometry(tools, changed):
    """Route the real paid producer through point qualification before native area damage."""
    changed["collect_srd_sunburst_creature_targets"] = _sphere_tool()
    changed.update(_point_cover(tools))
    target = deepcopy(tools["resolve_spell_area_save_damage_target"])
    for effects in list(_lists(target["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            effect = effects[index]
            if effect.get("call") == "install_srd_timed_condition_source":
                effect.setdefault("export_context", {})["_sunburst_blinded_source_id"] = (
                    "_timed_condition_source_id"
                )
                if not any(
                    "_sunburst_blinded_source_id" in str(row)
                    and row.get("condition") == "spell == 'sunburst'"
                    for row in effects
                ):
                    effects.insert(
                        index + 1,
                        _when(
                            "spell == 'sunburst'",
                            [
                                _set(
                                    "state.timed_condition_sources.{_sunburst_blinded_source_id}.dispel_ongoing",
                                    False,
                                ),
                            ],
                        ),
                    )
    changed["resolve_spell_area_save_damage_target"] = target
    area = deepcopy(tools["cast_spell_instantaneous_area_save_damage"])
    body = area["effects"]
    if len(body) == 1 and body[0].get("condition") == "spell == 'prismatic_spray'":
        body = body[0]["on_false"]
    area["parameters"] = [
        p
        for p in area["parameters"]
        if p["name"]
        not in {
            "targets",
            "half_cover_targets",
            "three_quarters_cover_targets",
            "origin_distance",
        }
    ] + _intent()
    area["pre_conditions"] = [
        p
        for p in area["pre_conditions"]
        if "origin_distance" not in p["condition"] and "spell != 'sunburst'" not in p["condition"]
    ]
    area["pre_conditions"].append(
        {
            "condition": "spell == 'sunburst' and range_feet == 150 and "
            "area_shape == 'sphere' and area_size_feet == 60",
            "error": "This source owner only admits Sunburst's printed Sphere.",
        }
    )
    area["effects"] = [
        _call(
            "collect_srd_sunburst_creature_targets",
            {
                "caster_state": "{_caster_state_id}",
                "sunburst_origin": "{sunburst_origin}",
                "sunburst_plane": "{sunburst_plane}",
            },
            ("_sunburst_sphere_targets", "_sunburst_origin", "_sunburst_plane"),
        ),
        _call(
            "collect_srd_sunburst_cover_targets",
            {
                "source_state": "{_caster_state_id}",
                "targets": "{_sunburst_sphere_targets}",
                "origin": "{_sunburst_origin}",
            },
            ("_srd_area_targets",),
        ),
        _set("_sunburst_half_cover", []),
        _set("_sunburst_three_quarters", []),
        _call(
            "dispel_srd_illumination_darkness_area",
            {"origin": "{_sunburst_origin}", "plane": "{_sunburst_plane}", "radius": 60},
        ),
    ] + _replace_names(
        body,
        {
            "targets": "_srd_area_targets",
            "half_cover_targets": "_sunburst_half_cover",
            "three_quarters_cover_targets": "_sunburst_three_quarters",
        },
    )
    area = _replace_names(
        area,
        {
            "half_cover_targets": "_sunburst_half_cover",
            "three_quarters_cover_targets": "_sunburst_three_quarters",
        },
    )
    changed["cast_srd_sunburst"] = area
    allowed = {p["name"] for p in area["parameters"]}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(changed.get(name, tools[name]))
        for parameter in _intent():
            if not any(p["name"] == parameter["name"] for p in tool["parameters"]):
                tool["parameters"].append(parameter)
        if "cast_srd_sunburst" not in str(tool["effects"]):
            for effects in list(_lists(tool["effects"])):
                for index, effect in enumerate(effects):
                    if effect.get("call") == "cast_spell_instantaneous_area_save_damage":
                        call = deepcopy(effect)
                        call["call"] = "cast_srd_sunburst"
                        call["params"] = {n: v for n, v in call["params"].items() if n in allowed}
                        call["params"].update({p["name"]: "{" + p["name"] + "}" for p in _intent()})
                        effects[index] = {
                            "condition": "spell == 'sunburst'",
                            "on_true": [call],
                            "on_false": [effect],
                        }
        changed[name] = tool
    legacy = deepcopy(
        changed.get(
            "cast_spell_instantaneous_area_save_damage",
            tools["cast_spell_instantaneous_area_save_damage"],
        )
    )
    if not any(p["condition"] == "spell != 'sunburst'" for p in legacy["pre_conditions"]):
        legacy["pre_conditions"].append(
            {
                "condition": "spell != 'sunburst'",
                "error": "Use Sunburst's actual chosen-point source owner.",
            }
        )
    changed["cast_spell_instantaneous_area_save_damage"] = legacy
