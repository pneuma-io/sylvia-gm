//! `agent/_builder_title.py`: optional first-reply title metadata, never a separate provider
//! request.

use sylvia_base::pystr::{is_space, lstrip};

/// `TITLE_LIMIT`, in characters.
pub const TITLE_LIMIT: usize = 56;
/// `TITLE_PREFIX`.
pub const TITLE_PREFIX: &str = "<!-- sylvia-chat-title:";

/// `TITLE_INSTRUCTION`.
pub fn title_instruction() -> &'static str {
    super::texts()["title_instruction"].as_str().unwrap_or_default()
}

/// `" ".join(text.split())`.
fn normalized(text: &str) -> String {
    text.split(is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `opening_title(text)`: an immediate single-line label, long openings shortened between words.
pub fn opening_title(text: &str) -> String {
    let normalized = normalized(text);
    if normalized.chars().count() <= TITLE_LIMIT {
        return normalized;
    }
    let head: String = normalized.chars().take(TITLE_LIMIT - 1).collect();
    let shortened = match head.rfind(' ') {
        Some(index) => &head[..index],
        None => head.as_str(),
    };
    format!("{}…", shortened.trim_end_matches(['.', ',', ';', ':']))
}

/// `FirstReplyTitle`: strips only the optional leading title comment, including one split
/// across stream chunks.
#[derive(Debug, Default, Clone)]
pub struct FirstReplyTitle {
    /// The title the reply supplied, when it was well formed.
    pub title: Option<String>,
    prefix: String,
    finished: bool,
    trim: bool,
}

impl FirstReplyTitle {
    pub fn new() -> Self {
        Self::default()
    }

    /// `feed(text, final=...)`: prose released promptly; malformed or absent metadata never
    /// triggers a retry.
    pub fn feed(&mut self, text: &str, last: bool) -> String {
        if text.is_empty() && self.prefix.is_empty() {
            return String::new();
        }
        if self.finished {
            if self.trim {
                let text = text.trim_start_matches(['\r', '\n', ' ']);
                self.trim = text.is_empty();
                return text.to_owned();
            }
            return text.to_owned();
        }
        self.prefix.push_str(text);
        let candidate = lstrip(&self.prefix).to_owned();
        if let Some(rest) = candidate.strip_prefix(TITLE_PREFIX) {
            if let Some(end) = rest.find("-->") {
                let title = normalized(&rest[..end]);
                let length = title.chars().count();
                if 0 < length && length <= TITLE_LIMIT && !title.contains(['<', '>']) {
                    self.title = Some(title);
                }
                self.finished = true;
                self.trim = true;
                self.prefix.clear();
                let remainder = rest[end + 3..].to_owned();
                return self.feed(&remainder, false);
            }
            if !last && candidate.chars().count() <= 256 {
                return String::new();
            }
            // Drop only an unfinished leading metadata line; keep any following answer.
            let remainder = candidate.split_once('\n').map(|(_, rest)| rest.to_owned()).unwrap_or_default();
            self.finished = true;
            self.prefix.clear();
            return remainder;
        }
        if !last && self.prefix.chars().count() <= 256 && TITLE_PREFIX.starts_with(candidate.as_str()) {
            return String::new();
        }
        self.finished = true;
        std::mem::take(&mut self.prefix)
    }
}
