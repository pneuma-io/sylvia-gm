//! Observe only Escape and the release position during an explicit panel drag.
//! HTML dropEffect cannot distinguish an outside drop from cancellation. The short-lived
//! Windows observer supplies that distinction without replacing the browser's docking gesture.

use crate::panel_windows::{self, PanelContext, PanelWindows, WindowPlacement};
use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tauri::{State, WebviewWindow};

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
pub(crate) struct Point {
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Copy, Debug, Default)]
struct Observation {
    released: Option<Point>,
    cancelled: bool,
}

impl Observation {
    // The first terminal input wins; Escape followed by releasing the mouse is still cancellation.
    fn release(&mut self, point: Point) {
        if !self.cancelled && self.released.is_none() {
            self.released = Some(point);
        }
    }

    fn cancel(&mut self) {
        if self.released.is_none() {
            self.cancelled = true;
        }
    }

    fn outside(self, source: &WindowPlacement, accepted_or_cancelled: bool) -> Option<Point> {
        if self.cancelled || accepted_or_cancelled {
            return None;
        }
        let point = self.released?;
        let inside = i64::from(point.x) >= i64::from(source.x)
            && i64::from(point.y) >= i64::from(source.y)
            && i64::from(point.x) < i64::from(source.x) + i64::from(source.width)
            && i64::from(point.y) < i64::from(source.y) + i64::from(source.height);
        (!inside).then_some(point)
    }
}

struct ActiveDrag {
    id: String,
    window: String,
    context: PanelContext,
    observer: platform::Observer,
    width: f64,
    height: f64,
}

#[derive(Default)]
pub struct PanelDrags(Mutex<Option<ActiveDrag>>);

impl PanelDrags {
    // Window teardown stops observation even if the renderer cannot send dragend.
    pub fn cancel_window(&self, label: &str) {
        let removed = self.0.lock().ok().and_then(|mut active| {
            if active.as_ref().is_some_and(|drag| drag.window == label) {
                active.take()
            } else {
                None
            }
        });
        drop(removed);
    }
}

#[tauri::command]
pub async fn begin_panel_drag(
    window: WebviewWindow,
    panels: State<'_, PanelWindows>,
    drags: State<'_, PanelDrags>,
    context: PanelContext,
    drag_id: String,
    width: f64,
    height: f64,
) -> Result<(), String> {
    panel_windows::check_drag_source(&window, &panels, &context)?;
    if drag_id.is_empty() || drag_id.len() > 96 || !width.is_finite() || !height.is_finite() {
        return Err("Panel drag is invalid".into());
    }
    let mut active = drags.0.lock().map_err(|_| "Panel drag is unavailable")?;
    // One physical gesture at a time; dispose of an abandoned renderer's prior observer.
    active.take();
    *active = Some(ActiveDrag {
        id: drag_id,
        window: window.label().into(),
        context,
        observer: platform::Observer::start()?,
        width: width.clamp(400.0, 1280.0),
        height: height.clamp(280.0, 960.0),
    });
    Ok(())
}

#[tauri::command]
pub async fn finish_panel_drag(
    window: WebviewWindow,
    panels: State<'_, PanelWindows>,
    drags: State<'_, PanelDrags>,
    context: PanelContext,
    drag_id: String,
    cancelled: bool,
) -> Result<Option<WindowPlacement>, String> {
    // Taking the exact gesture makes duplicate/late completion harmless and stops native
    // observation even when navigation has invalidated this context.
    let drag = {
        let mut active = drags.0.lock().map_err(|_| "Panel drag is unavailable")?;
        if !active.as_ref().is_some_and(|drag| {
            drag.id == drag_id && drag.window == window.label() && drag.context == context
        }) {
            return Ok(None);
        }
        active.take().expect("checked active drag")
    };
    let observed = drag.observer.finish();
    if cancelled || panel_windows::check_drag_source(&window, &panels, &context).is_err() {
        return Ok(None);
    }
    let position = window
        .outer_position()
        .map_err(|_| "Panel window position is unavailable")?;
    let size = window
        .outer_size()
        .map_err(|_| "Panel window size is unavailable")?;
    let bounds = WindowPlacement {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    };
    observed
        .outside(&bounds, false)
        .map(|point| panel_windows::placement_at_drop(&window, point, drag.width, drag.height))
        .transpose()
}

#[cfg(windows)]
mod platform {
    use super::{Observation, Point};
    use std::{cell::Cell, ptr::null_mut, sync::mpsc, thread};
    use windows_sys::Win32::{
        Foundation::{LPARAM, LRESULT, WPARAM},
        System::{LibraryLoader::GetModuleHandleW, Threading::GetCurrentThreadId},
        UI::{
            Input::KeyboardAndMouse::{GetAsyncKeyState, VK_ESCAPE, VK_LBUTTON},
            WindowsAndMessaging::*,
        },
    };

    thread_local! { static OBSERVATION: Cell<Observation> = Cell::new(Observation::default()); }

    // Low-level hooks execute on this dedicated message-loop thread, never inspect text,
    // never suppress input, and do no I/O. Only the first release point or Escape is retained.
    // https://learn.microsoft.com/windows/win32/winmsg/lowlevelmouseproc
    // https://learn.microsoft.com/windows/win32/winmsg/lowlevelkeyboardproc
    unsafe extern "system" fn mouse(code: i32, message: WPARAM, data: LPARAM) -> LRESULT {
        if code == HC_ACTION as i32 && message == WM_LBUTTONUP as usize {
            // Windows supplies this pointer for the duration of the HC_ACTION callback.
            let point = unsafe { (*(data as *const MSLLHOOKSTRUCT)).pt };
            OBSERVATION.with(|cell| {
                let mut observed = cell.get();
                observed.release(Point {
                    x: point.x,
                    y: point.y,
                });
                cell.set(observed);
            });
            unsafe {
                PostQuitMessage(0);
            }
        }
        unsafe { CallNextHookEx(null_mut(), code, message, data) }
    }

    unsafe extern "system" fn keyboard(code: i32, message: WPARAM, data: LPARAM) -> LRESULT {
        if code == HC_ACTION as i32
            && matches!(message as u32, WM_KEYDOWN | WM_SYSKEYDOWN)
            && unsafe { (*(data as *const KBDLLHOOKSTRUCT)).vkCode } == u32::from(VK_ESCAPE)
        {
            OBSERVATION.with(|cell| {
                let mut observed = cell.get();
                observed.cancel();
                cell.set(observed);
            });
            unsafe {
                PostQuitMessage(0);
            }
        }
        unsafe { CallNextHookEx(null_mut(), code, message, data) }
    }

    pub(super) struct Observer {
        thread_id: u32,
        worker: Option<thread::JoinHandle<Observation>>,
    }

    impl Observer {
        pub fn start() -> Result<Self, String> {
            let (ready, receive) = mpsc::sync_channel(1);
            let worker = thread::Builder::new()
                .name("sylvia-panel-drag".into())
                .spawn(move || {
                    // Both hooks and their timer belong to this message loop and are uninstalled
                    // on every exit. A lost renderer cannot leave an indefinite input observer.
                    unsafe {
                        let module = GetModuleHandleW(std::ptr::null());
                        let mouse_hook = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse), module, 0);
                        let key_hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), module, 0);
                        let timer = SetTimer(null_mut(), 0, 120_000, None);
                        let valid = !mouse_hook.is_null()
                            && !key_hook.is_null()
                            && timer != 0
                            && GetAsyncKeyState(i32::from(VK_LBUTTON)) < 0
                            && GetAsyncKeyState(i32::from(VK_ESCAPE)) >= 0;
                        let _ = ready.send(valid.then(|| GetCurrentThreadId()));
                        if valid {
                            let mut message = std::mem::zeroed();
                            loop {
                                let received = GetMessageW(&mut message, null_mut(), 0, 0);
                                if received <= 0 || message.message == WM_TIMER {
                                    break;
                                }
                                TranslateMessage(&message);
                                DispatchMessageW(&message);
                            }
                        }
                        if timer != 0 {
                            KillTimer(null_mut(), timer);
                        }
                        if !key_hook.is_null() {
                            UnhookWindowsHookEx(key_hook);
                        }
                        if !mouse_hook.is_null() {
                            UnhookWindowsHookEx(mouse_hook);
                        }
                    }
                    OBSERVATION.with(Cell::get)
                })
                .map_err(|_| "Panel drag tracking could not start")?;
            match receive.recv().ok().flatten() {
                Some(thread_id) => Ok(Self {
                    thread_id,
                    worker: Some(worker),
                }),
                None => {
                    let _ = worker.join();
                    Err(
                        "Panel drag tracking could not start. Try again or use Arrange panels."
                            .into(),
                    )
                }
            }
        }

        pub fn finish(mut self) -> Observation {
            self.stop()
        }

        fn stop(&mut self) -> Observation {
            let Some(worker) = self.worker.take() else {
                return Observation::default();
            };
            // The worker has a message queue before reporting readiness. Completed workers
            // need no message; their recorded release/cancellation is already final.
            if !worker.is_finished() {
                unsafe {
                    PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0);
                }
            }
            worker.join().unwrap_or_default()
        }
    }

    impl Drop for Observer {
        fn drop(&mut self) {
            self.stop();
        }
    }
}

#[cfg(not(windows))]
mod platform {
    use super::Observation;
    pub(super) struct Observer;
    impl Observer {
        pub fn start() -> Result<Self, String> {
            Err("Outside-window panel dragging is unavailable on this platform. Use Arrange panels.".into())
        }
        pub fn finish(self) -> Observation {
            Observation::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source() -> WindowPlacement {
        WindowPlacement {
            x: -1600,
            y: 100,
            width: 1400,
            height: 900,
        }
    }

    #[test]
    fn only_an_observed_outside_release_detaches() {
        let mut observed = Observation::default();
        assert_eq!(observed.outside(&source(), false), None);
        observed.release(Point { x: 500, y: 200 });
        assert_eq!(
            observed.outside(&source(), false),
            Some(Point { x: 500, y: 200 })
        );
        assert_eq!(observed.outside(&source(), true), None);
        observed.release(Point { x: 900, y: 900 });
        assert_eq!(observed.released, Some(Point { x: 500, y: 200 }));
    }

    #[test]
    fn escape_then_mouse_release_is_cancellation() {
        let mut observed = Observation::default();
        observed.cancel();
        observed.release(Point { x: 500, y: 200 });
        assert_eq!(observed.outside(&source(), false), None);
    }

    #[test]
    fn inside_and_frame_drops_stay_in_source() {
        for point in [Point { x: -1600, y: 100 }, Point { x: -201, y: 999 }] {
            let mut observed = Observation::default();
            observed.release(point);
            assert_eq!(observed.outside(&source(), false), None);
        }
    }
}
