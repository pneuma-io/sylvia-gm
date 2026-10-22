from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from typing import Iterator


ENCODED_SOURCE_COMMIT = "1b5b500"

CANONICAL_DIRS = (
    "Character",
    "Classes",
    "Combat",
    "CQC",
    "Firearms",
    "Explosives",
    "Hacking",
    "Drones",
    "Social",
    "Investigation",
    "Equipment",
    "Environment",
    "Specialties",
    "Hubs",
    "Maps",
)
LOCKED_TESTING_SOURCES = (
    "Testing/BALANCE CONTRACT.md",
    "Testing/SPECIALTY TEST APPROVAL.md",
    "Testing/BALANCE PARAMETERS.md",
    "Testing/BALANCE SCENARIOS.md",
    "Testing/BENCHMARK FIXTURES.md",
    "Testing/REFERENCE EQUIPMENT.md",
    "Testing/REFERENCE EQUIPMENT VALUES.md",
    "Testing/CODEX BALANCE SUITE HANDOFF.md",
)

RULE_MODULE_SOURCES: dict[str, tuple[str, ...]] = {
    "checks": ("Character/CHECK.md", "Character/DIFFICULTY.md", "Character/NATURAL 1.md", "Character/NATURAL 20.md"),
    "resources": ("Combat/AP.md", "Combat/HP.md", "Combat/RES.md", "Combat/PENETRATION.md"),
    "movement_geometry": ("Combat/GRID.md", "Combat/MOVEMENT.md", "Combat/STANCE.md", "Combat/COVER.md", "Combat/DESTRUCTIBLE COVER.md", "Combat/BREACHING.md", "Combat/CASUALTY MOVEMENT.md", "Combat/LINE OF SIGHT.md"),
    "firearms": ("Combat/ATTACK ROLL.md", "Combat/HIT QUALITY.md", "Combat/CRITICAL HIT.md", "Firearms/WEAPON PROFILE.md", "Firearms/PROJECTILE.md", "Firearms/MATERIAL PENETRATION.md", "Firearms/RECOIL.md", "Firearms/AMMUNITION.md", "Firearms/MAGAZINE.md", "Firearms/CHAMBER.md", "Firearms/CYCLING.md", "Firearms/RELOAD.md", "Firearms/FIREARM MANIPULATION.md", "Firearms/FULL AUTO.md", "Firearms/BURST FIRE.md", "Firearms/AIM.md"),
    "prepared_fire": ("Combat/SUPPRESSION.md", "Combat/OVERWATCH.md", "Combat/PREP ACTION.md", "Combat/TRIGGER ORDERING.md", "Combat/TEAM TURN.md"),
    "team_tactics": ("Combat/AP.md", "Combat/TEAM TURN.md", "Combat/TEAM INITIATIVE.md", "Combat/GRID.md", "Combat/LINE OF SIGHT.md", "Combat/COVER.md", "Character/TARGET KNOWLEDGE.md"),
    "cqc": ("CQC/CQC ACTION.md", "CQC/CQC DEFENSE.md", "CQC/CONTROL.md", "CQC/ESCAPE.md", "CQC/SHOVE.md", "CQC/TRIP.md", "CQC/THROW.md", "CQC/DISARM.md"),
    "explosives": ("Explosives/GRENADE.md", "Explosives/GRENADE PLACEMENT.md", "Explosives/FUSE TIMING.md", "Explosives/EXPLOSIVE SAVE.md", "Explosives/BLAST GEOMETRY.md"),
    "medical": ("Character/Skills/MEDICINE.md", "Combat/DOWNED.md", "Combat/INJURY.md", "Combat/LIFE-THREATENING CONDITION.md", "Combat/STABILIZE.md", "Combat/RESUSCITATION.md", "Combat/DEATH'S DOOR.md"),
    "information": ("Character/TARGET KNOWLEDGE.md", "Character/Skills/STEALTH.md", "Investigation/TEAM KNOWLEDGE.md"),
    "drones": ("Drones/DRONE.md", "Drones/MANUAL CONTROL.md", "Drones/AUTONOMOUS DRONE.md", "Drones/STANDING ORDER.md", "Drones/SIGNAL.md", "Drones/LOSS OF SIGNAL.md"),
    "hacking": ("Classes/HACKER.md", "Hacking/NODE.md", "Hacking/LINK.md", "Hacking/NETWORK.md", "Hacking/NETWORK SEGMENT.md", "Hacking/GATEWAY.md", "Hacking/LOCAL ACCESS.md", "Hacking/PRIVILEGE.md", "Hacking/INTRUSION.md", "Hacking/HACKING VISIBILITY.md", "Hacking/MONITORING.md", "Hacking/DETECTION.md", "Hacking/FOOTHOLD.md"),
    "social": ("Classes/INFLUENCER.md", "Social/SOCIAL FACT.md", "Social/LEVERAGE.md", "Social/CONTACT.md", "Social/FACTION REPUTATION.md", "Social/NPC MORALE.md"),
    "progression": ("Character/LEVEL.md", "Character/SPECIALTY.md", "Character/CLASS-SKILL INDEPENDENCE.md"),
    "classes": tuple(f"Classes/{name}.md" for name in ("BULWARK", "RIFLEMAN", "BREACHER", "GHOST", "SHARPSHOOTER", "TECH", "HACKER", "CORPSMAN", "INFLUENCER")),
    "specialties": tuple(f"Specialties/{name}.md" for name in ("ANCHOR", "ARSENAL", "AUTODRONES", "BREAKER", "DIPLOMAT", "ENFORCER", "EVADER", "FIELD ENGINEER", "FIELD MEDIC", "FLANKER", "FPV DRONES", "GUARDIAN", "GUNNER", "HANDLER", "ICON", "INFILTRATOR", "LOCAL HACKER", "NETWORKER", "OPERATOR", "RESUSCITATOR", "SENTINEL", "SHIELD BEARER", "SHOCK", "SNAPSHOT", "SNIPER", "TRAUMA")),
    "scenario_policies": ("Testing/BALANCE SCENARIOS.md",),
    "parameter_manifest": ("Testing/BALANCE PARAMETERS.md",),
    "benchmark_fixtures": ("Testing/BENCHMARK FIXTURES.md",),
    "reference_equipment": ("Testing/REFERENCE EQUIPMENT.md", "Testing/REFERENCE EQUIPMENT VALUES.md"),
}


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    path: str
    encoded_source_commit: str
    current_sha256: str
    coverage_status: str


def repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "AGENTS.md").is_file() and (path / "Testing").is_dir():
            return path
    raise RuntimeError("could not locate Trigger Discipline repository root")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha256_bytes(payload: bytes) -> str:
    """Hash canonical text semantically across Git LF / Windows CRLF worktrees."""
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def protected_paths(root: Path | None = None) -> list[Path]:
    root = repo_root(root)
    paths: list[Path] = []
    for directory in CANONICAL_DIRS:
        base = root / directory
        if base.exists():
            paths.extend(path for path in base.rglob("*") if path.is_file())
    paths.extend(root / relative for relative in LOCKED_TESTING_SOURCES if (root / relative).is_file())
    canvas = root / "Trigger Discipline.canvas"
    if canvas.is_file():
        paths.append(canvas)
    return sorted(set(paths))


def protected_fingerprint(root: Path | None = None) -> dict[str, str]:
    root = repo_root(root)
    return {path.relative_to(root).as_posix(): _sha256(path) for path in protected_paths(root)}


@contextmanager
def immutable_sources(root: Path | None = None) -> Iterator[dict[str, str]]:
    root = repo_root(root)
    before = protected_fingerprint(root)
    try:
        yield before
    finally:
        after = protected_fingerprint(root)
        if before != after:
            changed = sorted(set(before) ^ set(after) | {key for key in before.keys() & after.keys() if before[key] != after[key]})
            raise RuntimeError(f"canonical/locked source mutation detected: {', '.join(changed)}")


def implementation_sources(root: Path | None = None) -> list[dict[str, str]]:
    root = repo_root(root)
    path_to_modules: dict[str, list[str]] = {}
    for module, paths in RULE_MODULE_SOURCES.items():
        for path in paths:
            path_to_modules.setdefault(path, []).append(module)
    return [
        {
            "path": path,
            "encoded_source_commit": ENCODED_SOURCE_COMMIT,
            "current_sha256": _source_sha256_bytes((root / path).read_bytes()),
            "mechanic_modules": sorted(modules),
            "implementation_coverage_status": "IMPLEMENTED" if "specialties" not in modules else "NODE_LEVEL_IN_COVERAGE_REPORT",
        }
        for path, modules in sorted(path_to_modules.items())
    ]


def current_commit(root: Path | None = None) -> str:
    root = repo_root(root)
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def source_drift(root: Path | None = None) -> list[dict[str, str]]:
    root = repo_root(root)
    findings: list[dict[str, str]] = []
    sources = implementation_sources(root)
    relative_paths = [str(item["path"]) for item in sources]
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", ENCODED_SOURCE_COMMIT, "--", *relative_paths],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
            encoded_hashes = {
                member.name.replace("\\", "/"): _source_sha256_bytes(extracted.read())
                for member in handle.getmembers()
                if member.isfile() and (extracted := handle.extractfile(member)) is not None
            }
    except (OSError, subprocess.CalledProcessError, tarfile.TarError):
        encoded_hashes = {}
    for item in sources:
        relative = item["path"]
        expected_hash = encoded_hashes.get(str(relative))
        if expected_hash is None:
            findings.append({"path": relative, "status": "SOURCE_IDENTITY_UNAVAILABLE", "encoded_source_commit": ENCODED_SOURCE_COMMIT})
            continue
        if expected_hash != item["current_sha256"]:
            findings.append(
                {
                    "path": relative,
                    "status": "SOURCE_DRIFT",
                    "encoded_source_commit": ENCODED_SOURCE_COMMIT,
                    "encoded_sha256": expected_hash,
                    "current_sha256": item["current_sha256"],
                }
            )
    return findings


def provenance_record(parameter_hash: str, root: Path | None = None) -> dict[str, object]:
    return {
        "repository_commit": current_commit(root),
        "encoded_source_commit": ENCODED_SOURCE_COMMIT,
        "suite_version": "0.1.0",
        "parameter_set_hash": parameter_hash,
        "sources": implementation_sources(root),
        "source_drift": source_drift(root),
    }


def fingerprint_hash(fingerprint: dict[str, str]) -> str:
    payload = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
