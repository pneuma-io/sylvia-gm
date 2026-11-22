//! `application/package_runtime.py`'s `_validate_shape` over an execution value (the check
//! `create_entity` runs on a new entity's state). `sylvia_app::package_runtime` has the same
//! check over loaded YAML.

use super::error::ExecError;
use crate::expr::value::Value;

const MAX_RUNTIME_DEPTH: usize = 64;
const MAX_RUNTIME_NODES: usize = 501_000;
const MAX_OBJECT_MEMBERS: usize = 25_000;

/// `_validate_shape(value)`: depth, node count, member count and JSON values, in Python's
/// visiting order. Values are trees, so the recursive-alias case cannot arise.
pub fn validate_shape(value: &Value) -> Result<(), ExecError> {
    fn visit(item: &Value, depth: usize, nodes: &mut usize) -> Result<(), ExecError> {
        if depth > MAX_RUNTIME_DEPTH {
            return Err(ExecError::package_runtime("Package runtime nesting is too deep"));
        }
        *nodes += 1;
        if *nodes > MAX_RUNTIME_NODES {
            return Err(ExecError::package_runtime("Package runtime has too many nodes"));
        }
        match item {
            Value::None | Value::Str(_) | Value::Bool(_) | Value::Int(_) => Ok(()),
            Value::Dict(map) => {
                if map.len() > MAX_OBJECT_MEMBERS {
                    return Err(ExecError::package_runtime("Package runtime object is too large"));
                }
                map.values().try_for_each(|child| visit(child, depth + 1, nodes))
            }
            Value::List(items) | Value::Tuple(items) => {
                items.iter().try_for_each(|child| visit(child, depth + 1, nodes))
            }
            Value::Float(number) if number.is_finite() => Ok(()),
            Value::Float(_) => {
                Err(ExecError::package_runtime("Package runtime contains a non-finite number"))
            }
            Value::Object(_) | Value::Method(_) => {
                Err(ExecError::package_runtime("Package runtime contains a non-JSON value"))
            }
        }
    }
    visit(value, 1, &mut 0)
}
