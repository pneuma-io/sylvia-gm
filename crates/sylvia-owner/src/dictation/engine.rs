//! faster-whisper's engine: CTranslate2's Whisper, through ct2rs's C++ bridge, on the
//! `ctranslate2.dll` the product ships (the locked ctranslate2 4.8.3 wheel's). Windows,
//! `dictation` feature.
//!
//! The DLL is linked delay-loaded (`build.rs`): nothing loads it until the first engine call,
//! so [`verify`] checks the files beside the owner first. A different DLL must never silently
//! give different transcripts; a worker whose files do not match answers as the build without
//! the speech runtime does.
//!
//! Every bridge call used here returns `Result` on the Rust side (`whisper`, `encode`,
//! `generate`, `detect_language`, `storage_view`): cxx wraps each in a C++ `try`/`catch` and turns
//! an exception into an `Err`, so no C++ exception unwinds into Rust. `n_mels` and
//! `is_multilingual` are declared without `Result`, so cxx makes their shims `noexcept`: an
//! exception there terminates the process instead of unwinding.

use ct2rs::sys::{ComputeType, Config, Device, StorageView, Whisper, WhisperOptions};
use sha2::{Digest, Sha256};
use std::path::Path;

/// The shipped engine files and their SHA-256 (the locked ctranslate2 4.8.3 wheel).
pub const ENGINE_FILES: [(&str, &str); 2] = [
    ("ctranslate2.dll", "ca1127837d032bf8217d0cbcd68e8cef1f93bda3111eff00ced98795d9e51160"),
    ("libiomp5md.dll", "982233366b0afcda1e0f55a0b134097e35b779613f54ddb69e685e6cd06b755f"),
];

/// Why the engine is not available.
#[derive(Debug)]
pub enum EngineError {
    /// A shipped file is missing or is not the expected one.
    Files(&'static str),
    /// CTranslate2 reported an error (its message).
    Engine(String),
}

/// Check that `folder` holds the expected engine files, before anything loads them.
pub fn verify(folder: &Path) -> Result<(), EngineError> {
    for (name, digest) in ENGINE_FILES {
        let bytes = std::fs::read(folder.join(name)).map_err(|_| EngineError::Files(name))?;
        if hex::encode(Sha256::digest(&bytes)) != digest {
            return Err(EngineError::Files(name));
        }
    }
    Ok(())
}

/// faster-whisper's `WhisperModel(device="cpu", compute_type="int8", cpu_threads=2,
/// num_workers=1)`: `ctranslate2.models.Whisper(path, device="cpu", device_index=0,
/// compute_type="int8", intra_threads=2, inter_threads=1)`.
pub struct Engine {
    whisper: Whisper,
}

/// One language and its probability from `detect_language`.
#[derive(Debug, Clone, PartialEq)]
pub struct Language {
    /// The language token, such as `<|en|>`.
    pub token: String,
    /// Its probability.
    pub probability: f32,
}

/// One `generate` result: `WhisperGenerationResult`.
#[derive(Debug, Clone, PartialEq)]
pub struct Generation {
    /// `sequences_ids`.
    pub sequences_ids: Vec<Vec<usize>>,
    /// `scores`.
    pub scores: Vec<f32>,
    /// `no_speech_prob`.
    pub no_speech_prob: f32,
}

/// ct2rs keeps `DetectionResult`'s fields private and gives it `Debug`; its `f32` is printed in
/// Rust's shortest round-trip form, so parsing it back gives the exact value.
fn parse_detection(debug: &str) -> Option<Language> {
    let token = debug.split("language: \"").nth(1)?.split('"').next()?.to_string();
    let probability = debug.split("probability: ").nth(1)?.trim_end_matches([' ', '}']).parse().ok()?;
    Some(Language { token, probability })
}

impl Engine {
    /// Load the model folder (call [`verify`] first).
    pub fn load(model: &Path) -> Result<Self, EngineError> {
        let config = Config {
            device: Device::CPU,
            compute_type: ComputeType::INT8,
            device_indices: vec![0],
            tensor_parallel: false,
            num_threads_per_replica: 2,
            max_queued_batches: 0,
            cpu_core_offset: -1,
        };
        Whisper::new(model, config)
            .map(|whisper| Self { whisper })
            .map_err(|e| EngineError::Engine(e.to_string()))
    }

    /// `n_mels`.
    pub fn n_mels(&self) -> usize {
        self.whisper.n_mels()
    }

    /// `model.encode(features, to_cpu=False)` on `[1, n_mels, frames]` features.
    pub fn encode(&self, features: &mut [f32], frames: usize) -> Result<StorageView<'static>, EngineError> {
        let shape = [1, self.n_mels(), frames];
        let input = StorageView::new(&shape, features, Device::CPU)
            .map_err(|e| EngineError::Engine(e.to_string()))?;
        self.whisper.encode(&input, false).map_err(|e| EngineError::Engine(e.to_string()))
    }

    /// `model.detect_language(encoder_output)[0]`: every language, best first.
    pub fn detect_language(&self, encoder_output: &StorageView) -> Result<Vec<Language>, EngineError> {
        let batches =
            self.whisper.detect_language(encoder_output).map_err(|e| EngineError::Engine(e.to_string()))?;
        let first =
            batches.into_iter().next().ok_or_else(|| EngineError::Engine("no detection result".into()))?;
        first
            .iter()
            .map(|result| parse_detection(&format!("{result:?}")))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| EngineError::Engine("unreadable detection result".into()))
    }

    /// `model.generate(encoder_output, [prompt], **options)[0]`, the prompt as vocabulary tokens.
    pub fn generate(
        &self,
        encoder_output: &StorageView,
        prompt: &[String],
        options: &WhisperOptions,
    ) -> Result<Generation, EngineError> {
        let results = self
            .whisper
            .generate(encoder_output, &[prompt.to_vec()], options)
            .map_err(|e| EngineError::Engine(e.to_string()))?;
        let result =
            results.into_iter().next().ok_or_else(|| EngineError::Engine("no generation result".into()))?;
        Ok(Generation {
            sequences_ids: result.sequences_ids,
            scores: result.scores,
            no_speech_prob: result.no_speech_prob,
        })
    }

    /// CTranslate2's own printout of a storage view (`operator<<`), as ct2rs's `Debug` gives it:
    /// `StorageView {<printout with each newline as ", "> }`.
    pub fn describe(view: &StorageView) -> String {
        format!("{view:?}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detection_results_parse_back_exactly() {
        let parsed =
            parse_detection("DetectionResult { language: \"<|en|>\", probability: 0.98765433 }").unwrap();
        assert_eq!(parsed, Language { token: "<|en|>".into(), probability: "0.98765433".parse().unwrap() });
        for value in [0.1_f32, 1.0, 1e-30, 0.333_333_34, f32::MIN_POSITIVE] {
            let text = format!("DetectionResult {{ language: \"<|xx|>\", probability: {value:?} }}");
            assert_eq!(parse_detection(&text).unwrap().probability.to_bits(), value.to_bits());
        }
    }
}
