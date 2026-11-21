//! YAML exactly as PyYAML 6.0.3 loads and dumps it: `yaml.SafeLoader`, `yaml.CSafeLoader` and
//! `yaml.safe_dump`.
//!
//! [`Loader::Pure`] is PyYAML's pure-Python pipeline, ported stage by stage: reader and scanner,
//! parser, composer with the resolver, and `SafeConstructor`. Sylvia also loads package runtimes
//! and authored Rulesets with `CSafeLoader`: libyaml 0.2.5's reader, scanner and parser, with
//! `_yaml.pyx`'s composer, under the same constructor. That parser is not PyYAML's. It accepts
//! tabs PyYAML refuses (`a: 1\t`), skips a byte order mark at the start of any line, reads its
//! input in 16 KiB chunks (so a refused character late in a long file is reported after an
//! earlier syntax error), moves the final marks to a new line, and words every error its own
//! way. [`Loader::C`] is a port of that C code (`scanner_c.rs`, `parser_c.rs`), so each loader
//! gives Python's values, error classes, messages and marks for the loader it stands for.
//!
//! Integers beyond the signed 64-bit range and strings holding a lone surrogate are refused
//! (`docs/rust-port/differences.md` rows 5 and 3), and [`safe_dump`] writes a date reached twice
//! in full where PyYAML anchors it (the date row there).
//!
//! Loading is compose then construct, so a caller can inspect the node graph before
//! construction, as Sylvia's bounded loaders do, and override the constructor methods its Python
//! loader subclass overrides ([`Hooks`]):
//!
//! ```
//! use sylvia_base::pyyaml::{self, Hooks, Loader};
//! let mut composed = pyyaml::compose("a: [1, 2]\n", Loader::Pure).unwrap();
//! let loaded = pyyaml::construct(&mut composed, Hooks::default()).unwrap();
//! assert_eq!(loaded.to_json().unwrap(), serde_json::json!({"a": [1, 2]}));
//! ```

mod compose;
mod construct;
mod emit;
mod error;
mod parser;
mod parser_c;
mod scanner;
mod scanner_c;
mod value;

pub use compose::{resolve_scalar, Composed, Node, NodeGraph, NodeId, NodeKind, MAP_TAG, SEQ_TAG, STR_TAG};
pub use construct::{construct, Constructor, DocumentHook, FlattenHook, Hooks, MappingHook};
pub use emit::safe_dump;
pub use error::{Mark, MarkedError, MarkedKind, YamlError};
pub use parser::{Event, EventKind};
pub use value::{fresh_float, key_eq, Date, DateTime, Loaded, NotJson, Object, ObjectId, Value, SHARED_NAN};

use compose::Composer;
use parser::Parser;
use parser_c::CParser;
use scanner::{Reader, Scanner};
use scanner_c::CScanner;

/// Which PyYAML loader's parser to behave as.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Loader {
    /// `yaml.SafeLoader`: `yaml.safe_load` and every pure-Python loader.
    Pure,
    /// `yaml.CSafeLoader`: libyaml's parser with the same constructor.
    C,
}

/// The parser behind a loader: PyYAML's own, or libyaml's under `CSafeLoader`.
pub(crate) enum EventSource {
    Pure(Box<Parser>),
    C(Box<CParser>),
}

impl EventSource {
    /// `decode_document_tags`: whether `%TAG` directives must decode (`yaml.parse` converts
    /// every event; the C composer never reads them).
    fn new(
        text: &str,
        stream_name: Option<&str>,
        loader: Loader,
        decode_document_tags: bool,
    ) -> Result<Self, YamlError> {
        Ok(match loader {
            Loader::Pure => Self::Pure(Box::new(Parser::new(Scanner::new(Reader::new(text, stream_name)?)))),
            Loader::C => {
                Self::C(Box::new(CParser::new(CScanner::new(text, stream_name), decode_document_tags)))
            }
        })
    }

    pub(crate) fn peek_event(&mut self) -> Result<Option<&Event>, YamlError> {
        match self {
            Self::Pure(parser) => parser.peek_event(),
            Self::C(parser) => parser.peek_event(),
        }
    }

    pub(crate) fn get_event(&mut self) -> Result<Option<Event>, YamlError> {
        match self {
            Self::Pure(parser) => parser.get_event(),
            Self::C(parser) => parser.get_event(),
        }
    }
}

/// `yaml.parse(text, Loader=...)`: every event of the stream.
pub fn parse(text: &str, loader: Loader) -> Result<Vec<Event>, YamlError> {
    let mut parser = EventSource::new(text, None, loader, true)?;
    let mut events = Vec::new();
    while let Some(event) = parser.get_event()? {
        events.push(event);
    }
    Ok(events)
}

/// `yaml.compose(text, Loader=...)`: the single document's node graph.
pub fn compose(text: &str, loader: Loader) -> Result<Composed, YamlError> {
    compose_named(text, None, loader)
}

fn compose_named(text: &str, stream_name: Option<&str>, loader: Loader) -> Result<Composed, YamlError> {
    Composer::new(EventSource::new(text, stream_name, loader, false)?, loader).single_node()
}

/// `yaml.load(text, Loader=...)` for a loader with the given overrides.
pub fn load(text: &str, loader: Loader, hooks: Hooks) -> Result<Loaded, YamlError> {
    let mut composed = compose(text, loader)?;
    construct(&mut composed, hooks)
}

/// `yaml.load(source, Loader=...)` for a source whose marks carry another name: `bytes` input
/// reads as `"<byte string>"` (`campaign_rules_update.py` passes the stored file's bytes).
/// Pass the bytes decoded as UTF-8; that is exact for valid UTF-8 without a UTF-16 byte order
/// mark, the only bytes Sylvia stores. A text file stream reads differently in PyYAML (in
/// chunks, marks without a snippet) and is not covered here.
pub fn load_named(text: &str, stream_name: &str, loader: Loader, hooks: Hooks) -> Result<Loaded, YamlError> {
    let mut composed = compose_named(text, Some(stream_name), loader)?;
    construct(&mut composed, hooks)
}

/// `yaml.safe_load(text)`.
pub fn safe_load(text: &str) -> Result<Loaded, YamlError> {
    load(text, Loader::Pure, Hooks::default())
}

/// `yaml.safe_load(stream)` for a text file stream, as `config/loader.py` reads the shipped
/// defaults (`with open(path, encoding="utf-8") as fh: yaml.safe_load(fh)`).
///
/// `text` is what `fh.read()` returns, newlines already translated (see [`read_text`]), and
/// `stream_name` is `fh.name`. PyYAML reads the stream 4,096 characters at a time and checks
/// each piece when the scanner first needs it, so a refused character is reported only once the
/// scan reaches its piece; marks carry no snippet.
pub fn safe_load_stream(text: &str, stream_name: &str) -> Result<Loaded, YamlError> {
    let reader = Reader::new_stream(text, stream_name)?;
    let source = EventSource::Pure(Box::new(Parser::new(Scanner::new(reader))));
    let mut composed = Composer::new(source, Loader::Pure).single_node()?;
    construct(&mut composed, Hooks::default())
}

/// The text Python's text mode reads (`open(path, encoding="utf-8").read()` and
/// `Path.read_text(encoding="utf-8")`): UTF-8, with `\r\n` and `\r` read as `\n`.
pub fn read_text(bytes: &[u8]) -> Result<String, std::string::FromUtf8Error> {
    let text = String::from_utf8(bytes.to_vec())?;
    Ok(if text.contains('\r') { text.replace("\r\n", "\n").replace('\r', "\n") } else { text })
}
