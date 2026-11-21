# YAML loads and dumps

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 20 sites. Every PyYAML call. PyYAML (YAML 1.1) reads `yes`, `no`, `on`, `off`, `~`, octal and sexagesimal numbers and dates as non-strings; most Rust YAML crates follow YAML 1.2 and do not. Dumps must reproduce PyYAML's quoting and key order.

Written by `docs/rust-port/tools/site_lists.py`. A worker ticks the Done column when the Rust side reproduces the site and a vector or comparison session proves it.

| Lane | Sites |
| --- | ---: |
| 1. Formats and content | 4 |
| 2. Game engine | 6 |
| 3. Storage and authority | 4 |
| 4. Providers and agents | 6 |
| 5. Owner, clients and release | 0 |

## Lane 1: Formats and content

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `builder/_ruleset_source.py:88` | `_read_source` | yaml.load | `loaded = yaml.load(source_bytes.decode("utf-8"), Loader=_SourceLoader)` | |
| `builder/_ruleset_source.py:590` | `compile_ruleset_source` | yaml.safe_dump | `"runtime/ruleset.yaml": yaml.safe_dump(runtime, sort_keys=False, allow_unicode=True).encode(` | |

## Lane 2: Game engine

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/package_runtime.py:929` | `_load_runtime` | yaml.load | `raw = yaml.load(text, Loader=_UniqueRuntimeLoader if authoring else _BoundedRuntimeLoader)` | |
| `runtime/_frontmatter.py:43` | `parse_frontmatter` | yaml.safe_load | `fm = yaml.safe_load(yaml_text) or {}` | |
| `runtime/_schema_validator.py:172` | `validate_ruleset` | yaml.safe_load | `data = yaml.safe_load(yaml_content)` | |

## Lane 3: Storage and authority

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `application/authority.py:5295` | `AuthorityOwner.inspect_package_candidate` | yaml.load | `runtime = yaml.load( runtime_bytes.decode("utf-8"), Loader=_BoundedRuntimeLoader )` | |
| `application/campaign_rules_update.py:652` | `_preview.runtime` | yaml.load | `return yaml.load(raw, Loader=_BoundedRuntimeLoader) if raw else {}` | |

## Lane 4: Providers and agents

| Where | Function | Kind | Code | Done |
| --- | --- | --- | --- | --- |
| `config/_user_settings.py:55` | `_replace` | yaml.safe_dump | `payload = yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True)` | |
| `config/_user_settings.py:87` | `read_user_settings` | yaml.safe_load | `document = yaml.safe_load(path.read_text(encoding="utf-8"))` | |
| `config/loader.py:264` | `_load_yaml` | yaml.safe_load | `data = yaml.safe_load(fh)` | |
