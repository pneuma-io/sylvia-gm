//! Microphone capture on cpal's WASAPI host, in place of `sounddevice` (PortAudio). Windows,
//! `dictation` feature.
//!
//! The device list is WASAPI's capture endpoints by their friendly names, the names PortAudio's
//! WASAPI host reports too, under one host, `Windows WASAPI`, at each endpoint's mix rate. So a
//! device's id is built as Python builds it (`device_id`), but from a different list: PortAudio
//! also lists every device under MME, DirectSound and WDM-KS, and its indexes differ. A saved
//! PortAudio id is matched to the same device by its identity hash: [`previous_identities`]
//! gives the hashes the same endpoint had under MME, DirectSound and WASAPI (difference row 38).
//!
//! A stream opens the endpoint's own shared-mode format and is downmixed to mono here, where
//! PortAudio asked the host for one channel; the recording's rate is the endpoint's rate, as
//! Python's is the listed device's `default_samplerate`.

use super::{CaptureSink, DeviceRow, Flow, Raised, Sound, Stream, StreamError};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Device, Host, SampleFormat, SizedSample};
use sha2::{Digest, Sha256};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// The host name in labels and ids.
pub const HOST: &str = "Windows WASAPI";
/// `HRESULT_FROM_WIN32(ERROR_ACCESS_DENIED)` as `std::io::Error` prints it: Windows' microphone
/// privacy setting refuses the endpoint with this code.
const ACCESS_DENIED: &str = "(os error -2147024891)";
/// What PortAudio's MME and DirectSound hosts report as every device's `default_samplerate`.
const LEGACY_RATE: &str = "44100.0";
/// MME device names: `WAVEINCAPS.szPname` holds 31 characters and a NUL.
const MME_NAME: usize = 31;

/// One listed capture endpoint.
struct Endpoint {
    device: Device,
    name: String,
    id: Option<String>,
    channels: u16,
    rate: u32,
}

/// `sounddevice` on cpal.
pub struct CpalSound {
    host: Option<Host>,
    endpoints: Vec<Endpoint>,
    default: i64,
}

impl Default for CpalSound {
    fn default() -> Self {
        Self { host: None, endpoints: Vec::new(), default: -1 }
    }
}

/// `str(float(rate))`: PortAudio reports rates as floats.
fn float_text(rate: u32) -> String {
    format!("{rate}.0")
}

fn identity(host: &str, name: &str, rate: &str) -> String {
    hex::encode(&Sha256::digest(format!("{host}\n{name}\n{rate}").as_bytes())[..8])
}

/// The identity hashes a PortAudio-era id of this endpoint could carry (`d-<index>-<hash>`):
/// its MME entry (the name cut to 31 characters, and in full), its DirectSound entry and its
/// WASAPI entry. WDM-KS names are kernel filter names, not endpoint names, so those ids are not
/// matched and the player chooses again.
pub fn previous_identities(name: &str, rate: u32) -> Vec<String> {
    let short: String = name.chars().take(MME_NAME).collect();
    let mut hashes = vec![
        identity("MME", &short, LEGACY_RATE),
        identity("MME", name, LEGACY_RATE),
        identity("Windows DirectSound", name, LEGACY_RATE),
        identity(HOST, name, &float_text(rate)),
    ];
    hashes.dedup();
    hashes
}

/// The identities of PortAudio's own default-input entries, which a saved id may name: they
/// follow the system default, as `default` does.
pub fn previous_default_identities() -> Vec<String> {
    vec![
        identity("MME", "Microsoft Sound Mapper - Input", LEGACY_RATE),
        identity("Windows DirectSound", "Primary Sound Capture Driver", LEGACY_RATE),
    ]
}

fn endpoint(device: Device) -> Option<Endpoint> {
    let name = device.description().ok()?.name().to_string();
    let config = device.default_input_config().ok()?;
    let id = device.id().ok().map(|id| id.to_string());
    Some(Endpoint { device, name, id, channels: config.channels(), rate: config.sample_rate() })
}

impl Sound for CpalSound {
    fn initialized(&self) -> bool {
        self.host.is_some()
    }

    fn terminate(&mut self) -> Result<(), Raised> {
        self.host = None;
        self.endpoints.clear();
        self.default = -1;
        Ok(())
    }

    fn initialize(&mut self) -> Result<(), Raised> {
        let host = cpal::host_from_id(cpal::HostId::Wasapi).map_err(|_| Raised)?;
        let endpoints: Vec<Endpoint> =
            host.input_devices().map_err(|_| Raised)?.filter_map(endpoint).collect();
        let default_id =
            host.default_input_device().and_then(|device| device.id().ok()).map(|id| id.to_string());
        self.default = default_id
            .and_then(|wanted| endpoints.iter().position(|endpoint| endpoint.id.as_deref() == Some(&wanted)))
            .map_or(-1, |index| index as i64);
        self.endpoints = endpoints;
        self.host = Some(host);
        Ok(())
    }

    fn query_devices(&mut self) -> Result<Vec<DeviceRow>, Raised> {
        Ok(self
            .endpoints
            .iter()
            .map(|endpoint| DeviceRow {
                name: endpoint.name.clone(),
                max_input_channels: i64::from(endpoint.channels),
                hostapi: 0,
                default_samplerate: serde_json::Number::from_f64(f64::from(endpoint.rate))
                    .unwrap_or_else(|| 0.into()),
            })
            .collect())
    }

    fn query_hostapis(&mut self) -> Result<Vec<String>, Raised> {
        Ok(vec![HOST.to_string()])
    }

    fn default_input(&mut self) -> Result<i64, Raised> {
        Ok(self.default)
    }

    fn previous_identities(&mut self, index: usize) -> Vec<String> {
        self.endpoints
            .get(index)
            .map_or_else(Vec::new, |endpoint| previous_identities(&endpoint.name, endpoint.rate))
    }

    fn previous_default_identities(&mut self) -> Vec<String> {
        previous_default_identities()
    }

    fn open_stream(
        &mut self,
        device: i64,
        rate: i64,
        sink: Arc<CaptureSink>,
    ) -> Result<Box<dyn Stream>, StreamError> {
        let endpoint =
            usize::try_from(device).ok().and_then(|at| self.endpoints.get(at)).ok_or(StreamError::Other)?;
        let config = endpoint.device.default_input_config().map_err(|error| classify(&error))?;
        if i64::from(config.sample_rate()) != rate {
            return Err(StreamError::Other);
        }
        let format = config.sample_format();
        let channels = usize::from(config.channels().max(1));
        let config = config.config();
        let stopped = Arc::new(AtomicBool::new(false));
        let errors = Arc::clone(&sink);
        let on_error = move |_error: cpal::Error| {
            errors.deliver(&[], true);
        };
        let stream = match format {
            SampleFormat::F32 => build::<f32>(&endpoint.device, &config, channels, sink, stopped, on_error),
            SampleFormat::I16 => build::<i16>(&endpoint.device, &config, channels, sink, stopped, on_error),
            SampleFormat::I32 => build::<i32>(&endpoint.device, &config, channels, sink, stopped, on_error),
            SampleFormat::U8 => build::<u8>(&endpoint.device, &config, channels, sink, stopped, on_error),
            _ => Err(StreamError::Other),
        }?;
        Ok(Box::new(CpalStream { stream: Some(stream) }))
    }
}

fn classify(error: &cpal::Error) -> StreamError {
    let denied = error.kind() == cpal::ErrorKind::PermissionDenied
        || error.message().is_some_and(|message| message.contains(ACCESS_DENIED));
    if denied {
        StreamError::Permission
    } else {
        StreamError::Other
    }
}

/// Downmix one interleaved block to mono: the channels' mean, in float32.
pub fn downmix<T: SizedSample + cpal::Sample>(data: &[T], channels: usize) -> Vec<f32>
where
    f32: cpal::FromSample<T>,
{
    data.chunks_exact(channels)
        .map(|frame| frame.iter().map(|sample| sample.to_sample::<f32>()).sum::<f32>() / channels as f32)
        .collect()
}

fn build<T>(
    device: &Device,
    config: &cpal::StreamConfig,
    channels: usize,
    sink: Arc<CaptureSink>,
    stopped: Arc<AtomicBool>,
    on_error: impl FnMut(cpal::Error) + Send + 'static,
) -> Result<cpal::Stream, StreamError>
where
    T: SizedSample + cpal::Sample + Send + 'static,
    f32: cpal::FromSample<T>,
{
    device
        .build_input_stream::<T, _, _>(
            *config,
            move |data: &[T], _: &cpal::InputCallbackInfo| {
                // After `CallbackStop` PortAudio calls nothing more.
                if stopped.load(Ordering::Acquire) {
                    return;
                }
                if sink.deliver(&downmix(data, channels), false) == Flow::Stop {
                    stopped.store(true, Ordering::Release);
                }
            },
            on_error,
            None,
        )
        .map_err(|error| classify(&error))
}

/// An open cpal stream.
struct CpalStream {
    stream: Option<cpal::Stream>,
}

impl Stream for CpalStream {
    fn start(&mut self) -> Result<(), StreamError> {
        self.stream.as_ref().ok_or(StreamError::Other)?.play().map_err(|error| classify(&error))
    }

    fn stop(&mut self) -> Result<(), Raised> {
        match &self.stream {
            Some(stream) => stream.pause().map_err(|_| Raised),
            None => Ok(()),
        }
    }

    fn abort(&mut self) -> Result<(), Raised> {
        self.stop()
    }

    fn close(&mut self) -> Result<(), Raised> {
        self.stream = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn previous_identities_are_portaudios_ids_for_the_same_endpoint() {
        // `_device_id`'s hash for a device PortAudio listed under MME, DirectSound and WASAPI.
        let name = "Microphone (Example USB Audio)";
        let python = |host: &str, rate: &str| {
            let digest = hex::encode(Sha256::digest(format!("{host}\n{name}\n{rate}").as_bytes()));
            digest[..16].to_string()
        };
        let hashes = previous_identities(name, 48_000);
        for expected in
            [python("MME", "44100.0"), python("Windows DirectSound", "44100.0"), python(HOST, "48000.0")]
        {
            assert!(hashes.contains(&expected), "{expected}");
        }
        // The ids this host makes are WASAPI's own.
        let id = super::super::device_id(3, name, &serde_json::Number::from_f64(48_000.0).unwrap(), HOST);
        assert!(id.ends_with(&python(HOST, "48000.0")), "{id}");
    }

    #[test]
    fn mme_names_are_cut_to_31_characters() {
        let name = "Microphone Array (Intel Smart Sound Technology for Digital Microphones)";
        let short: String = name.chars().take(31).collect();
        assert_eq!(short, "Microphone Array (Intel Smart S");
        assert!(previous_identities(name, 48_000).contains(&identity("MME", &short, "44100.0")));
    }

    #[test]
    fn blocks_are_downmixed_to_their_mean() {
        assert_eq!(downmix(&[0.5_f32, -0.5, 1.0, 0.0], 2), vec![0.0, 0.5]);
        assert_eq!(downmix(&[16_384_i16, 16_384], 2), vec![0.5]);
        assert_eq!(downmix(&[0.25_f32, 0.75], 1), vec![0.25, 0.75]);
    }
}
