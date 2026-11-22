//! `agent/_context_ledger.Tagged`: a canonical message with its ledger tags.
//!
//! Python keeps the tags as attributes on a `dict` subclass, so the message still equals (and
//! serializes as) the plain message; only the context ledger reads them. Here the message and
//! its tags are kept apart: a request is built from [`Tagged::message`], and the ledger reads
//! [`Tagged::tags`].

use serde_json::Value as Json;
use sylvia_base::JsonMap;

/// `STABLE_KEY`: a fixed instruction block.
pub const STABLE_KEY: &str = "_stable_instruction";
/// `LEDGER_KEY`: a saved message's conversation identity.
pub const LEDGER_KEY: &str = "_ledger_key";
/// `HOST_RECORD_KEY`: an application-created user-role record.
pub const HOST_RECORD_KEY: &str = "_host_record";
/// `LEDGER_ENTRY_KEY`.
pub const LEDGER_ENTRY_KEY: &str = "_ledger_entry";
/// `ACTIVE_RECORDS_KEY`: on the fresh-state message, the still-active continuity record ids
/// per fact list. Host-only metadata, never message content.
pub const ACTIVE_RECORDS_KEY: &str = "_active_record_ids";

/// A canonical message and its ledger tags (`Tagged`).
#[derive(Debug, Clone, PartialEq)]
pub struct Tagged {
    pub message: JsonMap,
    pub tags: JsonMap,
}

impl Tagged {
    /// A message without tags (a plain `dict` in Python).
    pub fn plain(message: JsonMap) -> Self {
        Self { message, tags: JsonMap::new() }
    }

    /// `tagged(message, **{key: value})`.
    pub fn with(message: JsonMap, key: &str, value: Json) -> Self {
        let mut tags = JsonMap::new();
        tags.insert(key.to_owned(), value);
        Self { message, tags }
    }

    /// `tag_of(message, name)`: the tag, else the message's own item of that name.
    pub fn tag_of(&self, name: &str) -> Option<&Json> {
        self.tags.get(name).or_else(|| self.message.get(name))
    }
}

impl From<JsonMap> for Tagged {
    fn from(message: JsonMap) -> Self {
        Self::plain(message)
    }
}
