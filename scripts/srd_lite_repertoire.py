"""Explain selected repertoire capacity; canonical validation remains authoritative."""


def repertoire_choices(runtime, source, level):
    """Return policy-eligible identities and capacity, never an accepted character."""
    policies = runtime['definitions']['spell_grants']
    policy = policies[source]
    if not 1 <= level <= len(policy['max_level']):
        raise ValueError('Class or lineage level is outside the retained policy')
    lists = policy['lists']
    if source == 'bard' and level >= 10:
        lists = ['bard', 'cleric', 'druid', 'wizard']
    cantrips, spells = [], []
    for identity, header in sorted(runtime['definitions']['spell_casting'].items()):
        fixed = identity in policy['fixed']
        legal = (level >= policy['fixed'][identity] if fixed else
                 any(name in header['classes'] for name in lists)
                 and policy['min_spell_level'] <= header['level'] <= policy['max_level'][level - 1])
        if legal:
            (cantrips if header['level'] == 0 else spells).append(identity)
    missing_fixed = sorted(name for name, earned in policy['fixed'].items()
                           if earned <= level and name not in runtime['definitions']['spell_casting'])
    required_cantrips = policy['complete_cantrips'][level - 1]
    required_spells = policy['complete_spells'][level - 1]
    return dict(source=source, level=level, cantrips=cantrips, spells=spells,
                required_cantrips=required_cantrips, required_spells=required_spells,
                missing_cantrip_capacity=(max(0, required_cantrips - len(cantrips))
                                         if isinstance(required_cantrips, int) and required_cantrips >= 0 else None),
                missing_spell_capacity=(max(0, required_spells - len(spells))
                                       if isinstance(required_spells, int) and required_spells >= 0 else None),
                missing_fixed=missing_fixed)


def repertoire_guidance(runtime):
    """Describe creation and its first earned step from the actual restricted headers."""
    policies = runtime['definitions']['spell_grants']
    lines = ['Selected repertoire only; these are capacity checks, not character acceptance. '
             'Keep complete distinct legal grants, subclasses and earned progression. '
             'A shortfall requires additional implemented profiles; never duplicate choices '
             'or substitute pending spells. Selected gameplay verification remains pending.']
    for source, policy in sorted(policies.items()):
        if policy['class_id'] != source:
            continue
        for level in (1, 2):
            if level < policy['minimum']:
                continue
            row = repertoire_choices(runtime, source, level)
            def count_text(count):
                return str(count) if isinstance(count, int) and count >= 0 else 'a variable number of'
            lines.append(f"{source} level {level}: requires {count_text(row['required_cantrips'])} cantrips "
                         f"and {count_text(row['required_spells'])} leveled spells; eligible cantrips "
                         f"{', '.join(row['cantrips']) or 'none'}; eligible spells "
                         f"{', '.join(row['spells']) or 'none'}; capacity shortfall "
                         f"{row['missing_cantrip_capacity'] if row['missing_cantrip_capacity'] is not None else 'rule-dependent'} cantrips, "
                         f"{row['missing_spell_capacity'] if row['missing_spell_capacity'] is not None else 'rule-dependent'} spells.")
            for automatic in policy['automatic_sources']:
                earned = policies[automatic]
                if automatic != source and level >= earned['minimum'] and not earned['subclass']:
                    missing = repertoire_choices(runtime, automatic, level)['missing_fixed']
                    if missing:
                        lines.append(f"{source} level {level}: automatic {automatic} requires "
                                     f"unavailable {', '.join(missing)}.")
            if source == 'wizard':
                required_book = 6 + 2 * (level - 1)
                lines.append(f"wizard level {level}: requires at least {required_book} distinct "
                             f"eligible spellbook entries; available {len(row['spells'])}; "
                             f"book capacity shortfall {max(0, required_book - len(row['spells']))}. "
                             'Evoker gains two additional required book choices from level 3; '
                             'check the retained rules for every later earned level.')
    return '\n'.join(lines)


def repertoire_summary(runtime):
    """Keep initial-validation guidance within the portable description field."""
    policies = runtime['definitions']['spell_grants']
    lines = ["Selected repertoire capacity only, not character acceptance. Counts are "
             "available/required; policy means a rule-dependent requirement. Keep complete "
             "distinct legal grants, subclasses and earned progression. Add implemented "
             "profiles for shortfalls; never duplicate choices or use pending spells. "
             "Gameplay verification remains pending."]
    for source, policy in sorted(policies.items()):
        if policy['class_id'] != source:
            continue
        for level in (1, 2):
            if level < policy['minimum']:
                continue
            row = repertoire_choices(runtime, source, level)
            def required(count):
                return str(count) if isinstance(count, int) and count >= 0 else 'policy'
            lines.append(f"{source} level {level}: cantrips {len(row['cantrips'])}/"
                         f"{required(row['required_cantrips'])}; spells {len(row['spells'])}/"
                         f"{required(row['required_spells'])}.")
            for automatic in policy['automatic_sources']:
                earned = policies[automatic]
                if automatic != source and level >= earned['minimum'] and not earned['subclass']:
                    missing = repertoire_choices(runtime, automatic, level)['missing_fixed']
                    if missing:
                        lines.append(f"{source} level {level}: mandatory {', '.join(missing)} unavailable.")
            if source == 'wizard':
                lines.append(f"wizard level {level}: spellbook {len(row['spells'])}/"
                             f"{6 + 2 * (level - 1)} distinct eligible entries.")
    lines.append('Evoker requires two additional book choices from level 3; retained rules '
                 'govern every later earned level. Detailed eligible choices remain in spell headers.')
    return '\n'.join(lines)


def explain_repertoire_rejections(program, runtime):
    """Add current capacity facts to completeness errors without changing conditions."""
    policies = runtime['definitions']['spell_grants']
    counts = []
    fixed = []
    for source, policy in sorted(policies.items()):
        if policy['class_id'] == source:
            row = repertoire_choices(runtime, source, 1)
            if row['missing_cantrip_capacity']:
                counts.append(f"{source} requires {row['required_cantrips']} distinct cantrips, "
                              f"available {len(row['cantrips'])} ({', '.join(row['cantrips']) or 'none'})")
        if source in ('favored_enemy', 'druidic', 'paladins_smite'):
            row = repertoire_choices(runtime, source, policy['minimum'])
            if row['missing_fixed']:
                fixed.append(f"{policy['class_id']} level {policy['minimum']}: {', '.join(row['missing_fixed'])}")
    wizard = repertoire_choices(runtime, 'wizard', 2)
    targets = {
        "Complete this source's required cantrip and leveled-spell choices before confirming the character.":
            ' Current level-1 cantrip capacity: ' + '; '.join(counts) + '. Full retained rules govern later levels.',
        'Complete the Wizard spellbook entries earned at each class level.':
            f" Current Wizard level-2 repertoire has {len(wizard['spells'])} eligible entries; requires 8: "
            + ', '.join(wizard['spells']) + '. Later earned-level requirements remain unchanged.',
        "Record every spell source automatically granted by the character's class and selected subclass.":
            ' Current mandatory-profile shortfalls: ' + '; '.join(fixed) + '.',
    }

    def visit(value):
        if isinstance(value, dict):
            require = value.get('require')
            if isinstance(require, dict) and require.get('error') in targets:
                require['error'] += targets[require['error']]
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(program['effects'])
