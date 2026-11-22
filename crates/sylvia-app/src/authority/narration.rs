//! The owner's live narration state. It lives in `sylvia_store::narration`, so the store handle
//! the owner already shares can carry the one instance (`StoreHandle::narration`); its types are
//! re-exported here, where `canonical_replay` reads them.

pub use sylvia_store::narration::*;
