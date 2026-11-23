//! `ProviderControl`: injected cancellation and steering control, holding no persistence handle.
//!
//! Python's control is shared by the session loop's thread and the provider's worker, with
//! `threading.Event`s for cancellation and queued steering. Here the two events are watch
//! channels, so a wait can be awaited with a timeout, and the steering queue is behind an async
//! lock because admitting steering persists it first (`persist` is the owner's write).

use crate::activity::{monotonic, AttemptProgress, Clock};
use std::future::Future;
use std::sync::Mutex;
use std::time::Duration;
use sylvia_base::{CodedError, CodedResult};
use tokio::sync::watch;

fn command_rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

#[derive(Debug, Default)]
struct Steering {
    queued: Vec<String>,
    closed: bool,
}

/// `ProviderControl`.
#[derive(Debug)]
pub struct ProviderControl {
    /// `progress`.
    pub progress: AttemptProgress,
    cancelled: watch::Sender<bool>,
    /// `_cancel_lock` and `_cancel_count`.
    cancel_count: Mutex<i64>,
    steering: tokio::sync::Mutex<Steering>,
    steering_ready: watch::Sender<bool>,
    status_hint: Mutex<Option<String>>,
}

impl Default for ProviderControl {
    fn default() -> Self {
        Self::new()
    }
}

/// Wait until `event` is set, for at most `timeout`; whether it is set (`Event.wait`).
async fn wait_set(event: &watch::Sender<bool>, timeout: Option<Duration>) -> bool {
    let mut receiver = event.subscribe();
    let set = async { receiver.wait_for(|set| *set).await.is_ok() };
    match timeout {
        None => set.await,
        Some(timeout) => tokio::time::timeout(timeout, set).await.unwrap_or_else(|_| *event.borrow()),
    }
}

impl ProviderControl {
    /// `ProviderControl()`.
    pub fn new() -> Self {
        Self::with_clock(monotonic())
    }

    /// A control whose activity progress reads `clock`.
    pub fn with_clock(clock: Clock) -> Self {
        Self {
            progress: AttemptProgress::new(clock),
            cancelled: watch::Sender::new(false),
            cancel_count: Mutex::new(0),
            steering: tokio::sync::Mutex::new(Steering::default()),
            steering_ready: watch::Sender::new(false),
            status_hint: Mutex::new(None),
        }
    }

    /// `set_status_hint(text)`: application progress only, never model-generated text.
    pub fn set_status_hint(&self, text: Option<&str>) -> CodedResult<()> {
        *self.status_hint.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = text.map(str::to_owned);
        let phase = if text.is_some_and(|text| !text.is_empty()) { "compacting" } else { "working" };
        self.progress.update(phase, None)
    }

    /// `status_hint`.
    pub fn status_hint(&self) -> Option<String> {
        self.status_hint.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
    }

    /// `cancelled`.
    pub fn cancelled(&self) -> bool {
        *self.cancelled.borrow()
    }

    /// `cancellation_count`.
    pub fn cancellation_count(&self) -> i64 {
        *self.cancel_count.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// `admit(start)`: one non-blocking provider admission, linearised against cancellation.
    pub fn admit<R>(&self, start: impl FnOnce() -> R) -> CodedResult<R> {
        let _count = self.cancel_count.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if self.cancelled() {
            return Err(command_rejected("Provider work was cancelled before admission"));
        }
        Ok(start())
    }

    /// `cancel()`.
    pub fn cancel(&self) -> CodedResult<()> {
        let mut count = self.cancel_count.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if self.cancelled() {
            return Ok(());
        }
        self.cancelled.send_replace(true);
        *count += 1;
        self.progress.update("stopping", None)
    }

    /// `wait_cancelled(timeout=None)`.
    pub async fn wait_cancelled(&self, timeout: Option<Duration>) -> bool {
        wait_set(&self.cancelled, timeout).await
    }

    /// `add_steering(text)`.
    pub async fn add_steering(&self, text: &str) -> CodedResult<()> {
        self.admit_steering(text, || async { Ok(()) }).await
    }

    /// `admit_steering(text, persist)`: persist and queue steering atomically while the
    /// provider still accepts it.
    pub async fn admit_steering<F, Fut>(&self, text: &str, persist: F) -> CodedResult<()>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = CodedResult<()>>,
    {
        let mut steering = self.steering.lock().await;
        if steering.closed {
            return Err(command_rejected("Agent session provider work no longer accepts steering"));
        }
        persist().await?;
        steering.queued.push(text.to_owned());
        self.steering_ready.send_replace(true);
        Ok(())
    }

    /// `take_steering()`.
    pub async fn take_steering(&self) -> Vec<String> {
        let mut steering = self.steering.lock().await;
        let result = std::mem::take(&mut steering.queued);
        self.steering_ready.send_replace(false);
        result
    }

    /// `wait_for_steering(timeout=None)`.
    pub async fn wait_for_steering(&self, timeout: Option<Duration>) -> Vec<String> {
        if !wait_set(&self.steering_ready, timeout).await {
            return Vec::new();
        }
        self.take_steering().await
    }

    /// `take_steering_or_close()`: queued steering, or close the live steering window.
    pub async fn take_steering_or_close(&self) -> Vec<String> {
        let mut steering = self.steering.lock().await;
        if !steering.queued.is_empty() {
            let result = std::mem::take(&mut steering.queued);
            self.steering_ready.send_replace(false);
            return result;
        }
        steering.closed = true;
        self.steering_ready.send_replace(false);
        Vec::new()
    }
}
