//! Retry timing: `provider/_retry_after.py` and `provider/_retry.py`.

use crate::error::{ErrorKind, ProviderResult};
use crate::types::RetryConfig;
use std::future::Future;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// `retry_after_seconds(error)` given the response's `retry-after` header value.
///
/// Seconds, or an HTTP date with a time zone, measured from now. Anything else, including a
/// value over 128 characters, a negative or non-finite delay and a date without a zone, is
/// `None`: no delay is invented.
pub fn retry_after_seconds(value: Option<&str>) -> Option<f64> {
    retry_after_seconds_at(value, now_micros())
}

/// [`retry_after_seconds`] with an explicit "now" in microseconds since the Unix epoch (UTC).
pub fn retry_after_seconds_at(value: Option<&str>, now_micros: i64) -> Option<f64> {
    let value = value?;
    if value.chars().count() > 128 {
        return None;
    }
    let delay = match python_float(value) {
        Some(delay) => delay,
        None => {
            let (deadline_seconds, zoned) = parsedate_to_epoch(value)?;
            if !zoned {
                return None;
            }
            // timedelta.total_seconds(): whole microseconds divided by 10**6.
            let difference = i128::from(deadline_seconds) * 1_000_000 - i128::from(now_micros);
            difference as f64 / 1e6
        }
    };
    (delay.is_finite() && delay >= 0.0).then_some(delay)
}

fn now_micros() -> i64 {
    let since = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    i64::try_from(since.as_micros()).unwrap_or(i64::MAX)
}

/// `float(text)` (`sylvia_base::pystr::parse_float`, Python's string rules), `None` where
/// Python raises `ValueError`.
pub fn python_float(text: &str) -> Option<f64> {
    sylvia_base::pystr::parse_float(text).ok()
}

/// `int(text)` for a date token. The values are only compared with the date limits, so an
/// integer beyond `i64` stands as the bound on its side and fails them as Python's would.
fn python_int(text: &str) -> Option<i64> {
    sylvia_base::pystr::parse_int(text).or_else(|error| error.bound_for_comparison()).ok()
}

const MONTH_NAMES: [&str; 24] = [
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
const DAY_NAMES: [&str; 7] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

fn named_zone(zone: &str) -> Option<i64> {
    Some(match zone {
        "UT" | "UTC" | "GMT" | "Z" => 0,
        "AST" => -400,
        "ADT" => -300,
        "EST" => -500,
        "EDT" => -400,
        "CST" => -600,
        "CDT" => -500,
        "MST" => -700,
        "MDT" => -600,
        "PST" => -800,
        "PDT" => -700,
        _ => return None,
    })
}

/// `email._parseaddr._parsedate_tz`: `[yy, mm, dd, hh, mm, ss]` and the zone offset in seconds
/// (`None` for `-0000` or an unknown zone).
#[allow(clippy::type_complexity)]
fn parsedate_tz(data: &str) -> Option<([i64; 6], Option<i64>)> {
    let mut data: Vec<String> =
        data.split(sylvia_base::pystr::is_space).filter(|s| !s.is_empty()).map(str::to_owned).collect();
    if data.is_empty() {
        return None;
    }
    if data[0].ends_with(',') || DAY_NAMES.contains(&data[0].to_lowercase().as_str()) {
        data.remove(0);
    } else if let Some(index) = data[0].rfind(',') {
        data[0] = data[0][index + 1..].to_owned();
    }
    if data.len() == 3 {
        let stuff: Vec<String> = data[0].split('-').map(str::to_owned).collect();
        if stuff.len() == 3 {
            let rest = data[1..].to_vec();
            data = stuff;
            data.extend(rest);
        }
    }
    if data.len() == 4 {
        let s = data[3].clone();
        let index = s.find('+').or_else(|| s.find('-'));
        match index {
            Some(i) if i > 0 => {
                data.truncate(3);
                data.push(s[..i].to_owned());
                data.push(s[i..].to_owned());
            }
            _ => data.push(String::new()),
        }
    }
    if data.len() < 5 {
        return None;
    }
    data.truncate(5);
    let [mut dd, mut mm, mut yy, mut tm, mut tz]: [String; 5] = data.try_into().ok()?;
    if dd.is_empty() || mm.is_empty() || yy.is_empty() {
        return None;
    }
    mm = mm.to_lowercase();
    if !MONTH_NAMES.contains(&mm.as_str()) {
        let swapped = dd.to_lowercase();
        dd = mm;
        mm = swapped;
        if !MONTH_NAMES.contains(&mm.as_str()) {
            return None;
        }
    }
    let mut month = MONTH_NAMES.iter().position(|name| *name == mm)? as i64 + 1;
    if month > 12 {
        month -= 12;
    }
    if dd.ends_with(',') {
        dd.pop();
    }
    if matches!(yy.find(':'), Some(i) if i > 0) {
        std::mem::swap(&mut yy, &mut tm);
    }
    if yy.ends_with(',') {
        yy.pop();
        if yy.is_empty() {
            return None;
        }
    }
    // yy[0].isdigit(), read as "is a decimal digit of any script". str.isdigit also accepts
    // superscript and other non-decimal digits; a date token starting with one is not handled
    // as Python handles it (it cannot parse as a date in either).
    if !yy.chars().next().is_some_and(|c| sylvia_base::pystr::decimal(c).is_some()) {
        std::mem::swap(&mut yy, &mut tz);
    }
    if tm.ends_with(',') {
        tm.pop();
    }
    let mut parts: Vec<&str> = tm.split(':').collect();
    let (thh, tmm, tss) = match parts.len() {
        2 => (parts[0], parts[1], "0"),
        3 => (parts[0], parts[1], parts[2]),
        1 if parts[0].contains('.') => {
            parts = parts[0].split('.').collect();
            match parts.len() {
                2 => (parts[0], parts[1], "0"),
                3 => (parts[0], parts[1], parts[2]),
                _ => return None,
            }
        }
        _ => return None,
    };
    let mut year = python_int(&yy)?;
    let day = python_int(&dd)?;
    let hour = python_int(thh)?;
    let minute = python_int(tmm)?;
    let second = python_int(tss)?;
    if year < 100 {
        if year > 68 {
            year += 1900;
        } else {
            year += 2000;
        }
    }
    let tz = tz.to_uppercase();
    let mut offset = named_zone(&tz);
    if offset.is_none() {
        offset = python_int(&tz);
        if offset == Some(0) && tz.starts_with('-') {
            offset = None;
        }
    }
    if let Some(value) = offset.filter(|value| *value != 0) {
        let (sign, magnitude) = if value < 0 { (-1, value.checked_neg()?) } else { (1, value) };
        offset = Some(sign * ((magnitude / 100).checked_mul(3600)?.checked_add((magnitude % 100) * 60)?));
    }
    Some(([year, month, day, hour, minute, second], offset))
}

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        _ => 28,
    }
}

/// `email.utils.parsedate_to_datetime(value)` as Unix seconds, and whether it had a zone.
/// `None` wherever Python raises `ValueError`, `TypeError` or `OverflowError`.
pub fn parsedate_to_epoch(value: &str) -> Option<(i64, bool)> {
    let ([year, month, day, hour, minute, second], offset) = parsedate_tz(value)?;
    // datetime(...) range checks.
    if !(1..=9999).contains(&year)
        || !(1..=12).contains(&month)
        || day < 1
        || day > days_in_month(year, month)
        || !(0..=23).contains(&hour)
        || !(0..=59).contains(&minute)
        || !(0..=59).contains(&second)
    {
        return None;
    }
    // timezone(timedelta(seconds=offset)) needs a strictly sub-day offset.
    if let Some(offset) = offset {
        if offset.abs() >= 86_400 {
            return None;
        }
    }
    let local = days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second;
    Some((local - offset.unwrap_or(0), offset.is_some()))
}

/// `_build_delay(attempt, config, retry_after)`.
///
/// A valid server delay is used as it is; otherwise exponential backoff capped at `max_delay`
/// plus up to 25% random jitter (`random.uniform(0, delay * 0.25)`).
pub fn build_delay(attempt: u32, config: &RetryConfig, retry_after: Option<f64>) -> f64 {
    build_delay_with(attempt, config, retry_after, random_unit())
}

/// [`build_delay`] with the uniform sample in `[0, 1)` supplied.
pub fn build_delay_with(attempt: u32, config: &RetryConfig, retry_after: Option<f64>, unit: f64) -> f64 {
    if let Some(retry_after) = retry_after {
        if retry_after.is_finite() && retry_after >= 0.0 {
            return retry_after;
        }
    }
    let exponent = f64::from(attempt) - 1.0;
    let delay = (config.base_delay * config.backoff_multiplier.powf(exponent)).min(config.max_delay);
    // random.uniform(a, b) is a + (b - a) * random().
    let jitter = (delay * 0.25) * unit;
    delay + jitter
}

fn random_unit() -> f64 {
    let mut bytes = [0u8; 8];
    if getrandom::getrandom(&mut bytes).is_err() {
        return 0.5;
    }
    // 53 random bits, as random.random() uses.
    (u64::from_le_bytes(bytes) >> 11) as f64 / (1u64 << 53) as f64
}

/// `retry_with_backoff(fn, retry_config)`.
///
/// Authentication errors and anything not retryable propagate at once. Rate limits use their
/// `retry_after`; timeouts and connection errors use backoff (and a connection error's own
/// `retry_after`). After `max_retries` retries the last error is returned.
pub async fn retry_with_backoff<T, F, Fut>(config: &RetryConfig, call: F) -> ProviderResult<T>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = ProviderResult<T>>,
{
    retry_with_backoff_sleeping(config, call, |delay| {
        tokio::time::sleep(Duration::from_secs_f64(delay.max(0.0)))
    })
    .await
}

/// [`retry_with_backoff`] with the wait supplied, so tests can observe each delay.
pub async fn retry_with_backoff_sleeping<T, F, Fut, S, SleepFut>(
    config: &RetryConfig,
    mut call: F,
    mut sleep: S,
) -> ProviderResult<T>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = ProviderResult<T>>,
    S: FnMut(f64) -> SleepFut,
    SleepFut: Future<Output = ()>,
{
    let attempts = config.max_retries + 1;
    let mut attempt = 1;
    loop {
        let error = match call().await {
            Ok(value) => return Ok(value),
            Err(error) => error,
        };
        if error.is(ErrorKind::Authentication) {
            return Err(error);
        }
        let rate_limited = error.is(ErrorKind::RateLimit);
        if !rate_limited && !error.is(ErrorKind::Timeout) && !error.is(ErrorKind::Connection) {
            return Err(error);
        }
        if attempt > config.max_retries {
            return Err(error);
        }
        let delay = build_delay(attempt, config, error.retry_after);
        if rate_limited {
            crate::log::warning(&format!(
                "Rate limit hit (attempt {attempt}/{attempts}), retrying in {delay:.1}s"
            ));
        } else {
            crate::log::warning(&format!(
                "{} (attempt {attempt}/{attempts}), retrying in {delay:.1}s",
                error.kind.class_name()
            ));
        }
        sleep(delay).await;
        attempt += 1;
    }
}
