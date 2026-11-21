//! Store failures, classified the way the Python store classifies its exceptions.
//!
//! Python raises `SchemaIdentityError` (code `schema_identity_mismatch`), `CommandRejected` and
//! friends with a code, lets `sqlite3.Error` escape from ordinary operations, and turns other
//! exceptions into messages at a few boundaries (startup migration above all). [`StoreError`]
//! keeps those three kinds apart so each boundary can make the same choice Python makes.

use sylvia_base::CodedError;

/// `SchemaIdentityError.code`.
pub const SCHEMA_IDENTITY_MISMATCH: &str = "schema_identity_mismatch";

/// A `SchemaIdentityError` with Python's message.
pub fn schema_identity(message: &str) -> StoreError {
    StoreError::Coded(
        CodedError::new(SCHEMA_IDENTITY_MISMATCH, message).with_error_type("SchemaIdentityError"),
    )
}

/// One failure inside the store.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    /// A coded Python exception (`SchemaIdentityError`, `CommandRejected`, `OwnerConflict`, ...).
    #[error("{0}")]
    Coded(CodedError),
    /// A `sqlite3.Error` from the database layer.
    #[error("{0}")]
    Sqlite(#[from] rusqlite::Error),
    /// Any other Python exception (`ValueError`, `OSError`, `RuntimeError`), with its text.
    #[error("{0}")]
    Other(String),
    /// Behaviour the port has not reached yet. Never a Python outcome; it names the pull
    /// request expected to replace it, so a database that needs it is refused rather than
    /// migrated differently.
    #[error("not yet ported: {0}")]
    NotPorted(&'static str),
}

impl From<CodedError> for StoreError {
    fn from(error: CodedError) -> Self {
        Self::Coded(error)
    }
}

impl From<std::io::Error> for StoreError {
    fn from(error: std::io::Error) -> Self {
        Self::Other(error.to_string())
    }
}

impl StoreError {
    /// Whether this is a `SchemaIdentityError`.
    pub fn is_schema_identity(&self) -> bool {
        matches!(self, Self::Coded(error) if error.code == SCHEMA_IDENTITY_MISMATCH)
    }

    /// The error `SQLiteAuthorityStore._migrate` raises for this failure: a
    /// `SchemaIdentityError` passes through, a `sqlite3.Error` becomes "could not be verified"
    /// and any other exception becomes "stored integrity could not be verified".
    pub fn at_startup(self) -> CodedError {
        match self {
            Self::Coded(error) if error.code == SCHEMA_IDENTITY_MISMATCH => error,
            Self::Sqlite(_) => {
                CodedError::new(SCHEMA_IDENTITY_MISMATCH, "Authority schema identity could not be verified")
                    .with_error_type("SchemaIdentityError")
            }
            Self::NotPorted(what) => CodedError::new("not_ported", format!("not yet ported: {what}")),
            Self::Coded(_) | Self::Other(_) => {
                CodedError::new(SCHEMA_IDENTITY_MISMATCH, "Authority stored integrity could not be verified")
                    .with_error_type("SchemaIdentityError")
            }
        }
    }
}

/// Result alias for store operations.
pub type StoreResult<T> = Result<T, StoreError>;
