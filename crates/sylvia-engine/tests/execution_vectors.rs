//! Compare sylvia_engine::exec with the frozen Python's `execute_tool`: the execution core.
//!
//! Regenerate with `tests/vectors/generate_execution_vectors.py` (needs the snapshot's Python).

mod exec_common;

#[test]
fn tools_execute_as_python() {
    let vectors = serde_json::from_str(include_str!("vectors/execution_vectors.json")).expect("valid JSON");
    exec_common::check_all(&vectors, 2500);
}
