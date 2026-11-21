//! The update status window as a real Windows task dialog. It opens a window on the desktop,
//! so it is ignored by default; run it on purpose with
//!
//!     cargo test -p sylvia-owner --test progress_dialog -- --ignored
//!
//! The test binaries carry a common-controls 6 manifest (`build.rs`, test targets only), as
//! the packaged helper executable will.

#[cfg(windows)]
#[test]
#[ignore = "opens a real dialog on the desktop"]
fn the_status_dialog_opens_shows_each_stage_and_closes_itself() {
    use sylvia_owner::desktop_update::progress::{phase, start_progress, Progress};
    let mut window = start_progress().expect("the task dialog opens with common controls 6");
    for stage in phase::ALL {
        window.stage(stage).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(400));
    }
    // A finished dialog clicks its own Close button.
    window.close();
    assert_eq!(window.stage(phase::WAITING).unwrap_err().code, "progress_lost");
}
