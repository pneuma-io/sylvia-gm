//! CPython 3.12's `math.hypot` and `math.dist` (`Modules/mathmodule.c`, `vector_norm`).
//!
//! Both are almost always correctly rounded, which a naive `sqrt` of a sum of squares (or
//! Rust's two-argument `f64::hypot`) is not: CPython scales by a power of two, squares exactly
//! with `fma`, sums with Neumaier compensation and corrects the square root by one Newton step.
//! The results here are bit-for-bit CPython's for finite inputs.

/// `frexp(x).1` for a positive finite `x`: the exponent `e` with `x = m * 2**e`, `0.5 <= m < 1`.
fn frexp_exponent(x: f64) -> i32 {
    let bits = x.to_bits();
    let biased = ((bits >> 52) & 0x7ff) as i32;
    if biased == 0 {
        // Subnormal: the exponent of its leading bit.
        let mantissa = bits & ((1u64 << 52) - 1);
        let leading = 63 - mantissa.leading_zeros() as i32;
        leading - 1074 + 1
    } else {
        biased - 1022
    }
}

/// `ldexp(1.0, exponent)`, exact for every exponent with a representable result.
fn power_of_two(exponent: i32) -> f64 {
    if exponent >= -1022 {
        f64::from_bits(((exponent + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (exponent + 1074))
    }
}

/// `dl_mul(x, y)`: the product and its exact rounding error.
fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    let z = x * y;
    (z, x.mul_add(y, -z))
}

/// `dl_fast_sum(a, b)` for `|a| >= |b|`.
fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let x = a + b;
    (x, (a - x) + b)
}

/// `vector_norm(n, vec, max, found_nan)` over non-negative magnitudes.
fn vector_norm(values: &mut [f64], max: f64, found_nan: bool) -> f64 {
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 || values.len() <= 1 {
        return max;
    }
    let max_e = frexp_exponent(max);
    if max_e < -1023 {
        for value in values.iter_mut() {
            *value /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * vector_norm(values, max / f64::MIN_POSITIVE, found_nan);
    }
    let scale = power_of_two(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for value in values.iter() {
        let x = value * scale;
        let (hi, lo) = dl_mul(x, x);
        let (sum, error) = dl_fast_sum(csum, hi);
        csum = sum;
        frac1 += lo;
        frac2 += error;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = dl_mul(-h, h);
    let (sum, error) = dl_fast_sum(csum, hi);
    csum = sum;
    frac1 += lo;
    frac2 += error;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

/// `math.hypot(*coordinates)`.
pub fn hypot(coordinates: &[f64]) -> f64 {
    let mut values: Vec<f64> = coordinates.iter().map(|x| x.abs()).collect();
    let found_nan = values.iter().any(|x| x.is_nan());
    let max = values.iter().fold(0.0f64, |max, x| if *x > max { *x } else { max });
    vector_norm(&mut values, max, found_nan)
}

/// `math.dist(p, q)` for points of the same dimension.
pub fn dist(p: &[f64], q: &[f64]) -> f64 {
    assert_eq!(p.len(), q.len(), "both points must have the same number of dimensions");
    let mut values: Vec<f64> = p.iter().zip(q).map(|(a, b)| (a - b).abs()).collect();
    let found_nan = values.iter().any(|x| x.is_nan());
    let max = values.iter().fold(0.0f64, |max, x| if *x > max { *x } else { max });
    vector_norm(&mut values, max, found_nan)
}

/// `round(x, digits)` for a float and `0 <= digits <= 22`: the exact binary value rounded half
/// to even at that decimal place, then read back (CPython's `double_round`, which formats with
/// `_Py_dg_dtoa` mode 3 and parses the digits).
pub fn round_digits(x: f64, digits: u32) -> f64 {
    assert!(digits <= 22, "the decimal scale must fit u128 arithmetic");
    if !x.is_finite() || x == 0.0 {
        return x;
    }
    let bits = x.abs().to_bits();
    let biased = ((bits >> 52) & 0x7ff) as i64;
    let fraction = bits & ((1u64 << 52) - 1);
    let (mantissa, exponent) =
        if biased == 0 { (fraction, -1074) } else { (fraction | (1u64 << 52), biased - 1075) };
    if exponent >= 0 {
        // An integer: no digits to round.
        return x;
    }
    let scaled = u128::from(mantissa) * 10u128.pow(digits);
    let shift = (-exponent) as u32;
    let rounded = if shift >= 128 {
        0
    } else {
        let quotient = scaled >> shift;
        let remainder = scaled & ((1u128 << shift) - 1);
        let half = 1u128 << (shift - 1);
        match remainder.cmp(&half) {
            std::cmp::Ordering::Greater => quotient + 1,
            std::cmp::Ordering::Less => quotient,
            std::cmp::Ordering::Equal => quotient + (quotient & 1),
        }
    };
    let magnitude: f64 = format!("{rounded}e-{digits}").parse().expect("a decimal literal");
    magnitude.copysign(x)
}

/// `sum(values)` over floats (CPython 3.12's compensated float path).
pub fn sum_floats(values: impl IntoIterator<Item = f64>) -> f64 {
    match crate::pynum::py_sum_numbers(values.into_iter().map(crate::pynum::PyNumber::Float)) {
        crate::pynum::PyNumber::Float(total) => total,
        crate::pynum::PyNumber::Int(total) => total as f64,
    }
}
