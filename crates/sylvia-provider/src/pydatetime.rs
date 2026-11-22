//! Python 3.12's `datetime.fromisoformat` and `datetime.isoformat`, for timestamps Sylvia reads
//! from files other programs write (the Codex runtime's model catalog). Lane 5.
//!
//! The parser follows CPython's C implementation (`_datetimemodule.c`:
//! `_find_isoformat_datetime_separator`, `parse_isoformat_date`, `parse_isoformat_time`,
//! `parse_hh_mm_ss_ff`, `iso_to_ymd`) over the string's UTF-8 bytes, reading a NUL past the
//! end as C does. Every failure Python reports as a `ValueError` is `None` here.

/// A `datetime`, with its UTC offset in microseconds when it is aware.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PyDateTime {
    pub year: i64,
    pub month: i64,
    pub day: i64,
    pub hour: i64,
    pub minute: i64,
    pub second: i64,
    pub microsecond: i64,
    pub offset: Option<i64>,
}

const MICROS: i64 = 1_000_000;
const DAY_MICROS: i64 = 86_400 * MICROS;

fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// `ymd_to_ord`: day 1 is 0001-01-01.
fn ymd_to_ord(year: i64, month: i64, day: i64) -> i64 {
    let y = year - 1;
    let before_year = y * 365 + y.div_euclid(4) - y.div_euclid(100) + y.div_euclid(400);
    let before_month: i64 = (1..month).map(|m| days_in_month(year, m)).sum();
    before_year + before_month + day
}

/// `ord_to_ymd`, for ordinals of years 1 to 9999 and their neighbours.
fn ord_to_ymd(ordinal: i64) -> (i64, i64, i64) {
    let mut year = (ordinal - 1).div_euclid(366) + 1;
    while ymd_to_ord(year + 1, 1, 1) <= ordinal {
        year += 1;
    }
    let mut month = 1;
    while month < 12 && ymd_to_ord(year, month + 1, 1) <= ordinal {
        month += 1;
    }
    (year, month, ordinal - ymd_to_ord(year, month, 1) + 1)
}

fn iso_week1_monday(year: i64) -> i64 {
    let first_day = ymd_to_ord(year, 1, 1);
    let first_weekday = (first_day + 6).rem_euclid(7);
    let monday = first_day - first_weekday;
    if first_weekday > 3 {
        monday + 7
    } else {
        monday
    }
}

/// `iso_to_ymd`.
fn iso_to_ymd(iso_year: i64, iso_week: i64, iso_day: i64) -> Option<(i64, i64, i64)> {
    if iso_week <= 0 || iso_week >= 53 {
        let first_weekday = (ymd_to_ord(iso_year, 1, 1) + 6).rem_euclid(7);
        let long_year = first_weekday == 3 || (first_weekday == 2 && is_leap(iso_year));
        if !(iso_week == 53 && long_year) {
            return None;
        }
    }
    if iso_day <= 0 || iso_day >= 8 {
        return None;
    }
    Some(ord_to_ymd(iso_week1_monday(iso_year) + (iso_week - 1) * 7 + iso_day - 1))
}

/// The string's bytes, reading `0` (C's terminator) past the end.
struct Bytes<'a>(&'a [u8]);

impl Bytes<'_> {
    fn at(&self, index: usize) -> u8 {
        self.0.get(index).copied().unwrap_or(0)
    }

    /// `parse_digits(ptr, var, n)`: the position after `n` digits and their value.
    fn digits(&self, start: usize, count: usize) -> Option<(usize, i64)> {
        let mut value = 0;
        for offset in 0..count {
            let byte = self.at(start + offset);
            if !byte.is_ascii_digit() {
                return None;
            }
            value = value * 10 + i64::from(byte - b'0');
        }
        Some((start + count, value))
    }
}

/// `_find_isoformat_datetime_separator`.
fn separator(text: &Bytes<'_>, len: usize) -> usize {
    if len == 7 {
        return 7;
    }
    if text.at(4) == b'-' {
        if text.at(5) == b'W' {
            if len < 8 {
                return usize::MAX;
            }
            if len > 8 && text.at(8) == b'-' {
                if len == 9 {
                    return usize::MAX;
                }
                if len > 10 && text.at(10).is_ascii_digit() {
                    return 8;
                }
                return 10;
            }
            return 8;
        }
        return 10;
    }
    if text.at(4) == b'W' {
        let mut index = 7;
        while index < len && text.at(index).is_ascii_digit() {
            index += 1;
        }
        if index < 9 {
            return index;
        }
        return if index % 2 == 0 { 7 } else { 8 };
    }
    8
}

/// `parse_isoformat_date(dtstr, len)`: year, month and day.
fn parse_date(text: &Bytes<'_>, len: usize) -> Option<(i64, i64, i64)> {
    let (mut p, year) = text.digits(0, 4)?;
    let separated = text.at(p) == b'-';
    if separated {
        p += 1;
    }
    if text.at(p) == b'W' {
        p += 1;
        let (after, week) = text.digits(p, 2)?;
        p = after;
        let day = if p < len {
            if separated {
                if text.at(p) != b'-' {
                    return None;
                }
                p += 1;
            }
            text.digits(p, 1)?.1
        } else {
            1
        };
        return iso_to_ymd(year, week, day);
    }
    let (after, month) = text.digits(p, 2)?;
    p = after;
    if separated {
        if text.at(p) != b'-' {
            return None;
        }
        p += 1;
    }
    let (_, day) = text.digits(p, 2)?;
    Some((year, month, day))
}

/// `parse_hh_mm_ss_ff(tstr, tstr_end)`: the fields, and whether anything follows them (`1`).
fn parse_clock(text: &Bytes<'_>, start: usize, end: usize) -> Result<([i64; 4], bool), ()> {
    let mut values = [0i64; 4];
    let mut p = start;
    let mut has_separator = true;
    let mut fraction = false;
    for (index, value) in values.iter_mut().take(3).enumerate() {
        let (after, parsed) = text.digits(p, 2).ok_or(())?;
        *value = parsed;
        p = after;
        let c = text.at(p);
        p += 1;
        if index == 0 {
            has_separator = c == b':';
        }
        if p >= end {
            return Ok((values, c != 0));
        } else if has_separator && c == b':' {
            continue;
        } else if c == b'.' || c == b',' {
            fraction = true;
            break;
        } else if !has_separator {
            p -= 1;
        } else {
            return Err(());
        }
    }
    let _ = fraction;
    let remaining = end.saturating_sub(p);
    let to_parse = remaining.min(6);
    if to_parse == 0 {
        // C indexes `correction[-1]` here; no input reaches it with a valid result.
        return Err(());
    }
    let (after, mut micro) = text.digits(p, to_parse).ok_or(())?;
    p = after;
    if to_parse < 6 {
        micro *= [100_000, 10_000, 1_000, 100, 10][to_parse - 1];
    }
    values[3] = micro;
    while text.at(p).is_ascii_digit() {
        p += 1;
    }
    Ok((values, text.at(p) != 0))
}

/// `parse_isoformat_time(dtstr, dtlen)`: the clock fields and the offset in microseconds.
fn parse_time(text: &Bytes<'_>, start: usize, end: usize) -> Option<([i64; 4], Option<i64>)> {
    let mut tz = start;
    loop {
        if matches!(text.at(tz), b'Z' | b'+' | b'-') {
            break;
        }
        tz += 1;
        if tz >= end {
            break;
        }
    }
    let (clock, more) = parse_clock(text, start, tz).ok()?;
    if tz == end {
        return (!more).then_some((clock, None));
    }
    if text.at(tz) == b'Z' {
        // C reads the byte after `Z`: the terminator, or an embedded NUL.
        return (text.at(tz + 1) == 0).then_some((clock, Some(0)));
    }
    let sign = if text.at(tz) == b'-' { -1 } else { 1 };
    let ([hours, minutes, seconds, micros], more) = parse_clock(text, tz + 1, end).ok()?;
    if more {
        return None;
    }
    Some((clock, Some(sign * ((hours * 3600 + minutes * 60 + seconds) * MICROS + micros))))
}

/// `datetime.fromisoformat(text)`; `None` for every `ValueError`.
pub fn fromisoformat(text: &str) -> Option<PyDateTime> {
    let bytes = Bytes(text.as_bytes());
    let len = text.len();
    let split = separator(&bytes, len);
    if split == usize::MAX {
        return None;
    }
    let (year, month, day) = parse_date(&bytes, split)?;
    // The separator is one character (`date_string[separator_location + 1:]`), so a non-ASCII
    // one spans its whole UTF-8 sequence; everything else valid is ASCII.
    let width = text.get(split..).and_then(|rest| rest.chars().next()).map_or(1, char::len_utf8);
    let (clock, offset) = if len > split { parse_time(&bytes, split + width, len)? } else { ([0; 4], None) };
    let [hour, minute, second, microsecond] = clock;
    if let Some(offset) = offset {
        // `timezone(timedelta(...))`: strictly within a day.
        if offset.abs() >= DAY_MICROS {
            return None;
        }
    }
    let valid = (1..=9999).contains(&year)
        && (1..=12).contains(&month)
        && (1..=days_in_month(year, month)).contains(&day)
        && (0..24).contains(&hour)
        && (0..60).contains(&minute)
        && (0..60).contains(&second);
    valid.then_some(PyDateTime { year, month, day, hour, minute, second, microsecond, offset })
}

impl PyDateTime {
    /// `isoformat()`.
    pub fn isoformat(&self) -> String {
        let mut out = format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        );
        if self.microsecond != 0 {
            out.push_str(&format!(".{:06}", self.microsecond));
        }
        if let Some(offset) = self.offset {
            let sign = if offset < 0 { '-' } else { '+' };
            let offset = offset.abs();
            let (seconds, micros) = (offset / MICROS, offset % MICROS);
            out.push_str(&format!("{sign}{:02}:{:02}", seconds / 3600, seconds % 3600 / 60));
            if seconds % 60 != 0 || micros != 0 {
                out.push_str(&format!(":{:02}", seconds % 60));
            }
            if micros != 0 {
                out.push_str(&format!(".{micros:06}"));
            }
        }
        out
    }

    /// Microseconds since 1970-01-01 UTC, for an aware value (a naive one cannot be compared
    /// with an aware one: Python's `TypeError`).
    pub fn utc_micros(&self) -> Option<i128> {
        let offset = self.offset?;
        let days = i128::from(ymd_to_ord(self.year, self.month, self.day) - ymd_to_ord(1970, 1, 1));
        let local = days * i128::from(DAY_MICROS)
            + i128::from((self.hour * 3600 + self.minute * 60 + self.second) * MICROS + self.microsecond);
        Some(local - i128::from(offset))
    }
}

/// Microseconds since 1970-01-01 UTC now (`datetime.now(UTC)`).
pub fn utc_now_micros() -> i128 {
    let elapsed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    i128::try_from(elapsed.as_micros()).unwrap_or(i128::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_helpers_agree() {
        assert_eq!(ymd_to_ord(1, 1, 1), 1);
        assert_eq!(ymd_to_ord(1970, 1, 1), 719_163);
        for ordinal in [1, 59, 60, 365, 366, 719_163, 739_943, 3_652_059] {
            let (y, m, d) = ord_to_ymd(ordinal);
            assert_eq!(ymd_to_ord(y, m, d), ordinal);
        }
    }
}
