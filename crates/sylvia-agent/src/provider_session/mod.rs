//! `agent/_provider_session.py`, the provider-backed GM session, ported in parts.
//!
//! - [`tools`]: the tool list a provider is shown (`_provider_tools` and its helpers).
//! - [`reads`]: the application-owner reads the request assembly makes ([`reads::OpeningReads`]).
//! - [`texts`]: the GM request's fixed texts and the small messages built from them.

pub mod reads;
pub mod texts;
pub mod tools;
