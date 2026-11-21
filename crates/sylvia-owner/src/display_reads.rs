//! Bounded workers for expensive, cancellable desktop projections: `interfaces/_display_reads.py`.
//!
//! Python runs display reads on a two-thread pool. A read still queued when the desktop
//! cancels its load identity is answered `display_load_cancelled` at once, before the
//! cancel request's own reply; a read already running finishes normally. A read whose load
//! identity was cancelled earlier is answered `display_load_cancelled` when its turn comes.
//! Two worker tasks taking jobs from one queue keep Python's first-in, first-out order.

use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use sylvia_base::Json;
use tokio::sync::{mpsc, Notify};

/// How many display reads run at once (`max_workers=2`).
const WORKERS: usize = 2;
/// How many cancelled load identities are remembered.
const CANCELLED_LIMIT: usize = 128;

/// The work of one read: produces the reply to write.
pub type ReadJob = Pin<Box<dyn Future<Output = Json> + Send>>;

struct Job {
    serial: u64,
    request_id: String,
    work: ReadJob,
}

struct Pending {
    serial: u64,
    request_id: String,
    load_id: Option<String>,
    started: bool,
}

struct State {
    pending: Vec<Pending>,
    /// Oldest first; a repeated cancel moves its identity to the end.
    cancelled: VecDeque<String>,
    next_serial: u64,
    closing: bool,
}

/// `DisplayReads(cancelled_reply)`.
pub struct DisplayReads {
    state: Arc<Mutex<State>>,
    queue: mpsc::UnboundedSender<Job>,
    cancelled_reply: Arc<dyn Fn(&str) + Send + Sync>,
    settled: Arc<Notify>,
}

impl DisplayReads {
    /// Start the workers. `reply` writes a finished read's reply; `cancelled_reply` writes the
    /// `display_load_cancelled` reply for a request id.
    pub fn new(
        reply: Arc<dyn Fn(Json) + Send + Sync>,
        cancelled_reply: Arc<dyn Fn(&str) + Send + Sync>,
    ) -> Self {
        let state = Arc::new(Mutex::new(State {
            pending: Vec::new(),
            cancelled: VecDeque::new(),
            next_serial: 0,
            closing: false,
        }));
        let (queue, jobs) = mpsc::unbounded_channel::<Job>();
        let jobs = Arc::new(tokio::sync::Mutex::new(jobs));
        let settled = Arc::new(Notify::new());
        for _ in 0..WORKERS {
            let (state, jobs, reply, cancelled_reply, settled) = (
                Arc::clone(&state),
                Arc::clone(&jobs),
                Arc::clone(&reply),
                Arc::clone(&cancelled_reply),
                Arc::clone(&settled),
            );
            tokio::spawn(async move {
                loop {
                    let job = { jobs.lock().await.recv().await };
                    let Some(job) = job else { return };
                    run_job(job, &state, &*reply, &*cancelled_reply).await;
                    settled.notify_waiters();
                }
            });
        }
        Self { state, queue, cancelled_reply, settled }
    }

    /// `submit(request_id, load_id, execute)`.
    pub fn submit(&self, request_id: String, load_id: Option<String>, work: ReadJob) {
        let serial = {
            let mut state = self.state.lock().expect("display read lock");
            let serial = state.next_serial;
            state.next_serial += 1;
            state.pending.push(Pending { serial, request_id: request_id.clone(), load_id, started: false });
            serial
        };
        let _ = self.queue.send(Job { serial, request_id, work });
    }

    /// `cancel(load_id)`: remember the identity and answer every read of it still queued.
    pub fn cancel(&self, load_id: &str) {
        let queued: Vec<String> = {
            let mut state = self.state.lock().expect("display read lock");
            state.cancelled.retain(|identity| identity != load_id);
            state.cancelled.push_back(load_id.to_owned());
            while state.cancelled.len() > CANCELLED_LIMIT {
                state.cancelled.pop_front();
            }
            let (queued, kept): (Vec<Pending>, Vec<Pending>) = std::mem::take(&mut state.pending)
                .into_iter()
                .partition(|pending| !pending.started && pending.load_id.as_deref() == Some(load_id));
            state.pending = kept;
            queued.into_iter().map(|pending| pending.request_id).collect()
        };
        // Python's future callbacks answer the cancelled reads during cancel(), so these
        // replies come before the cancel request's own. The workers skip those jobs.
        for request_id in queued {
            (self.cancelled_reply)(&request_id);
        }
    }

    /// `idle()`: no read is queued or running.
    pub fn idle(&self) -> bool {
        self.state.lock().expect("display read lock").pending.is_empty()
    }

    /// `close()`: drop queued reads and wait for running ones.
    pub async fn close(&self) {
        {
            let mut state = self.state.lock().expect("display read lock");
            state.closing = true;
            state.pending.retain(|pending| pending.started);
        }
        loop {
            let notified = self.settled.notified();
            if self.idle() {
                return;
            }
            notified.await;
        }
    }
}

async fn run_job(
    job: Job,
    state: &Mutex<State>,
    reply: &(dyn Fn(Json) + Send + Sync),
    cancelled_reply: &(dyn Fn(&str) + Send + Sync),
) {
    let cancelled = {
        let mut state = state.lock().expect("display read lock");
        let closing = state.closing;
        let Some(index) = state.pending.iter().position(|pending| pending.serial == job.serial) else {
            return;
        };
        if closing {
            state.pending.remove(index);
            return;
        }
        state.pending[index].started = true;
        let load_id = state.pending[index].load_id.clone();
        load_id.is_some_and(|load_id| state.cancelled.contains(&load_id))
    };
    if cancelled {
        cancelled_reply(&job.request_id);
    } else {
        reply(job.work.await);
    }
    state.lock().expect("display read lock").pending.retain(|pending| pending.serial != job.serial);
}
