//! The speech runtime on the shipped native libraries: `WhisperModel` for the worker
//! ([`super::ModelFactory`], [`super::Model`]) built from [`super::whisper`], with
//! `decode_audio` on libswresample and the features on NumPy's OpenBLAS. Windows, `dictation`
//! feature.
//!
//! Every native file is checked against its SHA-256 before anything loads it: a different
//! library must never silently give different transcripts. A folder that does not match fails
//! the load, which the worker answers as Python answers a failed `WhisperModel(...)`.

use super::engine::{self, Engine};
use super::openblas::OpenBlas;
use super::swresample::Swresample;
use super::vad::{self, Silero};
use super::whisper::{Pipeline, Thresholds, WhisperTokenizer};
use super::{audio, DecodeOptions, Model, ModelFactory, Raised, Segment, Segments, LANGUAGE_CODES};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// The decode and feature libraries beside the owner, with the SHA-256 they must have
/// (`docs/rust-port/tools/stage_dictation_natives.sh`).
pub const AUDIO_FILES: [(&str, &str); 3] = [
    ("swresample-6.dll", "3d950dfb94a55fc3b25fdd55ea4c49aa0361506d0c304d2615d3fa49a21634ad"),
    ("avutil-60.dll", "4afe3a74c43b9860d64ebdbdaef30b8758d907a1d3128ff8fc0e28d3b7227008"),
    (
        "libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll",
        "ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8",
    ),
];

/// Check every native file the runtime loads: audio, engine, ONNX Runtime and the VAD model.
pub fn verify(folder: &Path) -> Result<(), &'static str> {
    let rows = AUDIO_FILES.iter().chain(engine::ENGINE_FILES.iter()).chain(vad::RUNTIME_FILES.iter());
    for (name, digest) in rows.chain(std::iter::once(&vad::VAD_MODEL)) {
        let bytes = std::fs::read(folder.join(name)).map_err(|_| *name)?;
        if hex::encode(Sha256::digest(&bytes)) != *digest {
            return Err(name);
        }
    }
    Ok(())
}

/// ONNX Runtime loads once per process; a second load attempt answers as the first did.
pub fn runtime(folder: &Path) -> Result<ort::environment::Environment, Raised> {
    static LOADED: OnceLock<Option<ort::environment::Environment>> = OnceLock::new();
    let library = folder.join(vad::RUNTIME_FILES[0].0);
    LOADED.get_or_init(|| vad::load_runtime(&library).ok()).clone().ok_or(Raised)
}

/// `WhisperModel` over the shipped native libraries in one folder.
pub struct SpeechFactory {
    natives: PathBuf,
}

impl SpeechFactory {
    /// The factory for the natives in `folder` (beside the owner in the product).
    pub fn new(folder: &Path) -> Self {
        Self { natives: folder.to_path_buf() }
    }
}

impl ModelFactory for SpeechFactory {
    fn load(&mut self, directory: &Path) -> Result<Box<dyn Model>, Raised> {
        let natives = std::path::absolute(&self.natives).map_err(|_| Raised)?;
        verify(&natives).map_err(|_| Raised)?;
        let swresample = Swresample::load(&natives).map_err(|_| Raised)?;
        let blas = OpenBlas::load(&natives).map_err(|_| Raised)?;
        let environment = runtime(&natives)?;
        let vad = Silero::new(&environment, &natives.join(vad::VAD_MODEL.0)).map_err(|_| Raised)?;
        let engine = Engine::load(directory).map_err(|_| Raised)?;
        let tokenizer = WhisperTokenizer::load(directory).map_err(|_| Raised)?;
        Ok(Box::new(Speech { swresample, blas, vad, engine, tokenizer }))
    }
}

/// One loaded model.
pub struct Speech {
    swresample: Swresample,
    blas: OpenBlas,
    vad: Silero,
    engine: Engine,
    tokenizer: WhisperTokenizer,
}

/// `decode_audio(BytesIO(wav), sampling_rate=16000)` on the worker's own recording.
pub fn decode_wav(swresample: &Swresample, wav: &[u8]) -> Result<Vec<f32>, Raised> {
    let pcm = audio::read_wav(wav).ok_or(Raised)?;
    if pcm.rate == 16_000 {
        return audio::decode(&pcm, None);
    }
    let rate = i32::try_from(pcm.rate).map_err(|_| Raised)?;
    let mut resampler = swresample.mono_s16_to_16k(rate).map_err(|_| Raised)?;
    audio::decode(&pcm, Some(&mut resampler))
}

impl Speech {
    /// The whole transcription, every field faster-whisper reports.
    pub fn transcription(
        &mut self,
        wav: &[u8],
        language: Option<&str>,
    ) -> Result<super::whisper::Transcription, Raised> {
        let samples = decode_wav(&self.swresample, wav)?;
        let mut pipeline = Pipeline {
            engine: &self.engine,
            tokenizer: &self.tokenizer,
            vad: &mut self.vad,
            blas: &self.blas,
            thresholds: Thresholds::worker(),
        };
        pipeline.transcribe(&samples, language).map_err(|_| Raised)
    }
}

impl Model for Speech {
    fn supports(&self, language: &str) -> bool {
        LANGUAGE_CODES.contains(&language)
    }

    fn transcribe<'a>(
        &'a mut self,
        audio: &'a [u8],
        options: &DecodeOptions,
    ) -> Result<Segments<'a>, Raised> {
        // faster-whisper decodes lazily; decoding everything first raises the same failures
        // before the first segment instead of during iteration, which the caller maps the same.
        let transcription = self.transcription(audio, options.language.as_deref())?;
        Ok(Box::new(transcription.segments.into_iter().map(|segment| {
            Ok(Segment {
                text: segment.text,
                no_speech_prob: segment.no_speech_prob,
                avg_logprob: segment.avg_logprob,
            })
        })))
    }
}
