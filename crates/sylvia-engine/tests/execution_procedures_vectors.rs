//! Compare sylvia_engine::exec's procedures and its time, flag, initiative, scene, deferral and
//! clock handlers with the frozen Python, run as `materialize_package_mechanic` runs them: with
//! the package's procedure executor and none of the runtime's managers.
//!
//! Regenerate with `tests/vectors/generate_execution_vectors.py` (needs the snapshot's Python).

mod exec_common;

#[test]
fn procedures_execute_as_python() {
    let vectors =
        serde_json::from_str(include_str!("vectors/execution_procedures_vectors.json")).expect("valid JSON");
    exec_common::check_all(&vectors, 2000);
}
