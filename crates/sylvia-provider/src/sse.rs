//! Server-sent events as the OpenAI SDK reads them (`openai/_streaming.py`), plus Sylvia's
//! `DoneTrackingStream` (`provider/_native_output.py`), which watches the raw bytes for the
//! `[DONE]` sentinel the SDK hides.
//!
//! Bytes arrive in whatever pieces the network delivers. The decoder keeps the SDK's exact
//! rules: a piece is split into lines keeping their endings, lines accumulate until the data
//! ends with a blank line (`\r\r`, `\n\n` or `\r\n\r\n`), and each such block is decoded line by
//! line per the SSE specification. Data left without a closing blank line at the end of the
//! stream yields no event.

use crate::pyobj::Incidental;
use sylvia_base::Json;

/// One decoded event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerSentEvent {
    pub event: Option<String>,
    pub data: String,
    pub id: Option<String>,
    pub retry: Option<i64>,
}

/// `bytes.splitlines(keepends=True)`: splits after `\n`, `\r` or `\r\n`.
fn split_lines_keep(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < data.len() {
        match data[index] {
            b'\n' => {
                lines.push(&data[start..=index]);
                start = index + 1;
            }
            b'\r' => {
                let end = if data.get(index + 1) == Some(&b'\n') { index + 1 } else { index };
                lines.push(&data[start..=end]);
                index = end;
                start = end + 1;
            }
            _ => {}
        }
        index += 1;
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}

fn strip_line_ending(line: &[u8]) -> &[u8] {
    if let Some(rest) = line.strip_suffix(b"\r\n") {
        rest
    } else if let Some(rest) = line.strip_suffix(b"\n").or_else(|| line.strip_suffix(b"\r")) {
        rest
    } else {
        line
    }
}

/// `SSEDecoder` with `_aiter_chunks`.
#[derive(Debug, Default)]
pub struct SseDecoder {
    pending: Vec<u8>,
    event: Option<String>,
    data: Vec<String>,
    last_event_id: Option<String>,
    retry: Option<i64>,
}

impl SseDecoder {
    /// A fresh decoder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed one piece of bytes as received; returns the events completed by it.
    pub fn feed(&mut self, piece: &[u8]) -> Result<Vec<ServerSentEvent>, Incidental> {
        let mut events = Vec::new();
        for line in split_lines_keep(piece) {
            self.pending.extend_from_slice(line);
            if self.pending.ends_with(b"\r\r")
                || self.pending.ends_with(b"\n\n")
                || self.pending.ends_with(b"\r\n\r\n")
            {
                let block = std::mem::take(&mut self.pending);
                self.decode_block(&block, &mut events)?;
            }
        }
        Ok(events)
    }

    /// The end of the byte stream: a final block without its blank line is still decoded line by
    /// line, which completes an event only if it contains an empty line.
    pub fn finish(&mut self) -> Result<Vec<ServerSentEvent>, Incidental> {
        let mut events = Vec::new();
        if !self.pending.is_empty() {
            let block = std::mem::take(&mut self.pending);
            self.decode_block(&block, &mut events)?;
        }
        Ok(events)
    }

    fn decode_block(&mut self, block: &[u8], events: &mut Vec<ServerSentEvent>) -> Result<(), Incidental> {
        // chunk.splitlines(): the same separators, endings removed.
        for raw in split_lines_keep(block) {
            let line = std::str::from_utf8(strip_line_ending(raw))
                .map_err(|error| Incidental(format!("'utf-8' codec can't decode bytes: {error}")))?;
            if let Some(event) = self.decode(line) {
                events.push(event);
            }
        }
        Ok(())
    }

    /// `SSEDecoder.decode(line)`.
    fn decode(&mut self, line: &str) -> Option<ServerSentEvent> {
        if line.is_empty() {
            if self.event.as_deref().unwrap_or("").is_empty()
                && self.data.is_empty()
                && self.last_event_id.as_deref().unwrap_or("").is_empty()
                && self.retry.is_none()
            {
                return None;
            }
            let event = ServerSentEvent {
                event: self.event.take().filter(|name| !name.is_empty()),
                data: std::mem::take(&mut self.data).join("\n"),
                id: self.last_event_id.clone(),
                retry: self.retry.take(),
            };
            return Some(event);
        }
        if line.starts_with(':') {
            return None;
        }
        let (field, value) = match line.split_once(':') {
            Some((field, value)) => (field, value.strip_prefix(' ').unwrap_or(value)),
            None => (line, ""),
        };
        match field {
            "event" => self.event = Some(value.to_owned()),
            "data" => self.data.push(value.to_owned()),
            "id" => {
                if !value.contains('\0') {
                    self.last_event_id = Some(value.to_owned());
                }
            }
            "retry" => {
                if let Some(retry) = crate::pyobj::parse_python_int(value) {
                    self.retry = Some(retry);
                }
            }
            _ => {}
        }
        None
    }
}

/// What the SDK's `AsyncStream` makes of one event.
#[derive(Debug, Clone, PartialEq)]
pub enum StreamItem {
    /// `[DONE]`: iteration stops here.
    Done,
    /// A parsed chunk for the transport.
    Chunk(Json),
}

/// `AsyncStream.__stream__` for one event: `[DONE]` stops, an `error` member raises `APIError`,
/// anything else is parsed as JSON (a parse failure raises `JSONDecodeError`).
pub fn stream_item(event: &ServerSentEvent) -> Result<StreamItem, Incidental> {
    if event.data.starts_with("[DONE]") {
        return Ok(StreamItem::Done);
    }
    let data = sylvia_base::pyjson::loads(&event.data).map_err(|error| Incidental(error.message))?;
    if let Some(error) = data.as_object().and_then(|map| map.get("error")) {
        if crate::pyobj::truthy(error) {
            let message = error
                .as_object()
                .and_then(|map| map.get("message"))
                .and_then(Json::as_str)
                .filter(|message| !message.is_empty())
                .unwrap_or("An error occurred during streaming");
            return Err(Incidental(message.to_owned()));
        }
    }
    Ok(StreamItem::Chunk(data))
}

/// `DoneTrackingStream.observe`: whether the raw bytes so far contained the `[DONE]` sentinel.
#[derive(Debug, Default, Clone)]
pub struct DoneTracker {
    tail: Vec<u8>,
    /// Set once `data: [DONE]` or `data:[DONE]` and a blank line were seen.
    pub done: bool,
}

impl DoneTracker {
    /// Watch one received piece.
    pub fn observe(&mut self, piece: &[u8]) {
        let mut combined = std::mem::take(&mut self.tail);
        combined.extend_from_slice(piece);
        let normalized = replace_crlf(&combined);
        if contains(&normalized, b"data: [DONE]\n\n") || contains(&normalized, b"data:[DONE]\n\n") {
            self.done = true;
        }
        let keep = combined.len().saturating_sub(32);
        self.tail = combined[keep..].to_vec();
    }
}

fn replace_crlf(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut index = 0;
    while index < data.len() {
        if data[index] == b'\r' && data.get(index + 1) == Some(&b'\n') {
            out.push(b'\n');
            index += 2;
        } else {
            out.push(data[index]);
            index += 1;
        }
    }
    out
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack.windows(needle.len()).any(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_like_python_bytes_splitlines() {
        assert_eq!(split_lines_keep(b"a\nb\r\nc\rd"), vec![&b"a\n"[..], b"b\r\n", b"c\r", b"d"]);
        assert_eq!(split_lines_keep(b"\n\n"), vec![&b"\n"[..], b"\n"]);
    }

    #[test]
    fn decodes_events_across_pieces() {
        let mut decoder = SseDecoder::new();
        assert!(decoder.feed(b"data: {\"a\"").unwrap().is_empty());
        let events = decoder.feed(b":1}\n\ndata: [DONE]\n\n").unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].data, "{\"a\":1}");
        assert_eq!(stream_item(&events[1]).unwrap(), StreamItem::Done);
    }
}
