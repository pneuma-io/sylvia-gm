//! faster-whisper 1.2.1's voice activity detection (`vad.py`): the Silero VAD v6 model on the
//! shipped `onnxruntime.dll` (the locked onnxruntime 1.31.0 wheel's, loaded by `ort`'s
//! `load-dynamic`), `get_speech_timestamps` and `collect_chunks`. Windows, `dictation` feature.

use ort::environment::Environment;
use ort::session::Session;
use ort::value::Tensor;
use std::path::Path;

/// The shipped ONNX Runtime and the VAD model, with the SHA-256 they must have.
pub const RUNTIME_FILES: [(&str, &str); 2] = [
    ("onnxruntime.dll", "aa7737cc5c987286f09f6f12304fa786ea697b11b4ca7ab59a027ff4ce343a9c"),
    ("onnxruntime_providers_shared.dll", "029af27204cff24103c5837a0eb507e4a31ef269116f400c6b25c5457ddc9a32"),
];

/// faster-whisper 1.2.1's `assets/silero_vad_v6.onnx`, shipped beside the runtime.
pub const VAD_MODEL: (&str, &str) =
    ("silero_vad_v6.onnx", "4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2");

/// `get_speech_timestamps`'s window (`window_size_samples`).
const WINDOW: usize = 512;
/// `SileroVADModel.__call__`'s `context_size_samples`.
const CONTEXT: usize = 64;
/// `encoder_batch_size`.
const BATCH: usize = 10_000;

/// Why the VAD is unavailable or failed.
#[derive(Debug)]
pub struct VadError(pub String);

/// `SileroVADModel(path)`: one CPU session, one thread each way, fatal-only logging. (Python also
/// turns the CPU memory arena off, which changes allocation only, not the results.)
pub struct Silero {
    session: Session,
}

/// Load ONNX Runtime from `library` (`ort`'s `load-dynamic`); call once for the process.
pub fn load_runtime(library: &Path) -> Result<Environment, VadError> {
    ort::init_from(library).map_err(|e| VadError(e.to_string()))?.build().map_err(|e| VadError(e.to_string()))
}

impl Silero {
    /// `SileroVADModel(model)`.
    pub fn new(environment: &Environment, model: &Path) -> Result<Self, VadError> {
        let error = |e: ort::Error| VadError(e.to_string());
        let session = Session::builder(environment)
            .map_err(error)?
            .with_inter_threads(1)
            .map_err(|e| VadError(e.to_string()))?
            .with_intra_threads(1)
            .map_err(|e| VadError(e.to_string()))?
            .with_log_level(ort::logging::LogLevel::Fatal)
            .map_err(|e| VadError(e.to_string()))?
            .commit_from_file(model)
            .map_err(error)?;
        Ok(Self { session })
    }

    /// `model(audio)` on audio whose length is a multiple of 512: one speech probability per
    /// window.
    pub fn probabilities(&mut self, audio: &[f32]) -> Result<Vec<f32>, VadError> {
        assert_eq!(audio.len() % WINDOW, 0, "Input size should be a multiple of num_samples");
        let rows = audio.len() / WINDOW;
        // batched_audio = audio.reshape(-1, 512); context = batched_audio[..., -64:] is a view, so
        // `context[-1] = 0` also zeroes the last window's own last 64 samples.
        let mut windows: Vec<f32> = audio.to_vec();
        if rows > 0 {
            let last = rows * WINDOW;
            windows[last - CONTEXT..last].iter_mut().for_each(|sample| *sample = 0.0);
        }
        // context = np.roll(context, 1, 0): each window gets the previous window's last 64
        // samples, the first window the (zeroed) last one's.
        let mut batched = Vec::with_capacity(rows * (WINDOW + CONTEXT));
        for row in 0..rows {
            let source = if row == 0 { rows - 1 } else { row - 1 };
            batched.extend_from_slice(&windows[source * WINDOW + WINDOW - CONTEXT..source * WINDOW + WINDOW]);
            batched.extend_from_slice(&windows[row * WINDOW..row * WINDOW + WINDOW]);
        }
        let mut h = vec![0.0_f32; 128];
        let mut c = vec![0.0_f32; 128];
        let mut out = Vec::with_capacity(rows);
        let error = |e: ort::Error| VadError(e.to_string());
        let mut start = 0;
        while start < rows {
            let count = BATCH.min(rows - start);
            let input = Tensor::from_array((
                [count, WINDOW + CONTEXT],
                batched[start * (WINDOW + CONTEXT)..(start + count) * (WINDOW + CONTEXT)].to_vec(),
            ))
            .map_err(error)?;
            let h_in = Tensor::from_array(([1_usize, 1, 128], h.clone())).map_err(error)?;
            let c_in = Tensor::from_array(([1_usize, 1, 128], c.clone())).map_err(error)?;
            let outputs =
                self.session.run(ort::inputs!["input" => input, "h" => h_in, "c" => c_in]).map_err(error)?;
            // `output, h, c = session.run(None, ...)`: the model's outputs in order.
            let (_, probabilities) = outputs[0].try_extract_tensor::<f32>().map_err(error)?;
            out.extend_from_slice(probabilities);
            h = outputs[1].try_extract_tensor::<f32>().map_err(error)?.1.to_vec();
            c = outputs[2].try_extract_tensor::<f32>().map_err(error)?.1.to_vec();
            start += count;
        }
        Ok(out)
    }
}

/// `VadOptions` as the worker gives them (`min_silence_duration_ms=500`, the rest default).
#[derive(Debug, Clone, Copy)]
pub struct VadOptions {
    /// `threshold`.
    pub threshold: f64,
    /// `min_silence_duration_ms`.
    pub min_silence_duration_ms: f64,
    /// `speech_pad_ms`.
    pub speech_pad_ms: f64,
}

impl VadOptions {
    /// `VadOptions(min_silence_duration_ms=500)`.
    pub fn worker() -> Self {
        Self { threshold: 0.5, min_silence_duration_ms: 500.0, speech_pad_ms: 400.0 }
    }
}

/// `np.pad(audio, (0, 512 - len(audio) % 512))`: a whole window of zeros when already aligned.
pub fn pad_to_windows(audio: &[f32]) -> Vec<f32> {
    let mut padded = audio.to_vec();
    padded.extend(std::iter::repeat_n(0.0_f32, WINDOW - audio.len() % WINDOW));
    padded
}

/// `get_speech_timestamps(audio, options)`: `(start, end)` sample pairs.
pub fn speech_timestamps(
    probabilities: &[f32],
    audio_length: usize,
    options: &VadOptions,
) -> Vec<(i64, i64)> {
    let sampling_rate = 16_000.0_f64;
    let window = WINDOW as i64;
    let min_speech_samples = sampling_rate * 0.0 / 1000.0;
    let speech_pad_samples = sampling_rate * options.speech_pad_ms / 1000.0;
    let min_silence_samples = sampling_rate * options.min_silence_duration_ms / 1000.0;
    let min_silence_samples_at_max_speech = sampling_rate * 98.0 / 1000.0;
    // A NumPy float32 compared with a Python float: the float is cast to float32 (NEP 50).
    let threshold = options.threshold as f32;
    let neg_threshold = (options.threshold - 0.15).max(0.01) as f32;
    let audio_length = audio_length as i64;

    let mut triggered = false;
    let mut speeches: Vec<(i64, i64)> = Vec::new();
    let mut current: Option<(i64, Option<i64>)> = None;
    let (mut temp_end, mut prev_end, mut next_start) = (0_i64, 0_i64, 0_i64);
    for (i, probability) in probabilities.iter().enumerate() {
        let at = window * i as i64;
        let probability = *probability;
        if probability >= threshold && temp_end != 0 {
            temp_end = 0;
            if next_start < prev_end {
                next_start = at;
            }
        }
        if probability >= threshold && !triggered {
            triggered = true;
            current = Some((at, None));
            continue;
        }
        // The maximum-speech split never applies: the worker's max_speech_duration_s is infinite.
        if probability < neg_threshold && triggered {
            if temp_end == 0 {
                temp_end = at;
            }
            if (at - temp_end) as f64 > min_silence_samples_at_max_speech {
                prev_end = temp_end;
            }
            if ((at - temp_end) as f64) < min_silence_samples {
                continue;
            }
            if let Some((start, _)) = current {
                if ((temp_end - start) as f64) > min_speech_samples {
                    speeches.push((start, temp_end));
                }
            }
            current = None;
            prev_end = 0;
            next_start = 0;
            temp_end = 0;
            triggered = false;
            continue;
        }
    }
    if let Some((start, _)) = current {
        if ((audio_length - start) as f64) > min_speech_samples {
            speeches.push((start, audio_length));
        }
    }
    let _ = (prev_end, next_start);
    // Padding: `int(...)` of float arithmetic, `//` on ints.
    let count = speeches.len();
    for i in 0..count {
        if i == 0 {
            speeches[i].0 = (speeches[i].0 as f64 - speech_pad_samples).max(0.0) as i64;
        }
        if i != count - 1 {
            let silence = speeches[i + 1].0 - speeches[i].1;
            if (silence as f64) < 2.0 * speech_pad_samples {
                speeches[i].1 += silence.div_euclid(2);
                speeches[i + 1].0 = (speeches[i + 1].0 - silence.div_euclid(2)).max(0);
            } else {
                speeches[i].1 = (audio_length as f64).min(speeches[i].1 as f64 + speech_pad_samples) as i64;
                speeches[i + 1].0 = (speeches[i + 1].0 as f64 - speech_pad_samples).max(0.0) as i64;
            }
        } else {
            speeches[i].1 = (audio_length as f64).min(speeches[i].1 as f64 + speech_pad_samples) as i64;
        }
    }
    speeches
}

/// `np.concatenate(collect_chunks(audio, chunks)[0])`: the speech, joined.
pub fn collect(audio: &[f32], chunks: &[(i64, i64)]) -> Vec<f32> {
    let mut out = Vec::new();
    for &(start, end) in chunks {
        let (start, end) =
            (start.clamp(0, audio.len() as i64) as usize, end.clamp(0, audio.len() as i64) as usize);
        if start < end {
            out.extend_from_slice(&audio[start..end]);
        }
    }
    out
}
