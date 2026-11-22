//! Compare sylvia_engine::exec's second batch of handlers with the frozen Python: dice and
//! rolls, damage, healing and conditions, saves, rerolls, hunger pools, table lookups, random
//! tables, iteration, sorting, context lists, shuffles and package choices.
//!
//! Regenerate with `tests/vectors/generate_execution_vectors.py` (needs the snapshot's Python).

mod exec_common;

#[test]
fn handlers_execute_as_python() {
    let vectors =
        serde_json::from_str(include_str!("vectors/execution_handlers_vectors.json")).expect("valid JSON");
    exec_common::check_all(&vectors, 2500);
}
