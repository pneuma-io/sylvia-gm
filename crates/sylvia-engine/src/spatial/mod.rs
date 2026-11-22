//! `spatial/`: deterministic, ruleset-neutral spatial state and its admission.
//!
//! Scenes are JSON, as in Python: an admitted scene keeps the caller's numbers as they were
//! written (an integer stays an integer, a float a float), and every bound is checked on their
//! `float(...)` values.

mod mutation;
mod scene;

pub use mutation::{mutate_spatial_scene, AdmittedSpatialMutation};
pub use scene::{admit_spatial_scene, AdmittedSpatialScene};

use sylvia_domain::error::DomainError;

/// `SPATIAL_ALGORITHM_VERSION`.
pub const SPATIAL_ALGORITHM_VERSION: &str = "sylvia.spatial.supercover.v2";

/// `SpatialValidationError`: a safe, classified rejection of spatial state, request or result.
/// A `ValueError` in Python; `str(error)` is "{message} at {path}".
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message} at {path}")]
pub struct SpatialValidationError {
    /// The message before " at {path}".
    pub message: &'static str,
    /// `error.code`.
    pub code: &'static str,
    /// `error.path`: `$` or a `$...` path into the payload.
    pub path: String,
}

/// What spatial admission raises: its own refusal, or an exception Python lets through.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum SpatialError {
    /// `SpatialValidationError`.
    #[error(transparent)]
    Validation(#[from] SpatialValidationError),
    /// An exception Python does not catch, such as the `AttributeError` a non-object entity
    /// footprint raises.
    #[error("{message}")]
    Uncaught {
        /// `type(error).__name__`.
        error_type: &'static str,
        /// `str(error)`.
        message: String,
    },
}

impl SpatialError {
    /// The refusal, when this is a `SpatialValidationError`.
    pub fn validation(&self) -> Option<&SpatialValidationError> {
        match self {
            Self::Validation(error) => Some(error),
            Self::Uncaught { .. } => None,
        }
    }
}

impl From<SpatialValidationError> for DomainError {
    /// As a `ValueError` subclass with no diagnostics.
    fn from(error: SpatialValidationError) -> Self {
        DomainError::Diagnosed {
            error_type: "SpatialValidationError",
            message: error.to_string(),
            diagnostics: Vec::new(),
        }
    }
}

impl From<SpatialError> for DomainError {
    fn from(error: SpatialError) -> Self {
        match error {
            SpatialError::Validation(error) => error.into(),
            SpatialError::Uncaught { error_type, message } => DomainError::Uncaught { error_type, message },
        }
    }
}
