from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .geometry import GridMap
from .model import ActorState, CoverObject, DetectionState, NetworkNodeState, Position, Side, Stance, WeaponState
from .parameters import FIREARM_SEEDS, ParameterSet


LEVELS = (1, 5, 10, 15, 20)
SPECIALIST_PROGRESSION = {
    1: (4, 2, 1, 1),
    5: (5, 3, 1, 5),
    10: (6, 4, 2, 10),
    15: (7, 5, 2, 15),
    20: (8, 6, 3, 20),
}
CLASS_CAST = {
    "BULWARK": "KORA",
    "RIFLEMAN": "VESPER",
    "BREACHER": "RIOT",
    "GHOST": "PIXIE",
    "SHARPSHOOTER": "SABLE",
    "TECH": "NOVA",
    "HACKER": "HEX",
    "CORPSMAN": "SOLACE",
    "INFLUENCER": "VELVET",
}
CLASS_SPECIALTIES = {
    "BULWARK": ("SHIELD BEARER", "ANCHOR", "GUARDIAN"),
    "RIFLEMAN": ("GUNNER", "OPERATOR", "ARSENAL"),
    "BREACHER": ("BREAKER", "SHOCK", "ENFORCER"),
    "GHOST": ("INFILTRATOR", "FLANKER", "EVADER"),
    "SHARPSHOOTER": ("SNIPER", "SENTINEL", "SNAPSHOT"),
    "TECH": ("FPV DRONES", "AUTODRONES", "FIELD ENGINEER"),
    "HACKER": ("LOCAL HACKER", "NETWORKER"),
    "CORPSMAN": ("FIELD MEDIC", "TRAUMA", "RESUSCITATOR"),
    "INFLUENCER": ("DIPLOMAT", "HANDLER", "ICON"),
}


def benchmark_control_values(level: int, control: str) -> tuple[int, int]:
    primary, _secondary, aptitude, _choices = SPECIALIST_PROGRESSION[level]
    if control == "GENERALIST":
        primary -= 1
    elif control == "TRAINING":
        primary = 5 if level == 1 else primary
        aptitude = 0
    elif control == "TALENT":
        primary, aptitude = 2, 3
    return primary, aptitude


def level_index(level: int) -> int:
    try:
        return LEVELS.index(level)
    except ValueError as error:
        raise ValueError(f"benchmark level must be one of {LEVELS}") from error


def weapon_state(weapon_id: str, parameters: ParameterSet) -> WeaponState:
    seed = FIREARM_SEEDS[weapon_id]
    capacity = parameters.values[f"equipment.{weapon_id}.capacity"]
    mechanisms = {
        "pistol": "self-loader",
        "smg": "self-loader",
        "service_rifle": "self-loader",
        "lmg": "self-loader",
        "shotgun": "pump action",
        "precision_rifle": "self-loader",
        "manual_precision_rifle": "bolt action",
    }
    modes = {
        "pistol": ("SEMI AUTO",),
        "smg": ("SEMI AUTO", "FULL AUTO"),
        "service_rifle": ("SEMI AUTO", "3-round BURST", "FULL AUTO"),
        "lmg": ("FULL AUTO",),
        "shotgun": ("SINGLE",),
        "precision_rifle": ("SEMI AUTO",),
        "manual_precision_rifle": ("SINGLE",),
    }
    return WeaponState(
        weapon_id,
        capacity,
        capacity,
        "LIVE",
        capacity * 2,
        0,
        False,
        mechanisms[weapon_id],
        modes[weapon_id],
    )


def benchmark_actor(
    class_id: str,
    level: int,
    side: Side,
    position: Position,
    parameters: ParameterSet,
    *,
    actor_suffix: str = "1",
    weapon_id: str = "service_rifle",
    control: str = "SPECIALIST",
) -> ActorState:
    primary, aptitude = benchmark_control_values(level, control)
    index = level_index(level)
    hp = parameters.values["hp.vector_l01_l05_l10_l15_l20"][index]
    res = parameters.values["res.vector_l01_l05_l10_l15_l20"][index]
    if class_id == "BULWARK":
        res += parameters.values["class.bulwark.res_bonus"]
    name = CLASS_CAST.get(class_id, "CONTROL")
    fixture = f"HR_{name}_L{level:02d}_{class_id}"
    return ActorState(
        f"{side.value}_{name}_{actor_suffix}",
        side,
        class_id,
        level,
        fixture,
        primary,
        aptitude,
        hp,
        hp,
        res,
        res,
        parameters.values["combat.ap.pool"],
        parameters.values["combat.ap.pool"],
        position,
        Stance.STANDING,
        0,
        weapon_state(weapon_id, parameters),
    )


def material_cover(
    cover_id: str,
    cells: set[tuple[int, int]],
    material: str,
    height: float,
    parameters: ParameterSet,
    *,
    destroyed_passable: bool = False,
    minor_debris: bool = False,
) -> CoverObject:
    return CoverObject(
        cover_id,
        cells,
        height,
        parameters.values[f"material.{material}.resistance"],
        parameters.values[f"material.{material}.cover_hp"],
        destroyed_passable=destroyed_passable,
        minor_debris=minor_debris,
    )


def build_map(map_id: str, parameters: ParameterSet) -> GridMap:
    if map_id == "MAP_OPEN_100":
        return GridMap(map_id, 100, 30)
    if map_id == "MAP_TIGHT_20":
        blockers: set[tuple[int, int]] = set()
        for cx, cy in ((6, 6), (14, 6), (6, 14), (14, 14)):
            blockers |= {(cx - 1, cy - 1), (cx, cy - 1), (cx - 1, cy), (cx, cy)}
        return GridMap(map_id, 20, 20, blockers)
    if map_id == "MAP_STREET_60":
        grid = GridMap(map_id, 60, 20)
        grid.covers = {
            "STREET_BARRIER_1": material_cover("STREET_BARRIER_1", {(20, y) for y in range(7, 13)}, "light_masonry", 1.25, parameters),
            "STREET_BARRIER_2": material_cover("STREET_BARRIER_2", {(40, y) for y in range(7, 13)}, "light_masonry", 1.25, parameters),
        }
        return grid
    if map_id == "MAP_BREACH_30":
        wall = {(15, y) for y in range(20) if y not in {9, 10}}
        grid = GridMap(map_id, 30, 20, wall)
        grid.covers = {
            "BREACH_DOOR": material_cover(
                "BREACH_DOOR",
                {(15, 9), (15, 10)},
                "timber",
                3.0,
                parameters,
                destroyed_passable=True,
                minor_debris=True,
            ),
            "DEFENDER_COVER_1": material_cover("DEFENDER_COVER_1", {(x, y) for x in range(21, 24) for y in range(5, 8)}, "light_masonry", 1.25, parameters),
            "DEFENDER_COVER_2": material_cover("DEFENDER_COVER_2", {(x, y) for x in range(21, 24) for y in range(13, 16)}, "light_masonry", 1.25, parameters),
        }
        return grid
    if map_id == "MAP_COMPOUND_80":
        blockers = {
            (x, y)
            for x0 in (27, 47)
            for x in range(x0, x0 + 6)
            for y in range(27, 33)
        }
        grid = GridMap(map_id, 80, 60, blockers)
        grid.covers = {
            f"COVER_{x}_{start}": material_cover(
                f"COVER_{x}_{start}", {(x, y) for y in range(start, start + 11)}, "light_masonry", 1.25, parameters
            )
            for x in (25, 55)
            for start in (10, 40)
        }
        return grid
    if map_id == "MAP_LONG_1000":
        return GridMap(map_id, 1000, 60)
    if map_id == "MAP_NETWORK_SITE_40":
        blockers = {(13, y) for y in range(30) if y != 15} | {(27, y) for y in range(30) if y != 15}
        return GridMap(map_id, 40, 30, blockers)
    raise KeyError(f"unknown map fixture {map_id}")


def network_site_nodes() -> dict[str, NetworkNodeState]:
    return {
        "NET40-A": NetworkNodeState(
            node_id="NET40-A",
            links={"NET40-B"},
            segment="OFFICE",
            privilege=0,
            required_privilege=1,
            access_difficulty=14,
            monitoring_difficulty=14,
            physical_position=Position(8, 15),
            local_access=True,
            discovered=True,
            detection_state=DetectionState.UNDETECTED,
            remote_functions={"local diagnostics"},
        ),
        "NET40-B": NetworkNodeState(
            node_id="NET40-B",
            links={"NET40-A", "NET40-C"},
            segment="OFFICE/SECURITY",
            privilege=0,
            required_privilege=1,
            access_difficulty=14,
            monitoring_difficulty=14,
            physical_position=Position(20, 15),
            detection_state=DetectionState.UNDETECTED,
            remote_functions={"gateway relay"},
        ),
        "NET40-C": NetworkNodeState(
            node_id="NET40-C",
            links={"NET40-B"},
            segment="SECURITY",
            privilege=0,
            required_privilege=2,
            access_difficulty=18,
            monitoring_difficulty=18,
            physical_position=Position(32, 15),
            detection_state=DetectionState.UNDETECTED,
            remote_functions={"door", "external objective"},
        ),
    }


def map_contracts() -> list[dict[str, Any]]:
    return [
        {"map_id": "MAP_OPEN_100", "size_m": [100, 30], "uses": ["range", "movement", "armor", "mirror"]},
        {"map_id": "MAP_TIGHT_20", "size_m": [20, 20], "uses": ["handling", "CQC", "grenades"]},
        {"map_id": "MAP_STREET_60", "size_m": [60, 20], "uses": ["cover", "suppression", "overwatch"]},
        {"map_id": "MAP_BREACH_30", "size_m": [30, 20], "uses": ["breach", "close gunplay", "CQC"]},
        {"map_id": "MAP_COMPOUND_80", "size_m": [80, 60], "uses": ["mixed team", "flanking", "medical"]},
        {"map_id": "MAP_LONG_1000", "size_m": [1000, 60], "uses": ["long range", "aim"]},
        {"map_id": "MAP_NETWORK_SITE_40", "size_m": [40, 30], "uses": ["hacking", "drone", "relay"]},
    ]
