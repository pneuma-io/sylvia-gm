//! Whole transcriptions on the shipped native libraries against faster-whisper 1.2.1 and the
//! worker's `local_dictation.transcribe` (`vectors/generate_dictation_transcribe_vectors.py`):
//! for each SAPI clip, a silence and generated noise, the worker's WAV, the decoded audio, the
//! VAD's probabilities and speech chunks, the features of the kept audio, every segment (text,
//! tokens, times, temperature and, by their bits, the scores), the detected language and the
//! worker's final text or error code, for English and for detection. Windows, `dictation`
//! feature, never part of check.sh (see the dictation check in docs/rust-port/README.md).
//!
//! Needs `SYLVIA_DICTATION_NATIVE_DIR` (every staged native file, `speech::verify`),
//! `SYLVIA_DICTATION_MODEL_DIR` and `SYLVIA_DICTATION_SPEECH_DIR`, as `dictation_engine` does,
//! and runs itself again under the desktop's settings for the speech worker.

use serde_json::Value as J;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;

fn folder(name: &str) -> PathBuf {
    PathBuf::from(std::env::var_os(name).unwrap_or_else(|| {
        eprintln!("dictation_transcribe: set {name}");
        std::process::exit(1)
    }))
}

/// The `data` chunk of a 16-bit mono WAV (SAPI writes a WAVEFORMATEX `fmt ` chunk).
fn wav_samples(bytes: &[u8]) -> (u32, Vec<u8>) {
    let rate = u32::from_le_bytes(bytes[24..28].try_into().unwrap());
    let mut at = 12;
    while at + 8 <= bytes.len() {
        let size = u32::from_le_bytes(bytes[at + 4..at + 8].try_into().unwrap()) as usize;
        if &bytes[at..at + 4] == b"data" {
            return (rate, bytes[at + 8..at + 8 + size].to_vec());
        }
        at += 8 + size + (size & 1);
    }
    panic!("no data chunk")
}

/// Python's `wave` writer with one channel, two-byte samples (`Recording.stop`).
fn worker_wav(rate: u32, pcm: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(44 + pcm.len());
    let length = pcm.len() as u32;
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + length).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16_u32.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&(rate * 2).to_le_bytes());
    out.extend_from_slice(&2_u16.to_le_bytes());
    out.extend_from_slice(&16_u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&length.to_le_bytes());
    out.extend_from_slice(pcm);
    out
}

/// The generator's `lcg_int16`.
fn lcg_int16(seed: u64, count: usize, amplitude: i64) -> Vec<u8> {
    let mut state = seed;
    let mut out = Vec::with_capacity(count * 2);
    for _ in 0..count {
        state = (state * 1_664_525 + 1_013_904_223) % (1 << 32);
        let value = ((state >> 16) as i64) % (2 * amplitude + 1) - amplitude;
        out.extend_from_slice(&(value as i16).to_le_bytes());
    }
    out
}

fn f32_digest(values: &[f32]) -> String {
    let bytes: Vec<u8> = values.iter().flat_map(|value| value.to_le_bytes()).collect();
    hex::encode(Sha256::digest(&bytes))
}

fn f64_hex(value: f64) -> String {
    hex::encode(value.to_le_bytes())
}

#[cfg(windows)]
fn check(native: &Path, model: &Path, speech: &Path) -> Vec<String> {
    use sylvia_owner::dictation::engine::Engine;
    use sylvia_owner::dictation::features::log_mel;
    use sylvia_owner::dictation::openblas::OpenBlas;
    use sylvia_owner::dictation::speech::{self, decode_wav, SpeechFactory};
    use sylvia_owner::dictation::swresample::Swresample;
    use sylvia_owner::dictation::vad::{
        collect, pad_to_windows, speech_timestamps, Silero, VadOptions, VAD_MODEL,
    };
    use sylvia_owner::dictation::whisper::{Pipeline, Thresholds, WhisperTokenizer};
    use sylvia_owner::dictation::{transcribe, ModelSpec};

    let vectors: J = serde_json::from_str(include_str!("vectors/dictation_transcribe_vectors.json")).unwrap();
    let mut failures = Vec::new();
    if let Err(name) = speech::verify(native) {
        return vec![format!("native files: {name} is missing or not the shipped one")];
    }
    let swresample = Swresample::load(native).expect("libswresample loads");
    let blas = OpenBlas::load(native).expect("OpenBLAS loads");
    let environment = speech::runtime(native).expect("ONNX Runtime loads");
    let mut vad = Silero::new(&environment, &native.join(VAD_MODEL.0)).expect("the VAD loads");
    let engine = Engine::load(model).expect("the engine loads");
    let tokenizer = WhisperTokenizer::load(model).expect("the tokenizer loads");
    let mut factory = SpeechFactory::new(native);

    let mut compare = |name: &str, field: &str, ours: String, theirs: &J| {
        let theirs = match theirs {
            J::String(text) => text.clone(),
            other => other.to_string(),
        };
        if ours != theirs {
            failures.push(format!("{name}: {field}: {ours} != {theirs}"));
        }
    };
    let mut wavs: Vec<(String, Vec<f32>)> = Vec::new();
    for clip in vectors["clips"].as_array().unwrap() {
        let name = clip["clip"].as_str().unwrap();
        let rate = clip["rate"].as_u64().unwrap() as u32;
        let pcm = match name {
            "silence-48k" => vec![0_u8; 96_000 * 2],
            "noise-16k" => lcg_int16(77, 48_000, 3000),
            _ => {
                let bytes =
                    std::fs::read(speech.join(format!("{name}.wav"))).expect("the SAPI clip is staged");
                if hex::encode(Sha256::digest(&bytes)) != vectors["clip_sha256"][name].as_str().unwrap() {
                    compare(name, "clip", "the staged clip".into(), &J::String("the recorded one".into()));
                    continue;
                }
                let (clip_rate, pcm) = wav_samples(&bytes);
                assert_eq!(clip_rate, rate);
                pcm
            }
        };
        let wav = worker_wav(rate, &pcm);
        compare(name, "wav", hex::encode(Sha256::digest(&wav)), &clip["wav_sha256"]);
        let audio = decode_wav(&swresample, &wav).expect("the WAV decodes");
        compare(name, "audio samples", audio.len().to_string(), &clip["audio_samples"]);
        compare(name, "audio", f32_digest(&audio), &clip["audio_sha256"]);
        let probabilities = vad.probabilities(&pad_to_windows(&audio)).expect("the VAD runs");
        compare(name, "VAD shape", format!("[{}]", probabilities.len()), &clip["vad_probabilities"]["shape"]);
        compare(name, "VAD", f32_digest(&probabilities), &clip["vad_probabilities"]["sha256"]);
        let chunks = speech_timestamps(&probabilities, audio.len(), &VadOptions::worker());
        let listed: Vec<[i64; 2]> = chunks.iter().map(|(start, end)| [*start, *end]).collect();
        compare(name, "speech chunks", serde_json::to_string(&listed).unwrap(), &clip["speech_chunks"]);
        let kept = collect(&audio, &chunks);
        compare(name, "kept samples", kept.len().to_string(), &clip["kept_samples"]);
        compare(name, "features", f32_digest(&log_mel(&kept, 160, &blas).values), &clip["features_sha256"]);

        for (key, language) in [("en", Some("en")), ("auto", None)] {
            let expected = &clip["transcriptions"][key];
            let label = format!("{name} [{key}]");
            let mut pipeline = Pipeline {
                engine: &engine,
                tokenizer: &tokenizer,
                vad: &mut vad,
                blas: &blas,
                thresholds: Thresholds::worker(),
            };
            match pipeline.transcribe(&audio, language) {
                Ok(result) => {
                    compare(&label, "language", result.language.clone(), &expected["language"]);
                    compare(
                        &label,
                        "language probability",
                        f64_hex(result.language_probability),
                        &expected["language_probability"],
                    );
                    let segments = expected["segments"].as_array().unwrap();
                    compare(
                        &label,
                        "segment count",
                        result.segments.len().to_string(),
                        &J::from(segments.len()),
                    );
                    for (ours, theirs) in result.segments.iter().zip(segments) {
                        let at = format!("{label} segment {}", ours.id);
                        compare(&at, "id", ours.id.to_string(), &theirs["id"]);
                        compare(&at, "seek", ours.seek.to_string(), &theirs["seek"]);
                        compare(&at, "start", f64_hex(ours.start), &theirs["start"]);
                        compare(&at, "end", f64_hex(ours.end), &theirs["end"]);
                        compare(&at, "text", ours.text.clone(), &theirs["text"]);
                        compare(
                            &at,
                            "tokens",
                            serde_json::to_string(&ours.tokens).unwrap(),
                            &theirs["tokens"],
                        );
                        compare(&at, "temperature", f64_hex(ours.temperature), &theirs["temperature"]);
                        compare(&at, "avg_logprob", f64_hex(ours.avg_logprob), &theirs["avg_logprob"]);
                        compare(
                            &at,
                            "compression_ratio",
                            f64_hex(ours.compression_ratio),
                            &theirs["compression_ratio"],
                        );
                        compare(
                            &at,
                            "no_speech_prob",
                            f64_hex(ours.no_speech_prob),
                            &theirs["no_speech_prob"],
                        );
                    }
                }
                Err(error) => {
                    compare(&label, "transcribe", format!("{error:?}"), &J::String("segments".into()))
                }
            }
            let worker = &expected["worker"];
            let ours = match transcribe(
                &wav,
                model,
                language.unwrap_or("auto"),
                &ModelSpec::shipped(),
                &mut factory,
            ) {
                Ok(text) => serde_json::json!({"text": text}),
                Err(failure) => serde_json::json!({"error": failure.code()}),
            };
            compare(&label, "worker", ours.to_string(), &J::String(worker.to_string()));
        }
        wavs.push((name.to_string(), audio));
    }
    // Other thresholds, with CTranslate2's sampling seeded and a fresh model per variant, as
    // the generator does.
    for variant in vectors["variants"].as_array().unwrap() {
        let label = variant["variant"].as_str().unwrap();
        let Some((_, audio)) = wavs.iter().find(|(name, _)| name == variant["clip"].as_str().unwrap()) else {
            compare(label, "clip", "missing".into(), &J::String("decoded".into()));
            continue;
        };
        let given = &variant["thresholds"];
        let pick = |key: &str, default: Option<f64>| match given.get(key) {
            Some(J::Null) => None,
            Some(value) => value.as_f64(),
            None => default,
        };
        let worker = Thresholds::worker();
        let thresholds = Thresholds {
            compression_ratio: pick("compression_ratio_threshold", worker.compression_ratio),
            log_prob: pick("log_prob_threshold", worker.log_prob),
            no_speech: pick("no_speech_threshold", worker.no_speech),
        };
        ct2rs::sys::set_random_seed(variant["seed"].as_u64().unwrap() as u32);
        let engine = Engine::load(model).expect("the engine loads");
        let mut pipeline =
            Pipeline { engine: &engine, tokenizer: &tokenizer, vad: &mut vad, blas: &blas, thresholds };
        let segments = variant["segments"].as_array().unwrap();
        match pipeline.transcribe(audio, Some("en")) {
            Ok(result) => {
                compare(label, "segment count", result.segments.len().to_string(), &J::from(segments.len()));
                for (ours, theirs) in result.segments.iter().zip(segments) {
                    let at = format!("{label} segment {}", ours.id);
                    compare(&at, "seek", ours.seek.to_string(), &theirs["seek"]);
                    compare(&at, "start", f64_hex(ours.start), &theirs["start"]);
                    compare(&at, "end", f64_hex(ours.end), &theirs["end"]);
                    compare(&at, "text", ours.text.clone(), &theirs["text"]);
                    compare(&at, "tokens", serde_json::to_string(&ours.tokens).unwrap(), &theirs["tokens"]);
                    compare(&at, "temperature", f64_hex(ours.temperature), &theirs["temperature"]);
                    compare(&at, "avg_logprob", f64_hex(ours.avg_logprob), &theirs["avg_logprob"]);
                    compare(
                        &at,
                        "compression_ratio",
                        f64_hex(ours.compression_ratio),
                        &theirs["compression_ratio"],
                    );
                    compare(&at, "no_speech_prob", f64_hex(ours.no_speech_prob), &theirs["no_speech_prob"]);
                }
            }
            Err(error) => compare(label, "transcribe", format!("{error:?}"), &J::String("segments".into())),
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
            wav_samples as fn(&[u8]) -> (u32, Vec<u8>),
            worker_wav as fn(u32, &[u8]) -> Vec<u8>,
        );
        let _ = (
            lcg_int16 as fn(u64, usize, i64) -> Vec<u8>,
            f32_digest as fn(&[f32]) -> String,
            f64_hex as fn(f64) -> String,
        );
        println!("dictation_transcribe: Windows only");
    }
    #[cfg(windows)]
    {
        if std::env::var_os("SYLVIA_DICTATION_TRANSCRIBE_CHILD").is_some() {
            let failures = check(&native, &model, &speech);
            for failure in &failures {
                println!("FAIL {failure}");
            }
            println!("transcribe: {}", if failures.is_empty() { "ok" } else { "FAILED" });
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
            .env("SYLVIA_DICTATION_TRANSCRIBE_CHILD", "1")
            .status()
            .expect("the harness runs itself");
        std::process::exit(i32::from(!status.success()));
    }
}
