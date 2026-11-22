//! What execution raises: the runtime's own exceptions (`runtime/_exceptions.py`) and any other
//! Python exception a handler lets through, kept as its class and `str(error)` because the
//! effect wrapper reports `Effect '{type}' failed: {str(error)}`.

use crate::expr::EvalError;
use crate::pyerror::PyError;
use sylvia_domain::authority::MechanicsChoiceRequest;

/// One exception in flight.
#[derive(Debug, Clone, PartialEq)]
pub enum ExecError {
    /// `PreConditionError(condition, message)`: the rules refusing, not an engine fault.
    PreCondition { condition: String, message: String },
    /// `MechanicsChoiceSuspended(request)`: typed control flow for one package choice.
    Suspended(Box<MechanicsChoiceRequest>),
    /// `EffectExecutionError(effect_type, message)`; `str()` is `Effect '{type}' failed: {message}`.
    Effect { effect_type: String, message: String },
    /// `ExpressionError` (and `FieldNotFoundError`), with `str(error)`.
    Expression { class: &'static str, message: String },
    /// Any other exception: its class (`ValueError`, `KeyError`, `PackageRuntimeError`, ...) and
    /// `str(error)`.
    Python { class: &'static str, message: String },
    /// Code the port has not reached yet, named. It is no Python exception: it passes through
    /// every handler and catch-all, as `Suspended` does, so "not ported yet" never becomes a
    /// gameplay failure. The owner answers `rust_port_pending`.
    PortPending(String),
}

impl ExecError {
    pub fn precondition(condition: impl Into<String>, message: impl Into<String>) -> Self {
        Self::PreCondition { condition: condition.into(), message: message.into() }
    }

    pub fn effect(effect_type: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Effect { effect_type: effect_type.into(), message: message.into() }
    }

    pub fn package_runtime(message: impl Into<String>) -> Self {
        Self::Python { class: "PackageRuntimeError", message: message.into() }
    }

    /// `KeyError(key)`: `str()` is the key's repr.
    pub fn key_error(key: &str) -> Self {
        Self::Python { class: "KeyError", message: crate::expr::value::Value::str(key).repr() }
    }

    pub fn type_error(message: impl Into<String>) -> Self {
        Self::Python { class: "TypeError", message: message.into() }
    }

    pub fn value_error(message: impl Into<String>) -> Self {
        Self::Python { class: "ValueError", message: message.into() }
    }

    /// Code not yet ported, named as `not yet ported: {what}`.
    pub fn port_pending(what: impl std::fmt::Display) -> Self {
        Self::PortPending(format!("not yet ported: {what}"))
    }

    /// Whether this leaves every execution layer untouched (a suspended choice or code not yet
    /// ported).
    pub fn passes_through(&self) -> bool {
        matches!(self, Self::Suspended(_) | Self::PortPending(_))
    }

    /// Python's class name.
    pub fn class_name(&self) -> &'static str {
        match self {
            Self::PreCondition { .. } => "PreConditionError",
            Self::Suspended(_) => "MechanicsChoiceSuspended",
            Self::Effect { .. } => "EffectExecutionError",
            Self::Expression { class, .. } | Self::Python { class, .. } => class,
            Self::PortPending(_) => "RustPortPending",
        }
    }

    /// `str(error)`.
    pub fn message(&self) -> String {
        match self {
            Self::PreCondition { message, .. } => message.clone(),
            Self::Suspended(_) => "Package mechanic is awaiting an authorized choice".into(),
            Self::Effect { effect_type, message } => format!("Effect '{effect_type}' failed: {message}"),
            Self::Expression { message, .. } | Self::Python { message, .. } => message.clone(),
            Self::PortPending(message) => message.clone(),
        }
    }

    /// `_execute_effect`'s wrapper: choices, preconditions and effect errors pass through;
    /// anything else becomes `EffectExecutionError(effect_type, str(error))`.
    pub fn in_effect(self, effect_type: &str) -> Self {
        match self {
            Self::PreCondition { .. } | Self::Suspended(_) | Self::Effect { .. } | Self::PortPending(_) => {
                self
            }
            other => Self::effect(effect_type, other.message()),
        }
    }
}

impl From<EvalError> for ExecError {
    fn from(error: EvalError) -> Self {
        match error {
            EvalError::Expression(error) => {
                Self::Expression { class: error.class_name(), message: error.message }
            }
            EvalError::Py(error) => error.into(),
        }
    }
}

impl From<PyError> for ExecError {
    fn from(error: PyError) -> Self {
        Self::Python { class: error.kind.name(), message: error.message }
    }
}

/// `int(value)` for a float: `OverflowError` for an infinity, `ValueError` for NaN, and the
/// integer-range refusal beyond `i64` (`differences.md` row 5).
pub fn float_to_int(value: f64) -> Result<i64, ExecError> {
    if value.is_nan() {
        return Err(ExecError::value_error("cannot convert float NaN to integer"));
    }
    if value.is_infinite() {
        return Err(ExecError::Python {
            class: "OverflowError",
            message: "cannot convert float infinity to integer".into(),
        });
    }
    let truncated = value.trunc();
    if (-9.223_372_036_854_776e18..9.223_372_036_854_776e18).contains(&truncated) {
        Ok(truncated as i64)
    } else {
        Err(ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE))
    }
}
