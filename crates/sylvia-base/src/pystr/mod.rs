//! Python `str` behaviour that Rust's `str` does not share, on CPython 3.12's Unicode 15.0
//! tables. One file per topic; this file only declares and re-exports them.

mod casefold;
mod len;
mod whitespace;

pub use casefold::{casefold, upper};
pub use len::len;
pub use whitespace::{is_space, isspace, lstrip, rstrip, strip};
