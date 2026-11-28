//! A bounded least-recently-used cache keyed by expression text: `functools.lru_cache(maxsize=N)`
//! over a one-argument function of a `str`.
//!
//! As `lru_cache` does, it keeps only results the function returned. An error is not stored, so
//! the next call computes it again. A hit makes its entry the most recent, and an insertion past
//! the bound drops the least recent. It is safe to share between threads, as `lru_cache` is.

use indexmap::IndexMap;
use std::sync::{Mutex, PoisonError};

/// One cache: at most `bound` entries.
pub struct TextCache<V> {
    bound: usize,
    /// Created on first use, so a cache can be a `static`.
    entries: Mutex<Option<IndexMap<Box<str>, V>>>,
}

impl<V: Clone> TextCache<V> {
    /// An empty cache of at most `bound` entries.
    pub const fn new(bound: usize) -> Self {
        Self { bound, entries: Mutex::new(None) }
    }

    /// The kept result for `text`, or `compute(text)`, kept if it succeeded.
    pub fn get_or_try<E>(&self, text: &str, compute: impl FnOnce(&str) -> Result<V, E>) -> Result<V, E> {
        {
            let mut guard = self.entries.lock().unwrap_or_else(PoisonError::into_inner);
            let entries = guard.get_or_insert_with(IndexMap::new);
            if let Some(index) = entries.get_index_of(text) {
                let last = entries.len() - 1;
                entries.move_index(index, last);
                return Ok(entries[last].clone());
            }
        }
        // Computed without the lock, as `lru_cache` calls the function outside its own.
        let value = compute(text)?;
        let mut guard = self.entries.lock().unwrap_or_else(PoisonError::into_inner);
        let entries = guard.get_or_insert_with(IndexMap::new);
        entries.shift_remove(text);
        entries.insert(text.into(), value.clone());
        while entries.len() > self.bound {
            entries.shift_remove_index(0);
        }
        Ok(value)
    }

    /// How many entries are kept.
    pub fn len(&self) -> usize {
        self.entries.lock().unwrap_or_else(PoisonError::into_inner).as_ref().map_or(0, IndexMap::len)
    }

    /// Whether nothing is kept.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Whether `text` has a kept entry, without making it the most recent.
    pub fn contains(&self, text: &str) -> bool {
        self.entries
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .is_some_and(|entries| entries.contains_key(text))
    }
}

#[cfg(test)]
mod tests {
    use super::TextCache;
    use std::cell::Cell;

    #[test]
    fn a_hit_computes_nothing() {
        let cache = TextCache::new(4);
        let calls = Cell::new(0);
        let compute = |text: &str| -> Result<usize, ()> {
            calls.set(calls.get() + 1);
            Ok(text.len())
        };
        assert_eq!(cache.get_or_try("abc", compute), Ok(3));
        assert_eq!(cache.get_or_try("abc", compute), Ok(3));
        assert_eq!(calls.get(), 1);
    }

    #[test]
    fn an_error_is_not_kept() {
        let cache: TextCache<usize> = TextCache::new(4);
        let calls = Cell::new(0);
        let fail = |_: &str| -> Result<usize, &str> {
            calls.set(calls.get() + 1);
            Err("bad")
        };
        assert_eq!(cache.get_or_try("x", fail), Err("bad"));
        assert_eq!(cache.get_or_try("x", fail), Err("bad"));
        assert_eq!(calls.get(), 2);
        assert!(cache.is_empty());
    }

    #[test]
    fn the_least_recent_entry_goes_first() {
        let cache = TextCache::new(2);
        let ok = |text: &str| -> Result<String, ()> { Ok(text.to_owned()) };
        cache.get_or_try("a", ok).unwrap();
        cache.get_or_try("b", ok).unwrap();
        // A hit on "a" makes "b" the least recent.
        cache.get_or_try("a", ok).unwrap();
        cache.get_or_try("c", ok).unwrap();
        assert!(cache.contains("a") && cache.contains("c") && !cache.contains("b"));
        assert_eq!(cache.len(), 2);
    }
}
