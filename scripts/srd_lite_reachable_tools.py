"""Omit private owners made unreachable by Lite's unconditional pending guards."""


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _calls(value):
    if isinstance(value, dict):
        if 'call' in value:
            yield value['call']
        for item in value.values():
            yield from _calls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _calls(item)


def omit_unreachable_private_tools(runtime, record):
    """Retain every public contract and transitively referenced private owner.

    Runtime and manifest data can also name owners, so they join the roots.
    Dynamic calls are refused rather than guessed. Pending public tools retain
    their original contracts and the exact unconditional rejection effect.
    """
    tools = runtime['tools']
    roots = {row['name'] for row in record['tools']}
    roots.update(value for value in _strings(record) if value in tools)
    roots.update(value for value in _strings({
        key: value for key, value in runtime.items() if key != 'tools'
    }) if value in tools)
    roots.update(name for name, owner in tools.items()
                 if owner.get('metadata', {}).get('category') != 'internal')
    seen = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen or name not in tools:
            continue
        seen.add(name)
        for target in _calls(tools[name]):
            if not isinstance(target, str) or '{' in target:
                raise ValueError('Cannot prune a dynamic private owner: ' + name)
        pending.extend(value for value in _strings(tools[name]) if value in tools)
    omitted = sorted(set(tools) - seen)
    for name in omitted:
        if tools[name].get('metadata', {}).get('category') != 'internal':
            raise ValueError('Only private unreachable tools may be omitted')
    runtime['tools'] = {name: owner for name, owner in tools.items() if name in seen}
    return omitted
