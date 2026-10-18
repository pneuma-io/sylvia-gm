"""SubscriptionManager — tiered token-allowance tracking."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierInfo:
    """Metadata for a single subscription tier."""

    name: str
    price: int  # USD per month
    tokens: int  # monthly allowance
    models: list[str] = field(default_factory=lambda: ["local"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "price": self.price,
            "tokens": self.tokens,
            "models": list(self.models),
        }


@dataclass(frozen=True)
class TopUpPack:
    """A one-time token top-up purchase."""

    price: int  # USD
    tokens: int  # tokens granted

    def to_dict(self) -> dict[str, Any]:
        return {"price": self.price, "tokens": self.tokens}


# ---------------------------------------------------------------------------
# Default tier catalogue
# ---------------------------------------------------------------------------

DEFAULT_TIERS: dict[str, TierInfo] = {
    "free": TierInfo(name="Free", price=0, tokens=0, models=["local"]),
    "cloud_lite": TierInfo(name="Cloud Lite", price=8, tokens=500_000),
    "cloud_standard": TierInfo(name="Cloud Standard", price=15, tokens=1_500_000),
    "cloud_plus": TierInfo(name="Cloud Plus", price=25, tokens=5_000_000),
}

DEFAULT_TOP_UPS: list[TopUpPack] = [
    TopUpPack(price=5, tokens=100_000),
    TopUpPack(price=10, tokens=250_000),
    TopUpPack(price=25, tokens=750_000),
]

# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SubscriptionManager:
    """Token-based usage tracker for monthly subscriptions.

    Per-user state is stored as JSON files under *data_dir*.  For v0.1
    this is local only; cloud-backed Stripe billing is deferred.
    """

    TIERS: dict[str, TierInfo] = DEFAULT_TIERS
    TOP_UPS: list[TopUpPack] = DEFAULT_TOP_UPS

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".sylvia-gm" / "subscriptions"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_allowance(self, user_id: str, estimated_tokens: int) -> bool:
        """Return True if *user_id* has enough remaining tokens for the request."""
        remaining = self._get_remaining(user_id)
        return remaining >= estimated_tokens

    def consume_tokens(self, user_id: str, tokens_used: int) -> bool:
        """Deduct *tokens_used* from *user_id*'s allowance.

        Returns True on success, False if insufficient tokens (no deduction).
        """
        record = self._load_user(user_id)
        remaining = record["allowance"] - record["used"]
        if tokens_used > remaining:
            return False
        record["used"] += tokens_used
        record["updated_at"] = time.time()
        self._save_user(user_id, record)
        return True

    def get_usage(self, user_id: str) -> dict[str, Any]:
        """Return usage summary: tier, used, allowance, percentage."""
        record = self._load_user(user_id)
        allowance = record["allowance"]
        used = record["used"]
        pct = (used / allowance * 100) if allowance > 0 else 0.0
        return {
            "user_id": user_id,
            "tier": record["tier"],
            "used": used,
            "allowance": allowance,
            "percentage": round(pct, 1),
        }

    def set_tier(self, user_id: str, tier_key: str) -> None:
        """Assign *user_id* to a specific tier, resetting the monthly counter."""
        if tier_key not in self.TIERS:
            raise ValueError(
                f"Unknown tier '{tier_key}'. Available: {list(self.TIERS)}"
            )
        tier = self.TIERS[tier_key]
        self._save_user(
            user_id,
            {
                "tier": tier_key,
                "allowance": tier.tokens,
                "used": 0,
                "updated_at": time.time(),
                "top_ups_used": 0,
            },
        )

    def apply_top_up(self, user_id: str, pack_index: int) -> int:
        """Apply a top-up pack to the user's account.

        Returns the number of tokens added.
        """
        if not (0 <= pack_index < len(self.TOP_UPS)):
            raise ValueError(f"Invalid pack index {pack_index} (0-{len(self.TOP_UPS)-1})")
        pack = self.TOP_UPS[pack_index]
        record = self._load_user(user_id)
        record["allowance"] += pack.tokens
        record["top_ups_used"] = record.get("top_ups_used", 0) + 1
        record["updated_at"] = time.time()
        self._save_user(user_id, record)
        return pack.tokens

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _user_path(self, user_id: str) -> Path:
        safe = "".join(c for c in user_id if c.isalnum() or c in "_-")
        return self._data_dir / f"{safe}.json"

    def _load_user(self, user_id: str) -> dict[str, Any]:
        path = self._user_path(user_id)
        if not path.is_file():
            # Default to free tier on first access — persist so the file exists.
            tier = self.TIERS["free"]
            record: dict[str, Any] = {
                "tier": "free",
                "allowance": tier.tokens,
                "used": 0,
                "updated_at": time.time(),
                "top_ups_used": 0,
            }
            self._save_user(user_id, record)
            return record
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_user(self, user_id: str, record: dict[str, Any]) -> None:
        path = self._user_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _get_remaining(self, user_id: str) -> int:
        record = self._load_user(user_id)
        return record["allowance"] - record["used"]
