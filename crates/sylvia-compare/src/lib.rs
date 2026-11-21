//! The comparison tool's reusable parts, for crates that compare with Python below the owner.
//!
//! - [`dbdump`]: dump a SQLite database table by table in the recording format, so a store
//!   test can compare a Rust-written database with a Python-written one the same way the
//!   sessions do.
//! - [`diff`]: normalise two recordings under declared rules and list where they differ.
//!
//! The `sylvia-compare` executable (`main.rs`) builds on these.

pub mod dbdump;
pub mod diff;
