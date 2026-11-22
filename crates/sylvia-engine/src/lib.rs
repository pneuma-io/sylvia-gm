//! Lane 2 (game engine): the Rust home of `src/sylvia_gm/runtime`, `ruleset`, `spatial` and
//! `dice.py`.
//!
//! A mechanic takes an in-memory view of campaign state and returns proposed changes
//! (`MechanicOutcome` in Python). Nothing here touches SQLite or an async runtime. Rolls must
//! reproduce Python's seeded `random.Random` stream exactly; see `docs/rust-port/README.md`.

pub mod dice;
pub mod dice_expr;
pub mod exec;
pub mod expr;
pub mod pyerror;
pub mod pynum;
pub mod pyrandom;
pub mod rng_queue;
pub mod ruleset;
pub mod spatial;

pub use dice::Dice;
pub use pyerror::{PyError, PyErrorKind, PyResult};
pub use pyrandom::PyRandom;
