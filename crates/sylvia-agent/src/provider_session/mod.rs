//! `agent/_provider_session.py`, the provider-backed GM session, ported in parts.
//!
//! - [`tools`]: the tool list a provider is shown (`_provider_tools` and its helpers).
//! - [`context`]: the adapter's construction and the campaign context it projects.
//! - [`messages`]: `_messages`, the GM request's legacy assembly.
//! - [`reads`]: the application-owner reads the request assembly makes ([`reads::OpeningReads`]).
//! - [`texts`]: the GM request's fixed texts and the small messages built from them.

pub mod context;
pub mod messages;
pub mod reads;
pub mod texts;
pub mod tools;
