//! faster-whisper 1.2.1's `decode_audio(audio, sampling_rate=16000)` on the worker's own WAV.
//!
//! Python opens the in-memory WAV with PyAV: FFmpeg's WAV demuxer reads 4096-byte packets
//! (2048 samples of 16-bit mono), `_group_frames` gathers them in an `AudioFifo` until it holds
//! at least 500,000 samples and then emits everything it holds, and
//! `AudioResampler(format="s16", layout="mono", rate=16000)` resamples each group (or passes it
//! through unchanged when the input is already 16-bit mono 16 kHz), then flushes. The result is
//! `int16 / 32768.0` as float32.
//!
//! The worker only ever decodes the WAV its own `Recording.stop` wrote ([`super::wav_bytes`]),
//! so only that format is read.

use super::Raised;

/// faster-whisper's `num_samples` for `_group_frames`.
pub const GROUP_SAMPLES: usize = 500_000;
/// Samples in one WAV demuxer packet of 16-bit mono (`MAX_SIZE` 4096 bytes).
pub const PACKET_SAMPLES: usize = 2048;

/// A 16-bit mono PCM WAV as `wave` writes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pcm {
    /// The sample rate.
    pub rate: u32,
    /// The samples.
    pub samples: Vec<i16>,
}

/// Read the WAV `wav_bytes` writes: `RIFF`/`WAVE`, a 16-byte PCM `fmt ` chunk with one channel
/// and 16 bits, then `data`.
pub fn read_wav(bytes: &[u8]) -> Option<Pcm> {
    let u16_at = |at: usize| bytes.get(at..at + 2).map(|b| u16::from_le_bytes([b[0], b[1]]));
    let u32_at = |at: usize| bytes.get(at..at + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]));
    if bytes.get(0..4)? != b"RIFF" || bytes.get(8..16)? != b"WAVEfmt " || u32_at(16)? != 16 {
        return None;
    }
    if u16_at(20)? != 1 || u16_at(22)? != 1 || u16_at(34)? != 16 || bytes.get(36..40)? != b"data" {
        return None;
    }
    let rate = u32_at(24)?;
    let length = usize::try_from(u32_at(40)?).ok()?;
    let data = bytes.get(44..44 + length)?;
    Some(Pcm {
        rate,
        samples: data.as_chunks::<2>().0.iter().map(|pair| i16::from_le_bytes(*pair)).collect(),
    })
}

/// `_group_frames(frames, 500000)` over the demuxer's packets: the sample counts of the groups.
pub fn group_sizes(samples: usize) -> Vec<usize> {
    let mut groups = Vec::new();
    let mut queued = 0;
    let mut at = 0;
    while at < samples {
        let packet = PACKET_SAMPLES.min(samples - at);
        queued += packet;
        at += packet;
        if queued >= GROUP_SAMPLES {
            groups.push(queued);
            queued = 0;
        }
    }
    if queued > 0 {
        groups.push(queued);
    }
    groups
}

/// The 16-bit resampling step, so the decode can be driven with libswresample or a stand-in.
pub trait Resample {
    /// Resample one group.
    fn convert(&mut self, group: &[i16]) -> Result<Vec<i16>, Raised>;
    /// Flush what the resampler still holds.
    fn flush(&mut self) -> Result<Vec<i16>, Raised>;
}

/// `decode_audio` from 16-bit samples: passthrough at 16 kHz, otherwise each group resampled,
/// then the flush; scaled to float32.
pub fn decode(pcm: &Pcm, resampler: Option<&mut dyn Resample>) -> Result<Vec<f32>, Raised> {
    let mut out: Vec<i16> = Vec::with_capacity(pcm.samples.len());
    match resampler {
        _ if pcm.rate == 16_000 => out.extend_from_slice(&pcm.samples),
        Some(resampler) => {
            let mut at = 0;
            for size in group_sizes(pcm.samples.len()) {
                out.extend(resampler.convert(&pcm.samples[at..at + size])?);
                at += size;
            }
            // `AudioResampler.resample(None)` before any frame returns nothing and builds no
            // graph, so an empty recording is not flushed either.
            if !pcm.samples.is_empty() {
                out.extend(resampler.flush()?);
            }
        }
        None => return Err(Raised),
    }
    Ok(out.iter().map(|sample| f32::from(*sample) / 32768.0).collect())
}

#[cfg(all(windows, feature = "dictation"))]
impl Resample for super::swresample::Resampler<'_> {
    fn convert(&mut self, group: &[i16]) -> Result<Vec<i16>, Raised> {
        super::swresample::Resampler::convert(self, group).map_err(|_| Raised)
    }
    fn flush(&mut self) -> Result<Vec<i16>, Raised> {
        super::swresample::Resampler::flush(self).map_err(|_| Raised)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn groups_close_at_the_first_packet_reaching_500000_samples() {
        assert_eq!(group_sizes(0), Vec::<usize>::new());
        assert_eq!(group_sizes(1), [1]);
        assert_eq!(group_sizes(501_760), [501_760]);
        assert_eq!(group_sizes(501_761), [501_760, 1]);
        assert_eq!(group_sizes(1_100_000), [501_760, 501_760, 96_480]);
    }

    #[test]
    fn the_workers_own_wav_reads_back() {
        let wav = crate::dictation::wav_bytes(&[0.5, -0.25, 1.0], 48_000).unwrap();
        assert_eq!(read_wav(&wav), Some(Pcm { rate: 48_000, samples: vec![16383, -8191, 32767] }));
        assert_eq!(read_wav(&wav[..43]), None);
    }
}
