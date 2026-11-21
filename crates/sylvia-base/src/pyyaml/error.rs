//! PyYAML's marks and errors (`yaml/error.py`, `yaml/reader.py`), with their exact text.

use crate::pystr::repr;
use std::rc::Rc;

/// A position in the input (`yaml.error.Mark`). `line` and `column` count from 0; `index` counts
/// characters from the start of the stream.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mark {
    /// `"<unicode string>"` for text, or the file name for a file stream.
    pub name: Rc<str>,
    pub index: usize,
    pub line: usize,
    pub column: usize,
    /// The input, for the source snippet a pure-Python mark shows. `None` for file streams and
    /// for the C loader's marks, which show no snippet.
    pub(crate) buffer: Option<Rc<[char]>>,
}

impl Mark {
    /// `Mark.get_snippet()`: the line around the mark with a caret under it.
    fn snippet(&self, indent: usize, max_length: usize) -> Option<String> {
        let buffer = self.buffer.as_ref()?;
        let breaks = ['\0', '\r', '\n', '\u{85}', '\u{2028}', '\u{2029}'];
        let pointer = self.index;
        let half = max_length as f64 / 2.0 - 1.0;
        let mut head = "";
        let mut start = pointer;
        while start > 0 && !breaks.contains(&buffer[start - 1]) {
            start -= 1;
            if (pointer - start) as f64 > half {
                head = " ... ";
                start += 5;
                break;
            }
        }
        let mut tail = "";
        let mut end = pointer;
        while end < buffer.len() && !breaks.contains(&buffer[end]) {
            end += 1;
            if (end - pointer) as f64 > half {
                tail = " ... ";
                end -= 5;
                break;
            }
        }
        let snippet: String = buffer[start..end].iter().collect();
        let caret = " ".repeat(indent + pointer - start + head.chars().count());
        Some(format!("{}{head}{snippet}{tail}\n{caret}^", " ".repeat(indent)))
    }
}

impl std::fmt::Display for Mark {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "  in \"{}\", line {}, column {}", self.name, self.line + 1, self.column + 1)?;
        if let Some(snippet) = self.snippet(4, 75) {
            write!(f, ":\n{snippet}")?;
        }
        Ok(())
    }
}

/// Which PyYAML error class.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarkedKind {
    Scanner,
    Parser,
    Composer,
    Constructor,
}

impl MarkedKind {
    /// The Python class name.
    pub fn class_name(self) -> &'static str {
        match self {
            Self::Scanner => "ScannerError",
            Self::Parser => "ParserError",
            Self::Composer => "ComposerError",
            Self::Constructor => "ConstructorError",
        }
    }
}

/// A `MarkedYAMLError` and its pieces.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarkedError {
    pub kind: MarkedKind,
    pub context: Option<String>,
    pub context_mark: Option<Mark>,
    pub problem: Option<String>,
    pub problem_mark: Option<Mark>,
    pub note: Option<String>,
}

impl MarkedError {
    pub(crate) fn new(
        kind: MarkedKind,
        context: Option<String>,
        context_mark: Option<Mark>,
        problem: impl Into<String>,
        problem_mark: Option<Mark>,
    ) -> Self {
        Self { kind, context, context_mark, problem: Some(problem.into()), problem_mark, note: None }
    }
}

impl std::fmt::Display for MarkedError {
    /// `MarkedYAMLError.__str__`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut lines: Vec<String> = Vec::new();
        if let Some(context) = &self.context {
            lines.push(context.clone());
        }
        if let Some(context_mark) = &self.context_mark {
            let same = match (&self.problem, &self.problem_mark) {
                (Some(_), Some(problem_mark)) => {
                    context_mark.name == problem_mark.name
                        && context_mark.line == problem_mark.line
                        && context_mark.column == problem_mark.column
                }
                _ => false,
            };
            if !same {
                lines.push(context_mark.to_string());
            }
        }
        if let Some(problem) = &self.problem {
            lines.push(problem.clone());
        }
        if let Some(problem_mark) = &self.problem_mark {
            lines.push(problem_mark.to_string());
        }
        if let Some(note) = &self.note {
            lines.push(note.clone());
        }
        f.write_str(&lines.join("\n"))
    }
}

/// Everything loading or dumping can raise.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum YamlError {
    /// `ReaderError`: a character YAML does not allow.
    Reader { name: Rc<str>, position: usize, character: u32, reason: &'static str },
    /// `ScannerError`, `ParserError`, `ComposerError`, `ConstructorError`.
    Marked(MarkedError),
    /// `RepresenterError` and `EmitterError` while dumping.
    Dump { class: &'static str, message: String },
    /// A Python built-in exception PyYAML lets through (`ValueError` from `int()`,
    /// `KeyError` from the bool table, `AttributeError` for a malformed `!!timestamp`, ...), or
    /// one raised by a caller's loader hook. `class` is the Python class name.
    Python { class: &'static str, message: String },
}

impl YamlError {
    /// Whether Python would catch it as `yaml.YAMLError`.
    pub fn is_yaml_error(&self) -> bool {
        !matches!(self, Self::Python { .. })
    }

    /// The Python class name.
    pub fn class_name(&self) -> &'static str {
        match self {
            Self::Reader { .. } => "ReaderError",
            Self::Marked(error) => error.kind.class_name(),
            Self::Dump { class, .. } | Self::Python { class, .. } => class,
        }
    }

    /// The marked error's pieces, if it is one.
    pub fn marked(&self) -> Option<&MarkedError> {
        match self {
            Self::Marked(error) => Some(error),
            _ => None,
        }
    }

    pub(crate) fn python(class: &'static str, message: impl Into<String>) -> Self {
        Self::Python { class, message: message.into() }
    }
}

impl std::fmt::Display for YamlError {
    /// `str(error)`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Reader { name, position, character, reason } => write!(
                f,
                "unacceptable character #x{character:04x}: {reason}\n  in \"{name}\", position {position}"
            ),
            Self::Marked(error) => error.fmt(f),
            Self::Dump { message, .. } | Self::Python { message, .. } => f.write_str(message),
        }
    }
}

impl std::error::Error for YamlError {}

/// `%r` of one character, as the scanner's messages show it.
pub(crate) fn char_repr(character: char) -> String {
    repr(&character.to_string())
}
