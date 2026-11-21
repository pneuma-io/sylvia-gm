//! Warnings the Python modules write with `logging`.
//!
//! The Python owner configures no logging handler, so a `WARNING` record reaches stderr through
//! `logging.lastResort` as the bare message. This keeps that one observable effect.

/// `logger.warning(message)` with no handler configured.
pub fn warning(message: &str) {
    eprintln!("{message}");
}
