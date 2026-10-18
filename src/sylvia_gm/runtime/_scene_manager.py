"""Scene management — location transitions and scene state.

Provides:
  Scene           — dataclass for a campaign location/scene
  TransitionResult — result of a scene transition
  SceneManager    — manages scene transitions, triggers enter/leave procedures
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sylvia_gm.runtime._types import ExecutionContext


@dataclass
class Scene:
    """A campaign location/scene.

    Attributes:
        scene_id: Unique identifier ("forest_clearing", "tavern_interior").
        name: Display name ("Forest Clearing").
        description: Flavor text for the scene.
        location_type: "dungeon", "wilderness", "settlement", "other".
        exits: Direction → scene_id mapping (e.g., {"north": "river_bank"}).
        npcs_present: Entity IDs of NPCs currently in the scene.
        time_of_day: Override for local time ("dawn", "day", "dusk", "night").
        lighting: Lighting condition ("bright", "dim", "dark", "magical").
        props: Arbitrary scene metadata (key → value).
        flags_on_enter: Flags to set when entering this scene.
        flags_on_leave: Flags to set when leaving this scene.
        meta: Extra metadata dict.
    """

    scene_id: str
    name: str = ""
    description: str = ""
    location_type: str = "other"
    exits: dict[str, str] = field(default_factory=dict)
    npcs_present: list[str] = field(default_factory=list)
    time_of_day: str | None = None
    lighting: str | None = None
    props: dict[str, Any] = field(default_factory=dict)
    flags_on_enter: list[str] = field(default_factory=list)
    flags_on_leave: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionResult:
    """Result of a scene transition.

    Attributes:
        old_scene_id: Previous scene (None if first transition).
        new_scene_id: New scene after transition.
        triggered_leave: Whether on_leave procedure was triggered.
        triggered_enter: Whether on_enter procedure was triggered.
        flags_set: List of flags that were set during transition.
    """

    old_scene_id: str | None
    new_scene_id: str
    triggered_leave: bool = False
    triggered_enter: bool = False
    flags_set: list[str] = field(default_factory=list)


class SceneManager:
    """Manages scene transitions and location state.

    Scenes are loaded from:
    1. Ruleset definitions (``definitions.scenes`` in the ruleset YAML).
    2. Campaign state (``_campaign.scenes`` for dynamic scenes).

    The campaign's current scene is persisted on ``_campaign.current_scene_id``
    via the StateBridge.
    """

    def __init__(self, state_bridge: Any = None):
        """Initialize the scene manager.

        Args:
            state_bridge: StateBridge instance for persistence.
                          Can be None for testing (in-memory only).
        """
        self._state_bridge = state_bridge
        self._current_scene_id: str | None = None
        self._scenes: dict[str, Scene] = {}

        # Load current scene from campaign state if available
        if state_bridge is not None:
            try:
                saved_scene = state_bridge.read_field(
                    "_campaign", "current_scene_id"
                )
                if saved_scene:
                    self._current_scene_id = str(saved_scene)
            except Exception:
                pass

    @property
    def current_scene_id(self) -> str | None:
        """The ID of the current scene, or None."""
        return self._current_scene_id

    @property
    def current_scene(self) -> Scene | None:
        """The current Scene object, or None."""
        if self._current_scene_id is None:
            return None
        return self._scenes.get(self._current_scene_id)

    def register_scene(self, scene: Scene) -> None:
        """Register a scene definition.

        Args:
            scene: The Scene to register.
        """
        self._scenes[scene.scene_id] = scene

    def register_scenes(self, scenes: list[Scene]) -> None:
        """Register multiple scene definitions at once."""
        for scene in scenes:
            self._scenes[scene.scene_id] = scene

    def load_scene(self, scene_id: str, ctx: ExecutionContext | None = None) -> Scene | None:
        """Load a scene definition by ID.

        Checks in order:
        1. Already registered scenes (self._scenes).
        2. Ruleset definitions (ctx.definitions.scenes).
        3. Campaign state (_campaign.scenes via StateBridge).

        Args:
            scene_id: The scene identifier to load.
            ctx: Optional ExecutionContext for ruleset definitions.

        Returns:
            The Scene if found, or None.
        """
        # 1. Already registered
        if scene_id in self._scenes:
            return self._scenes[scene_id]

        # 2. Ruleset definitions
        if ctx is not None:
            definitions_scenes = ctx.definitions.get("scenes", {})
            if isinstance(definitions_scenes, dict):
                scene_data = definitions_scenes.get(scene_id)
                if scene_data and isinstance(scene_data, dict):
                    scene = Scene(
                        scene_id=scene_id,
                        name=scene_data.get("name", scene_id),
                        description=scene_data.get("description", ""),
                        location_type=scene_data.get("location_type", "other"),
                        exits=scene_data.get("exits", {}),
                        npcs_present=scene_data.get("npcs_present", []),
                        time_of_day=scene_data.get("time_of_day"),
                        lighting=scene_data.get("lighting"),
                        props=scene_data.get("props", {}),
                        flags_on_enter=scene_data.get("flags_on_enter", []),
                        flags_on_leave=scene_data.get("flags_on_leave", []),
                        meta=scene_data.get("meta", {}),
                    )
                    self._scenes[scene_id] = scene
                    return scene

        # 3. Campaign state (dynamic scenes)
        if self._state_bridge is not None:
            try:
                campaign_scenes = self._state_bridge.read_field(
                    "_campaign", "scenes"
                )
                if isinstance(campaign_scenes, dict):
                    scene_data = campaign_scenes.get(scene_id)
                    if scene_data and isinstance(scene_data, dict):
                        scene = Scene(
                            scene_id=scene_id,
                            name=scene_data.get("name", scene_id),
                            description=scene_data.get("description", ""),
                            location_type=scene_data.get("location_type", "other"),
                            exits=scene_data.get("exits", {}),
                            npcs_present=scene_data.get("npcs_present", []),
                            time_of_day=scene_data.get("time_of_day"),
                            lighting=scene_data.get("lighting"),
                            props=scene_data.get("props", {}),
                            flags_on_enter=scene_data.get("flags_on_enter", []),
                            flags_on_leave=scene_data.get("flags_on_leave", []),
                            meta=scene_data.get("meta", {}),
                        )
                        self._scenes[scene_id] = scene
                        return scene
            except Exception:
                pass

        return None

    def transition_to(
        self,
        new_scene_id: str,
        ctx: ExecutionContext,
    ) -> TransitionResult:
        """Transition the party to a new scene.

        Triggers enter/leave procedures via ``ctx.procedure_executor``
        if available. Sets scene flags and persists the new scene to
        the campaign state.

        Args:
            new_scene_id: The scene to transition to.
            ctx: ExecutionContext with procedure_executor and state_bridge.

        Returns:
            TransitionResult summarizing the transition.
        """
        old_scene_id = self._current_scene_id
        old_scene = self._scenes.get(old_scene_id) if old_scene_id else None

        result = TransitionResult(
            old_scene_id=old_scene_id,
            new_scene_id=new_scene_id,
        )

        flag_store = ctx.extras.get("flag_store")

        # ── Leave old scene ──────────────────────────────────────────
        if old_scene_id is not None:
            # Trigger on_leave procedure
            if ctx.procedure_executor is not None:
                try:
                    ctx.procedure_executor(f"on_leave_{old_scene_id}", ctx)
                    result.triggered_leave = True
                except Exception:
                    pass

                # Also trigger generic on_scene_change
                try:
                    ctx.procedure_executor("on_scene_change", ctx)
                except Exception:
                    pass

            # Set flags_on_leave (always, regardless of procedure_executor)
            if old_scene is not None:
                for flag in old_scene.flags_on_leave:
                    if flag_store is not None:
                        flag_store.set(flag, True)
                    ctx.vars[f"_flag_{flag}"] = True
                    result.flags_set.append(flag)

        # Load the new scene if not already registered
        if new_scene_id not in self._scenes:
            self.load_scene(new_scene_id, ctx)

        new_scene = self._scenes.get(new_scene_id)

        # Update current scene
        self._current_scene_id = new_scene_id

        # Persist to campaign state
        if self._state_bridge is not None:
            try:
                self._state_bridge.write_field(
                    "_campaign", "current_scene_id", new_scene_id
                )
            except Exception:
                pass

        # ── Enter new scene ─────────────────────────────────────────
        # Trigger on_enter procedure
        if ctx.procedure_executor is not None:
            try:
                ctx.procedure_executor(f"on_enter_{new_scene_id}", ctx)
                result.triggered_enter = True
            except Exception:
                pass

        # Set flags_on_enter (always, regardless of procedure_executor)
        if new_scene is not None:
            for flag in new_scene.flags_on_enter:
                if flag_store is not None:
                    flag_store.set(flag, True)
                ctx.vars[f"_flag_{flag}"] = True
                result.flags_set.append(flag)

        # Inject scene into context vars for State Memo
        if new_scene is not None:
            ctx.vars["_current_scene"] = new_scene.scene_id
            ctx.vars["_current_scene_name"] = new_scene.name
            ctx.vars["_current_scene_description"] = new_scene.description
            ctx.vars["_current_scene_type"] = new_scene.location_type

        return result
