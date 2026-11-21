//! Python `str` behaviour that Rust's `str` does not share, on CPython 3.12's Unicode 15.0
//! tables. One file per topic; this file only declares and re-exports them.

mod whitespace;

pub use whitespace::{is_space, isspace, lstrip, rstrip, strip};
