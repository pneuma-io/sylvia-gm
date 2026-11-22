//! A pull-based stream written as straight-line async code, for providers that port a Python
//! async generator whose body interleaves `yield` with awaits and `try`/`finally`.
//!
//! [`generator`] turns `body(co)` into a [`Stream`]: the body runs only while the stream is
//! polled, `co.yield_item(item).await` hands one item to the consumer and resumes when the next
//! item is asked for, and dropping the stream drops the suspended body (Python's `aclose()`),
//! so its guards run.

use futures_util::Stream;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};

/// The body's handle for yielding items.
pub struct Co<T> {
    slot: Arc<Mutex<Option<T>>>,
}

impl<T> Co<T> {
    /// `yield item`: suspend until the consumer has taken it.
    pub async fn yield_item(&self, item: T) {
        *self.slot.lock().expect("generator slot") = Some(item);
        Suspend { suspended: false }.await;
    }
}

struct Suspend {
    suspended: bool,
}

impl Future for Suspend {
    type Output = ();

    fn poll(mut self: Pin<&mut Self>, _context: &mut Context<'_>) -> Poll<()> {
        if self.suspended {
            return Poll::Ready(());
        }
        // The stream returns the item straight away; its consumer polls again for the next.
        self.suspended = true;
        Poll::Pending
    }
}

struct Generator<'a, T> {
    slot: Arc<Mutex<Option<T>>>,
    body: Option<Pin<Box<dyn Future<Output = ()> + Send + 'a>>>,
}

impl<T> Stream for Generator<'_, T> {
    type Item = T;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<T>> {
        let Some(body) = self.body.as_mut() else { return Poll::Ready(None) };
        let finished = body.as_mut().poll(context).is_ready();
        if finished {
            self.body = None;
        }
        match self.slot.lock().expect("generator slot").take() {
            Some(item) => Poll::Ready(Some(item)),
            None if finished => Poll::Ready(None),
            None => Poll::Pending,
        }
    }
}

/// The stream of what `body` yields.
pub fn generator<'a, T, F>(body: impl FnOnce(Co<T>) -> F) -> Pin<Box<dyn Stream<Item = T> + Send + 'a>>
where
    T: Send + 'a,
    F: Future<Output = ()> + Send + 'a,
{
    let slot = Arc::new(Mutex::new(None));
    let future = body(Co { slot: Arc::clone(&slot) });
    Box::pin(Generator { slot, body: Some(Box::pin(future)) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;

    #[tokio::test]
    async fn yields_in_order_and_runs_only_when_polled() {
        let ran = Arc::new(Mutex::new(Vec::new()));
        let log = Arc::clone(&ran);
        let mut stream = generator(move |co| async move {
            log.lock().unwrap().push("start");
            co.yield_item(1).await;
            log.lock().unwrap().push("after 1");
            tokio::task::yield_now().await;
            co.yield_item(2).await;
            log.lock().unwrap().push("end");
        });
        assert!(ran.lock().unwrap().is_empty());
        assert_eq!(stream.next().await, Some(1));
        assert_eq!(*ran.lock().unwrap(), ["start"]);
        assert_eq!(stream.next().await, Some(2));
        assert_eq!(stream.next().await, None);
        assert_eq!(*ran.lock().unwrap(), ["start", "after 1", "end"]);
        assert_eq!(stream.next().await, None);
    }

    #[tokio::test]
    async fn dropping_the_stream_drops_the_body() {
        struct Guard(Arc<Mutex<bool>>);
        impl Drop for Guard {
            fn drop(&mut self) {
                *self.0.lock().unwrap() = true;
            }
        }
        let dropped = Arc::new(Mutex::new(false));
        let flag = Arc::clone(&dropped);
        let mut stream = generator(move |co| async move {
            let _guard = Guard(flag);
            co.yield_item(1).await;
            co.yield_item(2).await;
        });
        assert_eq!(stream.next().await, Some(1));
        drop(stream);
        assert!(*dropped.lock().unwrap());
    }
}
