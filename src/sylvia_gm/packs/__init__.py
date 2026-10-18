"""Premium campaign packs subpackage.

T5.4 — ZIP-based campaign pack loader.
Packs contain campaign.yaml, optional ruleset.yaml, assets/, and pack.json metadata.
"""

from sylvia_gm.packs.loader import PackLoader, PackMetadata

__all__ = ["PackLoader", "PackMetadata"]
