"""Compatibility import for the package-contained Lite equipment migration."""

from sylvia_gm.application.srd_lite_weapon_set_migration import (
    MIGRATION_ID,
    PACKAGE_ID,
    migrate_state,
)

__all__ = ["MIGRATION_ID", "PACKAGE_ID", "migrate_state"]
