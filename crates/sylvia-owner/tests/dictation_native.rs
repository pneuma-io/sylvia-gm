//! Dictation on the native libraries the product ships, against the Python snapshot, bit for bit.
//! Windows, `dictation` feature, never part of check.sh (see the dictation check in
//! docs/rust-port/README.md).
//!
//! `SYLVIA_DICTATION_NATIVE_DIR` names a folder holding the shipped DLLs: `swresample-6.dll` and
//! `avutil-60.dll` (the LGPL FFmpeg 8.1.2 build) and NumPy 2.5.4's
//! `libscipy_openblas64_-…dll` (`docs/rust-port/tools/stage_dictation_natives.sh` stages it).
//!
//! OpenBLAS reads its thread count from the environment when it loads, as under NumPy, so this
//! harness runs itself again once per setting: `OMP_NUM_THREADS=2` (the desktop's setting for the
//! speech worker) checks every feature vector, and `OMP_NUM_THREADS=1` checks the vectors
//! recorded under it, so a change in threading shows. The `=2` run also checks the decode and
//! resampling vectors.

use serde_json::Value as J;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(windows)]
use sylvia_owner::dictation::{
    audio::{decode, Pcm},
    features::log_mel,
    openblas::{OpenBlas, LIBRARY as OPENBLAS},
    swresample::{Swresample, LIBRARY as SWRESAMPLE},
};

fn json(text: &str) -> J {
    serde_json::from_str(text).expect("valid JSON")
}

fn floats(hex_text: &str) -> Vec<f32> {
    hex::decode(hex_text).unwrap().as_chunks::<4>().0.iter().map(|bytes| f32::from_le_bytes(*bytes)).collect()
}

fn bytes_of(values: &[f32]) -> Vec<u8> {
    values.iter().flat_map(|value| value.to_le_bytes()).collect()
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn lcg_int16(seed: u64, count: usize, amplitude: i64) -> Vec<i16> {
    let mut state = seed;
    (0..count)
        .map(|_| {
            state = (state * 1_664_525 + 1_013_904_223) % (1 << 32);
            (((state >> 16) as i64) % (2 * amplitude + 1) - amplitude) as i16
        })
        .collect()
}

/// The generator's signals, as faster-whisper scales decoded 16-bit audio.
fn signal(spec: &J) -> Vec<f32> {
    let count = spec["count"].as_u64().unwrap() as usize;
    let pcm: Vec<i16> = match spec["kind"].as_str().unwrap() {
        "zeros" => vec![0; count],
        "lcg" => lcg_int16(spec["seed"].as_u64().unwrap(), count, spec["amplitude"].as_i64().unwrap()),
        _ => {
            let period = spec["period"].as_u64().unwrap() as usize;
            let amplitude = spec["amplitude"].as_i64().unwrap() as i16;
            (0..count)
                .map(|i| if (i / (period / 2)).is_multiple_of(2) { amplitude } else { -amplitude })
                .collect()
        }
    };
    pcm.iter().map(|sample| f32::from(*sample) / 32768.0).collect()
}

fn first_difference(name: &str, got: &[f32], expected: &[f32]) -> Option<String> {
    if got.len() != expected.len() {
        return Some(format!("{name}: {} values, expected {}", got.len(), expected.len()));
    }
    let differing: Vec<usize> =
        (0..got.len()).filter(|&i| got[i].to_bits() != expected[i].to_bits()).collect();
    differing.first().map(|&i| {
        format!(
            "{name}: {} of {} differ; first at {i}: got {:08x}, expected {:08x}",
            differing.len(),
            got.len(),
            got[i].to_bits(),
            expected[i].to_bits()
        )
    })
}

#[cfg(windows)]
fn check_features(folder: &Path, threads: &str) -> Vec<String> {
    let vectors = json(include_str!("vectors/dictation_feature_vectors.json"));
    let mut failures = Vec::new();
    let library = &vectors["openblas"];
    let file = std::fs::read(folder.join(OPENBLAS)).expect("the OpenBLAS DLL is staged");
    if sha256_hex(&file) != library["sha256"].as_str().unwrap() {
        failures.push("the staged OpenBLAS is not the recorded library".into());
    }
    let blas = match OpenBlas::load(folder) {
        Ok(blas) => blas,
        Err(error) => return vec![format!("OpenBLAS did not load: {error:?}")],
    };
    if blas.config() != library["config"].as_str().unwrap() {
        failures.push(format!("OpenBLAS reports {:?}", blas.config()));
    }
    let recorded = &vectors["threads"][threads];
    assert_eq!(recorded["omp_num_threads"].as_str(), Some(threads));
    for case in recorded["features"].as_array().unwrap() {
        let name = format!("{} (OMP_NUM_THREADS={threads})", case["signal"]["name"].as_str().unwrap());
        let features = log_mel(&signal(&case["signal"]), 160, &blas);
        let expected_shape: Vec<u64> =
            case["shape"].as_array().unwrap().iter().map(|n| n.as_u64().unwrap()).collect();
        if [features.rows as u64, features.frames as u64][..] != expected_shape[..] {
            failures.push(format!(
                "{name}: shape {}x{}, expected {expected_shape:?}",
                features.rows, features.frames
            ));
            continue;
        }
        if let Some(values) = case.get("values").and_then(J::as_str) {
            failures.extend(first_difference(&name, &features.values, &floats(values)));
        } else {
            let column = |frame: usize| -> Vec<f32> {
                (0..features.rows).map(|band| features.values[band * features.frames + frame]).collect()
            };
            failures.extend(first_difference(
                &format!("{name} first frame"),
                &column(0),
                &floats(case["first_frame"].as_str().unwrap()),
            ));
            failures.extend(first_difference(
                &format!("{name} last frame"),
                &column(features.frames - 1),
                &floats(case["last_frame"].as_str().unwrap()),
            ));
        }
        if sha256_hex(&bytes_of(&features.values)) != case["sha256"].as_str().unwrap() {
            failures.push(format!("{name}: SHA-256 differs"));
        }
    }
    failures
}

#[cfg(windows)]
fn check_decode(folder: &Path) -> Vec<String> {
    let vectors = json(include_str!("vectors/dictation_decode_vectors.json"));
    let mut failures = Vec::new();
    for (name, digest) in vectors["dlls"].as_object().unwrap() {
        let file = std::fs::read(folder.join(name)).expect("the FFmpeg DLLs are staged");
        if sha256_hex(&file) != digest.as_str().unwrap() {
            failures.push(format!("the staged {name} is not the recorded library"));
        }
    }
    assert_eq!(folder.join(SWRESAMPLE).file_name().unwrap(), "swresample-6.dll");
    let library = match Swresample::load(folder) {
        Ok(library) => library,
        Err(error) => return vec![format!("libswresample did not load: {error:?}")],
    };
    for case in vectors["cases"].as_array().unwrap() {
        let rate = case["rate"].as_u64().unwrap() as u32;
        let count = case["count"].as_u64().unwrap() as usize;
        let name = format!("decode {rate} Hz, {count} samples");
        let pcm = Pcm { rate, samples: lcg_int16(case["seed"].as_u64().unwrap(), count, 32767) };
        let decoded = if rate == 16_000 {
            decode(&pcm, None)
        } else {
            let mut resampler = library.mono_s16_to_16k(rate as i32).expect("libswresample initialises");
            decode(&pcm, Some(&mut resampler))
        };
        let Ok(decoded) = decoded else {
            failures.push(format!("{name}: decode failed"));
            continue;
        };
        let raw = bytes_of(&decoded);
        if decoded.len() as u64 != case["samples"].as_u64().unwrap() {
            failures.push(format!("{name}: {} samples, expected {}", decoded.len(), case["samples"]));
        } else if sha256_hex(&raw) != case["sha256"].as_str().unwrap() {
            let head = &raw[..raw.len().min(256)];
            failures.push(format!(
                "{name}: SHA-256 differs (head {})",
                if hex::encode(head) == case["head"].as_str().unwrap() { "same" } else { "differs" }
            ));
        }
    }
    failures
}

fn native_folder() -> Option<PathBuf> {
    std::env::var_os("SYLVIA_DICTATION_NATIVE_DIR").map(PathBuf::from)
}

fn main() {
    let Some(folder) = native_folder() else {
        eprintln!("dictation_native: set SYLVIA_DICTATION_NATIVE_DIR to the staged native DLLs");
        std::process::exit(1);
    };
    #[cfg(not(windows))]
    {
        let _ = folder;
        println!("dictation_native: Windows only");
    }
    #[cfg(windows)]
    {
        if let Ok(threads) = std::env::var("SYLVIA_DICTATION_NATIVE_CHILD") {
            let mut failures = check_features(&folder, &threads);
            if threads == "2" {
                failures.extend(check_decode(&folder));
            }
            for failure in &failures {
                println!("FAIL {failure}");
            }
            std::process::exit(i32::from(!failures.is_empty()));
        }
        let mut failed = false;
        for threads in ["2", "1"] {
            let status = Command::new(std::env::current_exe().unwrap())
                .env_remove("OPENBLAS_NUM_THREADS")
                .env_remove("GOTO_NUM_THREADS")
                // As the desktop starts the speech worker (src-tauri/src/dictation.rs).
                .env("OMP_NUM_THREADS", threads)
                .env("MKL_NUM_THREADS", threads)
                .env("SYLVIA_DICTATION_NATIVE_CHILD", threads)
                .status()
                .expect("the harness runs itself");
            println!("OMP_NUM_THREADS={threads}: {}", if status.success() { "ok" } else { "FAILED" });
            failed |= !status.success();
        }
        std::process::exit(i32::from(failed));
    }
}
