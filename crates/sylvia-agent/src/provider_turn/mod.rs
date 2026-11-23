//! The GM provider turn's shared state and its decision points.
//!
//! - [`state`]: `agent/_provider_turn_state.py`, the turn locals.
//! - [`decisions`]: `agent/_provider_turn_decisions.py`, the pure questions the phases ask.
//! - [`round`]: one provider round's preparation (`prepare_round_catalog`, `prepare_provider_round`).

pub mod decisions;
pub mod round;
pub mod state;
