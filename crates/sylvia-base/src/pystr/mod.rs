//! Python `str` behaviour that Rust's `str` does not share, on CPython 3.12's Unicode 15.0
//! tables. One file per topic; this file only declares and re-exports them.

mod casefold;
mod float_parse;
mod int_parse;
mod len;
mod repr;
mod whitespace;

pub use casefold::{casefold, upper};
pub use float_parse::parse_float;
pub use int_parse::{decimal, parse_int, IntParseError, ValueError, INT_MAX_STR_DIGITS, INT_RANGE_MESSAGE};
pub use len::len;
pub use repr::{is_printable, repr, repr_200, repr_json};
pub use whitespace::{is_space, isspace, lstrip, rstrip, strip};
