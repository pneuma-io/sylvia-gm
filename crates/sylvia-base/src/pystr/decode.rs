//! Python's strict UTF-8 decode, with `str(UnicodeDecodeError)` as CPython words it.

/// `data.decode("utf-8")`: the text, or the `str(UnicodeDecodeError)` Python gives.
pub fn utf8_decode(data: &[u8]) -> Result<String, String> {
    match std::str::from_utf8(data) {
        Ok(text) => Ok(text.to_owned()),
        Err(error) => {
            let start = error.valid_up_to();
            let (end, reason) = match error.error_len() {
                None => (data.len(), "unexpected end of data"),
                Some(1) if matches!(data[start], 0x80..=0xC1 | 0xF5..=0xFF) => {
                    (start + 1, "invalid start byte")
                }
                Some(length) => (start + length, "invalid continuation byte"),
            };
            Err(if end - start == 1 {
                format!("'utf-8' codec can't decode byte 0x{:02x} in position {start}: {reason}", data[start])
            } else {
                format!("'utf-8' codec can't decode bytes in position {start}-{}: {reason}", end - 1)
            })
        }
    }
}
