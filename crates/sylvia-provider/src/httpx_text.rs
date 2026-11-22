//! httpx 0.28.1's reading of a response as text, and `urllib.parse.quote`, shared by the
//! validation probe (`config::validation`, lane 4) and the Gemini transport (`gemini`, lane 5).
//!
//! - [`TextDecoder`] is httpx's incremental text decoder (`codecs.getincrementaldecoder(...)`
//!   with `errors="replace"`), for the encoding `Response.encoding` names (differences row 35
//!   for charsets other than UTF-8, ASCII and Latin-1).
//! - [`LineDecoder`] is httpx's line decoder for `aiter_lines()`: lines split as
//!   `str.splitlines()` splits them, with a trailing carriage return held back for the next piece.
//! - [`quote`] is `urllib.parse.quote(text, safe="")`.
//!
//! Moved unchanged from `config::validation` (lane 4, 23 Nov 2026); `validation_vectors` is the
//! proof for all five.

/// `str.splitlines()`'s line boundaries.
pub(crate) fn is_line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\u{0b}'
            | '\u{0c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// `text.splitlines()`.
pub(crate) fn splitlines(text: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    let mut characters = text.chars().peekable();
    while let Some(c) = characters.next() {
        if is_line_break(c) {
            if c == '\r' && characters.peek() == Some(&'\n') {
                characters.next();
            }
            lines.push(std::mem::take(&mut current));
        } else {
            current.push(c);
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// httpx's `LineDecoder`.
#[derive(Default)]
pub(crate) struct LineDecoder {
    buffer: Vec<String>,
    trailing_cr: bool,
}

impl LineDecoder {
    pub(crate) fn decode(&mut self, text: &str) -> Vec<String> {
        let mut text = text.to_owned();
        if self.trailing_cr {
            text.insert(0, '\r');
            self.trailing_cr = false;
        }
        if text.ends_with('\r') {
            self.trailing_cr = true;
            text.pop();
        }
        if text.is_empty() {
            return Vec::new();
        }
        let trailing_newline = text.chars().next_back().is_some_and(is_line_break);
        let mut lines = splitlines(&text);
        if lines.len() == 1 && !trailing_newline {
            self.buffer.push(lines.remove(0));
            return Vec::new();
        }
        if !self.buffer.is_empty() {
            let first = format!("{}{}", self.buffer.concat(), lines.first().cloned().unwrap_or_default());
            if lines.is_empty() {
                lines.push(first);
            } else {
                lines[0] = first;
            }
            self.buffer.clear();
        }
        if !trailing_newline {
            self.buffer = vec![lines.pop().unwrap_or_default()];
        }
        lines
    }

    pub(crate) fn flush(&mut self) -> Vec<String> {
        if self.buffer.is_empty() && !self.trailing_cr {
            return Vec::new();
        }
        let line = self.buffer.concat();
        self.buffer.clear();
        self.trailing_cr = false;
        vec![line]
    }
}

/// The incremental decoder for a response's text (`codecs.getincrementaldecoder(...)(errors=
/// "replace")`): UTF-8 by default and for `utf-8`/`utf8`, byte by byte for `ascii` and Latin-1.
pub(crate) enum TextDecoder {
    Utf8(Vec<u8>),
    Ascii,
    Latin1,
}

impl TextDecoder {
    /// `Response.encoding`: the `charset` of `Content-Type`, else UTF-8.
    pub(crate) fn for_content_type(content_type: Option<&str>) -> Self {
        let charset = content_type.and_then(|value| {
            value.split(';').skip(1).find_map(|parameter| {
                let (name, value) = parameter.split_once('=')?;
                (name.trim().eq_ignore_ascii_case("charset"))
                    .then(|| value.trim().trim_matches('"').to_ascii_lowercase())
            })
        });
        match charset.as_deref() {
            Some("ascii" | "us-ascii") => Self::Ascii,
            Some("latin-1" | "latin1" | "iso-8859-1" | "iso8859-1" | "l1") => Self::Latin1,
            _ => Self::Utf8(Vec::new()),
        }
    }

    pub(crate) fn decode(&mut self, data: &[u8], last: bool) -> String {
        match self {
            Self::Ascii => {
                data.iter().map(|byte| if byte.is_ascii() { char::from(*byte) } else { '\u{FFFD}' }).collect()
            }
            Self::Latin1 => data.iter().map(|byte| char::from(*byte)).collect(),
            Self::Utf8(pending) => {
                pending.extend_from_slice(data);
                let mut out = String::new();
                loop {
                    match std::str::from_utf8(pending) {
                        Ok(valid) => {
                            out.push_str(valid);
                            pending.clear();
                            break;
                        }
                        Err(error) => {
                            let good = error.valid_up_to();
                            out.push_str(std::str::from_utf8(&pending[..good]).expect("valid prefix"));
                            match error.error_len() {
                                Some(bad) => {
                                    out.push('\u{FFFD}');
                                    pending.drain(..good + bad);
                                }
                                None if last => {
                                    out.push('\u{FFFD}');
                                    pending.clear();
                                    break;
                                }
                                None => {
                                    pending.drain(..good);
                                    break;
                                }
                            }
                        }
                    }
                }
                out
            }
        }
    }
}

/// `urllib.parse.quote(text, safe="")`.
pub(crate) fn quote(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(char::from(byte)),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}
