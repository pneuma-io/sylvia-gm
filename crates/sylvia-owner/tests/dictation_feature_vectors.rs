//! The parts of faster-whisper's log-Mel features computed in Rust, against vectors from the
//! Python snapshot, bit for bit (`vectors/generate_dictation_feature_vectors.py`: NumPy 2.5.4 on
//! Windows x86-64): the Hann window, the Mel filter bank, `np.fft.rfft` and complex64 `np.abs`.
//! The full features need NumPy's OpenBLAS and are checked by `dictation_native` (the
//! `dictation` feature).
#![cfg(windows)]

use serde_json::Value as J;
use sylvia_owner::dictation::features::{cabs, hann_window, mel_filters, RealFft, N_FFT};

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/dictation_feature_vectors.json")).expect("valid JSON")
    })
}

fn floats(hex_text: &J) -> Vec<f32> {
    hex::decode(hex_text.as_str().unwrap())
        .unwrap()
        .as_chunks::<4>()
        .0
        .iter()
        .map(|bytes| f32::from_le_bytes(*bytes))
        .collect()
}

/// How many values differ in their bits, and the first one.
fn compare(name: &str, got: &[f32], expected: &[f32]) -> Option<String> {
    if got.len() != expected.len() {
        return Some(format!("{name}: {} values, expected {}", got.len(), expected.len()));
    }
    let differing: Vec<usize> =
        (0..got.len()).filter(|&index| got[index].to_bits() != expected[index].to_bits()).collect();
    differing.first().map(|&first| {
        format!(
            "{name}: {} of {} differ; first at {first}: got {:08x}, expected {:08x}",
            differing.len(),
            got.len(),
            got[first].to_bits(),
            expected[first].to_bits()
        )
    })
}

#[test]
fn the_window_and_filter_bank_match_numpy() {
    let mut failures = Vec::new();
    failures.extend(compare("window", &hann_window(), &floats(&vectors()["window"])));
    failures.extend(compare("mel_filters", &mel_filters(), &floats(&vectors()["mel_filters"])));
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn rfft_frames_match_numpy_pocketfft() {
    let fft = RealFft::new(N_FFT);
    let mut failures = Vec::new();
    for frame in vectors()["rfft_frames"].as_array().unwrap() {
        let spectrum = fft.rfft(&floats(&frame["input"]));
        let real: Vec<f32> = spectrum.iter().map(|value| value.0).collect();
        let imaginary: Vec<f32> = spectrum.iter().map(|value| value.1).collect();
        let magnitude: Vec<f32> = spectrum.iter().map(|value| cabs(value.0, value.1)).collect();
        let seed = &frame["seed"];
        failures.extend(compare(&format!("seed {seed} real"), &real, &floats(&frame["real"])));
        failures.extend(compare(&format!("seed {seed} imag"), &imaginary, &floats(&frame["imag"])));
        failures.extend(compare(&format!("seed {seed} abs"), &magnitude, &floats(&frame["abs"])));
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn complex_abs_matches_numpy() {
    let cases = &vectors()["abs"];
    let (real, imaginary) = (floats(&cases["real"]), floats(&cases["imag"]));
    let got: Vec<f32> = real.iter().zip(&imaginary).map(|(re, im)| cabs(*re, *im)).collect();
    if let Some(failure) = compare("abs", &got, &floats(&cases["abs"])) {
        panic!("{failure}");
    }
}
