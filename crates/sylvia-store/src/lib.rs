//! Lane 3 (storage and authority): the Rust home of `src/sylvia_gm/infrastructure` and
//! `application/authority.py`.
//!
//! Python's store and authority import each other; here they are modules of one crate with
//! explicit transaction operations. One dedicated thread owns the SQLite connection and takes
//! requests over a channel. See `docs/rust-port/README.md`.
