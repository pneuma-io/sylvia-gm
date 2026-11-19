"""Static architecture guards: domain dependency direction and the type-check exception ratchet.

Architecture contract section 4: dependencies point inward, and the domain layer has no SQLite,
HTTP, desktop, provider or filesystem dependencies and imports no infrastructure or interfaces.
The legacy type-check exceptions in pyproject.toml are measured debt that may only shrink.

Both checks read source text only. The import check follows static imports, including relative
imports, aliases, imports under TYPE_CHECKING and package re-exports, through the explicitly
allowed packages; it rejects dynamic imports outright rather than trying to follow them. It does
not see code reached through objects passed in at run time, and it does not police third-party
imports made by the allowed packages themselves (runtime/ reads package files, for example).
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Packages domain code may import directly. Extending this is an architecture decision.
DOMAIN_ALLOWED_PACKAGES = ("sylvia_gm.domain", "sylvia_gm.runtime", "sylvia_gm.ruleset")
# Packages those may reach in turn. Anything else reached from the domain is an outer layer.
DOMAIN_REACHABLE_PACKAGES = (*DOMAIN_ALLOWED_PACKAGES, "sylvia_gm.dice", "sylvia_gm.core")
# Libraries that would give the domain SQLite, network, process, file or provider access.
DOMAIN_FORBIDDEN_LIBRARIES = frozenset({
    "aiohttp", "anthropic", "asyncio", "ftplib", "glob", "google", "http", "httpx",
    "importlib", "io", "mcp", "openai", "os", "requests", "shutil", "socket", "sqlite3",
    "ssl", "subprocess", "sys", "tempfile", "urllib", "websockets",
})
PURE_PATH_NAMES = frozenset({"PurePath", "PurePosixPath", "PureWindowsPath"})
DOMAIN_GUIDANCE = (
    "The domain layer stays free of outer layers and I/O (architecture contract section 4). "
    "Move this dependency into application/ or infrastructure/ and pass plain values in. Only "
    "if the imported code is itself pure and ruleset-neutral, add its package to "
    "DOMAIN_ALLOWED_PACKAGES in tests/test_architecture_guards.py in a reviewed change."
)

# Modules exempt from strict type checking on main, 19 November 2026. This set may only shrink.
LEGACY_TYPE_CHECK_EXCEPTIONS = frozenset({
    "sylvia_gm.agent._builder_question_answers",
    "sylvia_gm.agent._builder_session",
    "sylvia_gm.agent._builder_source_tool",
    "sylvia_gm.agent._character_profile_tools",
    "sylvia_gm.agent._content_definition_tool",
    "sylvia_gm.agent._library_source_tool",
    "sylvia_gm.agent._provider_session",
    "sylvia_gm.agent._ruleset_test_coverage",
    "sylvia_gm.agent._ruleset_test_provider",
    "sylvia_gm.agent._ruleset_test_runner",
    "sylvia_gm.agent._runner",
    "sylvia_gm.agent._session_loop",
    "sylvia_gm.agent._stateless_mcp",
    "sylvia_gm.agent._working_context",
    "sylvia_gm.application.action_sequences",
    "sylvia_gm.application.authority",
    "sylvia_gm.application.builder_references",
    "sylvia_gm.application.builder_session_migration",
    "sylvia_gm.application.builder_source_selection",
    "sylvia_gm.application.bundled_campaign_plans",
    "sylvia_gm.application.bundled_packages",
    "sylvia_gm.application.campaign_library_selection",
    "sylvia_gm.application.campaign_plan_sources",
    "sylvia_gm.application.campaign_starting_world",
    "sylvia_gm.application.character_card_import",
    "sylvia_gm.application.character_profiles",
    "sylvia_gm.application.character_rules",
    "sylvia_gm.application.initial_character_state",
    "sylvia_gm.application.interview_content",
    "sylvia_gm.application.launch_compatibility",
    "sylvia_gm.application.library_sources",
    "sylvia_gm.application.map_segment_preview",
    "sylvia_gm.application.package_library",
    "sylvia_gm.application.package_materialization",
    "sylvia_gm.application.package_runtime",
    "sylvia_gm.application.plan_bundle",
    "sylvia_gm.application.plan_snapshot",
    "sylvia_gm.application.player_actions",
    "sylvia_gm.application.reusable_participants",
    "sylvia_gm.application.reusable_plans",
    "sylvia_gm.application.ruleset_testing",
    "sylvia_gm.application.setting_character_requirements",
    "sylvia_gm.application.spatial_preview",
    "sylvia_gm.application.srd_lite_weapon_set_migration",
    "sylvia_gm.application.tactical_grid",
    "sylvia_gm.builder",
    "sylvia_gm.builder._interview_manifest",
    "sylvia_gm.builder._package_snapshots",
    "sylvia_gm.builder._plan_draft_check",
    "sylvia_gm.builder._plan_snapshot_materializer",
    "sylvia_gm.builder._prompts",
    "sylvia_gm.builder._ruleset_source",
    "sylvia_gm.campaign.rift",
    "sylvia_gm.config.chatgpt_settings",
    "sylvia_gm.config.provider_settings",
    "sylvia_gm.dice",
    "sylvia_gm.domain._content_schema_campaign_export",
    "sylvia_gm.domain._content_schema_character_profile_v2",
    "sylvia_gm.domain._content_schema_ruleset",
    "sylvia_gm.domain.authority",
    "sylvia_gm.domain.campaign_plan",
    "sylvia_gm.domain.package_library",
    "sylvia_gm.domain.panel_layout",
    "sylvia_gm.infrastructure.package_archive",
    "sylvia_gm.infrastructure.participant_history",
    "sylvia_gm.infrastructure.sqlite_authority",
    "sylvia_gm.infrastructure.sqlite_builder_launch",
    "sylvia_gm.infrastructure.sqlite_interview_navigation",
    "sylvia_gm.infrastructure.sqlite_packages",
    "sylvia_gm.interfaces.owner_process",
    "sylvia_gm.interfaces.ruleset_test_worker",
    "sylvia_gm.interfaces.webui",
    "sylvia_gm.portable_artifact",
    "sylvia_gm.provider",
    "sylvia_gm.provider._base",
    "sylvia_gm.provider._chat_metadata",
    "sylvia_gm.provider._context_fit",
    "sylvia_gm.provider._gemini",
    "sylvia_gm.provider._native_output",
    "sylvia_gm.provider._openai_compat",
    "sylvia_gm.provider._protocol_routes",
    "sylvia_gm.provider._reasoning",
    "sylvia_gm.runtime._aoe_resolver",
    "sylvia_gm.runtime._companion_registry",
    "sylvia_gm.runtime._content_registry",
    "sylvia_gm.runtime._distance_tracker",
    "sylvia_gm.runtime._expression_parser",
    "sylvia_gm.runtime._initiative",
    "sylvia_gm.runtime._parameter_declarations",
    "sylvia_gm.runtime._scene_awareness",
    "sylvia_gm.runtime._state_bridge",
    "sylvia_gm.runtime._state_memo",
    "sylvia_gm.runtime._turn_system",
    "sylvia_gm.session",
    "sylvia_gm.session._divergence",
    "sylvia_gm.session._narrative_scanner",
    "sylvia_gm.session._prompt_assembler",
    "sylvia_gm.spatial._engine",
    "sylvia_gm.spatial._interface",
    "sylvia_gm.state._changelog",
    "sylvia_gm.state._schema",
    "sylvia_gm.state._snapshot",
    "sylvia_gm.state._store",
    "sylvia_gm.state.state_api",
    "sylvia_gm.tools._dm_campaign_handlers",
    "sylvia_gm.tools._registry",
    "sylvia_gm.tools._toolsets",
})
# Inline "type: ignore" comments per source file on the same date. These may only shrink too.
INLINE_TYPE_IGNORES = {
    "src/sylvia_gm/agent/_context_ledger.py": 2,
    "src/sylvia_gm/application/package_runtime.py": 1,
    "src/sylvia_gm/infrastructure/campaign_archive.py": 1,
    "src/sylvia_gm/infrastructure/sqlite_authority.py": 1,
    "src/sylvia_gm/interfaces/local_owner.py": 10,
    "src/sylvia_gm/provider/__init__.py": 2,
    "src/sylvia_gm/provider/_context_capacity.py": 1,
    "src/sylvia_gm/provider/_factory.py": 1,
    "src/sylvia_gm/ruleset/schema.py": 1,
    "src/sylvia_gm/session/_prompt_assembler.py": 1,
    "src/sylvia_gm/state/_store.py": 1,
    "src/sylvia_gm/state/state_api.py": 1,
    "src/sylvia_gm/tools/_toolsets.py": 1,
}
TYPE_GUIDANCE = (
    "New code stays strictly typed. Fix the types; a new exception needs a scoped, reviewed "
    "change to tests/test_architecture_guards.py that says why it is unavoidable."
)
WEAKENING_MYPY_KEYS = frozenset({
    "allow_incomplete_defs", "allow_untyped_calls", "allow_untyped_defs", "check_untyped_defs",
    "disable_error_code", "disallow_untyped_defs", "follow_imports", "ignore_errors", "strict",
    "warn_return_any",
})


def _module_name(src: Path, path: Path) -> str:
    parts = path.relative_to(src).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _module_path(src: Path, module: str) -> Path | None:
    base = src.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _within(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == package or module.startswith(package + ".") for package in packages)


def _imports(src: Path, path: Path) -> list[tuple[int, str, tuple[str, ...]]]:
    """Every statically imported module as (line, absolute name, imported names)."""

    module = _module_name(src, path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: list[tuple[int, str, tuple[str, ...]]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            names = tuple(alias.name for alias in node.names)
            found.append((node.lineno, base, names))
            found.extend(
                (node.lineno, f"{base}.{name}", ())
                for name in names if _module_path(src, f"{base}.{name}") is not None
            )
        elif isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "__import__")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
        ):
            found.append((node.lineno, "<dynamic import>", ()))
    return found


def domain_import_violations(src: Path) -> list[str]:
    """Explain every way the domain package reaches an outer layer, I/O or a dynamic import."""

    domain_files = sorted((src / "sylvia_gm" / "domain").rglob("*.py"))
    violations = []
    for path in domain_files:
        where = path.relative_to(src.parent).as_posix()
        for line, module, names in _imports(src, path):
            top = module.split(".")[0]
            if module == "<dynamic import>":
                problem = "uses a dynamic import, which this check cannot follow"
            elif top == "sylvia_gm" and module != "sylvia_gm":
                if _within(module, DOMAIN_ALLOWED_PACKAGES):
                    continue
                problem = f"imports {module}"
            elif top in DOMAIN_FORBIDDEN_LIBRARIES:
                problem = f"imports {module}"
            elif top == "pathlib" and not (names and set(names) <= PURE_PATH_NAMES):
                problem = f"imports {module} {', '.join(names) or ''}".rstrip() + " (file access)"
            else:
                continue
            violations.append(f"{where}:{line}: {problem}. {DOMAIN_GUIDANCE}")
    # Follow internal imports, including the packages Python initialises on the way.
    domain_modules = {_module_name(src, path) for path in domain_files}
    parents: dict[str, str | None] = dict.fromkeys(domain_modules)
    queue = deque(parents)
    while queue:
        current = queue.popleft()
        path = _module_path(src, current)
        if path is None or not _within(current, DOMAIN_REACHABLE_PACKAGES):
            continue
        for _line, module, _names in _imports(src, path):
            parts = module.split(".")
            for size in range(2, len(parts) + 1):
                target = ".".join(parts[:size])
                if parts[0] != "sylvia_gm" or target in parents:
                    continue
                if _module_path(src, target) is None:
                    break
                parents[target] = current
                if _within(target, DOMAIN_REACHABLE_PACKAGES):
                    queue.append(target)
                    continue
                if current not in domain_modules:  # direct imports are reported above
                    chain, step = [target], parents[target]
                    while step is not None:
                        chain.append(step)
                        step = parents[step]
                    violations.append(
                        f"{' <- '.join(chain)}: the domain reaches {target} through re-exports "
                        f"or imports of allowed packages. {DOMAIN_GUIDANCE}"
                    )
                break
    return violations


def type_check_exception_problems(
    pyproject: str,
    src: Path,
    legacy: frozenset[str] = LEGACY_TYPE_CHECK_EXCEPTIONS,
    inline: dict[str, int] = INLINE_TYPE_IGNORES,
) -> list[str]:
    """Explain each way the strict type-check exceptions grew, widened or went stale."""

    mypy = tomllib.loads(pyproject)["tool"]["mypy"]
    problems = []
    if mypy.get("strict") is not True:
        problems.append(f"[tool.mypy] must keep strict = true. {TYPE_GUIDANCE}")
    for key in sorted(WEAKENING_MYPY_KEYS & set(mypy) - {"strict"}):
        problems.append(f"[tool.mypy] sets {key} for every module. {TYPE_GUIDANCE}")
    exempt: set[str] = set()
    for override in mypy.get("overrides", []):
        modules = override.get("module", [])
        modules = [modules] if isinstance(modules, str) else list(modules)
        own = [module for module in modules if module.split(".")[0] == "sylvia_gm"]
        weakening = sorted(WEAKENING_MYPY_KEYS & set(override))
        if not own or not weakening:
            continue
        if any("*" in module for module in own):
            problems.append(f"A wildcard type-check exception covers {own}. {TYPE_GUIDANCE}")
        if set(override) != {"module", "ignore_errors"} or override["ignore_errors"] is not True:
            problems.append(
                f"Project modules {own} get a new kind of exception ({', '.join(weakening)}). "
                f"{TYPE_GUIDANCE}"
            )
        exempt.update(own)
    for module in sorted(exempt - legacy):
        problems.append(f"{module} is newly exempt from strict type checks. {TYPE_GUIDANCE}")
    for module in sorted(legacy - exempt):
        problems.append(
            f"{module} is now strictly typed. Remove it from LEGACY_TYPE_CHECK_EXCEPTIONS in "
            "tests/test_architecture_guards.py so the exception cannot come back."
        )
    for module in sorted(exempt):
        if "*" not in module and _module_path(src, module) is None:
            problems.append(f"{module} no longer exists; remove its type-check exception.")
    counts: dict[str, int] = {}
    for path in sorted((src / "sylvia_gm").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        where = path.relative_to(src.parent).as_posix()
        if re.search(r"#\s*mypy:", text):
            problems.append(f"{where} changes mypy settings inline. {TYPE_GUIDANCE}")
        if re.search(r"#\s*type:\s*ignore(?!\[)", text):
            problems.append(f"{where} has a type: ignore without an error code. {TYPE_GUIDANCE}")
        if count := len(re.findall(r"#\s*type:\s*ignore", text)):
            counts[where] = count
    for where in sorted(set(counts) | set(inline)):
        now, before = counts.get(where, 0), inline.get(where, 0)
        if now > before:
            problems.append(f"{where} has {now} type: ignore comments, up from {before}. "
                            f"{TYPE_GUIDANCE}")
        elif now < before:
            problems.append(f"{where} now has {now} type: ignore comments. Lower its entry in "
                            "INLINE_TYPE_IGNORES so they cannot come back.")
    return problems


def test_domain_imports_point_inward() -> None:
    violations = domain_import_violations(ROOT / "src")
    assert not violations, "\n".join(violations)


def test_type_check_exceptions_cannot_grow_silently() -> None:
    problems = type_check_exception_problems(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), ROOT / "src",
    )
    assert not problems, "\n".join(problems)


def _tree(root: Path, files: dict[str, str]) -> Path:
    """Write a tiny source tree of package modules for the checker fixtures."""

    src = root / "src"
    for name, text in {
        "sylvia_gm/__init__.py": "", "sylvia_gm/domain/__init__.py": "",
        "sylvia_gm/runtime/__init__.py": "", "sylvia_gm/application/__init__.py": "",
        "sylvia_gm/application/owner.py": "", "sylvia_gm/infrastructure/__init__.py": "",
        "sylvia_gm/interfaces/__init__.py": "", "sylvia_gm/provider/__init__.py": "",
        "sylvia_gm/agent/__init__.py": "", "sylvia_gm/agent/loop.py": "", **files,
    }.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text, encoding="utf-8")
    return src


def test_domain_import_check_accepts_the_allowed_forms(tmp_path: Path) -> None:
    src = _tree(tmp_path, {
        "sylvia_gm/domain/model.py": (
            "from __future__ import annotations\n"
            "import json\n"
            "from pathlib import PureWindowsPath\n"
            "from typing import TYPE_CHECKING\n"
            "from . import other\n"
            "from .other import VALUE as value\n"
            "import sylvia_gm.runtime.pure as pure\n"
            "if TYPE_CHECKING:\n"
            "    from sylvia_gm.domain.other import VALUE\n"
        ),
        "sylvia_gm/domain/other.py": "VALUE = 1\n",
        "sylvia_gm/runtime/pure.py": "from pathlib import Path\nimport sylvia_gm.dice\n",
        "sylvia_gm/dice.py": "import os\n",
    })
    assert domain_import_violations(src) == []


@pytest.mark.parametrize(
    "source,expected",
    [
        ("from sylvia_gm.infrastructure import store\n", "imports sylvia_gm.infrastructure"),
        ("import sylvia_gm.application.owner as owner\n", "imports sylvia_gm.application.owner"),
        ("from ..interfaces import api\n", "imports sylvia_gm.interfaces"),
        ("from sylvia_gm import provider\n", "imports sylvia_gm.provider"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from sylvia_gm.agent.loop import Loop\n",
            "imports sylvia_gm.agent.loop",
        ),
        ("import sqlite3\n", "imports sqlite3"),
        ("import httpx as client\n", "imports httpx"),
        ("from os import path\n", "imports os"),
        ("from pathlib import Path\n", "imports pathlib Path (file access)"),
        ("import importlib\nimportlib.import_module('x')\n", "imports importlib"),
        ("module = __import__('sylvia_gm.application')\n", "uses a dynamic import"),
    ],
    ids=["absolute", "alias", "relative", "package-member", "type-checking", "sqlite",
         "http-alias", "os", "filesystem", "importlib", "dunder-import"],
)
def test_domain_import_check_rejects_each_forbidden_form(
    tmp_path: Path, source: str, expected: str,
) -> None:
    src = _tree(tmp_path, {"sylvia_gm/domain/model.py": source})
    violations = domain_import_violations(src)
    assert violations, "the forbidden import was not detected"
    assert violations[0].startswith("src/sylvia_gm/domain/model.py:")
    assert expected in violations[0]
    assert "architecture contract section 4" in violations[0]


def test_domain_import_check_follows_re_exports_out_of_allowed_packages(tmp_path: Path) -> None:
    src = _tree(tmp_path, {
        "sylvia_gm/domain/model.py": "from sylvia_gm.runtime import helper\n",
        "sylvia_gm/runtime/__init__.py": "from sylvia_gm.application.owner import helper\n",
    })
    assert domain_import_violations(src) == [
        "sylvia_gm.application <- sylvia_gm.runtime <- sylvia_gm.domain.model: the domain "
        "reaches sylvia_gm.application through re-exports or imports of allowed packages. "
        f"{DOMAIN_GUIDANCE}"
    ]


_PYPROJECT = """
[tool.mypy]
strict = true

[[tool.mypy.overrides]]
module = ["numpy", "numpy.*"]
follow_imports = "skip"

[[tool.mypy.overrides]]
module = [{modules}]
ignore_errors = true
"""


def _pyproject(modules: list[str]) -> str:
    return _PYPROJECT.format(modules=", ".join(f'"{module}"' for module in modules))


LEGACY = frozenset({"sylvia_gm.dice"})


def test_type_check_ratchet_accepts_the_recorded_exceptions(tmp_path: Path) -> None:
    src = _tree(tmp_path, {"sylvia_gm/dice.py": "x = 1  # type: ignore[assignment]\n"})
    problems = type_check_exception_problems(
        _pyproject(["sylvia_gm.dice"]), src, LEGACY, {"src/sylvia_gm/dice.py": 1},
    )
    assert problems == []


def test_type_check_ratchet_lowers_the_baseline_when_debt_is_paid(tmp_path: Path) -> None:
    src = _tree(tmp_path, {"sylvia_gm/dice.py": ""})
    problems = type_check_exception_problems(
        _pyproject(["sylvia_gm.agent.loop"]), src,
        frozenset({"sylvia_gm.dice", "sylvia_gm.agent.loop"}), {"src/sylvia_gm/dice.py": 1},
    )
    assert any("sylvia_gm.dice is now strictly typed" in line for line in problems)
    assert any("dice.py now has 0 type: ignore comments" in line for line in problems)


@pytest.mark.parametrize(
    "pyproject,expected",
    [
        (_pyproject(["sylvia_gm.dice", "sylvia_gm.agent.loop"]),
         "sylvia_gm.agent.loop is newly exempt"),
        (_pyproject(["sylvia_gm.dice", "sylvia_gm.agent.*"]), "A wildcard type-check exception"),
        (_PYPROJECT.format(modules='"sylvia_gm.dice"').replace(
            "ignore_errors = true", 'disable_error_code = ["arg-type"]'),
         "new kind of exception (disable_error_code)"),
        (_pyproject(["sylvia_gm.dice"]).replace("strict = true", "strict = false"),
         "must keep strict = true"),
        (_pyproject(["sylvia_gm.dice", "sylvia_gm.removed"]), "sylvia_gm.removed no longer exists"),
    ],
    ids=["new-module", "wildcard", "new-kind", "strict-off", "stale"],
)
def test_type_check_ratchet_rejects_growth(tmp_path: Path, pyproject: str, expected: str) -> None:
    src = _tree(tmp_path, {"sylvia_gm/dice.py": ""})
    problems = type_check_exception_problems(pyproject, src, LEGACY, {})
    assert any(expected in line for line in problems), problems


def test_type_check_ratchet_counts_inline_ignores(tmp_path: Path) -> None:
    src = _tree(tmp_path, {
        "sylvia_gm/dice.py": "x = 1  # type: ignore\n",
        "sylvia_gm/agent/loop.py": "# mypy: ignore-errors\n",
    })
    problems = type_check_exception_problems(_pyproject(["sylvia_gm.dice"]), src, LEGACY, {})
    assert any("dice.py has a type: ignore without an error code" in line for line in problems)
    assert any("dice.py has 1 type: ignore comments, up from 0" in line for line in problems)
    assert any("loop.py changes mypy settings inline" in line for line in problems)
