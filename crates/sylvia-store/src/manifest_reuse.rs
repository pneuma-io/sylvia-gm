//! `sqlite_packages._manifest_from_json`'s `functools.lru_cache(maxsize=64)`: an exact published
//! manifest's parse, reused by its exact canonical text.
//!
//! A manifest is immutable for its text, so an earlier parse is the value a fresh parse would
//! give; callers still read the current database rows (status, identity) first, as in Python. As
//! with `lru_cache`, at most [`MAXIMUM_MANIFESTS`] are kept, the least recently used is evicted
//! first, and a parse that fails is never kept. There is one cache per process, as Python's is
//! module-level. It changes only how long a read takes, never what it answers: without it every
//! snapshot parsed SRD Lite's whole manifest (about 700 KB) once per lock entry.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use sylvia_domain::package_library::PackageManifest;

/// `lru_cache(maxsize=64)`.
pub const MAXIMUM_MANIFESTS: usize = 64;

/// Parses kept by their exact text, in least recently used order.
pub struct ManifestReuse<T> {
    maximum: usize,
    state: Mutex<State<T>>,
}

struct State<T> {
    values: HashMap<String, (Arc<T>, u64)>,
    /// Last use to text: the first entry is the least recently used.
    order: BTreeMap<u64, String>,
    clock: u64,
}

impl<T> Default for State<T> {
    fn default() -> Self {
        Self { values: HashMap::new(), order: BTreeMap::new(), clock: 0 }
    }
}

impl<T> ManifestReuse<T> {
    pub fn new(maximum: usize) -> Self {
        Self { maximum, state: Mutex::new(State::default()) }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State<T>> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The kept parse of this exact text, moved to most recently used, or `parse` run on it and
    /// the result kept when it succeeds.
    pub fn get_or_parse<E>(&self, text: &str, parse: impl FnOnce(&str) -> Result<T, E>) -> Result<Arc<T>, E> {
        {
            let mut state = self.lock();
            state.clock += 1;
            let now = state.clock;
            if let Some((value, used)) = state.values.get_mut(text) {
                let (previous, value) = (*used, Arc::clone(value));
                *used = now;
                state.order.remove(&previous);
                state.order.insert(now, text.to_owned());
                return Ok(value);
            }
        }
        let parsed = Arc::new(parse(text)?);
        let mut state = self.lock();
        if !state.values.contains_key(text) && self.maximum > 0 {
            state.clock += 1;
            let now = state.clock;
            state.values.insert(text.to_owned(), (Arc::clone(&parsed), now));
            state.order.insert(now, text.to_owned());
            while state.values.len() > self.maximum {
                let Some((_, evicted)) = state.order.pop_first() else { break };
                state.values.remove(&evicted);
            }
        }
        Ok(parsed)
    }

    /// How many parses are kept (for tests).
    pub fn len(&self) -> usize {
        self.lock().values.len()
    }

    /// Whether nothing is kept.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// The process's one cache (`_manifest_from_json`'s).
pub fn manifests() -> &'static ManifestReuse<PackageManifest> {
    static CACHE: OnceLock<ManifestReuse<PackageManifest>> = OnceLock::new();
    CACHE.get_or_init(|| ManifestReuse::new(MAXIMUM_MANIFESTS))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Result<String, String> {
        Ok(text.to_uppercase())
    }

    fn refuse(_: &str) -> Result<String, String> {
        Err("parsed again".into())
    }

    #[test]
    fn a_hit_returns_the_kept_parse_without_parsing() {
        let cache = ManifestReuse::new(4);
        let first = cache.get_or_parse("one", parse).unwrap();
        let again = cache.get_or_parse("one", refuse).unwrap();
        assert!(Arc::ptr_eq(&first, &again));
        assert_eq!(*again, "ONE");
    }

    #[test]
    fn failures_are_never_kept() {
        let cache = ManifestReuse::<String>::new(4);
        assert!(cache.get_or_parse("one", refuse).is_err());
        assert!(cache.is_empty());
        assert!(cache.get_or_parse("one", parse).is_ok());
    }

    #[test]
    fn the_least_recently_used_parse_is_evicted_past_the_bound() {
        let cache = ManifestReuse::new(2);
        cache.get_or_parse("one", parse).unwrap();
        cache.get_or_parse("two", parse).unwrap();
        cache.get_or_parse("one", refuse).unwrap(); // "two" is now the least recently used
        cache.get_or_parse("three", parse).unwrap();
        assert_eq!(cache.len(), 2);
        assert!(cache.get_or_parse("two", refuse).is_err());
        assert!(cache.get_or_parse("one", refuse).is_ok());
    }

    #[test]
    fn the_process_cache_keeps_sixty_four() {
        assert_eq!(manifests().maximum, MAXIMUM_MANIFESTS);
        assert_eq!(MAXIMUM_MANIFESTS, 64);
    }
}
