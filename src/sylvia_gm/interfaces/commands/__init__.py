"""The dependencies captured by a single selected owner command."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.agent._provider_resolver import ProviderResolver
    from sylvia_gm.agent._session_loop import CampaignSessionLoop
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.interfaces.local_api import LocalCommandBus


@dataclass(frozen=True)
class DispatchContext:
    """Keep command dependencies and selection stable during dispatch."""

    bus: LocalCommandBus
    application_owner: AuthorityOwner
    owner_id: str
    provider_resolver: ProviderResolver
    model_choices: Callable[[], Sequence[Mapping[str, object]]]
    session_loop: CampaignSessionLoop
    storage_root: Path
    reload_provider_resolver: Callable[[], None] | None
    gm_system_prompt: str | None
    method: str
