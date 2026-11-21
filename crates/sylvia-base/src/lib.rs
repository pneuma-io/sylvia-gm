//! Shared foundations for the Sylvia GM Rust port.
//!
//! Owned by the rewrite manager. Every lane depends on this crate, so changes here go through
//! the manager, not through a lane's own pull request.
//!
//! - [`pyjson`]: JSON text byte-for-byte as Python's `json.dumps` writes it. Anything hashed or
//!   stored must go through it, never through `serde_json::to_string`.
//! - [`hash`]: SHA-256 helpers with the hex form Python's `hexdigest()` gives.
//! - [`error`]: the coded error every owner reply is built from.
//! - [`pystr`]: Python `str` behaviour Rust lacks (whitespace, case folding, `upper`, code-point
//!   `len`), on Unicode 15.0.
//! - [`pybase64`]: CPython's `binascii` base64 and the `base64` calls Sylvia makes.
//! - [`pyurl`]: `urllib.parse`'s `urlsplit`, `urlunsplit` and strict `unquote`, CPython 3.12.
//! - [`pyyaml`]: YAML loaded and dumped exactly as PyYAML 6.0.3 does (`SafeLoader`,
//!   `CSafeLoader`, `safe_dump`).
//! - [`winreg`]: `winreg.QueryValueEx` as CPython 3.12 reads the current user's registry
//!   (read-only; the rule 9 module for the registry).

pub mod error;
pub mod hash;
pub mod pybase64;
pub mod pyjson;
pub mod pystr;
pub mod pyurl;
pub mod pyyaml;
pub mod winreg;

pub use error::{CodedError, CodedResult};

/// JSON value used at Sylvia's extension points (package-declared fields, provider payloads).
///
/// Object keys keep insertion order (`serde_json` is built with `preserve_order`), which is what
/// a Python `dict` does. Integers and floats stay distinct, as in Python: `1` and `1.0` encode
/// differently.
pub type Json = serde_json::Value;
/// Ordered JSON object, the counterpart of a Python `dict[str, Any]`.
pub type JsonMap = serde_json::Map<String, serde_json::Value>;
