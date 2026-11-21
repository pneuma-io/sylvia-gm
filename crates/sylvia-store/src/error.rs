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
    /// `_BackupFailure`, a `CommandRejected` with its phase, reason and cleanup codes.
    #[error("Database backup failed")]
    Backup(crate::backup::BackupFailure),
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

/// A lane 1 domain `ValueError` is a rejection the owner reports; a `TypeError` is a fault.
impl From<sylvia_domain::DomainError> for StoreError {
    fn from(error: sylvia_domain::DomainError) -> Self {
        match error.rejection() {
            Some(coded) => Self::Coded(coded),
            None => Self::Other(error.message().to_owned()),
        }
    }
}

impl From<std::io::Error> for StoreError {
    fn from(error: std::io::Error) -> Self {
        Self::Other(error.to_string())
    }
}

/// A Python `ValueError(message)`: the owner replies `command_rejected` with the message.
pub fn value_error(message: impl Into<String>) -> StoreError {
    StoreError::Coded(CodedError::rejected(message).with_error_type("ValueError"))
}

impl StoreError {
    /// The owner reply for this error, as Python's owner gives it: every exception it catches
    /// (`ValueError`, `CommandRejected`, the `AuthorityError` family) is a rejection with
    /// Python's code, message and class. `None` is a fault it does not catch (a
    /// `sqlite3.Error`, `TypeError`, `KeyError` and the like, and work not yet ported), which
    /// becomes the owner's internal-error reply.
    pub fn rejection(&self) -> Option<CodedError> {
        match self {
            Self::Coded(error) => Some(error.clone()),
            Self::Backup(failure) => Some(failure.to_coded()),
            Self::Sqlite(_) | Self::Other(_) | Self::NotPorted(_) => None,
        }
    }

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
            Self::Coded(_) | Self::Other(_) | Self::Backup(_) => {
                CodedError::new(SCHEMA_IDENTITY_MISMATCH, "Authority stored integrity could not be verified")
                    .with_error_type("SchemaIdentityError")
            }
        }
    }
}

/// Result alias for store operations.
pub type StoreResult<T> = Result<T, StoreError>;
