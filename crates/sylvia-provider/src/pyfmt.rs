//! Python number formatting that provider text depends on.

/// `f"{value:g}"` for a Python `float`: six significant digits, trailing zeros removed,
/// exponent form when the decimal exponent is below -4 or at least 6.
///
/// Rust's `{:e}` formatting rounds the exact binary value correctly with ties to even, as
/// CPython's `float.__format__` does, so the digits agree; only the layout is ported here.
pub fn format_g(value: f64) -> String {
    format_general(value, 6)
}

/// `format(value, f".{precision}g")`.
pub fn format_general(value: f64, precision: usize) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let precision = precision.max(1);
    if value == 0.0 {
        return if value.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    // Round to `precision` significant digits first; the exponent of the rounded value decides
    // the layout (9.999995 rounds to 1.00000e+01, which prints as 10).
    let scientific = format!("{:.*e}", precision - 1, value);
    let (mantissa, exponent) = scientific.split_once('e').expect("Rust {:e} always has an exponent");
    let exponent: i32 = exponent.parse().expect("exponent is an integer");
    if exponent < -4 || exponent >= precision as i32 {
        let mantissa = strip_zeros(mantissa);
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    } else {
        let decimals = (precision as i32 - 1 - exponent).max(0) as usize;
        strip_zeros(&format!("{value:.decimals$}")).to_owned()
    }
}

fn strip_zeros(text: &str) -> &str {
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.')
    } else {
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_cases() {
        assert_eq!(format_g(30.0), "30");
        assert_eq!(format_g(1e-5), "1e-05");
        assert_eq!(format_g(0.0001), "0.0001");
        assert_eq!(format_g(123456.0), "123456");
        assert_eq!(format_g(1234567.0), "1.23457e+06");
        assert_eq!(format_g(1.5), "1.5");
        assert_eq!(format_g(999999.5), "1e+06");
    }
}
