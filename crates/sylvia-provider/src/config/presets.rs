//! `_PROVIDER_PRESETS` (`config/provider_settings.py`): the shipped services the Models surface
//! offers, generated from the Python reference (`src/generate_tables.py`).

use super::Data;

/// One shipped service.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Preset {
    pub id: &'static str,
    pub name: &'static str,
    pub mode: &'static str,
    pub base_url: &'static str,
    pub credential_required: bool,
    pub endpoint_editable: bool,
    pub default_transport: &'static str,
    pub launch_hint: Option<&'static str>,
    pub setup_hint: &'static str,
    pub setup_url: Option<&'static str>,
}

#[rustfmt::skip]
#[path = "presets_table.rs"]
mod table;

/// Every preset in Python's order.
pub fn presets() -> &'static [Preset] {
    table::PRESETS
}

/// `_PROVIDER_PRESETS.get(preset_id)`.
pub fn preset(preset_id: &str) -> Option<&'static Preset> {
    table::PRESETS.iter().find(|preset| preset.id == preset_id)
}

impl Preset {
    /// `{"provider_id": id, **preset}`, keys in Python's order.
    pub fn projection(&self) -> Data {
        let text = |value: &str| Data::Str(value.to_owned());
        let mut pairs = vec![
            (text("provider_id"), text(self.id)),
            (text("name"), text(self.name)),
            (text("mode"), text(self.mode)),
            (text("base_url"), text(self.base_url)),
            (text("credential_required"), Data::Bool(self.credential_required)),
            (text("endpoint_editable"), Data::Bool(self.endpoint_editable)),
            (text("default_transport"), text(self.default_transport)),
        ];
        if let Some(hint) = self.launch_hint {
            pairs.push((text("launch_hint"), text(hint)));
        }
        pairs.push((text("setup_hint"), text(self.setup_hint)));
        if let Some(url) = self.setup_url {
            pairs.push((text("setup_url"), text(url)));
        }
        Data::Dict(pairs)
    }
}
