//! The owner's bounded line protocol: `_read_message` and `_write_message` in Python.

use std::io::{BufRead, Read, Write};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// `_MAX_MESSAGE_BYTES`.
pub const MAX_MESSAGE_BYTES: usize = 67_108_864;

/// What reading one line produced.
#[derive(Debug)]
pub enum Frame {
    /// A JSON object.
    Message(JsonMap),
    /// Input ended cleanly between messages.
    End,
    /// `OwnerProtocolError`: the line was read but is not a usable message. The owner answers
    /// with `invalid_owner_request` and keeps running.
    Invalid(&'static str),
    /// `OwnerFramingError` or a decoding failure: the stream itself can no longer be trusted.
    /// Python lets this end the owner.
    Fatal(&'static str),
    /// A raw line for the recovery-only owner, which reads its own bounded requests
    /// (`recovery_service._read`): the line, the end of input, or a stream that is not strict
    /// UTF-8.
    Raw(crate::desktop_update::recovery_service::RecoveryLine),
}

/// `stream.readline(65537)` on the recovery-only owner's text input: up to and including the
/// next newline, decoded as strict UTF-8. Python stops at 65,537 characters; a line that long
/// is refused afterwards either way, so reading stops at the newline or at four bytes per
/// character past that bound.
pub fn read_raw_line(input: &mut impl BufRead) -> crate::desktop_update::recovery_service::RecoveryLine {
    use crate::desktop_update::recovery_service::RecoveryLine;
    let mut line = Vec::new();
    let mut limited = input.take(65_537 * 4 + 1);
    match limited.read_until(b'\n', &mut line) {
        Ok(0) => RecoveryLine::End,
        Ok(_) => match String::from_utf8(line) {
            Ok(text) => RecoveryLine::Line(text),
            Err(_) => RecoveryLine::Broken,
        },
        Err(_) => RecoveryLine::Broken,
    }
}

/// Read one message. Mirrors `_read_message`, including its limits.
pub fn read_message(input: &mut impl BufRead) -> Frame {
    let mut line = Vec::new();
    // One byte past the limit is enough to tell "too long" from "exactly at the limit".
    let mut limited = input.take(MAX_MESSAGE_BYTES as u64 + 1);
    match limited.read_until(b'\n', &mut line) {
        Ok(0) => return Frame::End,
        Ok(_) => {}
        Err(_) => return Frame::Fatal("Owner input could not be read"),
    }
    if line.len() > MAX_MESSAGE_BYTES || line.last() != Some(&b'\n') {
        return Frame::Fatal("Owner message exceeds the bounded line protocol");
    }
    // Python reads stdin as strict UTF-8; a bad byte raises outside the protocol errors.
    let Ok(text) = std::str::from_utf8(&line) else {
        return Frame::Fatal("Owner input is not valid UTF-8");
    };
    match serde_json::from_str::<Json>(text) {
        Ok(Json::Object(message)) => Frame::Message(message),
        Ok(_) => Frame::Invalid("Owner message must be a JSON object"),
        Err(_) => Frame::Invalid("Owner message is not valid JSON"),
    }
}

/// Write one message: compact, ASCII-only, newline-terminated, flushed. Mirrors
/// `_write_message` byte for byte.
pub fn write_message(output: &mut impl Write, payload: &Json) -> std::io::Result<()> {
    let mut line = pyjson::dumps(payload, Dump::WIRE);
    line.push('\n');
    output.write_all(line.as_bytes())?;
    output.flush()
}

/// `{"id": ..., "ok": true, "result": ...}`.
pub fn ok_reply(id: &str, result: Json) -> Json {
    let mut reply = JsonMap::new();
    reply.insert("id".into(), Json::String(id.to_owned()));
    reply.insert("ok".into(), Json::Bool(true));
    reply.insert("result".into(), result);
    Json::Object(reply)
}

/// `{"id": ..., "ok": false, "error": {...}}`. `id` is `null` when the request had no usable one.
pub fn error_reply(id: Option<&str>, error: Json) -> Json {
    let mut reply = JsonMap::new();
    reply.insert("id".into(), id.map_or(Json::Null, |id| Json::String(id.to_owned())));
    reply.insert("ok".into(), Json::Bool(false));
    reply.insert("error".into(), error);
    Json::Object(reply)
}

/// The short error object the run loop writes itself: only `code` and `message`.
pub fn plain_error(code: &str, message: &str) -> Json {
    let mut error = JsonMap::new();
    error.insert("code".into(), Json::String(code.to_owned()));
    error.insert("message".into(), Json::String(message.to_owned()));
    Json::Object(error)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn reads_objects_and_classifies_bad_lines() {
        let mut input = Cursor::new(b"{\"id\":\"1\"}\n[1]\nnot json\n".to_vec());
        assert!(matches!(read_message(&mut input), Frame::Message(m) if m["id"] == "1"));
        assert!(matches!(read_message(&mut input), Frame::Invalid("Owner message must be a JSON object")));
        assert!(matches!(read_message(&mut input), Frame::Invalid("Owner message is not valid JSON")));
        assert!(matches!(read_message(&mut input), Frame::End));
    }

    #[test]
    fn a_line_without_newline_is_a_framing_error() {
        let mut input = Cursor::new(b"{\"id\":\"1\"}".to_vec());
        assert!(matches!(read_message(&mut input), Frame::Fatal(_)));
    }

    #[test]
    fn writes_ascii_compact_lines() {
        let mut out = Vec::new();
        let reply = ok_reply("r1", serde_json::json!({"name": "R\u{e8}gles \u{1f3b2}", "n": 1.0}));
        write_message(&mut out, &reply).unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "{\"id\":\"r1\",\"ok\":true,\"result\":{\"name\":\"R\\u00e8gles \\ud83c\\udfb2\",\"n\":1.0}}\n"
        );
    }
}
