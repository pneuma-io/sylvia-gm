//! CPython's `random.Random`, reproduced draw for draw.
//!
//! Sylvia's dice are reproducible: a package tool's stream is `random.Random(seed)` with a seed
//! derived from SHA-256 (see [`seed_from_basis`]), and the simulation-mode dice queue is
//! `random.Random(seed)` too. No Rust crate gives the same numbers, so this module ports the
//! pieces of CPython 3.12 that Sylvia reaches:
//!
//! - `_randommodule.c`: MT19937 (`genrand_uint32`), `init_genrand`, `init_by_array`, the integer
//!   seed path (the absolute value split into 32-bit limbs, least significant first) and
//!   `getrandbits`.
//! - `Lib/random.py`: the version 2 seed for `str`/`bytes` (the bytes followed by their SHA-512,
//!   read as one big-endian integer), `_randbelow_with_getrandbits`, `randrange`, `randint`,
//!   `choice`, `shuffle` and `random`.
//!
//! Error messages are Python's own text. Vectors from the 3.12 snapshot interpreter are in
//! `tests/vectors/pyrandom_vectors.json`.

use crate::pyerror::{PyError, PyResult};
use sha2::{Digest, Sha512};
use sylvia_base::Json;

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// One `random.Random` instance: the MT19937 state and its position.
#[derive(Clone)]
pub struct PyRandom {
    mt: [u32; N],
    index: usize,
}

impl std::fmt::Debug for PyRandom {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PyRandom").field("index", &self.index).finish_non_exhaustive()
    }
}

impl PyRandom {
    fn unseeded() -> Self {
        Self { mt: [0; N], index: N + 1 }
    }

    /// `random.Random(seed)` for a non-negative integer seed.
    pub fn from_u64(seed: u64) -> Self {
        let mut rng = Self::unseeded();
        rng.seed_u64(seed);
        rng
    }

    /// `random.Random(seed)` for any `int` in the `i64` range. Python seeds by `abs(seed)`.
    pub fn from_i64(seed: i64) -> Self {
        Self::from_u64(seed.unsigned_abs())
    }

    /// `random.Random(seed)` for any `int` in the `i128` range.
    pub fn from_i128(seed: i128) -> Self {
        let magnitude = seed.unsigned_abs();
        // Truncation is intended: these are the four 32-bit limbs.
        #[allow(clippy::cast_possible_truncation)]
        let limbs: [u32; 4] = std::array::from_fn(|i| (magnitude >> (32 * i)) as u32);
        Self::from_int_limbs(&limbs)
    }

    /// `random.Random(seed)` for an integer of any size, given as the 32-bit limbs of its
    /// absolute value, least significant first.
    pub fn from_int_limbs(limbs: &[u32]) -> Self {
        let mut rng = Self::unseeded();
        rng.seed_int_limbs(limbs);
        rng
    }

    /// `random.Random(seed)` for a `bytes` or `bytearray` seed (version 2).
    pub fn from_bytes(seed: &[u8]) -> Self {
        let mut rng = Self::unseeded();
        rng.seed_bytes(seed);
        rng
    }

    /// `random.Random(seed)` for a `str` seed (version 2): its UTF-8 bytes.
    pub fn from_str_seed(seed: &str) -> Self {
        Self::from_bytes(seed.as_bytes())
    }

    /// `random.Random(os.urandom(16))`, the stream `dice.py` uses outside a seeded tool.
    pub fn from_entropy() -> Self {
        let mut bytes = [0u8; 16];
        getrandom::getrandom(&mut bytes).expect("the operating system supplies random bytes");
        Self::from_bytes(&bytes)
    }

    /// `rng.seed(seed)` for a non-negative integer.
    pub fn seed_u64(&mut self, seed: u64) {
        // Truncation is intended: these are the low and high 32-bit limbs.
        #[allow(clippy::cast_possible_truncation)]
        let limbs = [seed as u32, (seed >> 32) as u32];
        self.seed_int_limbs(&limbs);
    }

    /// `rng.seed(seed)` for any `int` in the `i64` range.
    pub fn seed_i64(&mut self, seed: i64) {
        self.seed_u64(seed.unsigned_abs());
    }

    /// `rng.seed(seed)` for an integer given as the limbs of its absolute value.
    ///
    /// CPython uses exactly as many limbs as the value needs, and one for zero.
    pub fn seed_int_limbs(&mut self, limbs: &[u32]) {
        let used = limbs.iter().rposition(|limb| *limb != 0).map_or(1, |last| last + 1);
        let key: Vec<u32> = (0..used).map(|i| limbs.get(i).copied().unwrap_or(0)).collect();
        self.init_by_array(&key);
    }

    /// `rng.seed(seed)` for `bytes`: `int.from_bytes(seed + sha512(seed).digest())`.
    pub fn seed_bytes(&mut self, seed: &[u8]) {
        let mut big_endian = seed.to_vec();
        big_endian.extend_from_slice(&Sha512::digest(seed));
        self.seed_int_limbs(&big_endian_limbs(&big_endian));
    }

    fn init_genrand(&mut self, seed: u32) {
        self.mt[0] = seed;
        for i in 1..N {
            let previous = self.mt[i - 1];
            // `i < 624`, so the cast is exact.
            #[allow(clippy::cast_possible_truncation)]
            let step = i as u32;
            self.mt[i] = 1_812_433_253u32.wrapping_mul(previous ^ (previous >> 30)).wrapping_add(step);
        }
        self.index = N;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mut i = 1usize;
        let mut j = 0usize;
        for _ in 0..N.max(key.len()) {
            let previous = self.mt[i - 1];
            // `j` indexes a key of at most `usize` limbs; CPython adds it as a 32-bit value.
            #[allow(clippy::cast_possible_truncation)]
            let j32 = j as u32;
            self.mt[i] = (self.mt[i] ^ (previous ^ (previous >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j32);
            i += 1;
            j += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            let previous = self.mt[i - 1];
            #[allow(clippy::cast_possible_truncation)]
            let i32_ = i as u32;
            self.mt[i] =
                (self.mt[i] ^ (previous ^ (previous >> 30)).wrapping_mul(1_566_083_941)).wrapping_sub(i32_);
            i += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
        }
        self.mt[0] = 0x8000_0000;
        self.index = N;
    }

    fn twist(&mut self) {
        let mag = |y: u32| if y & 1 == 0 { 0 } else { MATRIX_A };
        for k in 0..N - M {
            let y = (self.mt[k] & UPPER_MASK) | (self.mt[k + 1] & LOWER_MASK);
            self.mt[k] = self.mt[k + M] ^ (y >> 1) ^ mag(y);
        }
        for k in N - M..N - 1 {
            let y = (self.mt[k] & UPPER_MASK) | (self.mt[k + 1] & LOWER_MASK);
            self.mt[k] = self.mt[k + M - N] ^ (y >> 1) ^ mag(y);
        }
        let y = (self.mt[N - 1] & UPPER_MASK) | (self.mt[0] & LOWER_MASK);
        self.mt[N - 1] = self.mt[M - 1] ^ (y >> 1) ^ mag(y);
        self.index = 0;
    }

    /// `genrand_uint32`: the next raw 32-bit output.
    pub fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            self.twist();
        }
        let mut y = self.mt[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// `rng.getrandbits(k)` as 32-bit limbs, least significant first.
    ///
    /// Python fills the limbs from the least significant end and drops the low bits of the
    /// last, most significant, draw. `k == 0` draws nothing.
    pub fn getrandbits_limbs(&mut self, k: u64) -> Vec<u32> {
        if k == 0 {
            return Vec::new();
        }
        if k <= 32 {
            return vec![self.next_u32() >> (32 - k)];
        }
        let words = usize::try_from((k - 1) / 32 + 1).expect("bit count fits in memory");
        let mut out = Vec::with_capacity(words);
        let mut remaining = k;
        for _ in 0..words {
            let mut r = self.next_u32();
            if remaining < 32 {
                r >>= 32 - remaining;
            }
            out.push(r);
            remaining = remaining.saturating_sub(32);
        }
        out
    }

    /// `rng.getrandbits(k)` for `k <= 128`.
    pub fn getrandbits(&mut self, k: u32) -> u128 {
        assert!(k <= 128, "getrandbits beyond 128 bits needs getrandbits_limbs");
        self.getrandbits_limbs(u64::from(k))
            .iter()
            .enumerate()
            .fold(0u128, |acc, (i, limb)| acc | (u128::from(*limb) << (32 * i)))
    }

    /// `rng._randbelow(n)` (`_randbelow_with_getrandbits`): uniform in `0..n` by rejection.
    ///
    /// Python loops forever for `n == 0`; every caller checks first, so this panics instead.
    pub fn randbelow(&mut self, n: u128) -> u128 {
        assert!(n > 0, "_randbelow is defined for n > 0");
        let k = 128 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `rng.randrange(stop)`.
    pub fn randrange_to(&mut self, stop: i128) -> PyResult<i128> {
        if stop > 0 {
            return Ok(self.randbelow_i128(stop));
        }
        Err(PyError::value("empty range for randrange()"))
    }

    /// `rng.randrange(start, stop)`.
    pub fn randrange(&mut self, start: i128, stop: i128) -> PyResult<i128> {
        let width = stop.checked_sub(start).expect("randrange width fits in i128");
        if width > 0 {
            return Ok(start + self.randbelow_i128(width));
        }
        Err(PyError::value(format!("empty range in randrange({start}, {stop})")))
    }

    /// `rng.randrange(start, stop, step)`.
    pub fn randrange_step(&mut self, start: i128, stop: i128, step: i128) -> PyResult<i128> {
        if step == 1 {
            return self.randrange(start, stop);
        }
        let width = stop.checked_sub(start).expect("randrange width fits in i128");
        let n = if step > 0 {
            floor_div(width + step - 1, step)
        } else if step < 0 {
            floor_div(width + step + 1, step)
        } else {
            return Err(PyError::value("zero step for randrange()"));
        };
        if n <= 0 {
            return Err(PyError::value(format!("empty range in randrange({start}, {stop}, {step})")));
        }
        Ok(start + step * self.randbelow_i128(n))
    }

    /// `rng.randint(a, b)`, which is `randrange(a, b + 1)` (and reports that range on error).
    pub fn randint(&mut self, a: i128, b: i128) -> PyResult<i128> {
        self.randrange(a, b.checked_add(1).expect("randint bound fits in i128"))
    }

    /// The index `rng.choice(seq)` picks for a sequence of `len` items.
    pub fn choice_index(&mut self, len: usize) -> PyResult<usize> {
        if len == 0 {
            return Err(PyError::index("Cannot choose from an empty sequence"));
        }
        let index = self.randbelow(len as u128);
        Ok(usize::try_from(index).expect("choice index is below the sequence length"))
    }

    /// `rng.choice(seq)`.
    pub fn choice<'a, T>(&mut self, seq: &'a [T]) -> PyResult<&'a T> {
        let index = self.choice_index(seq.len())?;
        Ok(&seq[index])
    }

    /// `rng.shuffle(x)`: Fisher-Yates from the end, as Python does it.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = usize::try_from(self.randbelow(i as u128 + 1)).expect("index within slice");
            items.swap(i, j);
        }
    }

    /// `rng.random()`: 53 random bits as a float in `[0, 1)`.
    pub fn random(&mut self) -> f64 {
        let a = self.next_u32() >> 5;
        let b = self.next_u32() >> 6;
        (f64::from(a) * 67_108_864.0 + f64::from(b)) * (1.0 / 9_007_199_254_740_992.0)
    }

    fn randbelow_i128(&mut self, n: i128) -> i128 {
        let n = u128::try_from(n).expect("positive bound");
        i128::try_from(self.randbelow(n)).expect("below a positive i128 bound")
    }
}

/// Python's `//` for integers: the quotient rounded toward negative infinity.
fn floor_div(a: i128, b: i128) -> i128 {
    let quotient = a / b;
    if (a % b != 0) && ((a < 0) != (b < 0)) {
        quotient - 1
    } else {
        quotient
    }
}

/// The 32-bit limbs (least significant first) of a big-endian unsigned integer.
fn big_endian_limbs(bytes: &[u8]) -> Vec<u32> {
    let mut limbs = Vec::with_capacity(bytes.len() / 4 + 1);
    let mut end = bytes.len();
    while end > 0 {
        let start = end.saturating_sub(4);
        let limb = bytes[start..end].iter().fold(0u32, |acc, byte| (acc << 8) | u32::from(*byte));
        limbs.push(limb);
        end = start;
    }
    limbs
}

/// The integer seed for SHA-256 digest bytes: `int.from_bytes(digest[:8], "big")`.
pub fn seed_from_digest(digest: &[u8; 32]) -> u64 {
    let mut first = [0u8; 8];
    first.copy_from_slice(&digest[..8]);
    u64::from_be_bytes(first)
}

/// A package tool's dice seed: the first eight bytes of the SHA-256 of
/// `canonical_json_bytes(basis)`, read big-endian (`application/package_runtime.py`).
///
/// The caller builds `basis` in Python's key order; the canonical encoding sorts the keys.
pub fn seed_from_basis(basis: &Json) -> u64 {
    let bytes = sylvia_base::pyjson::canonical_bytes(basis);
    seed_from_digest(&sylvia_base::hash::sha256(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floor_division_matches_python() {
        assert_eq!(floor_div(7, 2), 3);
        assert_eq!(floor_div(-7, 2), -4);
        assert_eq!(floor_div(7, -2), -4);
        assert_eq!(floor_div(-7, -2), 3);
        assert_eq!(floor_div(-6, 2), -3);
    }

    #[test]
    fn limbs_read_big_endian_bytes() {
        assert_eq!(big_endian_limbs(&[0x01, 0x02, 0x03, 0x04, 0x05]), vec![0x0203_0405, 0x01]);
        assert_eq!(big_endian_limbs(&[]), Vec::<u32>::new());
    }

    #[test]
    fn zero_seed_uses_one_limb() {
        let mut a = PyRandom::from_u64(0);
        let mut b = PyRandom::from_int_limbs(&[0, 0, 0]);
        let mut c = PyRandom::from_int_limbs(&[]);
        for _ in 0..10 {
            let x = a.next_u32();
            assert_eq!(x, b.next_u32());
            assert_eq!(x, c.next_u32());
        }
    }
}
