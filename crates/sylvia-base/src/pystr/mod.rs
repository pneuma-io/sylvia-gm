//! Python `str` behaviour that Rust's `str` does not share, on CPython 3.12's Unicode 15.0
//! tables. One file per topic; this file only declares and re-exports them.

mod alnum;
mod casefold;
mod decode;
mod float_parse;
mod identifier;
mod int_parse;
mod len;
mod lines;
mod repr;
mod whitespace;

pub use alnum::is_alnum;
pub use casefold::{casefold, upper};
pub use decode::utf8_decode;
pub use float_parse::parse_float;
pub use identifier::isidentifier;
pub use int_parse::{decimal, parse_int, IntParseError, ValueError, INT_MAX_STR_DIGITS, INT_RANGE_MESSAGE};
pub use len::len;
pub use lines::{splitlines, splitlines_keepends};
pub use repr::{is_printable, repr, repr_200, repr_json};
pub use whitespace::{is_space, isspace, lstrip, rstrip, strip};
