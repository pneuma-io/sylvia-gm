//! Base64 as CPython 3.12 does it: `binascii.a2b_base64` and `binascii.b2a_base64`
//! (`Modules/binascii.c`), and the `base64` functions Sylvia calls on top of them.
//!
//! - `base64.decodebytes` (PyYAML's `!!binary`): [`a2b_base64`] without strict mode, which
//!   skips characters outside the alphabet and stops at the padding.
//! - `base64.b64decode(s, validate=True)` (saved credential envelopes): [`b64decode_validate`],
//!   strict mode, which refuses any character outside the alphabet and any misplaced padding.
//! - `base64.b64encode` and `base64.encodebytes`: [`b64encode`] and [`encodebytes`].
//!
//! Errors are `binascii.Error` (a `ValueError`); the `String` is its message.

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const PAD: u8 = b'=';

fn value_of(byte: u8) -> Option<u32> {
    ALPHABET.iter().position(|c| *c == byte).map(|value| value as u32)
}

/// `binascii.a2b_base64(data, strict_mode=strict)`.
pub fn a2b_base64(data: &[u8], strict: bool) -> Result<Vec<u8>, String> {
    if strict && data.first() == Some(&PAD) {
        return Err("Leading padding not allowed".into());
    }
    let mut out = Vec::new();
    let mut quad_pos = 0;
    let mut left_char: u32 = 0;
    let mut pads = 0;
    let mut padding_started = false;
    for (index, &byte) in data.iter().enumerate() {
        if byte == PAD {
            padding_started = true;
            if strict && quad_pos == 0 {
                return Err("Excess padding not allowed".into());
            }
            if quad_pos >= 2 {
                pads += 1;
                if quad_pos + pads >= 4 {
                    // A complete pad sequence ends the input; strict mode refuses anything after it.
                    if strict && index + 1 < data.len() {
                        return Err("Excess data after padding".into());
                    }
                    return Ok(out);
                }
            }
            continue;
        }
        let Some(value) = value_of(byte) else {
            if strict {
                return Err("Only base64 data is allowed".into());
            }
            continue;
        };
        if strict && padding_started {
            return Err("Discontinuous padding not allowed".into());
        }
        pads = 0;
        match quad_pos {
            0 => {
                quad_pos = 1;
                left_char = value;
            }
            1 => {
                quad_pos = 2;
                out.push(((left_char << 2) | (value >> 4)) as u8);
                left_char = value & 0x0F;
            }
            2 => {
                quad_pos = 3;
                out.push(((left_char << 4) | (value >> 2)) as u8);
                left_char = value & 0x03;
            }
            _ => {
                quad_pos = 0;
                out.push(((left_char << 6) | value) as u8);
                left_char = 0;
            }
        }
    }
    match quad_pos {
        0 => Ok(out),
        // binascii counts the data characters as (bytes written) / 3 * 4 + 1.
        1 => Err(format!(
            "Invalid base64-encoded string: number of data characters ({}) cannot be 1 more than a multiple of 4",
            out.len() / 3 * 4 + 1
        )),
        _ => Err("Incorrect padding".into()),
    }
}

/// `binascii.b2a_base64(data, newline=newline)`.
pub fn b2a_base64(data: &[u8], newline: bool) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4 + 1);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for (i, shift) in [18, 12, 6, 0].into_iter().enumerate() {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[((n >> shift) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    if newline {
        out.push('\n');
    }
    out
}

/// `base64.b64encode(data).decode()`.
pub fn b64encode(data: &[u8]) -> String {
    b2a_base64(data, false)
}

/// `base64.encodebytes(data).decode()`: lines of 76 characters (57 bytes), each ending in a
/// newline; nothing for empty input.
pub fn encodebytes(data: &[u8]) -> String {
    data.chunks(57).map(|line| b2a_base64(line, true)).collect()
}

/// `base64.b64decode(text, validate=True)` for a `str`: a non-ASCII character is refused first
/// (`ValueError`), then strict `a2b_base64`.
pub fn b64decode_validate(text: &str) -> Result<Vec<u8>, String> {
    if !text.is_ascii() {
        return Err("string argument should contain only ASCII characters".into());
    }
    a2b_base64(text.as_bytes(), true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        for length in 0..70 {
            let data: Vec<u8> = (0..length).map(|i| (i * 37 % 256) as u8).collect();
            assert_eq!(b64decode_validate(&b64encode(&data)).unwrap(), data);
            assert_eq!(a2b_base64(encodebytes(&data).as_bytes(), false).unwrap(), data);
        }
    }
}
