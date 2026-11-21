//! `domain/inventory.py`: rules-neutral holdings.
//!
//! The shared schemas the package format refers to, and in [`holdings`] the holding
//! validation, transfers and player projection.

mod holdings;

pub use holdings::{player_inventory, transfer_holding, validate_holdings, HoldingTransfer};
use std::sync::OnceLock;
use sylvia_base::{pyjson, Json};

/// `INVENTORY_RECORD_ID`.
pub const INVENTORY_RECORD_ID: &str = "sylvia-fallback-inventory";
/// `INVENTORY_ENTITY_KIND`.
pub const INVENTORY_ENTITY_KIND: &str = "inventory_ledger";

/// `inventory_update_schema()`: the shared inventory tool's input schema, exported from Python
/// by `generate_inventory_schema.py` because the model reads its text.
pub fn inventory_update_schema() -> &'static Json {
    static SCHEMA: OnceLock<Json> = OnceLock::new();
    SCHEMA.get_or_init(|| {
        pyjson::loads(include_str!("inventory_update_schema.json")).expect("the exported schema is JSON")
    })
}

/// `inventory_holding_schema()`: the structured holding shape Rulesets project inventory with.
pub fn inventory_holding_schema() -> &'static Json {
    &inventory_update_schema()["properties"]["holdings"]["items"]
}
