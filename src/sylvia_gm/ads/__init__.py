"""Ad system subpackage.

T5.2 — Three-tier ad model:
  1. Splash screen ad — one impression during loading (3-5 seconds, static image)
  2. Sidebar companion ad — opt-in static ad in dead column space, rotates every ~10 min
  3. Disable all ads — single toggle, respectful messaging

Local ad storage: config/ad_sponsors.json — JSON array of sponsor entries.
"""

from sylvia_gm.ads.manager import AdManager

__all__ = ["AdManager"]
