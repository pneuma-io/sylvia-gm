//! `src/sylvia_gm/runtime/_rng_queue.py`: the pre-rolled dice queue for simulation mode.
//!
//! The snapshot exports it from `sylvia_gm.runtime` but no product code calls it. It is ported
//! whole so the export exists; `from_dict` accepts what `to_dict` writes.

use crate::pyerror::{PyError, PyResult};
use crate::pyrandom::PyRandom;
use sylvia_base::{Json, JsonMap};

/// `DEFAULT_QUEUE_SIZE`.
pub const DEFAULT_QUEUE_SIZE: i64 = 12;

/// The die fields of an entry, in Python's order.
pub const FIELD_NAMES: [&str; 7] = ["d20", "d4", "d6", "d8", "d10", "d12", "d100"];
const SIDES: [i128; 7] = [20, 4, 6, 8, 10, 12, 100];

/// `RNGQueueEntry`: one value for every standard die, in [`FIELD_NAMES`] order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RngQueueEntry {
    /// d20, d4, d6, d8, d10, d12, d100.
    pub values: [i64; 7],
}

impl RngQueueEntry {
    /// `to_dict()` (`dataclasses.asdict`).
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        for (name, value) in FIELD_NAMES.iter().zip(self.values) {
            map.insert((*name).into(), value.into());
        }
        Json::Object(map)
    }
}

/// `RNGQueue`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RngQueue {
    /// Number of entries.
    pub size: i64,
    /// Seed, or `None` for OS entropy.
    pub seed: Option<i64>,
    /// The entries.
    pub entries: Vec<RngQueueEntry>,
    index: i64,
}

impl RngQueue {
    /// `RNGQueue(size, seed)`.
    pub fn new(size: i64, seed: Option<i64>) -> PyResult<Self> {
        if size < 1 {
            return Err(PyError::value(format!("Queue size must be at least 1, got {size}")));
        }
        let mut queue = Self { size, seed, entries: Vec::new(), index: 0 };
        queue.generate();
        Ok(queue)
    }

    /// `index`.
    pub fn index(&self) -> i64 {
        self.index
    }

    /// `remaining`.
    pub fn remaining(&self) -> i64 {
        self.size - self.index
    }

    /// `consume()`: the next entry; the index wraps at the end.
    pub fn consume(&mut self) -> RngQueueEntry {
        let entry = self.peek();
        self.index = (self.index + 1).rem_euclid(self.size);
        entry
    }

    /// `peek()`.
    pub fn peek(&self) -> RngQueueEntry {
        self.entries[usize::try_from(self.index).expect("index within the queue")]
    }

    /// `reset_index()`.
    pub fn reset_index(&mut self) {
        self.index = 0;
    }

    /// `regenerate(seed)`: a new seed replaces the old one; `None` keeps it.
    pub fn regenerate(&mut self, seed: Option<i64>) {
        if seed.is_some() {
            self.seed = seed;
        }
        self.generate();
    }

    /// `_generate()`: a private `random.Random(seed)` rolls every die once per entry.
    fn generate(&mut self) {
        // `random.Random()` with no seed draws from the OS; that stream cannot be compared.
        let mut rng = self.seed.map_or_else(PyRandom::from_entropy, PyRandom::from_i64);
        self.entries.clear();
        for _ in 0..self.size {
            let values = SIDES.map(|sides| {
                let value = rng.randint(1, sides).expect("a standard die has a range");
                i64::try_from(value).expect("die face fits in i64")
            });
            self.entries.push(RngQueueEntry { values });
        }
        self.index = 0;
    }

    /// `to_context_block()`: the `[RNG_QUEUE]` text given to the model.
    pub fn to_context_block(&self) -> String {
        let mut lines = vec!["[RNG_QUEUE]".to_owned()];
        lines
            .push(format!("seed={}", self.seed.map_or_else(|| "random".to_owned(), |seed| seed.to_string())));
        lines.push(format!("size={}", self.size));
        lines.push(format!("index={}", self.index));
        lines.push("---".to_owned());
        for (i, entry) in self.entries.iter().enumerate() {
            let marker = if i64::try_from(i) == Ok(self.index) { ">" } else { " " };
            let [d20, d4, d6, d8, d10, d12, d100] = entry.values;
            lines.push(format!(
                "{marker}[{i}] d20={d20} d4={d4} d6={d6} d8={d8} d10={d10} d12={d12} d100={d100}"
            ));
        }
        lines.push("[/RNG_QUEUE]".to_owned());
        lines.join("\n")
    }

    /// `to_dict()`.
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("seed".into(), self.seed.map_or(Json::Null, Json::from));
        map.insert("size".into(), self.size.into());
        map.insert("index".into(), self.index.into());
        map.insert("entries".into(), Json::Array(self.entries.iter().map(RngQueueEntry::to_dict).collect()));
        Json::Object(map)
    }

    /// `RNGQueue.from_dict(data)` for data `to_dict` wrote. Python raises `KeyError` or
    /// `TypeError` for malformed data; Rust returns `None`.
    pub fn from_dict(data: &Json) -> Option<Self> {
        let size = data.get("size")?.as_i64()?;
        let seed = match data.get("seed") {
            None | Some(Json::Null) => None,
            Some(seed) => Some(seed.as_i64()?),
        };
        let index = data.get("index")?.as_i64()?;
        let mut entries = Vec::new();
        for entry in data.get("entries")?.as_array()? {
            let object = entry.as_object()?;
            if object.len() != FIELD_NAMES.len() {
                return None;
            }
            let mut values = [0i64; 7];
            for (slot, name) in values.iter_mut().zip(FIELD_NAMES) {
                *slot = object.get(name)?.as_i64()?;
            }
            entries.push(RngQueueEntry { values });
        }
        Some(Self { size, seed, entries, index })
    }
}
