//! aiohttp 3.14.3's `web.FileResponse` for the WebUI's download handles
//! (`web_fileresponse.py`, with the request helpers it reads from `web_request.py` and
//! `helpers.py`, and `email.utils.parsedate` from CPython 3.12): the strong `ETag`,
//! `Last-Modified`, `Accept-Ranges`, `HEAD`, the four conditional headers (412 and 304), and a
//! single byte range with `If-Range` (206 and 416).
//!
//! Not ported, as unreachable: the `.gz` and `.br` sibling lookup (a download handle names only
//! a copy the host made of an owner's export, which never has a sibling) and the content-type
//! guess (aiohttp's own `MimeTypes()` table has none of the four export suffixes, so it is
//! always `application/octet-stream`).

use std::path::Path;

/// What `prepare` decided, and the headers it sets besides the caller's.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prepared {
    pub status: u16,
    pub headers: Vec<(&'static str, String)>,
    /// The bytes to send, or `None` for an empty body of unknown length (aiohttp's chunked
    /// empty body, as a 416 or a failed stat sends it).
    pub body: Option<Vec<u8>>,
    /// The body is empty with a known length of zero, or (for a 304) absent.
    pub framing: Framing,
}

/// How the empty or full body is framed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Framing {
    /// The bytes, with `Content-Length`.
    Sized,
    /// No body and no length (`304 Not Modified`).
    None,
    /// An empty chunked body (`Transfer-Encoding: chunked`).
    Chunked,
}

/// The request headers `prepare` reads (the first value of each, as `CIMultiDict.get`).
#[derive(Debug, Clone, Default)]
pub struct Conditions<'a> {
    pub head: bool,
    pub if_match: Option<&'a str>,
    pub if_none_match: Option<&'a str>,
    pub if_modified_since: Option<&'a str>,
    pub if_unmodified_since: Option<&'a str>,
    pub if_range: Option<&'a str>,
    pub range: Option<&'a str>,
}

/// `ETag(value, is_weak)`.
#[derive(Debug, Clone, PartialEq, Eq)]
struct ETag {
    value: String,
    weak: bool,
}

const ETAG_ANY: &str = "*";

fn etag_list() -> &'static regex::Regex {
    static PATTERN: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    // `LIST_QUOTED_ETAG_RE`.
    PATTERN.get_or_init(|| {
        regex::Regex::new(r#"((W/)?"([!\x23-\x7E\x80-\xff]+)")(?:\s*,\s*|$)|(.)"#)
            .expect("the pattern compiles")
    })
}

/// `_if_match_or_none_impl(header)`: `None` for a missing or empty header.
fn etags(header: Option<&str>) -> Option<Vec<ETag>> {
    let header = header.filter(|header| !header.is_empty())?;
    if header == ETAG_ANY {
        return Some(vec![ETag { value: ETAG_ANY.into(), weak: false }]);
    }
    let mut found = Vec::new();
    for captures in etag_list().captures_iter(header) {
        // Any symbol in the fourth group means the rest of the list is invalid.
        if captures.get(4).is_some() {
            break;
        }
        found.push(ETag {
            value: captures.get(3).map_or("", |value| value.as_str()).to_owned(),
            weak: captures.get(2).is_some(),
        });
    }
    Some(found)
}

/// `_etag_match(etag_value, etags, weak=)`.
fn etag_match(value: &str, etags: &[ETag], weak: bool) -> bool {
    if etags.len() == 1 && etags[0].value == ETAG_ANY {
        return true;
    }
    etags.iter().any(|etag| (weak || !etag.weak) && etag.value == value)
}

const MONTHS: [&str; 24] = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
];
const DAYS: [&str; 7] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

/// Python's `int(text)`.
fn int(text: &str) -> Option<i64> {
    sylvia_base::pystr::parse_int(text).ok()
}

/// `email.utils.parsedate(data)[:6]`: `_parsedate_tz` without its time zone, which aiohttp's
/// `parse_http_date` drops.
fn parsedate(data: &str) -> Option<(i64, i64, i64, i64, i64, i64)> {
    let mut data: Vec<String> =
        data.split(sylvia_base::pystr::is_space).filter(|part| !part.is_empty()).map(str::to_owned).collect();
    if data.is_empty() {
        return None;
    }
    // The space after the day-of-week's comma is optional.
    if data[0].ends_with(',') || DAYS.contains(&data[0].to_lowercase().as_str()) {
        data.remove(0);
    } else if let Some(index) = data[0].rfind(',') {
        data[0] = data[0][index + 1..].to_owned();
    }
    if data.len() == 3 {
        // RFC 850, deprecated.
        let stuff: Vec<String> = data[0].split('-').map(str::to_owned).collect();
        if stuff.len() == 3 {
            data = stuff.into_iter().chain(data[1..].iter().cloned()).collect();
        }
    }
    if data.len() == 4 {
        let s = data[3].clone();
        let index = s.find('+').or_else(|| s.find('-'));
        match index {
            Some(index) if index > 0 => {
                data.truncate(3);
                data.push(s[..index].to_owned());
                data.push(s[index..].to_owned());
            }
            _ => data.push(String::new()),
        }
    }
    if data.len() < 5 {
        return None;
    }
    data.truncate(5);
    let (mut dd, mut mm, mut yy, mut tm, mut tz) =
        (data[0].clone(), data[1].clone(), data[2].clone(), data[3].clone(), data[4].clone());
    if dd.is_empty() || mm.is_empty() || yy.is_empty() {
        return None;
    }
    mm = mm.to_lowercase();
    if !MONTHS.contains(&mm.as_str()) {
        let swapped = dd.to_lowercase();
        dd = mm;
        mm = swapped;
        if !MONTHS.contains(&mm.as_str()) {
            return None;
        }
    }
    let mut month = MONTHS.iter().position(|name| *name == mm).expect("checked") as i64 + 1;
    if month > 12 {
        month -= 12;
    }
    if dd.ends_with(',') {
        dd.pop();
    }
    if yy.find(':').is_some_and(|index| index > 0) {
        std::mem::swap(&mut yy, &mut tm);
    }
    if yy.ends_with(',') {
        yy.pop();
        if yy.is_empty() {
            return None;
        }
    }
    // `str.isdigit()` on the first character.
    if !yy.chars().next().is_some_and(|c| c.is_ascii_digit() || (!c.is_ascii() && c.is_numeric())) {
        std::mem::swap(&mut yy, &mut tz);
    }
    if tm.ends_with(',') {
        tm.pop();
    }
    let _ = tz;
    let parts: Vec<&str> = tm.split(':').collect();
    let (thh, tmm, tss) = match parts.as_slice() {
        [hours, minutes] => (*hours, *minutes, "0"),
        [hours, minutes, seconds] => (*hours, *minutes, *seconds),
        [one] if one.contains('.') => match one.split('.').collect::<Vec<_>>().as_slice() {
            [hours, minutes] => (*hours, *minutes, "0"),
            [hours, minutes, seconds] => (*hours, *minutes, *seconds),
            _ => return None,
        },
        _ => return None,
    };
    let (mut year, day, hour, minute, second) = (int(&yy)?, int(&dd)?, int(thh)?, int(tmm)?, int(tss)?);
    if year < 100 {
        year += if year > 68 { 1900 } else { 2000 };
    }
    Some((year, month, day, hour, minute, second))
}

/// Days from 1970-01-01 to the given proleptic Gregorian date.
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let of_era = year - era * 400;
    let of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let of_cycle = of_era * 365 + of_era / 4 - of_era / 100 + of_year;
    era * 146_097 + of_cycle - 719_468
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        2 if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// `parse_http_date(text).timestamp()`: the fields as a UTC `datetime` (its offset ignored),
/// `None` when `datetime` refuses them.
pub fn http_date_timestamp(text: Option<&str>) -> Option<f64> {
    let (year, month, day, hour, minute, second) = parsedate(text?)?;
    let valid = (1..=9999).contains(&year)
        && (1..=12).contains(&month)
        && (1..=days_in_month(year, month)).contains(&day)
        && (0..24).contains(&hour)
        && (0..60).contains(&minute)
        && (0..60).contains(&second);
    if !valid {
        return None;
    }
    Some((days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second) as f64)
}

/// `time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(seconds))`.
pub fn http_date(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let rest = seconds.rem_euclid(86_400);
    // civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let of_era = z - era * 146_097;
    let year_of_era = (of_era - of_era / 1460 + of_era / 36_524 - of_era / 146_096) / 365;
    let of_year = of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let mp = (5 * of_year + 2) / 153;
    let day = of_year - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    let weekday = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"][days.rem_euclid(7) as usize];
    let name = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        [(month - 1) as usize];
    format!(
        "{weekday}, {day:02} {name} {year:04} {:02}:{:02}:{:02} GMT",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

/// A digit string as an integer (Python's exact `int`), saturated where it cannot matter:
/// anything that large is past every file's end.
fn digits(text: &str) -> Option<i128> {
    if text.is_empty() {
        return None;
    }
    Some(text.parse::<i128>().unwrap_or(i128::MAX / 4))
}

/// `request.http_range`: `(start, stop)` of the slice, or `Err` for its `ValueError`.
fn http_range(header: Option<&str>) -> Result<(Option<i128>, Option<i128>), ()> {
    let Some(header) = header else {
        return Ok((None, None));
    };
    let rest = header.strip_prefix("bytes=").ok_or(())?;
    let (start, end) = rest.split_once('-').ok_or(())?;
    let ascii_digits = |text: &str| text.bytes().all(|byte| byte.is_ascii_digit());
    if !ascii_digits(start) || !ascii_digits(end) {
        return Err(());
    }
    let (mut start, mut end) = (digits(start), digits(end));
    if start.is_none() && end.is_some() {
        // No start: the tail of the content.
        start = end.map(|end| -end);
        end = None;
    }
    if let (Some(first), Some(last)) = (start, end) {
        // The header's end is inclusive; a slice's stop is not.
        let stop = last + 1;
        if first >= stop {
            return Err(());
        }
        end = Some(stop);
    }
    if start.is_none() && end.is_none() {
        return Err(());
    }
    Ok((start, end))
}

/// The file's `stat` as `FileResponse` reads it.
pub struct Stat {
    pub size: u64,
    pub mtime_ns: u128,
}

impl Stat {
    /// `os.stat(path)`; `Err` keeps the `io::Error` for the 403 or 404 it becomes.
    pub fn of(path: &Path) -> std::io::Result<Self> {
        let metadata = std::fs::metadata(path)?;
        if !metadata.is_file() {
            return Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied));
        }
        let mtime_ns = metadata
            .modified()?
            .duration_since(std::time::UNIX_EPOCH)
            .map(|elapsed| elapsed.as_nanos())
            .unwrap_or(0);
        Ok(Self { size: metadata.len(), mtime_ns })
    }

    fn etag(&self) -> String {
        format!("{:x}-{:x}", self.mtime_ns, self.size)
    }

    fn mtime(&self) -> f64 {
        self.mtime_ns as f64 / 1e9
    }

    fn last_modified(&self) -> String {
        http_date(self.mtime().ceil() as i64)
    }
}

fn empty(status: u16, headers: Vec<(&'static str, String)>, framing: Framing) -> Prepared {
    Prepared { status, headers, body: Some(Vec::new()), framing }
}

/// `FileResponse(path).prepare(request)`: the status, the headers it adds and the body.
pub fn prepare(path: &Path, conditions: &Conditions<'_>) -> Prepared {
    const OCTET: &str = "application/octet-stream";
    let failed = |error: std::io::Error| {
        let status = if error.kind() == std::io::ErrorKind::PermissionDenied { 403 } else { 404 };
        Prepared {
            status,
            headers: vec![("Content-Type", OCTET.into())],
            body: None,
            framing: Framing::Chunked,
        }
    };
    let stat = match Stat::of(path) {
        Ok(stat) => stat,
        Err(error) => return failed(error),
    };
    let etag = stat.etag();
    let if_match = etags(conditions.if_match);
    if if_match.as_ref().is_some_and(|tags| !etag_match(&etag, tags, false)) {
        return empty(412, vec![("Content-Length", "0".into())], Framing::Sized);
    }
    let unmodified_since = http_date_timestamp(conditions.if_unmodified_since);
    if unmodified_since.is_some_and(|since| if_match.is_none() && stat.mtime() > since) {
        return empty(412, vec![("Content-Length", "0".into())], Framing::Sized);
    }
    let not_modified = || {
        empty(
            304,
            vec![("ETag", format!("\"{etag}\"")), ("Last-Modified", stat.last_modified())],
            Framing::None,
        )
    };
    let if_none_match = etags(conditions.if_none_match);
    if if_none_match.as_ref().is_some_and(|tags| etag_match(&etag, tags, true)) {
        return not_modified();
    }
    let modified_since = http_date_timestamp(conditions.if_modified_since);
    if modified_since.is_some_and(|since| if_none_match.is_none() && stat.mtime() <= since) {
        return not_modified();
    }
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) => return failed(error),
    };
    // `fstat` after opening, so a file changed between the two is described as sent.
    let stat =
        Stat { size: bytes.len() as u64, mtime_ns: Stat::of(path).map_or(stat.mtime_ns, |now| now.mtime_ns) };
    let size = i128::from(stat.size as i64);
    let mut status = 200;
    let mut count = size;
    let mut start: Option<i128> = None;
    let range_applies = http_date_timestamp(conditions.if_range).is_none_or(|since| stat.mtime() <= since);
    if range_applies {
        let unsatisfiable = || Prepared {
            status: 416,
            headers: vec![("Content-Range", format!("bytes */{size}")), ("Content-Type", OCTET.into())],
            body: None,
            framing: Framing::Chunked,
        };
        let Ok((first, stop)) = http_range(conditions.range) else {
            return unsatisfiable();
        };
        if let Some(mut first) = first {
            if first < 0 && stop.is_none() {
                first = (first + size).max(0);
                count = size - first;
            } else {
                count = stop.unwrap_or(size).min(size) - first;
            }
            if first >= size {
                return unsatisfiable();
            }
            start = Some(first);
            status = 206;
        }
    }
    let mut headers = vec![
        ("Content-Type", OCTET.into()),
        ("ETag", format!("\"{}\"", stat.etag())),
        ("Last-Modified", stat.last_modified()),
        ("Content-Length", count.to_string()),
        ("Accept-Ranges", "bytes".into()),
    ];
    let offset = start.unwrap_or(0);
    if status == 206 {
        headers.push(("Content-Range", format!("bytes {offset}-{}/{size}", offset + count - 1)));
    }
    let body = if conditions.head || count == 0 {
        Vec::new()
    } else {
        let from = offset as usize;
        bytes[from..from + count as usize].to_vec()
    };
    Prepared { status, headers, body: Some(body), framing: Framing::Sized }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates_parse_as_email_utils_parsedate() {
        assert_eq!(http_date_timestamp(Some("Fri, 15 Jan 2027 08:00:01 GMT")), Some(1_800_000_001.0));
        assert_eq!(http_date_timestamp(Some("Fri, 01 Jan 2100 00:00:00 +0500")), Some(4_102_444_800.0));
        assert_eq!(http_date_timestamp(Some("Friday, 01-Jan-99 00:00:00 GMT")), Some(915_148_800.0));
        assert_eq!(http_date_timestamp(Some("Fri Jan  1 00:00:00 2100")), Some(4_102_444_800.0));
        assert_eq!(http_date_timestamp(Some("Sat, 31 Feb 2100 00:00:00 GMT")), None);
        assert_eq!(http_date_timestamp(Some("yesterday")), None);
        assert_eq!(http_date_timestamp(Some("")), None);
        assert_eq!(http_date(1_800_000_001), "Fri, 15 Jan 2027 08:00:01 GMT");
        assert_eq!(http_date(0), "Thu, 01 Jan 1970 00:00:00 GMT");
    }

    #[test]
    fn ranges_parse_as_aiohttp() {
        assert_eq!(http_range(Some("bytes=0-9")), Ok((Some(0), Some(10))));
        assert_eq!(http_range(Some("bytes=-16")), Ok((Some(-16), None)));
        assert_eq!(http_range(Some("bytes=990-")), Ok((Some(990), None)));
        assert_eq!(http_range(Some("bytes=9-3")), Err(()));
        assert_eq!(http_range(Some("bytes=-")), Err(()));
        assert_eq!(http_range(Some("bytes=0-1,4-5")), Err(()));
        assert_eq!(http_range(None), Ok((None, None)));
    }

    #[test]
    fn etag_lists_stop_at_garbage() {
        let tags = |text| etags(Some(text)).unwrap();
        assert_eq!(
            tags(r#""a", W/"b""#),
            [ETag { value: "a".into(), weak: false }, ETag { value: "b".into(), weak: true }]
        );
        assert!(tags(r#"garbage, "a""#).is_empty());
        assert!(tags(r#""a" "b""#).is_empty());
        assert_eq!(etags(Some("")), None);
        assert_eq!(tags("*"), [ETag { value: "*".into(), weak: false }]);
    }
}
