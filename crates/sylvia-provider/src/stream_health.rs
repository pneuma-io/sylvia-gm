//! `provider/_stream_health.py`: a per-chunk inactivity bound for a provider stream.

use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::provider::ChunkStream;
use crate::types::ProviderStreamChunk;
use std::time::{Duration, Instant};

/// Wraps a stream so each wait for the next chunk is bounded by `health_interval` seconds.
pub struct StreamHealthMonitor<'a> {
    stream: ChunkStream<'a>,
    health_interval: f64,
    provider_name: String,
    chunk_count: u64,
    last_chunk_at: Instant,
}

impl<'a> StreamHealthMonitor<'a> {
    /// `StreamHealthMonitor(stream, health_interval=60.0, provider_name="unknown")`.
    pub fn new(stream: ChunkStream<'a>, health_interval: f64, provider_name: impl Into<String>) -> Self {
        Self {
            stream,
            health_interval,
            provider_name: provider_name.into(),
            chunk_count: 0,
            last_chunk_at: Instant::now(),
        }
    }

    /// The next chunk, `None` at the end, or `StaleConnectionError` after the interval.
    pub async fn next(&mut self) -> Option<ProviderResult<ProviderStreamChunk>> {
        use futures_util::StreamExt;
        let wait = Duration::from_secs_f64(self.health_interval.max(0.0));
        match tokio::time::timeout(wait, self.stream.next()).await {
            Ok(item) => {
                if item.is_some() {
                    self.chunk_count += 1;
                    self.last_chunk_at = Instant::now();
                }
                item
            }
            Err(_) => {
                crate::log::warning(&format!(
                    "Stream health check failed for {} after {:.0}s ({} chunks received)",
                    self.provider_name, self.health_interval, self.chunk_count
                ));
                Some(Err(ProviderError::new(
                    ErrorKind::StaleConnection,
                    format!(
                        "Stream from {} dead: no chunks for {}s ({} received)",
                        self.provider_name,
                        sylvia_base::pyjson::float_repr(self.health_interval),
                        self.chunk_count
                    ),
                )))
            }
        }
    }

    /// Number of chunks received so far.
    pub fn chunk_count(&self) -> u64 {
        self.chunk_count
    }

    /// Seconds since the last chunk arrived.
    pub fn idle_seconds(&self) -> f64 {
        self.last_chunk_at.elapsed().as_secs_f64()
    }
}
