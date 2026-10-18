"""Campaign folder creation module.

Provides :func:`create_campaign_folder` for initializing self-contained
campaign directories on disk.  Pure filesystem operations — no LLM calls,
no async I/O, no server dependencies.
"""

from ._folder import CreateCampaignResult, create_campaign_folder

__all__ = ["create_campaign_folder", "CreateCampaignResult"]
