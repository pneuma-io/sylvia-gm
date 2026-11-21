//! Lane 2 (game engine): the Rust home of `src/sylvia_gm/runtime`, `ruleset`, `spatial` and
//! `dice.py`.
//!
//! A mechanic takes an in-memory view of campaign state and returns proposed changes
//! (`MechanicOutcome` in Python). Nothing here touches SQLite or an async runtime. Rolls must
//! reproduce Python's seeded `random.Random` stream exactly; see `docs/rust-port/README.md`.

pub mod pyerror;
pub mod pyrandom;

pub use pyerror::{PyError, PyErrorKind, PyResult};
pub use pyrandom::PyRandom;
