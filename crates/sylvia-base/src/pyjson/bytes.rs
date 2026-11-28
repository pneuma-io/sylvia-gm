//! `json.loads(data)` for `bytes`: `data.decode(detect_encoding(data), "surrogatepass")`, then
//! the decoder, without the BOM check `json.loads` applies to `str`.
//!
//! `surrogatepass` lets lone surrogates through, which a Rust `String` cannot hold. They decode
//! here as U+FFFD, one character as in Python and inert to the JSON scanner, so a JSON error keeps
//! Python's text and position. Text that then parses held a lone surrogate in a string, which is
//! differences row 3, and is refused with this module's own "Invalid JSON" message. CPython
//! decodes all of the input first, so a later decode error still wins.

use super::decode_error;
use crate::{pystr, CodedError, Json};

/// `json.detect_encoding(b)`.
pub fn detect_encoding(data: &[u8]) -> &'static str {
    if data.starts_with(&[0, 0, 0xFE, 0xFF]) || data.starts_with(&[0xFF, 0xFE, 0, 0]) {
        return "utf-32";
    }
    if data.starts_with(&[0xFE, 0xFF]) || data.starts_with(&[0xFF, 0xFE]) {
        return "utf-16";
    }
    if data.starts_with(&[0xEF, 0xBB, 0xBF]) {
        return "utf-8-sig";
    }
    if data.len() >= 4 {
        if data[0] == 0 {
            return if data[1] != 0 { "utf-16-be" } else { "utf-32-be" };
        }
        if data[1] == 0 {
            return if data[2] != 0 || data[3] != 0 { "utf-16-le" } else { "utf-32-le" };
        }
    } else if data.len() == 2 {
        if data[0] == 0 {
            return "utf-16-be";
        }
        if data[1] == 0 {
            return "utf-16-le";
        }
    }
    "utf-8"
}

/// The decoded text and whether it held a lone surrogate, or `str(UnicodeDecodeError)`.
type Decoded = Result<(String, bool), String>;

fn position_text(name: &str, data: &[u8], start: usize, end: usize, reason: &str) -> String {
    if end - start == 1 {
        format!("'{name}' codec can't decode byte 0x{:02x} in position {start}: {reason}", data[start])
    } else {
        format!("'{name}' codec can't decode bytes in position {start}-{}: {reason}", end - 1)
    }
}

/// A UTF-8 error message for a tail starting at `start`, with its positions made absolute.
fn shift(message: &str, start: usize) -> String {
    let (head, rest) = message.split_once(" in position ").expect("position");
    let (positions, reason) = rest.split_once(": ").expect("reason");
    let moved = match positions.split_once('-') {
        Some((first, last)) => {
            format!("{}-{}", first.parse::<usize>().unwrap() + start, last.parse::<usize>().unwrap() + start)
        }
        None => (positions.parse::<usize>().unwrap() + start).to_string(),
    };
    format!("{head} in position {moved}: {reason}")
}

/// UTF-8 with `surrogatepass`: the three-byte encodings of U+D800..U+DFFF pass.
fn utf8(data: &[u8]) -> Decoded {
    let mut text = String::new();
    let mut surrogate = false;
    let mut offset = 0;
    while offset < data.len() {
        match std::str::from_utf8(&data[offset..]) {
            Ok(rest) => {
                text.push_str(rest);
                break;
            }
            Err(error) => {
                let start = offset + error.valid_up_to();
                text.push_str(std::str::from_utf8(&data[offset..start]).expect("valid prefix"));
                let encoded_surrogate = data.len() >= start + 3
                    && data[start] == 0xED
                    && (0xA0..=0xBF).contains(&data[start + 1])
                    && (0x80..=0xBF).contains(&data[start + 2]);
                if !encoded_surrogate {
                    let message = pystr::utf8_decode(&data[start..]).expect_err("the tail fails to decode");
                    return Err(shift(&message, start));
                }
                surrogate = true;
                text.push(char::REPLACEMENT_CHARACTER);
                offset = start + 3;
            }
        }
    }
    Ok((text, surrogate))
}

/// UTF-16 with `surrogatepass`, from `start` (after any BOM); positions count from the data start.
fn utf16(data: &[u8], start: usize, little: bool, name: &str) -> Decoded {
    let unit = |index: usize| {
        let pair = [data[index], data[index + 1]];
        if little {
            u16::from_le_bytes(pair)
        } else {
            u16::from_be_bytes(pair)
        }
    };
    let mut text = String::new();
    let mut surrogate = false;
    let mut index = start;
    while index + 1 < data.len() {
        let first = unit(index);
        if (0xD800..0xDC00).contains(&first) && index + 3 < data.len() {
            let second = unit(index + 2);
            if (0xDC00..0xE000).contains(&second) {
                let code = 0x10000 + ((u32::from(first) - 0xD800) << 10) + (u32::from(second) - 0xDC00);
                text.push(char::from_u32(code).expect("a paired surrogate"));
                index += 4;
                continue;
            }
        }
        match char::from_u32(u32::from(first)) {
            Some(character) => text.push(character),
            None => {
                surrogate = true;
                text.push(char::REPLACEMENT_CHARACTER);
            }
        }
        index += 2;
    }
    if index < data.len() {
        return Err(position_text(name, data, index, data.len(), "truncated data"));
    }
    Ok((text, surrogate))
}

/// UTF-32 with `surrogatepass`, from `start` (after any BOM).
fn utf32(data: &[u8], start: usize, little: bool, name: &str) -> Decoded {
    let mut text = String::new();
    let mut surrogate = false;
    let mut index = start;
    while index + 3 < data.len() {
        let quad = [data[index], data[index + 1], data[index + 2], data[index + 3]];
        let code = if little { u32::from_le_bytes(quad) } else { u32::from_be_bytes(quad) };
        if code > 0x10FFFF {
            return Err(position_text(name, data, index, index + 4, "code point not in range(0x110000)"));
        }
        match char::from_u32(code) {
            Some(character) => text.push(character),
            None => {
                surrogate = true;
                text.push(char::REPLACEMENT_CHARACTER);
            }
        }
        index += 4;
    }
    if index < data.len() {
        return Err(position_text(name, data, index, data.len(), "truncated data"));
    }
    Ok((text, surrogate))
}

/// `data.decode(encoding, "surrogatepass")` for the encodings `detect_encoding` names.
fn decode(data: &[u8], encoding: &str) -> Decoded {
    match encoding {
        "utf-8" => utf8(data),
        // `utf_8_sig.decode` decodes the rest as UTF-8, so positions are after the BOM.
        "utf-8-sig" => utf8(&data[3..]),
        // After its BOM, the generic codec reports the byte order it read.
        "utf-16" if data[0] == 0xFF => utf16(data, 2, true, "utf-16-le"),
        "utf-16" => utf16(data, 2, false, "utf-16-be"),
        "utf-16-le" => utf16(data, 0, true, "utf-16-le"),
        "utf-16-be" => utf16(data, 0, false, "utf-16-be"),
        "utf-32" if data[0] == 0xFF => utf32(data, 4, true, "utf-32-le"),
        "utf-32" => utf32(data, 4, false, "utf-32-be"),
        "utf-32-le" => utf32(data, 0, true, "utf-32-le"),
        "utf-32-be" => utf32(data, 0, false, "utf-32-be"),
        other => unreachable!("detect_encoding never names {other}"),
    }
}

/// `data.decode(json.detect_encoding(data), "surrogatepass")`: the text `json.loads(data)`
/// reads, and whether a lone surrogate in it became U+FFFD; or the `UnicodeDecodeError` message.
pub fn decode_detected(data: &[u8]) -> Result<(String, bool), String> {
    decode(data, detect_encoding(data))
}

/// `json.loads(data)` for `bytes`: a `UnicodeDecodeError`, Python's `JSONDecodeError`, or the
/// value. Text Python reads and [`super::loads`] refuses keeps that function's message.
pub fn loads_bytes(data: &[u8]) -> Result<Json, CodedError> {
    let (text, surrogate) = decode(data, detect_encoding(data))
        .map_err(|message| CodedError::rejected(message).with_error_type("UnicodeDecodeError"))?;
    // `json.loads` checks for a leading BOM only on `str` input; for bytes the decoder sees it.
    if text.starts_with('\u{feff}') {
        return Err(CodedError::rejected(decode_error::message(&text, "Expecting value", 0))
            .with_error_type("JSONDecodeError"));
    }
    let value = super::loads(&text)?;
    if surrogate {
        return Err(CodedError::rejected("Invalid JSON: lone surrogate in the decoded text"));
    }
    Ok(value)
}
