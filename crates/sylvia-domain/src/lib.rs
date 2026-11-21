//! Lane 1 (formats and content): the Rust home of `src/sylvia_gm/domain`, plus the file-based
//! formats in `state`, `campaign` and `planning`.
//!
//! Identities, package locks, closure hashes, archive admission and content schemas live here.
//! This crate does no I/O beyond reading bytes it is handed, opens no database and starts no
//! async runtime, so the owner, the Builder, content tools and SylviaBench can all call it.
//!
//! Hashed or stored JSON goes through `sylvia_base::pyjson`. See `docs/rust-port/README.md`.
