//! A whole dictation through the worker's own path on the shipped native libraries: SAPI speech
//! played into `Recording` as capture blocks, `Recording::stop`'s WAV, then `transcribe` with the
//! speech runtime's model factory, against Python's `Recording` and `local_dictation.transcribe`
//! on the same blocks (`vectors/generate_dictation_recording_vectors.py`). No audio device is
//! opened. Windows, `dictation` feature, never part of check.sh (see the dictation check in
//! docs/rust-port/README.md).
//!
//! Needs `SYLVIA_DICTATION_NATIVE_DIR`, `SYLVIA_DICTATION_MODEL_DIR` and
//! `SYLVIA_DICTATION_SPEECH_DIR`, as `dictation_transcribe` does, and runs itself again under
//! the desktop's settings for the speech worker.

use serde_json::Value as J;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;

fn folder(name: &str) -> PathBuf {
    PathBuf::from(std::env::var_os(name).unwrap_or_else(|| {
        eprintln!("dictation_recording: set {name}");
        std::process::exit(1)
    }))
}

/// The `data` chunk and rate of a 16-bit mono WAV (SAPI writes a WAVEFORMATEX `fmt ` chunk).
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
mod played {
    use std::sync::Arc;
    use sylvia_owner::dictation::{CaptureSink, DeviceRow, Flow, Raised, Sound, Stream, StreamError};

    /// One synthetic input that plays a clip's float32 blocks when its stream starts.
    pub struct Played {
        pub rate: u32,
        pub samples: Vec<f32>,
        pub block: usize,
    }

    struct Playing {
        samples: Vec<f32>,
        block: usize,
        sink: Arc<CaptureSink>,
    }

    impl Stream for Playing {
        fn start(&mut self) -> Result<(), StreamError> {
            for block in self.samples.chunks(self.block) {
                if self.sink.deliver(block, false) == Flow::Stop {
                    break;
                }
            }
            Ok(())
        }
        fn stop(&mut self) -> Result<(), Raised> {
            Ok(())
        }
        fn abort(&mut self) -> Result<(), Raised> {
            Ok(())
        }
        fn close(&mut self) -> Result<(), Raised> {
            Ok(())
        }
    }

    impl Sound for Played {
        fn initialized(&self) -> bool {
            false
        }
        fn terminate(&mut self) -> Result<(), Raised> {
            Ok(())
        }
        fn initialize(&mut self) -> Result<(), Raised> {
            Ok(())
        }
        fn query_devices(&mut self) -> Result<Vec<DeviceRow>, Raised> {
            Ok(vec![DeviceRow {
                name: "Synthetic input".into(),
                max_input_channels: 1,
                hostapi: 0,
                default_samplerate: serde_json::Number::from_f64(f64::from(self.rate)).unwrap(),
            }])
        }
        fn query_hostapis(&mut self) -> Result<Vec<String>, Raised> {
            Ok(vec!["Synthetic".into()])
        }
        fn default_input(&mut self) -> Result<i64, Raised> {
            Ok(0)
        }
        fn open_stream(
            &mut self,
            _: i64,
            _: i64,
            sink: Arc<CaptureSink>,
        ) -> Result<Box<dyn Stream>, StreamError> {
            Ok(Box::new(Playing { samples: self.samples.clone(), block: self.block, sink }))
        }
    }
}

#[cfg(windows)]
fn check(native: &Path, model: &Path, speech: &Path) -> Vec<String> {
    use sylvia_owner::dictation::speech::{self, SpeechFactory};
    use sylvia_owner::dictation::{transcribe, ModelSpec, Recording};

    let vectors: J = serde_json::from_str(include_str!("vectors/dictation_recording_vectors.json")).unwrap();
    if let Err(name) = speech::verify(native) {
        return vec![format!("native files: {name} is missing or not the shipped one")];
    }
    let mut factory = SpeechFactory::new(native);
    let mut failures = Vec::new();
    for clip in vectors["clips"].as_array().unwrap() {
        let name = clip["clip"].as_str().unwrap();
        let bytes = std::fs::read(speech.join(format!("{name}.wav"))).expect("the SAPI clip is staged");
        if hex::encode(Sha256::digest(&bytes)) != vectors["clip_sha256"][name].as_str().unwrap() {
            failures.push(format!("{name}: the staged clip is not the recorded one"));
            continue;
        }
        let (rate, pcm) = wav_samples(&bytes);
        let samples = pcm.iter().map(|sample| f32::from(*sample) / 32768.0).collect();
        let mut sound = played::Played { rate, samples, block: clip["block"].as_u64().unwrap() as usize };
        let mut recording = Recording::new("default", &mut sound).expect("the input is listed");
        recording.start(&mut sound).expect("the stream starts");
        let wav = recording.stop().expect("the recording stops");
        let digest = hex::encode(Sha256::digest(&wav));
        if digest != clip["wav_sha256"].as_str().unwrap() {
            failures.push(format!("{name}: WAV {digest} != {}", clip["wav_sha256"]));
        }
        for language in ["en", "auto"] {
            let ours = match transcribe(&wav, model, language, &ModelSpec::shipped(), &mut factory) {
                Ok(text) => serde_json::json!({"text": text}),
                Err(failure) => serde_json::json!({"error": failure.code()}),
            };
            if ours != clip[language] {
                failures.push(format!("{name} [{language}]: {ours} != {}", clip[language]));
            }
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
        let _ = (native, model, speech, wav_samples as fn(&[u8]) -> (u32, Vec<i16>));
        println!("dictation_recording: Windows only");
    }
    #[cfg(windows)]
    {
        if std::env::var_os("SYLVIA_DICTATION_RECORDING_CHILD").is_some() {
            let failures = check(&native, &model, &speech);
            for failure in &failures {
                println!("FAIL {failure}");
            }
            println!("recording: {}", if failures.is_empty() { "ok" } else { "FAILED" });
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
            .env("SYLVIA_DICTATION_RECORDING_CHILD", "1")
            .status()
            .expect("the harness runs itself");
        std::process::exit(i32::from(!status.success()));
    }
}
