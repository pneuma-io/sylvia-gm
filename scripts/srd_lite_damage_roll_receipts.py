"""Retain original spell dice when a native rule adjusts the damage total."""

ROLLS = ('_spell_damage', '_spell_area_damage',
         '_spell_component_1_damage', '_spell_component_2_damage')


def _lists(rows):
    yield rows
    for row in rows:
        for branch in ('on_true', 'on_false'):
            yield from _lists(row.get(branch, []))
        if 'for_each' in row:
            yield from _lists(row['for_each']['do'])


def saved_roll_name(roll):
    return '_visual_original' + roll


def bind_original_damage_roll_receipts(runtime):
    """Snapshot actual dice before adjustment and forward them through existing casts.

    Native dice and damage calls remain unchanged. The display can then pair the
    original selected faces with the final native modifier/total, without losing
    an adjustment or presenting adjusted damage as the sum of those faces.
    """
    tools = runtime['tools']
    providers = {}
    sites = 0
    for name, owner in tools.items():
        fields = set()
        for rows in list(_lists(owner['effects'])):
            for index in range(len(rows) - 1, -1, -1):
                effect = rows[index]
                roll = effect.get('store_as')
                if 'dice' not in effect or roll not in ROLLS:
                    continue
                saved = saved_roll_name(roll)
                rows[index + 1:index + 1] = [{'set': {'field': saved, 'value': roll}}]
                fields.add(roll)
                sites += 1
        if fields:
            owner['effects'][:0] = [{'set': {'field': saved_roll_name(roll), 'value': None}}
                                   for roll in sorted(fields)]
            providers[name] = fields
    if not all(any(roll in fields for fields in providers.values()) for roll in ROLLS):
        raise ValueError('Keep every native spell damage pool owner')

    forwarded = set()
    # Existing export maps specify which native roll belongs to the parent.
    # Follow only those same bindings, leaving unrelated raw damage owners alone.
    while True:
        changed = False
        for name, owner in tools.items():
            for rows in _lists(owner['effects']):
                for effect in rows:
                    child = effect.get('call')
                    if not isinstance(child, str) or child not in providers:
                        continue
                    exports = effect.get('export_context')
                    fields = providers[child]
                    inherited = fields if exports == '*' else {
                        roll for roll in fields if isinstance(exports, dict) and exports.get(roll) == roll}
                    for roll in inherited:
                        saved = saved_roll_name(roll)
                        if isinstance(exports, dict):
                            exports[saved] = saved
                        forwarded.add((name, child, roll))
                    new = inherited - providers.get(name, set())
                    if new:
                        providers.setdefault(name, set()).update(new)
                        changed = True
        if not changed:
            break

    captures = 0
    for rows in _lists(tools['cast_spell']['effects']):
        for effect in rows:
            assignment = effect.get('set', {})
            if assignment.get('field') != '_retained_cast_tail_context':
                continue
            value = assignment['value']
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError('Keep the native retained cast context transport')
            for roll in ROLLS:
                saved = saved_roll_name(roll)
                value.append({'key': "'" + saved + "'", 'missing': saved + ' == null', 'value': saved})
            captures += 1
    if captures != 1:
        raise ValueError('Keep the sole native final cast context capture')
    return {'damage_roll_capture_sites': sites, 'damage_roll_export_bindings': len(forwarded),
            'damage_roll_cast_context_fields': len(ROLLS)}
