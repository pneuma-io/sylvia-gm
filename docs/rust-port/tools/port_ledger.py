"""Build the Rust port's ledgers from the frozen Python snapshot.

Reference tooling for the port: it reads the Python source it is retiring, and is deleted with
it. Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe docs/rust-port/tools/port_ledger.py --init

`--init` writes the per-lane file ledgers, the command burn-down, the Rust command table and
the handshake vectors. It refuses to overwrite a ledger that already exists, because workers
edit those by hand afterwards. `--inventory PATH` writes the file-by-file Python inventory.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = "src/sylvia_gm/"
PORT = ROOT / "docs" / "rust-port"

LANES = {
    1: ("Formats and content", "sylvia-domain"),
    2: ("Game engine", "sylvia-engine"),
    3: ("Storage and authority", "sylvia-store"),
    4: ("Providers and agents", "sylvia-provider, sylvia-agent"),
    5: ("Owner, clients and release", "sylvia-owner"),
}

# Directory -> (lane, Rust crate). Application services are assigned per file below.
DIRECTORY_LANES = {
    "domain": (1, "sylvia-domain"), "state": (1, "sylvia-domain"),
    "campaign": (1, "sylvia-domain"), "planning": (1, "sylvia-domain"),
    "builder": (1, "sylvia-app::builder"),
    "runtime": (2, "sylvia-engine"), "ruleset": (2, "sylvia-engine"),
    "spatial": (2, "sylvia-engine::spatial"),
    "infrastructure": (3, "sylvia-store"),
    "provider": (4, "sylvia-provider"), "config": (4, "sylvia-provider::config"),
    "session": (4, "sylvia-agent"), "tools": (4, "sylvia-agent"), "agent": (4, "sylvia-agent"),
    "interfaces": (5, "sylvia-owner"), "core": (5, "sylvia-owner"),
}
ROOT_FILES = {"dice.py": (2, "sylvia-engine::dice")}
APPLICATION_LANES = {
    2: ("package_runtime", "player_actions", "action_sequences", "tactical_grid", "shot_effects",
        "piece_table", "result_cards", "result_card_admission", "spatial_preview",
        "map_segment_preview", "actor_decisions", "decision_scope", "_runtime_cache"),
    3: ("authority", "campaign_rules_update", "campaign_sequels", "campaign_inventory",
        "ruleless_play", "knowledge_search", "session"),
    4: ("character_chat",),
    5: ("launch_compatibility",),
}


def git_files(pattern: str) -> list[str]:
    result = subprocess.run(["git", "ls-files", pattern], cwd=ROOT, check=True,
                            capture_output=True, text=True)
    return sorted(set(result.stdout.splitlines()))


def line_count(path: str) -> int:
    return (ROOT / path).read_bytes().count(b"\n")


def lane_of(path: str) -> tuple[int, str]:
    relative = path[len(APP):]
    parts = relative.split("/")
    if len(parts) == 1:
        return ROOT_FILES.get(parts[0], (5, "sylvia-owner"))
    if parts[0] == "application":
        if parts[1] == "desktop_update":
            return 5, "sylvia-owner::desktop_update"
        stem = Path(parts[1]).stem
        for lane, stems in APPLICATION_LANES.items():
            if stem in stems:
                crate = "sylvia-store::authority" if stem == "authority" else f"sylvia-app::{stem}"
                return lane, crate
        return 1, f"sylvia-app::{stem}"
    return DIRECTORY_LANES[parts[0]]


def write(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        print(f"kept (already exists): {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote: {path.relative_to(ROOT)}")


def lane_ledgers(commit: str, overwrite: bool) -> None:
    rows: dict[int, list[tuple[str, int, str]]] = defaultdict(list)
    for path in git_files("src/sylvia_gm/**/*.py") + git_files("src/sylvia_gm/*.py"):
        lane, target = lane_of(path)
        rows[lane].append((path, line_count(path), target))
    for lane, (title, _crate) in LANES.items():
        entries = sorted(set(rows[lane]))
        total = sum(lines for _path, lines, _target in entries)
        text = [
            f"# Lane {lane} ledger: {title}", "",
            f"Python snapshot `{commit}`. {len(entries)} files, {total:,} lines.", "",
            "One row per Python file this lane retires. Status is `pending`, `in progress`,",
            "`ported` (Rust replacement merged and its comparison checks pass) or `retired`",
            "(no Rust replacement needed; say why in Notes). Only this lane's worker edits this",
            "file; a row moves to another lane only through the manager.", "",
            "| Python file | Lines | Rust target | Status | Notes |",
            "| --- | ---: | --- | --- | --- |",
        ]
        text += [f"| `{path}` | {lines:,} | `{target}` | pending | |"
                 for path, lines, target in entries]
        slug = title.lower().replace(",", "").replace(" ", "-")
        write(PORT / "ledger" / f"lane-{lane}-{slug}.md", "\n".join(text) + "\n",
              overwrite=overwrite)


def commands(commit: str, overwrite: bool) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
    from sylvia_gm.interfaces.commands import registry

    source = (ROOT / APP / "interfaces/commands/registry.py").read_text(encoding="utf-8")
    declared = []
    for command in registry._COMMANDS:  # noqa: SLF001 - reference tooling reads the table
        marker = f"Command(\n        '{command.name}'"
        position = source.find(f"'{command.name}'")
        tail = source[position:position + 220]
        topic = handler = ""
        if "_handler(" in tail.split("Command(")[0]:
            call = tail.split("_handler(")[1].split(")")[0]
            topic, handler = (part.strip().strip("'") for part in call.split(","))
        del marker
        declared.append((command.name, topic, handler, command.webui, command.diagnostics))
    # Methods the run loop answers itself, before the registry (owner_process.run).
    run_loop = [
        "shutdown", "desktop_update_confirm_startup", "desktop_update_state",
        "desktop_update_check", "desktop_update_download", "desktop_update_cancel",
        "desktop_update_defer", "desktop_update_prepare", "desktop_update_handoff",
        "desktop_update_reserve", "desktop_update_abort", "desktop_update_shutdown",
    ]

    rust = [
        "//! The owner's command table.",
        "//!",
        f"//! Written once from `interfaces/commands/registry.py` at snapshot `{commit}` by",
        "//! `docs/rust-port/tools/port_ledger.py`, and maintained by hand since. When a command is",
        "//! ported, add its handler in `dispatch.rs`, change its status here to `Ported` and",
        "//! tick it in `docs/rust-port/commands.md`.",
        "",
        "/// Whether the Rust owner answers a command itself yet.",
        "#[derive(Debug, Clone, Copy, PartialEq, Eq)]",
        "pub enum Status {",
        "    /// Still answered only by the Python owner.",
        "    Pending,",
        "    /// Answered by the Rust owner and covered by a comparison session.",
        "    Ported,",
        "}",
        "",
        "/// One owner command.",
        "#[derive(Debug, Clone, Copy)]",
        "pub struct Command {",
        "    /// The `method` of the request.",
        "    pub name: &'static str,",
        "    /// Python handler module under `interfaces/commands`, empty for run-loop methods.",
        "    pub topic: &'static str,",
        "    /// May be called through the WebUI host.",
        "    pub webui: bool,",
        "    /// Recorded by local diagnostics.",
        "    pub diagnostics: bool,",
        "    /// Port status.",
        "    pub status: Status,",
        "}",
        "",
        "const fn pending(name: &'static str, topic: &'static str, webui: bool, diagnostics: bool) -> Command {",
        "    Command { name, topic, webui, diagnostics, status: Status::Pending }",
        "}",
        "",
        "/// Every command in `COMMAND_REGISTRY`, in Python's declaration order.",
        "pub const REGISTRY: &[Command] = &[",
    ]
    for name, topic, _handler, webui, diagnostics in declared:
        rust.append(f'    pending("{name}", "{topic}", {str(webui).lower()}, '
                    f"{str(diagnostics).lower()}),")
    rust += [
        "];",
        "",
        "/// Methods `owner_process.run` answers itself before consulting the registry.",
        "pub const RUN_LOOP_METHODS: &[&str] = &[",
    ]
    rust += [f'    "{name}",' for name in run_loop]
    rust += [
        "];",
        "",
        "/// Look a registry command up by name.",
        "pub fn find(name: &str) -> Option<&'static Command> {",
        "    REGISTRY.iter().find(|command| command.name == name)",
        "}",
        "",
        "#[cfg(test)]",
        "mod tests {",
        "    use super::*;",
        "",
        "    #[test]",
        "    fn table_matches_the_snapshot_registry() {",
        f"        assert_eq!(REGISTRY.len(), {len(declared)});",
        "        let mut names: Vec<&str> = REGISTRY.iter().map(|command| command.name).collect();",
        "        names.sort_unstable();",
        "        names.dedup();",
        f"        assert_eq!(names.len(), {len(declared)}, \"command names are unique\");",
        f"        assert_eq!(REGISTRY.iter().filter(|c| c.webui).count(), "
        f"{sum(1 for row in declared if row[3])});",
        f"        assert_eq!(REGISTRY.iter().filter(|c| c.diagnostics).count(), "
        f"{sum(1 for row in declared if row[4])});",
        "    }",
        "}",
    ]
    write(ROOT / "crates/sylvia-owner/src/commands/table.rs", "\n".join(rust) + "\n",
          overwrite=overwrite)

    table = [
        "# Owner command burn-down", "",
        f"Python snapshot `{commit}`. {len(declared)} registry commands and {len(run_loop)} "
        "run-loop methods.", "",
        "A command is done when the Rust owner answers it and a comparison session covering it",
        "passes against the recorded Python owner. Tick it here in the same pull request that",
        "flips its status in `crates/sylvia-owner/src/commands/table.rs`. Handlers live with the lane",
        "that owns the service behind them; lane 5 owns the dispatch itself.", "",
        f"Ported: 1 of {len(declared) + len(run_loop)} (`shutdown`).", "",
        "## Run-loop methods", "",
        "| Method | Rust | Comparison session |", "| --- | --- | --- |",
    ]
    table += [f"| `{name}` | {'yes' if name == 'shutdown' else 'no'} | "
              f"{'every session' if name == 'shutdown' else ''} |" for name in run_loop]
    table += ["", "## Registry commands", "",
              "| Command | Python handler | WebUI | Rust | Comparison session |",
              "| --- | --- | --- | --- | --- |"]
    for name, topic, handler, webui, _diagnostics in declared:
        where = f"`{topic}.{handler}`" if topic else "run loop"
        table.append(f"| `{name}` | {where} | {'yes' if webui else 'no'} | no | |")
    write(PORT / "commands.md", "\n".join(table) + "\n", overwrite=overwrite)

    vectors = ROOT / "crates/sylvia-owner/tests/vectors"
    write(vectors / "handshake_zero.txt", derive_owner_response("00" * 32) + "\n", overwrite=True)
    write(vectors / "handshake_pattern.txt",
          derive_owner_response("0123456789abcdef" * 4) + "\n", overwrite=True)


def inventory(commit: str, destination: Path) -> None:
    areas: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path in git_files("*.py"):
        if path.startswith(("crates/", "docs/rust-port/")):
            continue  # the port's own reference tooling is not part of the snapshot
        parts = path.split("/")
        if path.startswith(APP):
            area = parts[2] if len(parts) > 3 else "package root"
        elif parts[0] == "scripts":
            area = "scripts (SRD prefix)" if parts[-1].startswith("srd_") else "scripts (other)"
        else:
            area = parts[0] if len(parts) > 1 else path
        areas[area].append((path, line_count(path)))
    total_files = sum(len(items) for items in areas.values())
    total_lines = sum(lines for items in areas.values() for _path, lines in items)
    app_files = sum(len(items) for area, items in areas.items()
                    if any(path.startswith(APP) for path, _ in items))
    app_lines = sum(lines for items in areas.values() for path, lines in items
                    if path.startswith(APP))
    text = [
        "# Python inventory for the Sylvia Rust port", "",
        f"Git commit `{commit}` (the accepted 0.4.1 code). One row per tracked Python file. "
        "Counts are newline counts and include comments and blank lines.", "",
        f"Application (`src/sylvia_gm`): **{app_files} files, {app_lines:,} lines**. "
        f"Repository: **{total_files:,} files, {total_lines:,} lines**.", "",
        "The per-lane port ledgers in the repository (`docs/rust-port/ledger/`) carry the "
        "status of each application file. This file is the frozen starting count.", "",
        "| Area | Files | Lines |", "| --- | ---: | ---: |",
    ]
    for area in sorted(areas):
        items = areas[area]
        text.append(f"| {area} | {len(items):,} | {sum(lines for _p, lines in items):,} |")
    for area in sorted(areas):
        text += ["", f"## {area}", "", "| Repository path | Lines |", "| --- | ---: |"]
        text += [f"| `{path}` | {lines:,} |" for path, lines in sorted(areas[area])]
    destination.write_bytes(("\n".join(text) + "\n").encode("utf-8"))
    print(f"wrote inventory: {destination} ({total_files} files, {total_lines} lines)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--commit", help="snapshot commit to record (default: HEAD)")
    arguments = parser.parse_args()
    commit = arguments.commit or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if arguments.init:
        lane_ledgers(commit, arguments.overwrite)
        commands(commit, arguments.overwrite)
    if arguments.inventory:
        inventory(commit, arguments.inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
