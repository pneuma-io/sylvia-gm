use super::{OwnerBridge, OWNER_CLOSE_NO_ACK, OWNER_CLOSE_PENDING, OWNER_CLOSE_PIPE_CLOSED};
use std::os::windows::io::{AsHandle, AsRawHandle, OwnedHandle};
use std::path::PathBuf;
use std::process::Command;
use std::sync::{mpsc, Arc, Mutex, Weak};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[link(name = "kernel32")]
extern "system" {
    fn WaitForSingleObject(handle: *mut std::ffi::c_void, timeout: u32) -> u32;
    fn GetExitCodeProcess(handle: *mut std::ffi::c_void, code: *mut u32) -> i32;
    fn TerminateProcess(handle: *mut std::ffi::c_void, code: u32) -> i32;
}

const OWNER: &str = r#"
import hashlib, hmac, json, os, pathlib, sys, time
mode, delay, code, marker, release = sys.argv[1:]
launch = json.loads(sys.stdin.readline())
response = hmac.new(bytes.fromhex(launch["secret_hex"]), b"sylvia-gm-f6-local-owner-handshake-v1", hashlib.sha256).hexdigest()
print(json.dumps({"type": "ready", "ok": True, "response_hex": response}), flush=True)
if mode not in ("eof", "eof_stuck"):
    request = json.loads(sys.stdin.readline())
    assert request["method"] == "shutdown"
    if mode in ("reject", "reject_once"):
        print(json.dumps({"id": request["id"], "ok": False, "error": {"code": "shutdown_refused", "message": "fixture remains alive"}}), flush=True)
        if mode == "reject_once":
            request = json.loads(sys.stdin.readline())
            assert request["method"] == "shutdown"
            print(json.dumps({"id": request["id"], "ok": True, "result": {"status": "closed"}}), flush=True)
    elif mode != "no_ack":
        print(json.dumps({"id": request["id"], "ok": True, "result": {"status": "closed"}}), flush=True)
if mode not in ("reject", "no_ack"):
    sys.stdout.flush()
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
if mode in ("reject", "no_ack", "ack_stuck", "eof_stuck"):
    while not pathlib.Path(release).exists(): time.sleep(.02)
else:
    time.sleep(float(delay))
pathlib.Path(marker).write_text("cleanup finished", encoding="utf-8")
raise SystemExit(int(code))
"#;

// This guard is only synthetic-test containment. A failed assertion cannot strand its child.
struct Containment {
    handle: OwnedHandle,
    release: PathBuf,
}
impl Drop for Containment {
    fn drop(&mut self) {
        let _ = std::fs::write(&self.release, "finish");
        unsafe {
            if WaitForSingleObject(self.handle.as_raw_handle(), 3000) != 0 {
                let _ = TerminateProcess(self.handle.as_raw_handle(), 130);
                assert_eq!(
                    WaitForSingleObject(self.handle.as_raw_handle(), 3000),
                    0,
                    "owned synthetic child not collected"
                );
            }
        }
    }
}

struct Fixture {
    bridge: Option<OwnerBridge>,
    containment: Containment,
    root: PathBuf,
    marker: PathBuf,
    preserved: PathBuf,
    job: Weak<super::dictation::ProcessJob>,
    child: Weak<Mutex<std::process::Child>>,
    code: Arc<Mutex<Option<i32>>>,
    notice: mpsc::Receiver<Result<i32, String>>,
}
impl Fixture {
    fn new(mode: &str, delay: &str, code: &str) -> Self {
        let python = std::env::var_os("SYLVIA_GM_TEST_PYTHON")
            .map(PathBuf::from)
            .expect("SYLVIA_GM_TEST_PYTHON must name the existing base interpreter, not a venv redirector retaining stdout");
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "sylvia-owner-bounded-{}-{nonce}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        let marker = root.join("cleanup-finished.txt");
        let release = root.join("release");
        let preserved = root.join("synthetic-data.txt");
        std::fs::write(&preserved, "keep this synthetic data").unwrap();
        let mut command = Command::new(python);
        command
            .args(["-c", OWNER, mode, delay, code])
            .arg(&marker)
            .arg(&release);
        let bridge =
            OwnerBridge::spawn_command(command, &root.join("unused.sqlite3"), None, None).unwrap();
        let handle = bridge
            .child
            .lock()
            .unwrap()
            .as_handle()
            .try_clone_to_owned()
            .unwrap();
        let job = Arc::downgrade(&bridge.job);
        let child = Arc::downgrade(&bridge.child);
        let saved = Arc::clone(&bridge.exit_code);
        let notice = bridge.exit_notice.lock().unwrap().take().unwrap();
        Self {
            bridge: Some(bridge),
            containment: Containment { handle, release },
            root,
            marker,
            preserved,
            job,
            child,
            code: saved,
            notice,
        }
    }

    fn bridge(&self) -> &OwnerBridge {
        self.bridge.as_ref().unwrap()
    }

    fn assert_live_and_preserved(&self) {
        assert_eq!(
            unsafe { WaitForSingleObject(self.containment.handle.as_raw_handle(), 0) },
            258
        );
        assert!(!self.job.upgrade().unwrap().is_empty().unwrap());
        assert!(
            self.child.upgrade().is_some(),
            "production reader must retain the child"
        );
        assert_eq!(
            std::fs::read_to_string(&self.preserved).unwrap(),
            "keep this synthetic data"
        );
    }

    fn await_live_eof(&self) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while !self
            .bridge()
            .closed
            .load(std::sync::atomic::Ordering::Acquire)
            && Instant::now() < deadline
        {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(self
            .bridge()
            .closed
            .load(std::sync::atomic::Ordering::Acquire));
        self.assert_live_and_preserved();
    }

    fn drop_bridge(&mut self) {
        let started = Instant::now();
        drop(self.bridge.take());
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "Drop must not perform blocking shutdown"
        );
    }

    fn finish(mut self, expected: i32) {
        self.drop_bridge();
        std::fs::write(&self.containment.release, "finish").unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(self.containment.handle.as_raw_handle(), 5000) },
            0
        );
        let mut code = u32::MAX;
        assert_ne!(
            unsafe { GetExitCodeProcess(self.containment.handle.as_raw_handle(), &mut code) },
            0
        );
        assert_eq!(code as i32, expected);
        assert_eq!(
            self.notice
                .recv_timeout(Duration::from_secs(3))
                .unwrap()
                .unwrap(),
            expected
        );
        assert_eq!(*self.code.lock().unwrap(), Some(expected));
        if let Some(job) = self.job.upgrade() {
            assert!(job.is_empty().unwrap());
        }
        assert_eq!(
            std::fs::read_to_string(&self.preserved).unwrap(),
            "keep this synthetic data"
        );
        // Remove only these known owned fixture files after actual process collection.
        for path in [&self.marker, &self.containment.release, &self.preserved] {
            std::fs::remove_file(path).unwrap();
        }
        std::fs::remove_dir(&self.root).unwrap();
        // Its process is collected; leave the guard's target absent without recreating data.
        self.containment.release = self.root.join("already-collected");
    }
}

// Pending is never credited as clean: require the real zero exit and empty owned job.
fn await_clean(bridge: &OwnerBridge) {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        match bridge.shutdown() {
            Ok(()) => return,
            Err(error)
                if (error == OWNER_CLOSE_PENDING || error == OWNER_CLOSE_PIPE_CLOSED)
                    && Instant::now() < deadline =>
            {
                std::thread::sleep(Duration::from_millis(20));
            }
            other => panic!("expected actual clean collection: {other:?}"),
        }
    }
}

#[test]
fn acknowledged_shutdown_waits_for_process_cleanup() {
    let fixture = Fixture::new("shutdown", "3.5", "0");
    await_clean(fixture.bridge());
    assert_eq!(
        std::fs::read_to_string(&fixture.marker).unwrap(),
        "cleanup finished"
    );
    assert_eq!(fixture.bridge().poll_owner_exit().unwrap(), Some(0));
    fixture
        .bridge()
        .shutdown()
        .expect("already-collected owner");
    fixture.finish(0);
}

#[test]
fn reply_pipe_eof_does_not_skip_reaping_the_owner() {
    let fixture = Fixture::new("eof", "1", "0");
    fixture.await_live_eof();
    await_clean(fixture.bridge());
    assert_eq!(fixture.bridge().poll_owner_exit().unwrap(), Some(0));
    fixture.finish(0);
}

#[test]
fn shutdown_keeps_a_real_nonzero_exit_as_failure() {
    let fixture = Fixture::new("shutdown", "0", "7");
    let deadline = Instant::now() + Duration::from_secs(5);
    // The collector briefly owns the child mutex; pending is not an exit result.
    let code = loop {
        match fixture.bridge().poll_owner_exit() {
            Ok(Some(code)) => break code,
            Ok(None) => {}
            Err(error) => assert_eq!(error, OWNER_CLOSE_PENDING, "unexpected collection failure"),
        }
        assert!(Instant::now() < deadline, "nonzero owner was not collected");
        let _ = fixture.bridge().shutdown();
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(code, 7);
    assert!(fixture.bridge().shutdown().is_err(), "nonzero is not clean");
    fixture.finish(7);
}

#[test]
fn drop_after_reply_pipe_eof_retains_ownership_until_collection() {
    let mut fixture = Fixture::new("eof", "1", "0");
    fixture.await_live_eof();
    fixture.drop_bridge();
    fixture.assert_live_and_preserved();
    fixture.finish(0);
}

#[test]
fn drop_does_not_kill_acknowledged_slower_cleanup() {
    let mut fixture = Fixture::new("shutdown", "3.5", "0");
    assert_eq!(
        fixture.bridge().shutdown().unwrap_err(),
        OWNER_CLOSE_PENDING
    );
    fixture.drop_bridge();
    fixture.assert_live_and_preserved();
    fixture.finish(0);
}

fn negative(mode: &str) {
    let mut fixture = Fixture::new(mode, "0", "0");
    if mode == "eof_stuck" {
        fixture.await_live_eof();
    }
    let began = Instant::now();
    let error = fixture
        .bridge()
        .shutdown()
        .expect_err("live fixture must not be called clean");
    assert!(
        began.elapsed() < Duration::from_secs(5),
        "reply bound plus test scheduling margin"
    );
    let expected = match mode {
        "reject" => "The authority rejected shutdown; its process and saved data remain owned",
        "no_ack" => OWNER_CLOSE_NO_ACK,
        "eof_stuck" => OWNER_CLOSE_PIPE_CLOSED,
        "ack_stuck" => OWNER_CLOSE_PENDING,
        _ => unreachable!(),
    };
    assert_eq!(error, expected);
    assert_eq!(
        fixture
            .bridge()
            .closing
            .load(std::sync::atomic::Ordering::Acquire),
        mode != "reject",
        "only a definite refusal permits editing to resume"
    );
    fixture.assert_live_and_preserved();
    fixture.drop_bridge();
    fixture.assert_live_and_preserved();
    fixture.finish(0);
}
#[test]
fn rejection_returns_failure_and_drop_is_bounded() {
    negative("reject");
}
#[test]
fn definite_refusal_allows_a_fresh_close_attempt() {
    let fixture = Fixture::new("reject_once", "0", "0");
    assert!(fixture
        .bridge()
        .shutdown()
        .unwrap_err()
        .contains("rejected shutdown"));
    assert!(!fixture
        .bridge()
        .closing
        .load(std::sync::atomic::Ordering::Acquire));
    await_clean(fixture.bridge());
    fixture.finish(0);
}
#[test]
fn no_ack_returns_failure_and_drop_is_bounded() {
    negative("no_ack");
}
#[test]
fn closed_pipe_live_owner_returns_failure_and_drop_is_bounded() {
    negative("eof_stuck");
}
#[test]
fn acknowledged_stuck_cleanup_returns_pending_and_drop_is_bounded() {
    negative("ack_stuck");
}

#[test]
fn busy_child_mutex_does_not_block_shutdown() {
    let mut fixture = Fixture::new("eof_stuck", "0", "0");
    let child = fixture.child.upgrade().unwrap();
    let guard = child.lock().unwrap();
    let began = Instant::now();
    assert!(fixture.bridge().shutdown().is_err());
    assert!(began.elapsed() < Duration::from_secs(1));
    drop(guard);
    drop(child);
    fixture.drop_bridge();
    fixture.assert_live_and_preserved();
    fixture.finish(0);
}

#[test]
fn blocked_input_does_not_block_or_duplicate_the_shutdown_request() {
    let mut fixture = Fixture::new("no_ack", "0", "0");
    let input = Arc::clone(&fixture.bridge().input);
    let guard = input.lock().unwrap();
    let began = Instant::now();
    assert!(fixture.bridge().shutdown().is_err());
    assert!(began.elapsed() < Duration::from_secs(5));
    let first = fixture
        .bridge()
        .shutdown_request
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .request_id
        .clone();
    assert!(fixture.bridge().shutdown().is_err());
    assert_eq!(
        fixture
            .bridge()
            .shutdown_request
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .request_id,
        first
    );
    assert_eq!(fixture.bridge().pending.lock().unwrap().len(), 1);
    drop(guard);
    drop(input);
    fixture.drop_bridge();
    fixture.assert_live_and_preserved();
    fixture.finish(0);
}
