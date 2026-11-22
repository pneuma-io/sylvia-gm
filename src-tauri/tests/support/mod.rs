use std::time::{Duration, Instant};
use sylvia_gm::OwnerBridge;

// Acknowledgement starts cleanup; require the same actual collection the desktop observes.
pub fn await_clean_shutdown(bridge: &OwnerBridge) {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        match bridge.shutdown() {
            Ok(()) => return,
            Err(error) => {
                assert!(
                    error == "Sylvia GM owner is still running; close has not completed"
                        || error == "Sylvia GM owner reply pipe closed while its process is still running; close has not completed",
                    "shutdown was not admitted: {error}"
                );
                assert!(
                    Instant::now() < deadline,
                    "owner was not collected: {error}"
                );
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
}

#[allow(dead_code)] // release_sidecar.rs starts its own packaged owner
/// The owner these tests run against: `SYLVIA_GM_TEST_OWNER=rust` starts the Rust port's owner
/// from the workspace build (`cargo build -p sylvia-owner` at the repository root); unset or
/// `python`, the checkout's Python owner, as before.
pub fn spawn_test_owner(database: &std::path::Path) -> Result<OwnerBridge, String> {
    match std::env::var("SYLVIA_GM_TEST_OWNER").as_deref() {
        Ok("rust") => OwnerBridge::spawn_rust(database),
        Ok("python") | Err(std::env::VarError::NotPresent) => {
            let environment = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .expect("repository root")
                .join(".venv");
            let python = if cfg!(windows) {
                environment.join("Scripts").join("python.exe")
            } else {
                environment.join("bin").join("python")
            };
            OwnerBridge::spawn_python(&python, database)
        }
        Ok(other) => panic!("SYLVIA_GM_TEST_OWNER must be rust or python, not {other}"),
        Err(error) => panic!("SYLVIA_GM_TEST_OWNER is unreadable: {error}"),
    }
}
