//! `runtime/_condition_evaluator.py`: the sandboxed evaluator, and the sandboxed functions of
//! `runtime/_expression_parser.py`.
//!
//! An evaluation reads the execution context (tool parameters, context variables, entity
//! snapshots, combat state, ruleset config and definitions) and gives a value or an error. The
//! error is the `ExpressionError` the evaluator raises itself, or a Python built-in exception
//! that escapes it (a `TypeError` from `[1] - [2]`, the budget's `RuntimeError`), because callers
//! such as `_resolve_value` catch only the former.

use super::collections as coll;
use super::compare::{py_order, Order};
use super::error::ExpressionError;
use super::ops::{self, PowError};
use super::parser::{function_whitelist, parse_expression, BinaryOp, Literal, Node, UnaryOp};
use super::text_cache::TextCache;
use super::value::{numeric, py_eq, Dict, Method, Numeric, Value};
use crate::pyerror::{int_range_error, PyError, PyErrorKind};
use std::cell::Cell;
use std::sync::Arc;

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "attribute_table.rs"]
mod attributes;

/// What an evaluation can raise.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum EvalError {
    /// `ExpressionError` or `FieldNotFoundError`.
    #[error("{0}")]
    Expression(ExpressionError),
    /// A Python built-in exception that escapes the evaluator.
    #[error("{0}")]
    Py(PyError),
}

impl EvalError {
    /// `type(error).__name__`.
    pub fn class_name(&self) -> &'static str {
        match self {
            Self::Expression(error) => error.class_name(),
            Self::Py(error) => error.kind.name(),
        }
    }

    /// `str(error)`.
    pub fn message(&self) -> &str {
        match self {
            Self::Expression(error) => &error.message,
            Self::Py(error) => &error.message,
        }
    }
}

impl From<ExpressionError> for EvalError {
    fn from(error: ExpressionError) -> Self {
        Self::Expression(error)
    }
}

impl From<PyError> for EvalError {
    fn from(error: PyError) -> Self {
        Self::Py(error)
    }
}

type EvalResult<T> = Result<T, EvalError>;

fn expression_error(message: impl Into<String>) -> EvalError {
    EvalError::Expression(ExpressionError::new(message))
}

/// `ExecutionBudget` from `runtime/_types.py`: one operation and call-depth bound for a whole
/// package action. Counters are cells so the evaluator can consume through a shared reference.
#[derive(Debug, Clone)]
pub struct ExecutionBudget {
    /// `max_operations`.
    pub max_operations: u64,
    /// `max_call_depth`.
    pub max_call_depth: u64,
    /// `operations`.
    pub operations: Cell<u64>,
    /// `call_depth`.
    pub call_depth: Cell<u64>,
    /// `private_random_sampled`.
    pub private_random_sampled: Cell<bool>,
    /// `preflight_only`.
    pub preflight_only: bool,
}

impl Default for ExecutionBudget {
    fn default() -> Self {
        Self {
            max_operations: 20_000,
            max_call_depth: 32,
            operations: Cell::new(0),
            call_depth: Cell::new(0),
            private_random_sampled: Cell::new(false),
            preflight_only: false,
        }
    }
}

impl ExecutionBudget {
    /// `consume_operation()`.
    pub fn consume_operation(&self) -> Result<(), PyError> {
        self.operations.set(self.operations.get() + 1);
        if self.operations.get() > self.max_operations {
            return Err(PyError::of(PyErrorKind::RuntimeError, "Package runtime operation budget exceeded"));
        }
        Ok(())
    }

    /// `enter_call()`: one level deeper, refused past `max_call_depth`.
    pub fn enter_call(&self) -> Result<(), PyError> {
        self.call_depth.set(self.call_depth.get() + 1);
        if self.call_depth.get() > self.max_call_depth {
            self.call_depth.set(self.call_depth.get() - 1);
            return Err(PyError::of(PyErrorKind::RuntimeError, "Package runtime call depth exceeded"));
        }
        Ok(())
    }

    /// `leave_call()`.
    pub fn leave_call(&self) -> Result<(), PyError> {
        if self.call_depth.get() < 1 {
            return Err(PyError::of(PyErrorKind::RuntimeError, "Package runtime call depth underflow"));
        }
        self.call_depth.set(self.call_depth.get() - 1);
        Ok(())
    }
}

/// The parts of `ExecutionContext` an expression reads.
#[derive(Debug, Clone, Copy)]
pub struct EvalContext<'a> {
    /// `ctx.params`.
    pub params: &'a Dict,
    /// `ctx.vars`.
    pub vars: &'a Dict,
    /// `ctx.entities`.
    pub entities: &'a Dict,
    /// `ctx.combat_state` (a dict).
    pub combat_state: &'a Value,
    /// `ctx.ruleset_config` (a dict).
    pub ruleset_config: &'a Value,
    /// `ctx.definitions` (a dict).
    pub definitions: &'a Value,
    /// `ctx.budget`.
    pub budget: Option<&'a ExecutionBudget>,
    /// Whether the dicts under the parameters are `initial_character_state._RequiredInputs`,
    /// whose `.get` raises `FieldNotFoundError` for a missing key. Only `_calculate` sets it.
    pub required_inputs: bool,
}

/// `_evaluation_expression(expression)`: Python's `lru_cache(maxsize=1024)` of successful parses.
/// An evaluation only reads its tree, so every caller can share one.
static EVALUATION_EXPRESSIONS: TextCache<Arc<Node>> = TextCache::new(1024);

/// `evaluate_expression(expr, context)`: the cached parse (`_evaluation_expression`), then
/// evaluate. A parse error is not kept, so it is raised again on every call, as in Python.
pub fn evaluate_expression(expr: &str, ctx: &EvalContext<'_>) -> EvalResult<Value> {
    let node = EVALUATION_EXPRESSIONS.get_or_try(expr, |text| parse_expression(text).map(Arc::new))?;
    evaluate(&node, ctx)
}

/// `evaluate_condition(expr, context)`.
pub fn evaluate_condition(expr: &str, ctx: &EvalContext<'_>) -> EvalResult<bool> {
    Ok(evaluate_expression(expr, ctx)?.truthy())
}

/// `evaluate(node, context)`.
pub fn evaluate(node: &Node, ctx: &EvalContext<'_>) -> EvalResult<Value> {
    match node {
        Node::Literal(literal) => literal_value(literal),
        Node::Field(segments) => field_access(segments, ctx),
        Node::Unary(op, operand) => unary(*op, operand, ctx),
        Node::Binary(op, left, right) => binary(*op, left, right, ctx),
        Node::Call(name, args) => call(name, args, ctx),
    }
}

fn literal_value(literal: &Literal) -> EvalResult<Value> {
    Ok(match literal {
        Literal::None => Value::None,
        Literal::Bool(flag) => Value::Bool(*flag),
        Literal::Int(int) => Value::Int(int.value.ok_or_else(int_range_error)?),
        Literal::Float(float) => Value::Float(*float),
        Literal::Str(text) => Value::str(text.as_str()),
    })
}

/// `_resolve_root(name, ctx)`.
pub fn resolve_root(name: &str, ctx: &EvalContext<'_>) -> Value {
    if let Some(param) = ctx.params.get(name) {
        if let Some(value) = ctx.vars.get(name) {
            return value.clone();
        }
        if let Value::Str(id) = param {
            if let Some(entity) = ctx.entities.get(id.as_ref()) {
                return entity.clone();
            }
        }
        return param.clone();
    }
    if let Some(value) = ctx.vars.get(name) {
        return value.clone();
    }
    if name == "combat" {
        return ctx.combat_state.clone();
    }
    if name == "combatants" || name == "party_members" {
        return Value::None;
    }
    if let Some(entity) = ctx.entities.get(name) {
        return entity.clone();
    }
    match name {
        "ruleset" => ctx.ruleset_config.clone(),
        "definitions" => ctx.definitions.clone(),
        _ => Value::None,
    }
}

/// `_traverse_field(value, field)` where a dict is `_RequiredInputs` when `required_inputs`:
/// a missing key raises instead of giving `None`. A present key holding null still gives `None`.
/// The one dict a creation calculation builds fresh, a `merge(...)` result, is plain in Python
/// and so returns `None` for its own missing keys: differences row 41 (merge results in
/// creation calculations).
pub(crate) fn traverse_required(value: &Value, field: &str, required_inputs: bool) -> EvalResult<Value> {
    if let (true, Value::Dict(map)) = (required_inputs, value) {
        if !map.contains_key(field) {
            return Err(EvalError::Expression(ExpressionError::field_not_found(
                "A required creation input is missing",
            )));
        }
    }
    Ok(traverse(value, field))
}

/// `_traverse_field(value, field)`.
pub(crate) fn traverse(value: &Value, field: &str) -> Value {
    match value {
        Value::None => Value::None,
        Value::Dict(map) => map.get(field).cloned().unwrap_or(Value::None),
        // `int(field)` of an identifier always fails, so a list or tuple gives None.
        Value::List(_) | Value::Tuple(_) => Value::None,
        Value::Object(object) => {
            if let Some((_, field_value)) = object.fields.iter().find(|(name, _)| *name == field) {
                field_value.clone()
            } else if object.methods.contains(&field) {
                Value::Method(Arc::new(Method { owner: value.clone(), name: field.to_owned() }))
            } else {
                // Dunder attributes are not modelled (`differences.md`).
                Value::None
            }
        }
        Value::Str(_) | Value::Int(_) | Value::Float(_) | Value::Bool(_) => {
            let type_name = value.type_name();
            match attributes::ATTRIBUTES.iter().find(|(owner, name, _)| *owner == type_name && *name == field)
            {
                Some((_, _, true)) => {
                    Value::Method(Arc::new(Method { owner: value.clone(), name: field.to_owned() }))
                }
                Some((_, name, false)) => scalar_attribute(value, name),
                None => Value::None,
            }
        }
        Value::Method(_) => Value::None,
    }
}

/// `x.real`, `x.imag`, `x.numerator`, `x.denominator` of an `int`, `bool` or `float`.
fn scalar_attribute(value: &Value, name: &str) -> Value {
    match (value, name) {
        (Value::Float(float), "real") => Value::Float(*float),
        (Value::Float(_), _) => Value::Float(0.0),
        (_, "imag") => Value::Int(0),
        (_, "denominator") => Value::Int(1),
        (Value::Bool(flag), _) => Value::Int(i64::from(*flag)),
        (other, _) => other.clone(),
    }
}

/// `_eval_field_access(node, ctx)`.
fn field_access(segments: &[(String, Option<Box<Node>>)], ctx: &EvalContext<'_>) -> EvalResult<Value> {
    let path = || segments.iter().map(|(name, _)| name.as_str()).collect::<Vec<_>>().join(".");
    let mut value = Value::None;
    for (index, (name, index_expr)) in segments.iter().enumerate() {
        value = if index == 0 {
            resolve_root(name, ctx)
        } else {
            traverse_required(&value, name, ctx.required_inputs)?
        };
        let Some(index_expr) = index_expr else { continue };
        let idx = evaluate(index_expr, ctx)?;
        let out_of_range = || {
            EvalError::Expression(ExpressionError::field_not_found(format!(
                "Index {} out of range for field '{}'",
                idx.repr(),
                path()
            )))
        };
        value = match &value {
            Value::List(items) | Value::Tuple(items) => {
                let position = sequence_index(&idx, items.len(), "Index must be integer, got", true)?;
                position.map_or_else(|| Err(out_of_range()), |p| Ok(items[p].clone()))?
            }
            Value::Str(text) => {
                let chars: Vec<char> = text.chars().collect();
                let position = sequence_index(&idx, chars.len(), "String index must be integer, got", false)?;
                position.map_or_else(|| Err(out_of_range()), |p| Ok(Value::str(chars[p].to_string())))?
            }
            Value::Dict(map) => {
                let key = match &idx {
                    Value::Str(key) => Some(key.to_string()),
                    Value::Int(_) => None,
                    other => {
                        return Err(expression_error(format!(
                            "Mapping index must be a string or integer, got {}",
                            other.type_name()
                        )))
                    }
                };
                match key.and_then(|key| map.get(&key).cloned()) {
                    Some(found) => found,
                    None => {
                        return Err(EvalError::Expression(ExpressionError::field_not_found(format!(
                            "Mapping key {} is unavailable for field '{}'",
                            idx.repr(),
                            path()
                        ))))
                    }
                }
            }
            other => {
                return Err(expression_error(format!(
                    "Cannot index into {} ({})",
                    other.type_name(),
                    other.repr()
                )))
            }
        };
    }
    Ok(value)
}

/// The position an index names in a sequence of `len` items (negative from the end), or
/// `None` when it is out of range (`IndexError`, which the caller turns into
/// `FieldNotFoundError`). A float index is truncated with `int()` first; a float beyond `i64`
/// cannot index anything, as in Python.
fn sequence_index(idx: &Value, len: usize, message: &str, with_repr: bool) -> EvalResult<Option<usize>> {
    let int = match idx {
        Value::Float(float) => match ops::float_to_int(*float) {
            Ok(int) => int,
            Err(error) if error.message == sylvia_base::pystr::INT_RANGE_MESSAGE => return Ok(None),
            Err(error) => return Err(error.into()),
        },
        Value::Int(int) => *int,
        Value::Bool(flag) => i64::from(*flag),
        other => {
            let message = if with_repr {
                format!("{message} {} ({})", other.type_name(), other.repr())
            } else {
                format!("{message} {}", other.type_name())
            };
            return Err(expression_error(message));
        }
    };
    let len = i64::try_from(len).expect("sequence length fits in i64");
    let position = if int < 0 { int + len } else { int };
    Ok((0..len).contains(&position).then(|| usize::try_from(position).expect("in range")))
}

fn unary(op: UnaryOp, operand: &Node, ctx: &EvalContext<'_>) -> EvalResult<Value> {
    let value = evaluate(operand, ctx)?;
    match op {
        UnaryOp::Not => Ok(Value::Bool(!value.truthy())),
        UnaryOp::Neg => match &value {
            Value::Bool(_) => {
                Err(expression_error(format!("Cannot negate boolean value ({})", value.repr())))
            }
            Value::Str(_) => Err(expression_error(format!("Cannot negate string value ({})", value.repr()))),
            Value::None => Err(expression_error("Cannot negate null")),
            other => Ok(ops::neg(other)?),
        },
    }
}

/// `_check_numeric_op(op, left, right)`.
fn check_numeric(symbol: &str, left: &Value, right: &Value) -> EvalResult<()> {
    let either = |test: fn(&Value) -> bool| test(left) || test(right);
    if either(|v| matches!(v, Value::Bool(_))) {
        return Err(expression_error(format!("Cannot use '{symbol}' with boolean values")));
    }
    if either(|v| matches!(v, Value::Str(_))) {
        return Err(expression_error(format!("Cannot use '{symbol}' with strings")));
    }
    if either(|v| matches!(v, Value::None)) {
        return Err(expression_error(format!("Cannot use '{symbol}' with null")));
    }
    Ok(())
}

fn is_zero(value: &Value) -> bool {
    match numeric(value) {
        Some(Numeric::Int(int)) => int == 0,
        Some(Numeric::Float(float)) => float == 0.0,
        None => false,
    }
}

fn binary(op: BinaryOp, left: &Node, right: &Node, ctx: &EvalContext<'_>) -> EvalResult<Value> {
    match op {
        BinaryOp::And => {
            let result = evaluate(left, ctx)?.truthy() && evaluate(right, ctx)?.truthy();
            return Ok(Value::Bool(result));
        }
        BinaryOp::Or => {
            let result = evaluate(left, ctx)?.truthy() || evaluate(right, ctx)?.truthy();
            return Ok(Value::Bool(result));
        }
        _ => {}
    }
    let left = evaluate(left, ctx)?;
    let right = evaluate(right, ctx)?;
    let symbol = op.symbol();
    match op {
        BinaryOp::Add => {
            if let (Value::Str(a), Value::Str(b)) = (&left, &right) {
                if a.chars().count() + b.chars().count() > 4096 {
                    return Err(expression_error("Combined text exceeds its bound"));
                }
                return Ok(Value::str(format!("{a}{b}")));
            }
            check_numeric(symbol, &left, &right)?;
            Ok(ops::add(&left, &right)?)
        }
        BinaryOp::Sub => {
            check_numeric(symbol, &left, &right)?;
            Ok(ops::sub(&left, &right)?)
        }
        BinaryOp::Mul => {
            check_numeric(symbol, &left, &right)?;
            Ok(ops::mul(&left, &right)?)
        }
        BinaryOp::FloorDiv => {
            check_numeric(symbol, &left, &right)?;
            if is_zero(&right) {
                return Err(expression_error("Division by zero"));
            }
            Ok(ops::floor_div_int(&left, &right)?)
        }
        BinaryOp::Mod => {
            check_numeric(symbol, &left, &right)?;
            if is_zero(&right) {
                return Err(expression_error("Modulo by zero"));
            }
            Ok(ops::modulo(&left, &right)?)
        }
        BinaryOp::Pow => {
            check_numeric(symbol, &left, &right)?;
            match ops::pow(&left, &right) {
                Ok(value) => Ok(value),
                Err(PowError::Overflow) => Err(expression_error(format!(
                    "Exponentiation overflow: {} ** {}",
                    left.to_str(),
                    right.to_str()
                ))),
                Err(PowError::Raised(error)) => Err(error.into()),
            }
        }
        BinaryOp::Eq | BinaryOp::Ne | BinaryOp::Ge | BinaryOp::Le | BinaryOp::Gt | BinaryOp::Lt => {
            comparison(op, &left, &right).map(Value::Bool)
        }
        BinaryOp::In => membership(&left, &right).map(Value::Bool),
        BinaryOp::NotIn => membership(&left, &right).map(|found| Value::Bool(!found)),
        BinaryOp::Contains => contains(&left, &right).map(Value::Bool),
        BinaryOp::And | BinaryOp::Or => unreachable!("handled above"),
    }
}

/// `float(value)` for the comparison operators: numbers only.
#[allow(clippy::cast_precision_loss)]
fn as_float(value: &Value) -> Option<f64> {
    match numeric(value)? {
        Numeric::Int(int) => Some(int as f64),
        Numeric::Float(float) => Some(float),
    }
}

/// `_eval_comparison(op, left, right)`.
fn comparison(op: BinaryOp, left: &Value, right: &Value) -> EvalResult<bool> {
    let symbol = op.symbol();
    match (left, right) {
        (Value::None, Value::None) => return Ok(op == BinaryOp::Eq),
        (Value::None, _) | (_, Value::None) => return Ok(op == BinaryOp::Ne),
        _ => {}
    }
    if matches!(left, Value::Bool(_)) || matches!(right, Value::Bool(_)) {
        if let (Value::Bool(a), Value::Bool(b)) = (left, right) {
            match op {
                BinaryOp::Eq => return Ok(a == b),
                BinaryOp::Ne => return Ok(a != b),
                _ => {}
            }
        }
        let other = if matches!(left, Value::Bool(_)) { right } else { left };
        return Err(expression_error(format!("Cannot compare bool with {}", other.type_name())));
    }
    if matches!(left, Value::Str(_)) || matches!(right, Value::Str(_)) {
        let (Value::Str(a), Value::Str(b)) = (left, right) else {
            return Err(expression_error(format!(
                "Cannot compare {} with {}",
                left.type_name(),
                right.type_name()
            )));
        };
        return match op {
            BinaryOp::Eq => Ok(a == b),
            BinaryOp::Ne => Ok(a != b),
            _ => Err(expression_error(format!("Cannot use '{symbol}' with strings"))),
        };
    }
    let (Some(a), Some(b)) = (as_float(left), as_float(right)) else {
        return Err(expression_error(format!(
            "Cannot compare {} ({}) with {} ({})",
            left.type_name(),
            left.repr(),
            right.type_name(),
            right.repr()
        )));
    };
    Ok(match op {
        BinaryOp::Eq => a == b,
        BinaryOp::Ne => a != b,
        BinaryOp::Ge => a >= b,
        BinaryOp::Le => a <= b,
        BinaryOp::Gt => a > b,
        BinaryOp::Lt => a < b,
        _ => false,
    })
}

/// The `TypeError` Python raises when hashing a value for a dict lookup, if any.
fn unhashable(value: &Value) -> Option<PyError> {
    match value {
        Value::List(_) | Value::Dict(_) => {
            Some(PyError::type_error(format!("unhashable type: '{}'", value.type_name())))
        }
        Value::Tuple(items) => items.iter().find_map(unhashable),
        Value::Object(object) => object.fields.iter().find_map(|(_, field)| unhashable(field)),
        _ => None,
    }
}

/// `left in collection`, for a list, tuple or dict on the right.
fn in_collection(left: &Value, collection: &Value) -> EvalResult<Option<bool>> {
    Ok(match collection {
        Value::List(items) | Value::Tuple(items) => Some(items.iter().any(|item| py_eq(item, left))),
        Value::Dict(map) => {
            if let Some(error) = unhashable(left) {
                return Err(error.into());
            }
            Some(matches!(left, Value::Str(key) if map.contains_key(key.as_ref())))
        }
        _ => None,
    })
}

/// `_eval_membership(left, right)`.
fn membership(left: &Value, right: &Value) -> EvalResult<bool> {
    if matches!(right, Value::None) {
        return Ok(false);
    }
    if let (Value::Str(needle), Value::Str(haystack)) = (left, right) {
        return Ok(haystack.contains(needle.as_ref()));
    }
    in_collection(left, right)?.ok_or_else(|| {
        expression_error(format!("Right operand of 'in' must be a collection, got {}", right.type_name()))
    })
}

/// `_eval_contains(left, right)`.
fn contains(left: &Value, right: &Value) -> EvalResult<bool> {
    if matches!(left, Value::None) {
        return Ok(false);
    }
    if let (Value::Str(haystack), Value::Str(needle)) = (left, right) {
        return Ok(haystack.contains(needle.as_ref()));
    }
    in_collection(right, left)?.ok_or_else(|| {
        expression_error(format!(
            "Left operand of 'contains' must be a string or collection, got {}",
            left.type_name()
        ))
    })
}

/// The Python signature of a sandboxed function, for its arity `TypeError`s.
struct Signature {
    /// The name Python reports (`_collection_length` for `len`).
    reported: &'static str,
    parameters: &'static [&'static str],
    /// Whether the evaluator passes `consume=` as a keyword argument.
    keyword_only: bool,
}

fn signature(name: &str) -> Option<Signature> {
    let (reported, parameters, keyword_only): (&'static str, &'static [&'static str], bool) = match name {
        "len" => ("_collection_length", &["value"], false),
        "merge" => ("_merge_objects", &["original", "changes"], false),
        "indices" => ("indices", &["stop"], false),
        "portion" => ("portion", &["value", "start", "stop"], false),
        "joined" => ("joined", &["first", "second"], false),
        "replaced" => ("replaced", &["value", "index", "item"], false),
        "without" => ("without", &["value", "removed"], false),
        "occurrences" => ("occurrences", &["value", "item"], false),
        "text" => ("text", &["value"], false),
        "total" => ("total", &["value"], false),
        "one" => ("one", &["value"], false),
        "ordered" => ("ordered", &["value"], false),
        "partitions" => ("partitions", &["items", "patterns", "sizes"], true),
        "completions" => ("completions", &["items", "patterns", "sizes", "universe"], true),
        _ => return None,
    };
    Some(Signature { reported, parameters, keyword_only })
}

/// CPython's `TypeError` for calling a Python function with the wrong number of positional
/// arguments.
fn arity_error(signature: &Signature, given: usize) -> Option<PyError> {
    let expected = signature.parameters.len();
    let plural = |n: usize| if n == 1 { "" } else { "s" };
    if given > expected {
        let given_text = if signature.keyword_only {
            format!("{given} positional argument{} (and 1 keyword-only argument) were given", plural(given))
        } else {
            format!("{given} {} given", if given == 1 { "was" } else { "were" })
        };
        return Some(PyError::type_error(format!(
            "{}() takes {expected} positional argument{} but {given_text}",
            signature.reported,
            plural(expected)
        )));
    }
    if given < expected {
        let missing: Vec<String> =
            signature.parameters[given..].iter().map(|name| format!("'{name}'")).collect();
        // CPython: "'a'", "'a' and 'b'", "'a', 'b', and 'c'".
        let names = match missing.as_slice() {
            [only] => only.clone(),
            [first, second] => format!("{first} and {second}"),
            [rest @ .., last] => format!("{}, and {last}", rest.join(", ")),
            [] => unreachable!("some are missing"),
        };
        return Some(PyError::type_error(format!(
            "{}() missing {} required positional argument{}: {names}",
            signature.reported,
            missing.len(),
            plural(missing.len())
        )));
    }
    None
}

/// `_eval_function(node, ctx)`.
fn call(name: &str, args: &[Node], ctx: &EvalContext<'_>) -> EvalResult<Value> {
    if !function_whitelist().contains(&name) {
        return Err(expression_error(format!(
            "Function '{name}' is not available. Available: {}.",
            function_whitelist().join(", ")
        )));
    }
    let values = args.iter().map(|arg| evaluate(arg, ctx)).collect::<EvalResult<Vec<_>>>()?;
    if matches!(name, "max" | "min" | "abs" | "floor" | "ceil") {
        for (index, value) in values.iter().enumerate() {
            let position = index + 1;
            match value {
                Value::None => {
                    return Err(expression_error(format!("Argument {position} to {name}() is null")))
                }
                Value::Str(_) => {
                    return Err(expression_error(format!(
                        "Argument {position} to {name}() is a string, expected number"
                    )))
                }
                Value::Bool(_) => {
                    return Err(expression_error(format!(
                        "Argument {position} to {name}() is a boolean, expected number"
                    )))
                }
                _ => {}
            }
        }
    }
    match apply(name, &values, ctx.budget) {
        Ok(value) => Ok(value),
        // The budget's RuntimeError passes through, as in Python. So does the 64-bit refusal,
        // which is not Python behaviour and so is not dressed as a Python call error.
        Err(error) if error.kind == PyErrorKind::RuntimeError => Err(error.into()),
        Err(error) if error.message == sylvia_base::pystr::INT_RANGE_MESSAGE => Err(error.into()),
        Err(error) => Err(expression_error(format!("Error calling {name}(): {}", error.message))),
    }
}

fn apply(name: &str, args: &[Value], budget: Option<&ExecutionBudget>) -> Result<Value, PyError> {
    match name {
        "max" => extreme(args, Order::Gt, "max"),
        "min" => extreme(args, Order::Lt, "min"),
        "abs" => {
            let [value] = args else {
                return Err(PyError::type_error(format!(
                    "abs() takes exactly one argument ({} given)",
                    args.len()
                )));
            };
            match value {
                Value::Int(int) => int.checked_abs().map(Value::Int).ok_or_else(int_range_error),
                Value::Float(float) => Ok(Value::Float(float.abs())),
                other => {
                    Err(PyError::type_error(format!("bad operand type for abs(): '{}'", other.type_name())))
                }
            }
        }
        "floor" | "ceil" => {
            let [value] = args else {
                return Err(PyError::type_error(format!(
                    "math.{name}() takes exactly one argument ({} given)",
                    args.len()
                )));
            };
            match value {
                Value::Int(int) => Ok(Value::Int(*int)),
                Value::Float(float) => {
                    let rounded = if name == "floor" { float.floor() } else { float.ceil() };
                    ops::float_to_int(rounded).map(Value::Int)
                }
                other => Err(PyError::type_error(format!("must be real number, not {}", other.type_name()))),
            }
        }
        _ => {
            let signature = signature(name).expect("a whitelisted function");
            if let Some(error) = arity_error(&signature, args.len()) {
                return Err(error);
            }
            match (name, args) {
                ("len", [value]) => match value {
                    Value::List(items) | Value::Tuple(items) => Ok(Value::Int(len(items.len()))),
                    Value::Dict(map) => Ok(Value::Int(len(map.len()))),
                    Value::Str(text) => Ok(Value::Int(len(text.chars().count()))),
                    _ => Err(PyError::type_error("len() requires an array, object or string")),
                },
                ("merge", [Value::Dict(original), Value::Dict(changes)]) => {
                    let mut merged = (**original).clone();
                    for (key, value) in changes.iter() {
                        merged.insert(key.clone(), value.clone());
                    }
                    Ok(Value::dict(merged))
                }
                ("merge", _) => Err(PyError::value("merge requires two objects")),
                ("indices", [stop]) => coll::indices(stop),
                ("portion", [value, start, stop]) => coll::portion(value, start, stop),
                ("joined", [first, second]) => coll::joined(first, second),
                ("replaced", [value, index, item]) => coll::replaced(value, index, item),
                ("without", [value, removed]) => coll::without(value, removed),
                ("occurrences", [value, item]) => coll::occurrences(value, item),
                ("text", [value]) => coll::text(value),
                ("total", [value]) => coll::total(value),
                ("one", [value]) => Ok(coll::one(value)),
                ("ordered", [value]) => coll::ordered(value),
                ("partitions", [items, patterns, sizes]) => coll::partitions(items, patterns, sizes, budget),
                ("completions", [items, patterns, sizes, universe]) => {
                    coll::completions(items, patterns, sizes, universe, budget)
                }
                _ => unreachable!("arity checked"),
            }
        }
    }
}

fn len(count: usize) -> i64 {
    i64::try_from(count).expect("length fits in i64")
}

/// `max(*args)` / `min(*args)`: one argument is iterated, several are compared; the first of
/// equal extremes wins.
fn extreme(args: &[Value], order: Order, name: &str) -> Result<Value, PyError> {
    let items: Vec<Value> = match args {
        [] => {
            return Err(PyError::type_error(format!("{name} expected at least 1 argument, got 0")));
        }
        [single] => match single {
            Value::List(items) | Value::Tuple(items) => items.to_vec(),
            Value::Dict(map) => map.keys().map(|key| Value::str(key.as_str())).collect(),
            Value::Str(text) => text.chars().map(|c| Value::str(c.to_string())).collect(),
            other => {
                return Err(PyError::type_error(format!("'{}' object is not iterable", other.type_name())))
            }
        },
        many => many.to_vec(),
    };
    let mut iter = items.into_iter();
    let Some(mut best) = iter.next() else {
        return Err(PyError::value(format!("{name}() iterable argument is empty")));
    };
    for item in iter {
        if py_order(order, &item, &best)? {
            best = item;
        }
    }
    Ok(best)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn eval_with(vars: Dict, expr: &str) -> EvalResult<Value> {
        let (empty, none) = (Dict::new(), Value::None);
        let ctx = EvalContext {
            params: &empty,
            vars: &vars,
            entities: &empty,
            combat_state: &none,
            ruleset_config: &none,
            definitions: &none,
            budget: None,
            required_inputs: false,
        };
        evaluate_expression(expr, &ctx)
    }

    #[test]
    fn nan_membership_compares_by_value() {
        // Python's `in` would find the very same NaN object by identity (differences.md).
        let mut vars = Dict::new();
        vars.insert("_nan".into(), Value::Float(f64::NAN));
        vars.insert("_l".into(), Value::list(vec![Value::Float(f64::NAN)]));
        assert_eq!(eval_with(vars, "_nan in _l"), Ok(Value::Bool(false)));
    }

    #[test]
    fn huge_powers_and_literals_are_refused() {
        let mut vars = Dict::new();
        vars.insert("_max".into(), Value::Int(i64::MAX));
        let refused = |result: EvalResult<Value>| matches!(result, Err(EvalError::Py(error)) if error.message == sylvia_base::pystr::INT_RANGE_MESSAGE);
        assert!(refused(eval_with(vars.clone(), "7 ** _max")));
        assert!(refused(eval_with(vars.clone(), "99999999999999999999 > 1")));
        vars.insert("_pair".into(), Value::list(vec![Value::Int(i64::MAX), Value::Int(1)]));
        assert!(refused(eval_with(vars, "total(_pair)")));
    }
}
