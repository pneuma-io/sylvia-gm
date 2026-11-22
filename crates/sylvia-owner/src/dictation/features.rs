//! faster-whisper 1.2.1's `FeatureExtractor` (`feature_extractor.py`): the 80-band log-Mel
//! spectrogram Whisper reads, computed exactly as its NumPy code computes it.
//!
//! Each NumPy step is reproduced with the arithmetic NumPy 2.5.4 performs on the reference
//! machine (x86-64 with AVX2 and FMA, NumPy's `X86_V3` loops):
//! - `np.fft.rfft` on float32 frames: pocketfft's real transform (`rfftp`, factors 4, 4, 5,
//!   5 for 400 points) with twiddles computed in double precision, translated from
//!   `pocketfft_hdronly.h` (BSD-3-Clause; `docs/rust-port/third-party-ports.md`);
//! - `np.abs` on complex64: NumPy's SIMD `cabsolute`, `larger * sqrt(fma(r, r, 1))`;
//! - `mel_filters @ magnitudes`: NumPy's own OpenBLAS, called as NumPy's `matmul` calls it
//!   ([`MelProduct`]); its kernel paths and thread partition decide the bits, so nothing else
//!   reproduces them on every CPU;
//! - `np.log10` on float32: the C library's `log10f`, which Rust's `f32::log10` also calls;
//! - the filter bank and the Hann window: float64 arithmetic with the C library's `exp`, `log`
//!   and `cos`, then rounded to float32.
//!
//! The reference vectors (`dictation_feature_vectors`) check every value bit for bit.

/// `n_fft`.
pub const N_FFT: usize = 400;
/// `hop_length`.
pub const HOP_LENGTH: usize = 160;
/// `feature_size`.
pub const N_MELS: usize = 80;
/// `sampling_rate`.
pub const SAMPLING_RATE: usize = 16_000;
/// Frequency bins of a 400-point real transform.
pub const N_BINS: usize = N_FFT / 2 + 1;

/// `np.linspace(start, stop, num)` in float64.
fn linspace(start: f64, stop: f64, num: usize) -> Vec<f64> {
    let div = (num - 1) as f64;
    let delta = stop - start;
    let step = delta / div;
    let mut values: Vec<f64> = (0..num)
        .map(|index| if step == 0.0 { index as f64 / div * delta } else { index as f64 * step })
        .map(|value| value + start)
        .collect();
    if num > 1 {
        values[num - 1] = stop;
    }
    values
}

/// `FeatureExtractor.get_mel_filters(16000, 400, n_mels=80).astype("float32")`, row-major
/// `[N_MELS][N_BINS]`.
pub fn mel_filters() -> Vec<f32> {
    let sr = SAMPLING_RATE as f64;
    // np.fft.rfftfreq(n=400, d=1.0/sr)
    let val = 1.0 / (N_FFT as f64 * (1.0 / sr));
    let fftfreqs: Vec<f64> = (0..N_BINS).map(|bin| bin as f64 * val).collect();
    let mels = linspace(0.0, 45.245640471924965, N_MELS + 2);
    let f_min = 0.0;
    let f_sp = 200.0 / 3.0;
    let mut freqs: Vec<f64> = mels.iter().map(|mel| f_min + f_sp * mel).collect();
    let min_log_hz = 1000.0;
    let min_log_mel = (min_log_hz - f_min) / f_sp;
    let logstep = 6.4_f64.ln() / 27.0;
    for (freq, mel) in freqs.iter_mut().zip(&mels) {
        if *mel >= min_log_mel {
            *freq = min_log_hz * (logstep * (mel - min_log_mel)).exp();
        }
    }
    let fdiff: Vec<f64> = freqs.windows(2).map(|pair| pair[1] - pair[0]).collect();
    let mut weights = vec![0.0_f32; N_MELS * N_BINS];
    for band in 0..N_MELS {
        let enorm = 2.0 / (freqs[band + 2] - freqs[band]);
        for bin in 0..N_BINS {
            let lower = -(freqs[band] - fftfreqs[bin]) / fdiff[band];
            let upper = (freqs[band + 2] - fftfreqs[bin]) / fdiff[band + 1];
            let weight = numpy_maximum(0.0, numpy_minimum(lower, upper));
            weights[band * N_BINS + bin] = (weight * enorm) as f32;
        }
    }
    weights
}

/// `np.minimum` on two finite float64 values (AVX2 `vminpd` keeps the second operand on ties).
fn numpy_minimum(a: f64, b: f64) -> f64 {
    if a < b {
        a
    } else {
        b
    }
}

/// `np.maximum` on two finite float64 values (AVX2 `vmaxpd` keeps the second operand on ties).
fn numpy_maximum(a: f64, b: f64) -> f64 {
    if a > b {
        a
    } else {
        b
    }
}

/// `np.hanning(401)[:-1].astype("float32")`.
pub fn hann_window() -> Vec<f32> {
    let m = (N_FFT + 1) as f64;
    // n = arange(1 - M, M, 2); 0.5 + 0.5 * cos(pi * n / (M - 1))
    (0..N_FFT)
        .map(|index| {
            let n = (1.0 - m) + 2.0 * index as f64;
            (0.5 + 0.5 * (std::f64::consts::PI * n / (m - 1.0)).cos()) as f32
        })
        .collect()
}

/// pocketfft's real forward transform of a fixed length, in double precision: for a float32
/// array NumPy runs the double loop (`rfftp<double>`) and rounds the result to complex64.
pub struct RealFft {
    length: usize,
    /// `(factor, twiddle offset)` from the first factor to the last.
    factors: Vec<(usize, usize)>,
    twiddles: Vec<f64>,
}

/// `sincos_2pibyn<double>`.
struct SinCos {
    n: usize,
    mask: usize,
    shift: usize,
    v1: Vec<(f64, f64)>,
    v2: Vec<(f64, f64)>,
}

impl SinCos {
    fn calc(x: usize, n: usize, ang: f64) -> (f64, f64) {
        let mut x = x << 3;
        if x < 4 * n {
            if x < 2 * n {
                if x < n {
                    return ((x as f64 * ang).cos(), (x as f64 * ang).sin());
                }
                return (((2 * n - x) as f64 * ang).sin(), ((2 * n - x) as f64 * ang).cos());
            }
            x -= 2 * n;
            if x < n {
                return (-(x as f64 * ang).sin(), (x as f64 * ang).cos());
            }
            return (-((2 * n - x) as f64 * ang).cos(), ((2 * n - x) as f64 * ang).sin());
        }
        x = 8 * n - x;
        if x < 2 * n {
            if x < n {
                return ((x as f64 * ang).cos(), -(x as f64 * ang).sin());
            }
            return (((2 * n - x) as f64 * ang).sin(), -((2 * n - x) as f64 * ang).cos());
        }
        x -= 2 * n;
        if x < n {
            return (-(x as f64 * ang).sin(), -(x as f64 * ang).cos());
        }
        (-((2 * n - x) as f64 * ang).cos(), -((2 * n - x) as f64 * ang).sin())
    }

    fn new(n: usize) -> Self {
        // `Thigh(0.25L*pi/n)`: MSVC's long double is double.
        let ang = 0.25 * std::f64::consts::PI / n as f64;
        let nval = (n + 2) / 2;
        let mut shift = 1;
        while (1_usize << shift) * (1_usize << shift) < nval {
            shift += 1;
        }
        let mask = (1_usize << shift) - 1;
        let mut v1 = vec![(1.0, 0.0); mask + 1];
        for (index, value) in v1.iter_mut().enumerate().skip(1) {
            *value = Self::calc(index, n, ang);
        }
        let mut v2 = vec![(1.0, 0.0); nval.div_ceil(mask + 1)];
        for (index, value) in v2.iter_mut().enumerate().skip(1) {
            *value = Self::calc(index * (mask + 1), n, ang);
        }
        Self { n, mask, shift, v1, v2 }
    }

    fn get(&self, index: usize) -> (f64, f64) {
        if 2 * index <= self.n {
            let (x1, x2) = (self.v1[index & self.mask], self.v2[index >> self.shift]);
            return (x1.0 * x2.0 - x1.1 * x2.1, x1.0 * x2.1 + x1.1 * x2.0);
        }
        let index = self.n - index;
        let (x1, x2) = (self.v1[index & self.mask], self.v2[index >> self.shift]);
        (x1.0 * x2.0 - x1.1 * x2.1, -(x1.0 * x2.1 + x1.1 * x2.0))
    }
}

impl RealFft {
    /// `rfftp<double>(length)`, for lengths whose factors are 4 and 5 (Whisper's 400).
    pub fn new(length: usize) -> Self {
        // factorize(): fours first, then a two moved to the front, then odd factors.
        let mut factors = Vec::new();
        let mut len = length;
        while len.is_multiple_of(4) {
            factors.push(4);
            len >>= 2;
        }
        assert!(!len.is_multiple_of(2), "a factor of 2 is not ported");
        let mut divisor = 3;
        while divisor * divisor <= len {
            while len.is_multiple_of(divisor) {
                factors.push(divisor);
                len /= divisor;
            }
            divisor += 2;
        }
        if len > 1 {
            factors.push(len);
        }
        assert!(factors.iter().all(|factor| *factor == 4 || *factor == 5), "only factors 4 and 5 are ported");
        // comp_twiddle()
        let twiddle = SinCos::new(length);
        let mut twiddles = Vec::new();
        let mut placed = Vec::new();
        let mut l1 = 1;
        for (k, &ip) in factors.iter().enumerate() {
            let ido = length / (l1 * ip);
            let offset = twiddles.len();
            if k < factors.len() - 1 {
                twiddles.resize(offset + (ip - 1) * (ido - 1), 0.0);
                for j in 1..ip {
                    for i in 1..=(ido - 1) / 2 {
                        let (r, im) = twiddle.get(j * l1 * i);
                        twiddles[offset + (j - 1) * (ido - 1) + 2 * i - 2] = r;
                        twiddles[offset + (j - 1) * (ido - 1) + 2 * i - 1] = im;
                    }
                }
            }
            placed.push((ip, offset));
            l1 *= ip;
        }
        Self { length, factors: placed, twiddles }
    }

    /// `exec(c, 1, r2hc=true)`: `data` becomes FFTPACK's half-complex order
    /// `R0, R1, I1, ..., R(n/2)`.
    pub fn forward(&self, data: &mut [f64]) {
        assert_eq!(data.len(), self.length);
        let mut scratch = vec![0.0_f64; self.length];
        let mut in_data = true;
        let mut l1 = self.length;
        for &(ip, offset) in self.factors.iter().rev() {
            let ido = self.length / l1;
            l1 /= ip;
            let wa = &self.twiddles[offset..];
            let (source, target): (&[f64], &mut [f64]) =
                if in_data { (&*data, &mut scratch) } else { (&scratch, &mut *data) };
            // Rust cannot borrow both halves at once through the same names; copy the source.
            let source = source.to_vec();
            match ip {
                4 => radf4(ido, l1, &source, target, wa),
                5 => radf5(ido, l1, &source, target, wa),
                _ => unreachable!("factors are checked in new"),
            }
            in_data = !in_data;
        }
        if !in_data {
            data.copy_from_slice(&scratch);
        }
    }

    /// `np.fft.rfft(frame)` for one float32 frame: `N/2 + 1` complex64 (re, im) pairs, NumPy's
    /// unpacking of the half-complex result, each rounded from double.
    pub fn rfft(&self, frame: &[f32]) -> Vec<(f32, f32)> {
        let mut data: Vec<f64> = frame.iter().map(|value| f64::from(*value)).collect();
        self.forward(&mut data);
        let bins = self.length / 2 + 1;
        let mut out = Vec::with_capacity(bins);
        out.push((data[0] as f32, 0.0));
        for bin in 1..bins {
            let real = data[2 * bin - 1];
            let imaginary = if 2 * bin < self.length { data[2 * bin] } else { 0.0 };
            out.push((real as f32, imaginary as f32));
        }
        out
    }
}

// pocketfft's constants are kept exactly as written in `pocketfft_hdronly.h`.
#[allow(clippy::excessive_precision, clippy::approx_constant)]
fn radf4(ido: usize, l1: usize, cc: &[f64], ch: &mut [f64], wa: &[f64]) {
    let hsqt2 = 0.707_106_781_186_547_524_400_844_362_104_849_f64;
    let cc_at = |a: usize, b: usize, c: usize| cc[a + ido * (b + l1 * c)];
    let ch_at = |a: usize, b: usize, c: usize| a + ido * (b + 4 * c);
    let wa_at = |x: usize, i: usize| wa[i + x * (ido - 1)];
    for k in 0..l1 {
        let (tr1, t) = (cc_at(0, k, 3) + cc_at(0, k, 1), cc_at(0, k, 3) - cc_at(0, k, 1));
        ch[ch_at(0, 2, k)] = t;
        let (tr2, t) = (cc_at(0, k, 0) + cc_at(0, k, 2), cc_at(0, k, 0) - cc_at(0, k, 2));
        ch[ch_at(ido - 1, 1, k)] = t;
        ch[ch_at(0, 0, k)] = tr2 + tr1;
        ch[ch_at(ido - 1, 3, k)] = tr2 - tr1;
    }
    if ido & 1 == 0 {
        for k in 0..l1 {
            let ti1 = -hsqt2 * (cc_at(ido - 1, k, 1) + cc_at(ido - 1, k, 3));
            let tr1 = hsqt2 * (cc_at(ido - 1, k, 1) - cc_at(ido - 1, k, 3));
            ch[ch_at(ido - 1, 0, k)] = cc_at(ido - 1, k, 0) + tr1;
            ch[ch_at(ido - 1, 2, k)] = cc_at(ido - 1, k, 0) - tr1;
            ch[ch_at(0, 3, k)] = ti1 + cc_at(ido - 1, k, 2);
            ch[ch_at(0, 1, k)] = ti1 - cc_at(ido - 1, k, 2);
        }
    }
    if ido <= 2 {
        return;
    }
    for k in 0..l1 {
        let mut i = 2;
        while i < ido {
            let ic = ido - i;
            // MULPM(a, b, c, d, e, f): a = c*e + d*f; b = c*f - d*e
            let mulpm = |c: f64, d: f64, e: f64, f: f64| (c * e + d * f, c * f - d * e);
            let (cr2, ci2) = mulpm(wa_at(0, i - 2), wa_at(0, i - 1), cc_at(i - 1, k, 1), cc_at(i, k, 1));
            let (cr3, ci3) = mulpm(wa_at(1, i - 2), wa_at(1, i - 1), cc_at(i - 1, k, 2), cc_at(i, k, 2));
            let (cr4, ci4) = mulpm(wa_at(2, i - 2), wa_at(2, i - 1), cc_at(i - 1, k, 3), cc_at(i, k, 3));
            let (tr1, tr4) = (cr4 + cr2, cr4 - cr2);
            let (ti1, ti4) = (ci2 + ci4, ci2 - ci4);
            let (tr2, tr3) = (cc_at(i - 1, k, 0) + cr3, cc_at(i - 1, k, 0) - cr3);
            let (ti2, ti3) = (cc_at(i, k, 0) + ci3, cc_at(i, k, 0) - ci3);
            ch[ch_at(i - 1, 0, k)] = tr2 + tr1;
            ch[ch_at(ic - 1, 3, k)] = tr2 - tr1;
            ch[ch_at(i, 0, k)] = ti1 + ti2;
            ch[ch_at(ic, 3, k)] = ti1 - ti2;
            ch[ch_at(i - 1, 2, k)] = tr3 + ti4;
            ch[ch_at(ic - 1, 1, k)] = tr3 - ti4;
            ch[ch_at(i, 2, k)] = tr4 + ti3;
            ch[ch_at(ic, 1, k)] = tr4 - ti3;
            i += 2;
        }
    }
}

#[allow(clippy::excessive_precision)]
fn radf5(ido: usize, l1: usize, cc: &[f64], ch: &mut [f64], wa: &[f64]) {
    let tr11 = 0.309_016_994_374_947_424_102_293_417_182_819_1_f64;
    let ti11 = 0.951_056_516_295_153_572_116_439_333_379_382_1_f64;
    let tr12 = -0.809_016_994_374_947_424_102_293_417_182_819_1_f64;
    let ti12 = 0.587_785_252_292_473_129_168_705_954_639_072_8_f64;
    let cc_at = |a: usize, b: usize, c: usize| cc[a + ido * (b + l1 * c)];
    let ch_at = |a: usize, b: usize, c: usize| a + ido * (b + 5 * c);
    let wa_at = |x: usize, i: usize| wa[i + x * (ido - 1)];
    for k in 0..l1 {
        let (cr2, ci5) = (cc_at(0, k, 4) + cc_at(0, k, 1), cc_at(0, k, 4) - cc_at(0, k, 1));
        let (cr3, ci4) = (cc_at(0, k, 3) + cc_at(0, k, 2), cc_at(0, k, 3) - cc_at(0, k, 2));
        ch[ch_at(0, 0, k)] = cc_at(0, k, 0) + cr2 + cr3;
        ch[ch_at(ido - 1, 1, k)] = cc_at(0, k, 0) + tr11 * cr2 + tr12 * cr3;
        ch[ch_at(0, 2, k)] = ti11 * ci5 + ti12 * ci4;
        ch[ch_at(ido - 1, 3, k)] = cc_at(0, k, 0) + tr12 * cr2 + tr11 * cr3;
        ch[ch_at(0, 4, k)] = ti12 * ci5 - ti11 * ci4;
    }
    if ido == 1 {
        return;
    }
    for k in 0..l1 {
        let mut i = 2;
        let mut ic = ido - 2;
        while i < ido {
            let mulpm = |c: f64, d: f64, e: f64, f: f64| (c * e + d * f, c * f - d * e);
            let (mut dr2, mut di2) =
                mulpm(wa_at(0, i - 2), wa_at(0, i - 1), cc_at(i - 1, k, 1), cc_at(i, k, 1));
            let (mut dr3, mut di3) =
                mulpm(wa_at(1, i - 2), wa_at(1, i - 1), cc_at(i - 1, k, 2), cc_at(i, k, 2));
            let (mut dr4, mut di4) =
                mulpm(wa_at(2, i - 2), wa_at(2, i - 1), cc_at(i - 1, k, 3), cc_at(i, k, 3));
            let (mut dr5, mut di5) =
                mulpm(wa_at(3, i - 2), wa_at(3, i - 1), cc_at(i - 1, k, 4), cc_at(i, k, 4));
            // POCKETFFT_REARRANGE(rx, ix, ry, iy)
            let rearrange = |rx: &mut f64, ix: &mut f64, ry: &mut f64, iy: &mut f64| {
                let (t1, t2, t3, t4) = (*rx + *ry, *ry - *rx, *ix + *iy, *ix - *iy);
                *rx = t1;
                *ix = t3;
                *ry = t4;
                *iy = t2;
            };
            rearrange(&mut dr2, &mut di2, &mut dr5, &mut di5);
            rearrange(&mut dr3, &mut di3, &mut dr4, &mut di4);
            ch[ch_at(i - 1, 0, k)] = cc_at(i - 1, k, 0) + dr2 + dr3;
            ch[ch_at(i, 0, k)] = cc_at(i, k, 0) + di2 + di3;
            let tr2 = cc_at(i - 1, k, 0) + tr11 * dr2 + tr12 * dr3;
            let ti2 = cc_at(i, k, 0) + tr11 * di2 + tr12 * di3;
            let tr3 = cc_at(i - 1, k, 0) + tr12 * dr2 + tr11 * dr3;
            let ti3 = cc_at(i, k, 0) + tr12 * di2 + tr11 * di3;
            let tr5 = ti11 * dr5 + ti12 * dr4;
            let ti5 = ti11 * di5 + ti12 * di4;
            let tr4 = ti12 * dr5 - ti11 * dr4;
            let ti4 = ti12 * di5 - ti11 * di4;
            ch[ch_at(i - 1, 2, k)] = tr2 + tr5;
            ch[ch_at(ic - 1, 1, k)] = tr2 - tr5;
            ch[ch_at(i, 2, k)] = ti5 + ti2;
            ch[ch_at(ic, 1, k)] = ti5 - ti2;
            ch[ch_at(i - 1, 4, k)] = tr3 + tr4;
            ch[ch_at(ic - 1, 3, k)] = tr3 - tr4;
            ch[ch_at(i, 4, k)] = ti4 + ti3;
            ch[ch_at(ic, 3, k)] = ti4 - ti3;
            i += 2;
            ic = ic.wrapping_sub(2);
        }
    }
}

/// `np.pad(x, (left, right), mode="reflect")` on a 1-D float32 array, as NumPy fills it: chunk
/// by chunk while the pad is wider than the data, and a single element repeated.
pub fn pad_reflect(input: &[f32], left: usize, right: usize) -> Vec<f32> {
    let original = input.len();
    let mut padded = vec![0.0_f32; left + original + right];
    padded[left..left + original].copy_from_slice(input);
    if original == 0 {
        return padded;
    }
    if original == 1 {
        let edge = input[0];
        padded.iter_mut().for_each(|value| *value = edge);
        return padded;
    }
    let (mut left_pad, mut right_pad) = (left, right);
    while left_pad > 0 || right_pad > 0 {
        let total = padded.len();
        let old_length = total - right_pad - left_pad;
        // include_edge=False
        let old_length = (old_length - 1) / (original - 1) * (original - 1) + 1 - 1;
        if left_pad > 0 {
            let chunk = old_length.min(left_pad);
            // padded[left_pad + chunk : left_pad : -1]
            let source: Vec<f32> = (0..chunk).map(|j| padded[left_pad + chunk - j]).collect();
            padded[left_pad - chunk..left_pad].copy_from_slice(&source);
            left_pad -= chunk;
        }
        if right_pad > 0 {
            let chunk = old_length.min(right_pad);
            // start = -right_pad - 2 (from the end), stepping back `chunk` items
            let start = total - right_pad - 2;
            let source: Vec<f32> = (0..chunk).map(|j| padded[start - j]).collect();
            let at = total - right_pad;
            padded[at..at + chunk].copy_from_slice(&source);
            right_pad -= chunk;
        }
    }
    padded
}

/// NumPy's SIMD `np.abs` of one complex64 value with FMA.
pub fn cabs(real: f32, imaginary: f32) -> f32 {
    let mut re = real.abs();
    let mut im = imaginary.abs();
    let inf = f32::INFINITY;
    if re == inf {
        im = inf;
    }
    if im == inf {
        re = inf;
    }
    if re.is_nan() {
        im = f32::NAN;
    }
    if im.is_nan() {
        re = f32::NAN;
    }
    // npyv_max_f32 / npyv_min_f32 (AVX `vmaxps`/`vminps`: the second operand on ties or NaN).
    let larger = if re > im { re } else { im };
    let smaller = if im < re { im } else { re };
    let divide = !(larger == 0.0 || smaller == inf);
    let ratio = if divide { smaller / larger } else { 0.0 };
    ratio.mul_add(ratio, 1.0).sqrt() * larger
}

/// The log-Mel features: `rows` bands by `frames` frames, row-major.
#[derive(Debug, Clone, PartialEq)]
pub struct Features {
    /// Bands (80).
    pub rows: usize,
    /// Frames.
    pub frames: usize,
    /// `rows * frames` values.
    pub values: Vec<f32>,
}

/// `mel_filters @ magnitudes`: `filters` C-ordered `N_MELS × N_BINS`, `magnitudes` stored frame
/// by frame (NumPy's Fortran-ordered `N_BINS × frames`), the result C-ordered `N_MELS × frames`.
/// In the product this is NumPy's own OpenBLAS (`super::openblas`).
pub trait MelProduct {
    /// The product.
    fn product(&self, filters: &[f32], magnitudes: &[f32], frames: usize) -> Vec<f32>;
}

#[cfg(all(windows, feature = "dictation"))]
impl MelProduct for super::openblas::OpenBlas {
    fn product(&self, filters: &[f32], magnitudes: &[f32], frames: usize) -> Vec<f32> {
        super::openblas::OpenBlas::product(self, filters, N_MELS, N_BINS, magnitudes, frames)
    }
}

/// `FeatureExtractor()(waveform, padding=padding)`.
pub fn log_mel(waveform: &[f32], padding: usize, blas: &dyn MelProduct) -> Features {
    let mut audio = waveform.to_vec();
    audio.extend(std::iter::repeat_n(0.0_f32, padding));
    let window = hann_window();
    let filters = mel_filters();
    let fft = RealFft::new(N_FFT);
    // stft(center=True, mode="reflect")
    let padded = pad_reflect(&audio, N_FFT / 2, N_FFT / 2);
    let n_frames = if padded.len() >= N_FFT { 1 + (padded.len() - N_FFT) / HOP_LENGTH } else { 0 };
    // magnitudes = np.abs(stft[..., :-1]) ** 2: the last frame is dropped.
    let frames = n_frames.saturating_sub(1);
    let mut magnitudes = vec![0.0_f32; N_BINS * frames];
    let mut frame = vec![0.0_f32; N_FFT];
    for t in 0..frames {
        for (index, value) in frame.iter_mut().enumerate() {
            *value = padded[t * HOP_LENGTH + index] * window[index];
        }
        for (bin, (re, im)) in fft.rfft(&frame).into_iter().enumerate() {
            let magnitude = cabs(re, im);
            magnitudes[t * N_BINS + bin] = magnitude * magnitude;
        }
    }
    let mut values = blas.product(&filters, &magnitudes, frames);
    // log10(clip(mel, 1e-10)); maximum(log, max - 8); (log + 4) / 4
    let floor = 1e-10_f32;
    for value in values.iter_mut() {
        *value = if *value < floor { floor } else { *value }.log10();
    }
    let max = values.iter().copied().fold(f32::NEG_INFINITY, |a, b| if b > a { b } else { a });
    let limit = max - 8.0;
    for value in values.iter_mut() {
        let kept = if *value > limit { *value } else { limit };
        *value = (kept + 4.0) / 4.0;
    }
    Features { rows: N_MELS, frames, values }
}
