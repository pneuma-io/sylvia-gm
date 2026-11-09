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
