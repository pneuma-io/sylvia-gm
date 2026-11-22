//! The dictation engine across the C++ boundary: ct2rs's bridge on the shipped `ctranslate2.dll`
//! against the locked wheel's `ctranslate2.models.Whisper`, bit for bit
//! (`vectors/generate_dictation_engine_vectors.py`). Windows, `dictation` feature, never part of
//! check.sh (see the dictation check in docs/rust-port/README.md).
//!
//! Needs `SYLVIA_DICTATION_NATIVE_DIR` (the staged DLLs, including `ctranslate2.dll` and
//! `libiomp5md.dll`), `SYLVIA_DICTATION_MODEL_DIR` (the pinned faster-whisper base model, as
//! `prepare_model` downloads it) and `SYLVIA_DICTATION_SPEECH_DIR` (the SAPI test speech,
//! `docs/rust-port/tools/sapi_speech.ps1`). It runs itself again under the desktop's settings
//! for the speech worker (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`) with the native folder first
//! on `PATH`, where the delay-loaded engine is found.

use serde_json::Value as J;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;

fn floats(hex_text: &str) -> Vec<f32> {
    hex::decode(hex_text).unwrap().as_chunks::<4>().0.iter().map(|bytes| f32::from_le_bytes(*bytes)).collect()
}

fn folder(name: &str) -> PathBuf {
    PathBuf::from(std::env::var_os(name).unwrap_or_else(|| {
        eprintln!("dictation_engine: set {name}");
        std::process::exit(1)
    }))
}

/// The `data` chunk of a 16-bit mono WAV (SAPI writes a WAVEFORMATEX `fmt ` chunk).
fn wav_samples(bytes: &[u8]) -> (u32, Vec<i16>) {
    let rate = u32::from_le_bytes(bytes[24..28].try_into().unwrap());
    let mut at = 12;
    while at + 8 <= bytes.len() {
        let size = u32::from_le_bytes(bytes[at + 4..at + 8].try_into().unwrap()) as usize;
        if &bytes[at..at + 4] == b"data" {
            let data = &bytes[at + 8..at + 8 + size];
            return (rate, data.as_chunks::<2>().0.iter().map(|pair| i16::from_le_bytes(*pair)).collect());
        }
        at += 8 + size + (size & 1);
    }
    panic!("no data chunk")
}

#[cfg(windows)]
fn check(native: &Path, model: &Path, speech: &Path) -> Vec<String> {
    use sylvia_owner::dictation::engine::{verify, Engine};
    use sylvia_owner::dictation::features::{log_mel, N_MELS};
    use sylvia_owner::dictation::openblas::OpenBlas;

    let vectors: J = serde_json::from_str(include_str!("vectors/dictation_engine_vectors.json")).unwrap();
    let mut failures = Vec::new();
    if let Err(error) = verify(native) {
        return vec![format!("engine files: {error:?}")];
    }
    let blas = OpenBlas::load(native).expect("OpenBLAS loads");
    let engine = match Engine::load(model) {
        Ok(engine) => engine,
        Err(error) => return vec![format!("engine load: {error:?}")],
    };
    let vocabulary: Vec<String> =
        std::fs::read_to_string(model.join("vocabulary.txt")).unwrap().lines().map(str::to_string).collect();
    for clip in vectors["clips"].as_array().unwrap() {
        let name = clip["clip"].as_str().unwrap();
        let bytes = std::fs::read(speech.join(format!("{name}.wav"))).expect("the SAPI clip is staged");
        if hex::encode(Sha256::digest(&bytes)) != vectors["clip_sha256"][name].as_str().unwrap() {
            failures.push(format!("{name}: the staged clip is not the recorded one"));
            continue;
        }
        let (rate, pcm) = wav_samples(&bytes);
        assert_eq!(rate, 16_000);
        let audio: Vec<f32> = pcm.iter().map(|sample| f32::from(*sample) / 32768.0).collect();
        let features = log_mel(&audio, 160, &blas);
        // pad_or_trim(features[:, :3000]) as one [1, 80, 3000] C-ordered segment.
        let mut segment = vec![0.0_f32; N_MELS * 3000];
        for band in 0..N_MELS {
            for frame in 0..features.frames.min(3000) {
                segment[band * 3000 + frame] = features.values[band * features.frames + frame];
            }
        }
        let segment_bytes: Vec<u8> = segment.iter().flat_map(|value| value.to_le_bytes()).collect();
        if hex::encode(Sha256::digest(&segment_bytes)) != clip["features_sha256"].as_str().unwrap() {
            failures.push(format!("{name}: features differ"));
        }
        let encoder_output = match engine.encode(&mut segment, 3000) {
            Ok(output) => output,
            Err(error) => return vec![format!("{name}: encode: {error:?}")],
        };
        let expected_text =
            format!("StorageView {{{} }}", clip["encoder_text"].as_str().unwrap().replace('\n', ", "));
        if Engine::describe(&encoder_output) != expected_text {
            failures.push(format!(
                "{name}: encoder printout {:?}, expected {expected_text:?}",
                Engine::describe(&encoder_output)
            ));
        }
        match engine.detect_language(&encoder_output) {
            Ok(languages) => {
                for (index, expected) in clip["languages"].as_array().unwrap().iter().enumerate() {
                    let got = &languages[index];
                    let probability = floats(expected[1].as_str().unwrap())[0];
                    if got.token != expected[0].as_str().unwrap()
                        || got.probability.to_bits() != probability.to_bits()
                    {
                        failures.push(format!(
                            "{name}: language {index} {:?} {:08x}, expected {expected}",
                            got.token,
                            got.probability.to_bits()
                        ));
                    }
                }
            }
            Err(error) => failures.push(format!("{name}: detect_language: {error:?}")),
        }
        let prompt: Vec<String> = clip["prompt"]
            .as_array()
            .unwrap()
            .iter()
            .map(|id| vocabulary[id.as_u64().unwrap() as usize].clone())
            .collect();
        let options = ct2rs::sys::WhisperOptions {
            beam_size: 5,
            patience: 1.0,
            length_penalty: 1.0,
            repetition_penalty: 1.0,
            no_repeat_ngram_size: 0,
            max_length: 448,
            sampling_topk: 1,
            sampling_temperature: 1.0,
            num_hypotheses: 1,
            return_scores: true,
            return_logits_vocab: false,
            return_no_speech_prob: true,
            max_initial_timestamp_index: 50,
            suppress_blank: true,
            suppress_tokens: clip["suppress_tokens"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| t.as_i64().unwrap() as i32)
                .collect(),
        };
        match engine.generate(&encoder_output, &prompt, &options) {
            Ok(result) => {
                let ids: Vec<Vec<u64>> = serde_json::from_value(clip["sequences_ids"].clone()).unwrap();
                let got_ids: Vec<Vec<u64>> = result
                    .sequences_ids
                    .iter()
                    .map(|ids| ids.iter().map(|id| *id as u64).collect())
                    .collect();
                if got_ids != ids {
                    failures.push(format!("{name}: tokens {got_ids:?}, expected {ids:?}"));
                }
                let scores = floats(clip["scores"].as_str().unwrap());
                if result.scores.iter().map(|s| s.to_bits()).collect::<Vec<_>>()
                    != scores.iter().map(|s| s.to_bits()).collect::<Vec<_>>()
                {
                    failures.push(format!("{name}: scores {:?}, expected {scores:?}", result.scores));
                }
                let no_speech = floats(clip["no_speech_prob"].as_str().unwrap())[0];
                if result.no_speech_prob.to_bits() != no_speech.to_bits() {
                    failures.push(format!(
                        "{name}: no_speech_prob {:e}, expected {no_speech:e}",
                        result.no_speech_prob
                    ));
                }
            }
            Err(error) => failures.push(format!("{name}: generate: {error:?}")),
        }
    }
    failures
}

fn main() {
    let native = folder("SYLVIA_DICTATION_NATIVE_DIR");
    let model = folder("SYLVIA_DICTATION_MODEL_DIR");
    let speech = folder("SYLVIA_DICTATION_SPEECH_DIR");
    #[cfg(not(windows))]
    {
        let _ = (
            native,
            model,
            speech,
            wav_samples as fn(&[u8]) -> (u32, Vec<i16>),
            floats as fn(&str) -> Vec<f32>,
        );
        println!("dictation_engine: Windows only");
    }
    #[cfg(windows)]
    {
        if std::env::var_os("SYLVIA_DICTATION_ENGINE_CHILD").is_some() {
            let failures = check(&native, &model, &speech);
            for failure in &failures {
                println!("FAIL {failure}");
            }
            println!("engine: {}", if failures.is_empty() { "ok" } else { "FAILED" });
            std::process::exit(i32::from(!failures.is_empty()));
        }
        let mut path = std::ffi::OsString::from(&native);
        if let Some(rest) = std::env::var_os("PATH") {
            path.push(";");
            path.push(rest);
        }
        let status = Command::new(std::env::current_exe().unwrap())
            .env_remove("OPENBLAS_NUM_THREADS")
            .env_remove("GOTO_NUM_THREADS")
            // As the desktop starts the speech worker (src-tauri/src/dictation.rs).
            .env("OMP_NUM_THREADS", "2")
            .env("MKL_NUM_THREADS", "2")
            .env("PATH", path)
            .env("SYLVIA_DICTATION_ENGINE_CHILD", "1")
            .status()
            .expect("the harness runs itself");
        std::process::exit(i32::from(!status.success()));
    }
}
