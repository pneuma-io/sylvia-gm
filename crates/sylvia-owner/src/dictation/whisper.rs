//! faster-whisper 1.2.1's `WhisperModel.transcribe` with the worker's options (`beam_size=5`,
//! `condition_on_previous_text=False`, `vad_filter=True` with `min_silence_duration_ms=500`,
//! `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`, the language given or detected), and
//! everything it calls for them: `Tokenizer` (`tokenizer.py`), `detect_language`,
//! `generate_with_fallback`, `_split_segments_by_timestamps`, `generate_segments` and
//! `restore_speech_timestamps`. Options the worker never sets (word timestamps, hotwords, clip
//! timestamps, prefixes, initial prompts, batching) are not ported. Windows, `dictation` feature.

use super::engine::{Engine, EngineError};
use super::features::{log_mel, MelProduct, N_MELS};
use super::vad::{collect, pad_to_windows, speech_timestamps, Silero, VadOptions};
use ct2rs::sys::WhisperOptions;
use std::io::Write as _;
use std::path::Path;

/// `nb_max_frames` (30 s of 10 ms frames).
const WINDOW_FRAMES: usize = 3000;
/// `feature_extractor.time_per_frame`.
const TIME_PER_FRAME: f64 = 160.0 / 16_000.0;
/// `time_precision`.
const TIME_PRECISION: f64 = 0.02;
/// `input_stride`.
const INPUT_STRIDE: usize = 2;
/// `max_length`.
const MAX_LENGTH: usize = 448;
/// `temperature`'s default schedule.
const TEMPERATURES: [f64; 6] = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];

/// What stopped a transcription.
#[derive(Debug)]
pub enum TranscribeError {
    /// The engine raised.
    Engine(EngineError),
    /// The VAD raised.
    Vad(String),
    /// The tokenizer raised.
    Tokenizer(String),
}

impl From<EngineError> for TranscribeError {
    fn from(error: EngineError) -> Self {
        Self::Engine(error)
    }
}

/// faster-whisper's `Tokenizer` over the model's `tokenizer.json`.
pub struct WhisperTokenizer {
    inner: tokenizers::Tokenizer,
    vocabulary: Vec<String>,
    sot: usize,
    eot: usize,
    sot_prev: usize,
    sot_lm: usize,
    no_timestamps: usize,
    no_speech: usize,
    transcribe: usize,
    translate: usize,
}

impl WhisperTokenizer {
    /// `tokenizers.Tokenizer.from_file(model / "tokenizer.json")`, and the model's
    /// `vocabulary.txt` for the engine's string tokens.
    pub fn load(model: &Path) -> Result<Self, TranscribeError> {
        let inner = tokenizers::Tokenizer::from_file(model.join("tokenizer.json"))
            .map_err(|e| TranscribeError::Tokenizer(e.to_string()))?;
        let vocabulary = std::fs::read_to_string(model.join("vocabulary.txt"))
            .map_err(|e| TranscribeError::Tokenizer(e.to_string()))?
            .lines()
            .map(str::to_string)
            .collect();
        let id = |token: &str| inner.token_to_id(token).map(|id| id as usize);
        let required =
            |token: &str| id(token).ok_or_else(|| TranscribeError::Tokenizer(format!("no {token}")));
        Ok(Self {
            sot: required("<|startoftranscript|>")?,
            eot: required("<|endoftext|>")?,
            sot_prev: required("<|startofprev|>")?,
            sot_lm: required("<|startoflm|>")?,
            no_timestamps: required("<|notimestamps|>")?,
            no_speech: id("<|nospeech|>")
                .or_else(|| id("<|nocaptions|>"))
                .ok_or_else(|| TranscribeError::Tokenizer("no <|nospeech|>".into()))?,
            transcribe: required("<|transcribe|>")?,
            translate: required("<|translate|>")?,
            vocabulary,
            inner,
        })
    }

    /// `timestamp_begin`.
    fn timestamp_begin(&self) -> usize {
        self.no_timestamps + 1
    }

    /// `tokenizer.token_to_id("<|language|>")`.
    fn language(&self, code: &str) -> Option<usize> {
        self.inner.token_to_id(&format!("<|{code}|>")).map(|id| id as usize)
    }

    /// `encode(text)`: ids without special tokens.
    fn encode(&self, text: &str) -> Result<Vec<usize>, TranscribeError> {
        let encoding =
            self.inner.encode(text, false).map_err(|e| TranscribeError::Tokenizer(e.to_string()))?;
        Ok(encoding.get_ids().iter().map(|id| *id as usize).collect())
    }

    /// `decode(tokens)`: the text tokens (below `eot`), special tokens skipped.
    pub fn decode(&self, tokens: &[usize]) -> Result<String, TranscribeError> {
        let ids: Vec<u32> =
            tokens.iter().filter(|token| **token < self.eot).map(|token| *token as u32).collect();
        self.inner.decode(&ids, true).map_err(|e| TranscribeError::Tokenizer(e.to_string()))
    }

    /// `non_speech_tokens`.
    fn non_speech_tokens(&self) -> Result<Vec<usize>, TranscribeError> {
        let mut symbols: Vec<String> =
            "\"#()*+/:;<=>@[\\]^_`{|}~「」『』".chars().map(String::from).collect();
        symbols.extend(
            "<< >> <<< >>> -- --- -( -[ (' (\" (( )) ((( ))) [[ ]] {{ }} ♪♪ ♪♪♪".split(' ').map(String::from),
        );
        let miscellaneous: Vec<String> = "♩♪♫♬♭♮♯".chars().map(String::from).collect();
        let mut result = std::collections::BTreeSet::new();
        result.insert(self.encode(" -")?[0]);
        result.insert(self.encode(" '")?[0]);
        // `set("♩♪♫♬♭♮♯")` iterates in hash order, which only changes when each is added.
        for symbol in symbols.iter().chain(miscellaneous.iter()) {
            for tokens in [self.encode(symbol)?, self.encode(&format!(" {symbol}"))?] {
                if tokens.len() == 1 || miscellaneous.contains(symbol) {
                    result.insert(tokens[0]);
                }
            }
        }
        Ok(result.into_iter().collect())
    }

    /// `get_suppressed_tokens(tokenizer, [-1])`.
    pub fn suppressed_tokens(&self) -> Result<Vec<usize>, TranscribeError> {
        let mut tokens: std::collections::BTreeSet<usize> = self.non_speech_tokens()?.into_iter().collect();
        tokens.extend([
            self.transcribe,
            self.translate,
            self.sot,
            self.sot_prev,
            self.sot_lm,
            self.no_speech,
        ]);
        Ok(tokens.into_iter().collect())
    }

    fn token_strings(&self, ids: &[usize]) -> Vec<String> {
        ids.iter().map(|id| self.vocabulary[*id].clone()).collect()
    }
}

/// One `Segment`.
#[derive(Debug, Clone, PartialEq)]
pub struct Segment {
    /// `id`.
    pub id: usize,
    /// `seek`.
    pub seek: usize,
    /// `start`, after `restore_speech_timestamps`.
    pub start: f64,
    /// `end`, after `restore_speech_timestamps`.
    pub end: f64,
    /// `text`.
    pub text: String,
    /// `tokens`.
    pub tokens: Vec<usize>,
    /// `temperature`.
    pub temperature: f64,
    /// `avg_logprob`.
    pub avg_logprob: f64,
    /// `compression_ratio`.
    pub compression_ratio: f64,
    /// `no_speech_prob`.
    pub no_speech_prob: f64,
}

/// The result of one transcription.
#[derive(Debug, Clone, PartialEq)]
pub struct Transcription {
    /// `info.language`.
    pub language: String,
    /// `info.language_probability`.
    pub language_probability: f64,
    /// The VAD's speech chunks.
    pub speech_chunks: Vec<(i64, i64)>,
    /// The segments.
    pub segments: Vec<Segment>,
}

/// `get_compression_ratio(text)`: zlib at its default level, as CPython's `zlib.compress`.
fn compression_ratio(text: &str) -> f64 {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(6));
    let _ = encoder.write_all(text.as_bytes());
    let compressed = encoder.finish().unwrap_or_default();
    text.len() as f64 / compressed.len() as f64
}

/// `pad_or_trim(features[:, start:start+count])` as one C-ordered `[N_MELS, 3000]` block.
fn window(values: &[f32], frames: usize, start: usize, count: usize) -> Vec<f32> {
    let mut out = vec![0.0_f32; N_MELS * WINDOW_FRAMES];
    for band in 0..N_MELS {
        for frame in 0..count.min(WINDOW_FRAMES) {
            out[band * WINDOW_FRAMES + frame] = values[band * frames + start + frame];
        }
    }
    out
}

/// Python's `round(value, 2)`: correctly rounded to two decimals.
fn round2(value: f64) -> f64 {
    format!("{value:.2}").parse().unwrap_or(value)
}

/// `SpeechTimestampsMap(chunks, 16000)`.
struct TimestampMap {
    chunk_end_sample: Vec<i64>,
    total_silence_before: Vec<f64>,
}

impl TimestampMap {
    fn new(chunks: &[(i64, i64)]) -> Self {
        let (mut previous_end, mut silent) = (0_i64, 0_i64);
        let mut chunk_end_sample = Vec::new();
        let mut total_silence_before = Vec::new();
        for (start, end) in chunks {
            silent += start - previous_end;
            previous_end = *end;
            chunk_end_sample.push(end - silent);
            total_silence_before.push(silent as f64 / 16_000.0);
        }
        Self { chunk_end_sample, total_silence_before }
    }

    fn original(&self, time: f64, is_end: bool) -> f64 {
        let sample = (time * 16_000.0) as i64;
        let index = match self.chunk_end_sample.iter().position(|end| *end == sample) {
            Some(index) if is_end => index,
            _ => {
                // bisect.bisect: the insertion point after equal values.
                let point = self.chunk_end_sample.partition_point(|end| *end <= sample);
                point.min(self.chunk_end_sample.len() - 1)
            }
        };
        round2(self.total_silence_before[index] + time)
    }
}

struct Decoded {
    tokens: Vec<usize>,
    no_speech_prob: f64,
    avg_logprob: f64,
    temperature: f64,
    compression_ratio: f64,
}

/// The transcription engine: CTranslate2, the tokenizer, the VAD and the feature product.
pub struct Pipeline<'a> {
    /// The model.
    pub engine: &'a Engine,
    /// Its tokenizer.
    pub tokenizer: &'a WhisperTokenizer,
    /// The VAD.
    pub vad: &'a mut Silero,
    /// The feature product (NumPy's OpenBLAS).
    pub blas: &'a dyn MelProduct,
    /// The decode thresholds ([`Thresholds::worker`] in the product).
    pub thresholds: Thresholds,
}

/// `compression_ratio_threshold`, `log_prob_threshold` and `no_speech_threshold`; `None` turns a
/// check off, as Python's `None` does.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Thresholds {
    /// `compression_ratio_threshold`.
    pub compression_ratio: Option<f64>,
    /// `log_prob_threshold`.
    pub log_prob: Option<f64>,
    /// `no_speech_threshold`.
    pub no_speech: Option<f64>,
}

impl Thresholds {
    /// The worker's: the default compression ratio threshold 2.4, `log_prob_threshold=-1.0`,
    /// `no_speech_threshold=0.6`.
    pub fn worker() -> Self {
        Self { compression_ratio: Some(2.4), log_prob: Some(-1.0), no_speech: Some(0.6) }
    }
}

impl Pipeline<'_> {
    /// `generate_with_fallback(encoder_output, prompt, tokenizer, options)`.
    fn generate_with_fallback(
        &self,
        encoder_output: &ct2rs::sys::StorageView,
        prompt: &[usize],
        suppress: &[usize],
    ) -> Result<Decoded, TranscribeError> {
        let max_initial_timestamp_index = (1.0_f64 / TIME_PRECISION).round_ties_even() as usize;
        let mut all: Vec<Decoded> = Vec::new();
        let mut below: Vec<usize> = Vec::new();
        let prompt = self.tokenizer.token_strings(prompt);
        let mut last_temperature = 0.0;
        for temperature in TEMPERATURES {
            last_temperature = temperature;
            let sampling = temperature > 0.0;
            let options = WhisperOptions {
                beam_size: if sampling { 1 } else { 5 },
                patience: 1.0,
                length_penalty: 1.0,
                repetition_penalty: 1.0,
                no_repeat_ngram_size: 0,
                max_length: MAX_LENGTH,
                sampling_topk: if sampling { 0 } else { 1 },
                sampling_temperature: if sampling { temperature as f32 } else { 1.0 },
                num_hypotheses: if sampling { 5 } else { 1 },
                return_scores: true,
                return_logits_vocab: false,
                return_no_speech_prob: true,
                max_initial_timestamp_index,
                suppress_blank: true,
                suppress_tokens: suppress.iter().map(|t| *t as i32).collect(),
            };
            let result = self.engine.generate(encoder_output, &prompt, &options)?;
            let tokens = result.sequences_ids.first().cloned().unwrap_or_default();
            let length = tokens.len() as f64;
            let score = f64::from(result.scores.first().copied().unwrap_or(0.0));
            // cum_logprob = scores[0] * (seq_len ** length_penalty); avg = cum / (seq_len + 1)
            let avg_logprob = (score * length.powf(1.0)) / (length + 1.0);
            let text = self.tokenizer.decode(&tokens)?;
            let ratio = compression_ratio(sylvia_base::pystr::strip(&text));
            let no_speech_prob = f64::from(result.no_speech_prob);
            all.push(Decoded { tokens, no_speech_prob, avg_logprob, temperature, compression_ratio: ratio });
            let limits = self.thresholds;
            let mut needs_fallback = false;
            if let Some(threshold) = limits.compression_ratio {
                if ratio > threshold {
                    needs_fallback = true;
                } else {
                    below.push(all.len() - 1);
                }
            }
            let low_logprob = limits.log_prob.is_some_and(|threshold| avg_logprob < threshold);
            if low_logprob {
                needs_fallback = true;
            }
            if limits.no_speech.is_some_and(|threshold| no_speech_prob > threshold) && low_logprob {
                needs_fallback = false;
            }
            if !needs_fallback {
                return Ok(all.pop().expect("just pushed"));
            }
        }
        // All failed: the highest average log probability (the first of equals), with the last
        // temperature tried.
        let pool: Vec<usize> = if below.is_empty() { (0..all.len()).collect() } else { below };
        let mut best = pool[0];
        for index in pool.into_iter().skip(1) {
            if all[index].avg_logprob > all[best].avg_logprob {
                best = index;
            }
        }
        let mut chosen = all.swap_remove(best);
        chosen.temperature = last_temperature;
        Ok(chosen)
    }

    /// `detect_language(features=features)` with one segment and threshold 0.5.
    fn detect_language(&self, values: &[f32], frames: usize) -> Result<(String, f64), TranscribeError> {
        let mut segment = window(values, frames, 0, frames.min(WINDOW_FRAMES));
        let encoder_output = self.engine.encode(&mut segment, WINDOW_FRAMES)?;
        let languages = self.engine.detect_language(&encoder_output)?;
        let top = languages
            .first()
            .ok_or_else(|| TranscribeError::Engine(EngineError::Engine("no language".into())))?;
        let code = top.token.trim_start_matches("<|").trim_end_matches("|>").to_string();
        // Over the threshold, or else the single segment's own top language (the majority vote).
        Ok((code, f64::from(top.probability)))
    }

    /// `WhisperModel.transcribe(audio, language=..., **worker options)` on decoded 16 kHz audio.
    pub fn transcribe(
        &mut self,
        audio: &[f32],
        language: Option<&str>,
    ) -> Result<Transcription, TranscribeError> {
        let padded = pad_to_windows(audio);
        let probabilities = self.vad.probabilities(&padded).map_err(|e| TranscribeError::Vad(e.0))?;
        let chunks = speech_timestamps(&probabilities, audio.len(), &VadOptions::worker());
        let kept = collect(audio, &chunks);
        let features = log_mel(&kept, 160, self.blas);
        let (language, language_probability) = match language {
            Some(code) => (code.to_string(), 1.0),
            None => self.detect_language(&features.values, features.frames)?,
        };
        let language_token = self
            .tokenizer
            .language(&language)
            .ok_or_else(|| TranscribeError::Tokenizer(format!("no <|{language}|>")))?;
        let prompt = vec![self.tokenizer.sot, language_token, self.tokenizer.transcribe];
        let suppress = self.tokenizer.suppressed_tokens()?;
        let timestamp_begin = self.tokenizer.timestamp_begin();

        let content_frames = features.frames.saturating_sub(1);
        let mut seek = 0_usize;
        let mut id = 0;
        let mut segments = Vec::new();
        while seek < content_frames {
            let time_offset = seek as f64 * TIME_PER_FRAME;
            let segment_size = WINDOW_FRAMES.min(content_frames - seek);
            let segment_duration = segment_size as f64 * TIME_PER_FRAME;
            let mut block = window(&features.values, features.frames, seek, segment_size);
            let encoder_output = self.engine.encode(&mut block, WINDOW_FRAMES)?;
            let decoded = self.generate_with_fallback(&encoder_output, &prompt, &suppress)?;
            // The no-speech check: skip the window unless the log probability is high enough.
            if let Some(threshold) = self.thresholds.no_speech {
                let mut should_skip = decoded.no_speech_prob > threshold;
                if self.thresholds.log_prob.is_some_and(|limit| decoded.avg_logprob > limit) {
                    should_skip = false;
                }
                if should_skip {
                    seek += segment_size;
                    continue;
                }
            }
            let previous_seek = seek;
            let tokens = &decoded.tokens;
            // _split_segments_by_timestamps
            let mut current: Vec<(f64, f64, Vec<usize>)> = Vec::new();
            let single_ending = tokens.len() >= 2
                && tokens[tokens.len() - 2] < timestamp_begin
                && timestamp_begin <= tokens[tokens.len() - 1];
            let consecutive: Vec<usize> = (1..tokens.len())
                .filter(|&i| tokens[i] >= timestamp_begin && tokens[i - 1] >= timestamp_begin)
                .collect();
            if !consecutive.is_empty() {
                let mut slices = consecutive.clone();
                if single_ending {
                    slices.push(tokens.len());
                }
                let mut last = 0;
                for slice in slices {
                    let sliced = tokens[last..slice].to_vec();
                    let start = time_offset + (sliced[0] - timestamp_begin) as f64 * TIME_PRECISION;
                    let end =
                        time_offset + (sliced[sliced.len() - 1] - timestamp_begin) as f64 * TIME_PRECISION;
                    current.push((start, end, sliced));
                    last = slice;
                }
                if single_ending {
                    seek += segment_size;
                } else {
                    seek += (tokens[last - 1] - timestamp_begin) * INPUT_STRIDE;
                }
            } else {
                let mut duration = segment_duration;
                let timestamps: Vec<usize> =
                    tokens.iter().copied().filter(|t| *t >= timestamp_begin).collect();
                if let Some(last) = timestamps.last() {
                    if *last != timestamp_begin {
                        duration = (*last - timestamp_begin) as f64 * TIME_PRECISION;
                    }
                }
                current.push((time_offset, time_offset + duration, tokens.clone()));
                seek += segment_size;
            }
            for (start, end, tokens) in current {
                let text = self.tokenizer.decode(&tokens)?;
                if start == end || sylvia_base::pystr::strip(&text).is_empty() {
                    continue;
                }
                id += 1;
                segments.push(Segment {
                    id,
                    seek: previous_seek,
                    start,
                    end,
                    text,
                    tokens,
                    temperature: decoded.temperature,
                    avg_logprob: decoded.avg_logprob,
                    compression_ratio: decoded.compression_ratio,
                    no_speech_prob: decoded.no_speech_prob,
                });
            }
        }
        // restore_speech_timestamps
        if !chunks.is_empty() {
            let map = TimestampMap::new(&chunks);
            for segment in &mut segments {
                segment.start = map.original(segment.start, false);
                segment.end = map.original(segment.end, true);
            }
        }
        Ok(Transcription { language, language_probability, speech_chunks: chunks, segments })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounding_follows_pythons_round_to_two_places() {
        // round(2.675, 2) == 2.67 and round(0.125, 2) == 0.12: the exact binary value, ties even.
        for (value, rounded) in [(2.675, 2.67), (0.125, 0.12), (0.135, 0.14), (1.005, 1.0), (-0.005, -0.01)] {
            assert_eq!(round2(value).to_bits(), f64::to_bits(rounded), "{value}");
        }
    }

    #[test]
    fn speech_times_map_back_past_the_removed_silence() {
        // Chunks at 1-2 s and 4-5 s: 1 s of silence before the first, 3 s before the second.
        let map = TimestampMap::new(&[(16_000, 32_000), (64_000, 80_000)]);
        assert_eq!(map.original(0.5, false), 1.5);
        // 1.0 s is the first chunk's end: an end stays in it, a start moves to the next chunk.
        assert_eq!(map.original(1.0, true), 2.0);
        assert_eq!(map.original(1.0, false), 4.0);
        // Past the last chunk's end stays in the last chunk.
        assert_eq!(map.original(9.0, false), 12.0);
    }

    #[test]
    fn the_compression_ratio_is_zlibs_at_its_default_level() {
        // len(b"aaaa...") / len(zlib.compress(b"a" * 100)) == 100 / 12
        assert_eq!(compression_ratio(&"a".repeat(100)), 100.0 / 12.0);
    }
}
