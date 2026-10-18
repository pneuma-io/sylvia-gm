use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, State, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, WindowEvent,
};

const MAX_PANEL_WINDOWS: usize = 64;
const MAX_ID_BYTES: usize = 96;
const MAX_TITLE_BYTES: usize = 256;
const MAX_CONTEXT_BYTES: usize = 512;
const MAX_PAYLOAD_BYTES: usize = 4 * 1024 * 1024;
const MIN_WINDOW_WIDTH: u32 = 400;
const MIN_WINDOW_HEIGHT: u32 = 280;
const DEFAULT_WINDOW_WIDTH: u32 = 960;
const DEFAULT_WINDOW_HEIGHT: u32 = 720;
const WINDOW_FRAME_WIDTH: u32 = 24;
const WINDOW_FRAME_HEIGHT: u32 = 80;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct PanelContext {
    pub campaign_id: String,
    pub session_id: String,
    pub generation: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WindowPlacement {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PanelWindowSpec {
    pub id: String,
    pub title: String,
    pub placement: Option<WindowPlacement>,
}

#[derive(Clone)]
struct RegisteredWindow {
    context: PanelContext,
    id: String,
    label: String,
}

#[derive(Default)]
struct Registry {
    context: Option<PanelContext>,
    windows: HashMap<String, RegisteredWindow>,
}

#[derive(Default)]
pub struct PanelWindows {
    sync: Mutex<()>,
    registry: Mutex<Registry>,
}

#[derive(Clone, Debug)]
struct MonitorBounds {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

// Native teardown is asynchronous; a replacement must not reuse a closing webview's label.
fn panel_label(id: &str) -> String {
    static NEXT_INSTANCE: AtomicU64 = AtomicU64::new(1);
    let instance = NEXT_INSTANCE.fetch_add(1, Ordering::Relaxed);
    format!("panel-{}-{instance}", hex::encode(id.as_bytes()))
}

fn validate_simple_id(value: &str, name: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > MAX_ID_BYTES
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(format!(
            "{name} must contain only letters, numbers, hyphens, or underscores"
        ));
    }
    Ok(())
}

fn validate_context(context: &PanelContext) -> Result<(), String> {
    for (value, name) in [
        (&context.campaign_id, "Panel campaign ID"),
        (&context.session_id, "Panel session ID"),
        (&context.generation, "Panel generation"),
    ] {
        if value.is_empty() || value.chars().any(char::is_control) {
            return Err(format!("{name} is invalid"));
        }
    }
    let encoded = serde_json::to_vec(context)
        .map_err(|_| "Panel context could not be encoded".to_string())?;
    if encoded.len() > MAX_CONTEXT_BYTES {
        return Err("Panel context is too large".to_string());
    }
    Ok(())
}

fn validate_payload(payload: &Value) -> Result<(), String> {
    let size = serde_json::to_vec(payload)
        .map_err(|_| "Panel message could not be encoded".to_string())?
        .len();
    if size > MAX_PAYLOAD_BYTES {
        return Err("Panel message is too large".to_string());
    }
    Ok(())
}

fn validate_reply_parts(result: &Option<Value>, error: &Option<String>) -> Result<(), String> {
    if result.is_some() && error.is_some() {
        return Err("Panel reply cannot contain both a result and an error".to_string());
    }
    Ok(())
}

fn trusted_local_asset(window: &WebviewWindow, path: &str) -> Result<(), String> {
    let url = window
        .url()
        .map_err(|_| "Panel caller URL is unavailable".to_string())?;
    let host = url.host_str().unwrap_or_default();
    let release_origin = match url.scheme() {
        "http" | "https" => host == "tauri.localhost" && url.port().is_none(),
        "tauri" => matches!(host, "localhost" | "tauri.localhost") && url.port().is_none(),
        _ => false,
    };
    let debug_origin = cfg!(debug_assertions)
        && url.scheme() == "http"
        && host == "127.0.0.1"
        && url.port() == Some(1430);
    if !(release_origin || debug_origin)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != path
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("Panel command caller is not trusted".to_string());
    }
    Ok(())
}

fn trusted_main(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Only Sylvia's main window may manage panel windows".to_string());
    }
    trusted_local_asset(window, "/app.html")
}

fn registered_child(
    window: &WebviewWindow,
    state: &PanelWindows,
    context: &PanelContext,
) -> Result<RegisteredWindow, String> {
    trusted_local_asset(window, "/panel.html")?;
    let registered = {
        let registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        if registry.context.as_ref() != Some(context) {
            return Err("Panel context is no longer active".to_string());
        }
        registry
            .windows
            .values()
            .find(|entry| entry.label == window.label())
            .cloned()
    }
    .ok_or_else(|| "Panel window is not registered".to_string())?;
    if registered.context != *context {
        return Err("Panel window registration is invalid".to_string());
    }
    Ok(registered)
}

// Drag input belongs only to the current local main/registered panel window.
pub(crate) fn check_drag_source(
    window: &WebviewWindow,
    state: &PanelWindows,
    context: &PanelContext,
) -> Result<(), String> {
    validate_context(context)?;
    if window.label() == "main" {
        trusted_main(window)?;
        if state
            .registry
            .lock()
            .map_err(|_| "Panel registry is unavailable")?
            .context
            .as_ref()
            != Some(context)
        {
            return Err("Panel context is no longer active".into());
        }
    } else {
        registered_child(window, state, context)?;
    }
    Ok(())
}

// Hook positions and saved placements use physical virtual-desktop coordinates. Size the
// detached panel using the destination monitor's scale, then reuse normal placement recovery.
pub(crate) fn placement_at_drop(
    window: &WebviewWindow,
    point: crate::panel_drag::Point,
    width: f64,
    height: f64,
) -> Result<WindowPlacement, String> {
    let monitors = window
        .available_monitors()
        .map_err(|_| "Display geometry is unavailable")?;
    let scale = monitors
        .iter()
        .find(|monitor| {
            let origin = monitor.position();
            let size = monitor.size();
            i64::from(point.x) >= i64::from(origin.x)
                && i64::from(point.y) >= i64::from(origin.y)
                && i64::from(point.x) < i64::from(origin.x) + i64::from(size.width)
                && i64::from(point.y) < i64::from(origin.y) + i64::from(size.height)
        })
        .map_or(1.0, |monitor| monitor.scale_factor());
    let preferred = drop_geometry(point, width, height, scale);
    recover_placement(Some(&preferred), &monitor_bounds(window)?)
        .ok_or_else(|| "No display is available for the panel".into())
}

fn drop_geometry(
    point: crate::panel_drag::Point,
    width: f64,
    height: f64,
    scale: f64,
) -> WindowPlacement {
    WindowPlacement {
        x: point.x.saturating_sub((32.0 * scale).round() as i32),
        y: point.y.saturating_sub((20.0 * scale).round() as i32),
        width: (width.clamp(400.0, 1280.0) * scale).round() as u32,
        height: (height.clamp(280.0, 960.0) * scale).round() as u32,
    }
}

fn intersect_area(a: &WindowPlacement, b: &MonitorBounds) -> u64 {
    let right = (i64::from(a.x) + i64::from(a.width)).min(i64::from(b.x) + i64::from(b.width));
    let bottom = (i64::from(a.y) + i64::from(a.height)).min(i64::from(b.y) + i64::from(b.height));
    let left = i64::from(a.x).max(i64::from(b.x));
    let top = i64::from(a.y).max(i64::from(b.y));
    u64::try_from((right - left).max(0) * (bottom - top).max(0)).unwrap_or(0)
}

// Fits saved physical geometry to a reachable monitor without changing the saved preference.
fn recover_placement(
    preferred: Option<&WindowPlacement>,
    monitors: &[MonitorBounds],
) -> Option<WindowPlacement> {
    let preferred = preferred?;
    if monitors.is_empty() {
        return None;
    }
    let monitor = monitors
        .iter()
        .max_by_key(|monitor| intersect_area(preferred, monitor))?;
    let available_width = monitor.width.saturating_sub(WINDOW_FRAME_WIDTH).max(1);
    let available_height = monitor.height.saturating_sub(WINDOW_FRAME_HEIGHT).max(1);
    let width = preferred.width.max(MIN_WINDOW_WIDTH).min(available_width);
    let height = preferred
        .height
        .max(MIN_WINDOW_HEIGHT)
        .min(available_height);
    let max_x = i64::from(monitor.x) + i64::from(monitor.width)
        - i64::from(width)
        - i64::from(WINDOW_FRAME_WIDTH);
    let max_y = i64::from(monitor.y) + i64::from(monitor.height)
        - i64::from(height)
        - i64::from(WINDOW_FRAME_HEIGHT);
    Some(WindowPlacement {
        x: i64::from(preferred.x)
            .clamp(i64::from(monitor.x), max_x)
            .try_into()
            .unwrap_or(monitor.x),
        y: i64::from(preferred.y)
            .clamp(i64::from(monitor.y), max_y)
            .try_into()
            .unwrap_or(monitor.y),
        width,
        height,
    })
}

fn monitor_bounds(window: &WebviewWindow) -> Result<Vec<MonitorBounds>, String> {
    window
        .available_monitors()
        .map_err(|_| "Available displays could not be read".to_string())
        .map(|monitors| {
            monitors
                .into_iter()
                .map(|monitor| MonitorBounds {
                    x: monitor.position().x,
                    y: monitor.position().y,
                    width: monitor.size().width,
                    height: monitor.size().height,
                })
                .collect()
        })
}

fn emit_main(app: &AppHandle, event: &str, payload: Value) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "Sylvia's main window is unavailable".to_string())?;
    main.emit(event, payload)
        .map_err(|_| "Panel event could not be delivered".to_string())
}

fn actual_placement(window: &WebviewWindow) -> Option<WindowPlacement> {
    window.outer_position().ok().and_then(|position| {
        window.inner_size().ok().map(|size| WindowPlacement {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
        })
    })
}

fn attach_window_events(
    window: &WebviewWindow,
    initial_placement: Option<WindowPlacement>,
) -> (Arc<Mutex<Option<WindowPlacement>>>, Arc<AtomicBool>) {
    let app = window.app_handle().clone();
    let label = window.label().to_string();
    let tracked_window = window.clone();
    let last_placement = Arc::new(Mutex::new(initial_placement));
    let callback_last_placement = Arc::clone(&last_placement);
    let suppress_placement = Arc::new(AtomicBool::new(true));
    let callback_suppress_placement = Arc::clone(&suppress_placement);
    window.on_window_event(move |event| match event {
        WindowEvent::Destroyed => {
            app.state::<crate::panel_drag::PanelDrags>()
                .cancel_window(&label);
        }
        WindowEvent::CloseRequested { .. } => {
            let removed = {
                let state = app.state::<PanelWindows>();
                state.registry.lock().ok().and_then(|mut registry| {
                    let entry = registry
                        .windows
                        .values()
                        .find(|entry| entry.label == label)
                        .cloned()?;
                    registry.windows.remove(&entry.id)
                })
            };
            if let Some(entry) = removed {
                let _ = emit_main(
                    &app,
                    "panel-window-closed",
                    json!({"context": entry.context, "window_id": entry.id}),
                );
            }
        }
        WindowEvent::Moved(_) | WindowEvent::Resized(_) => {
            let entry = {
                let state = app.state::<PanelWindows>();
                state.registry.lock().ok().and_then(|registry| {
                    registry
                        .windows
                        .values()
                        .find(|entry| entry.label == label)
                        .cloned()
                })
            };
            let placement = actual_placement(&tracked_window);
            if let (Some(entry), Some(placement)) = (entry, placement) {
                let changed = callback_last_placement
                    .lock()
                    .map(|mut last| {
                        if last.as_ref() == Some(&placement) {
                            false
                        } else {
                            *last = Some(placement.clone());
                            true
                        }
                    })
                    .unwrap_or(false);
                if callback_suppress_placement.load(Ordering::Acquire) || !changed {
                    return;
                }
                let _ = emit_main(
                    &app,
                    "panel-window-placement",
                    json!({
                        "context": entry.context,
                        "window_id": entry.id,
                        "placement": placement,
                    }),
                );
            }
        }
        _ => {}
    });
    (last_placement, suppress_placement)
}

fn create_panel_window(
    main: &WebviewWindow,
    context: &PanelContext,
    spec: &PanelWindowSpec,
    label: &str,
) -> Result<(), String> {
    let init_context = serde_json::to_string(&json!({
        "context": context,
        "window_id": spec.id,
    }))
    .map_err(|_| "Panel initialization could not be encoded".to_string())?;
    let init_script = format!(
        "if (window.location.pathname === '/panel.html') {{ window.__sylviaPanelContext = {init_context}; }}"
    );
    let monitors = monitor_bounds(main)?;
    let recovered = recover_placement(spec.placement.as_ref(), &monitors);
    let mut builder = WebviewWindowBuilder::new(
        main.app_handle(),
        label,
        WebviewUrl::App("panel.html".into()),
    )
    .title(&spec.title)
    .visible(false)
    .resizable(true)
    .disable_drag_drop_handler()
    .min_inner_size(f64::from(MIN_WINDOW_WIDTH), f64::from(MIN_WINDOW_HEIGHT))
    .initialization_script(init_script);
    if recovered.is_none() {
        builder = builder.inner_size(
            f64::from(DEFAULT_WINDOW_WIDTH),
            f64::from(DEFAULT_WINDOW_HEIGHT),
        );
    }
    let window = builder
        .build()
        .map_err(|_| "Panel window could not be opened".to_string())?;
    if let Some(placement) = recovered {
        window
            .set_position(PhysicalPosition::new(placement.x, placement.y))
            .and_then(|_| window.set_size(PhysicalSize::new(placement.width, placement.height)))
            .map_err(|_| "Panel window placement could not be restored".to_string())?;
    }
    let initial_placement = actual_placement(&window);
    let (last_placement, suppress_placement) = attach_window_events(&window, initial_placement);
    window
        .show()
        .map_err(|_| "Panel window could not be shown".to_string())?;
    // Geometry queries wait for the UI thread, whose resize callback also takes this mutex.
    let shown_placement = actual_placement(&window);
    if let Ok(mut last) = last_placement.lock() {
        *last = shown_placement;
    }
    suppress_placement.store(false, Ordering::Release);
    Ok(())
}

#[tauri::command]
pub async fn sync_panel_windows(
    window: WebviewWindow,
    state: State<'_, PanelWindows>,
    context: Option<PanelContext>,
    windows: Vec<PanelWindowSpec>,
    restore_layout: Option<bool>,
) -> Result<Value, String> {
    trusted_main(&window)?;
    let _sync = state
        .sync
        .lock()
        .map_err(|_| "Panel window synchronization is unavailable".to_string())?;
    if windows.len() > MAX_PANEL_WINDOWS {
        return Err("Too many panel windows were requested".to_string());
    }
    if context.is_none() && !windows.is_empty() {
        return Err("Panel windows require an active campaign context".to_string());
    }
    if let Some(context) = &context {
        validate_context(context)?;
    }
    let mut ids = HashSet::new();
    for spec in &windows {
        validate_simple_id(&spec.id, "Panel window ID")?;
        if !ids.insert(spec.id.clone()) {
            return Err("Panel window IDs must be unique".to_string());
        }
        if spec.title.is_empty() || spec.title.len() > MAX_TITLE_BYTES {
            return Err("Panel window title is invalid".to_string());
        }
    }

    let (same_context, existing) = {
        let registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        (registry.context == context, registry.windows.clone())
    };
    let mut kept = HashMap::new();
    // An explicit layout reset recreates windows through normal placement recovery. Routine
    // projection refreshes retain them, so they cannot fight the player's native resize/drag.
    if same_context && !restore_layout.unwrap_or(false) {
        for spec in &windows {
            let Some(entry) = existing.get(&spec.id) else {
                continue;
            };
            let Some(native_window) = window.app_handle().get_webview_window(&entry.label) else {
                continue;
            };
            if native_window.set_title(&spec.title).is_ok() {
                kept.insert(spec.id.clone(), entry.clone());
            }
        }
    }
    let outgoing = existing
        .values()
        .filter(|entry| !kept.contains_key(&entry.id))
        .map(|entry| entry.label.clone())
        .collect::<Vec<_>>();
    {
        let mut registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        registry.context = context.clone();
        registry.windows = kept.clone();
    }

    for label in outgoing {
        if let Some(outgoing_window) = window.app_handle().get_webview_window(&label) {
            let _ = outgoing_window.destroy();
        }
    }

    let mut opened = Vec::new();
    let mut failed = Vec::new();
    if let Some(context) = &context {
        for spec in &windows {
            if kept.contains_key(&spec.id) {
                continue;
            }
            let registered = RegisteredWindow {
                context: context.clone(),
                id: spec.id.clone(),
                label: panel_label(&spec.id),
            };
            let registration_result = {
                let mut registry = state
                    .registry
                    .lock()
                    .map_err(|_| "Panel window registry is unavailable".to_string())?;
                if registry.context.as_ref() != Some(context) {
                    Err("Panel context changed while windows were opening".to_string())
                } else {
                    registry
                        .windows
                        .insert(registered.id.clone(), registered.clone());
                    Ok(())
                }
            };
            registration_result?;
            if create_panel_window(&window, context, spec, &registered.label).is_err() {
                {
                    let mut registry = state
                        .registry
                        .lock()
                        .map_err(|_| "Panel window registry is unavailable".to_string())?;
                    if registry
                        .windows
                        .get(&registered.id)
                        .is_some_and(|entry| entry.label == registered.label)
                    {
                        registry.windows.remove(&registered.id);
                    }
                }
                if let Some(created) = window.app_handle().get_webview_window(&registered.label) {
                    let _ = created.destroy();
                }
                failed.push(spec.id.clone());
                continue;
            }
            if state
                .registry
                .lock()
                .map_err(|_| "Panel window registry is unavailable")?
                .context
                .as_ref()
                != Some(context)
            {
                if let Some(created) = window.app_handle().get_webview_window(&registered.label) {
                    let _ = created.destroy();
                }
                return Err("Panel context changed while windows were opening".to_string());
            }
            opened.push(spec.id.clone());
        }
    }
    Ok(json!({
        "status": if failed.is_empty() { "synced" } else { "partial" },
        "context": context,
        "window_ids": windows.into_iter().filter(|spec| !failed.contains(&spec.id)).map(|spec| spec.id).collect::<Vec<_>>(),
        "opened": opened,
        "failed_window_ids": failed,
    }))
}

#[tauri::command]
pub fn focus_panel_window(
    window: WebviewWindow,
    state: State<'_, PanelWindows>,
    context: PanelContext,
    window_id: String,
) -> Result<(), String> {
    trusted_main(&window)?;
    let label = {
        let registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable")?;
        if registry.context.as_ref() != Some(&context) {
            return Err("Panel context is no longer active".to_string());
        }
        if window_id == "sidebar" {
            "main".to_string()
        } else {
            registry
                .windows
                .get(&window_id)
                .ok_or("Panel window is unavailable")?
                .label
                .clone()
        }
    };
    let target = window
        .app_handle()
        .get_webview_window(&label)
        .ok_or("Panel window is unavailable")?;
    target
        .unminimize()
        .and_then(|_| target.set_focus())
        .map_err(|_| "Panel window could not be focused".to_string())
}

#[tauri::command]
pub fn publish_panel_workspace(
    window: WebviewWindow,
    state: State<'_, PanelWindows>,
    context: PanelContext,
    payload: Value,
) -> Result<Value, String> {
    trusted_main(&window)?;
    validate_context(&context)?;
    validate_payload(&payload)?;
    let targets = {
        let registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        if registry.context.as_ref() != Some(&context) {
            return Err("Panel context is no longer active".to_string());
        }
        registry.windows.values().cloned().collect::<Vec<_>>()
    };
    let mut delivered = 0;
    for target in targets {
        if let Some(panel) = window.app_handle().get_webview_window(&target.label) {
            panel
                .emit(
                    "panel-workspace",
                    json!({
                        "context": context,
                        "payload": payload,
                        "window_id": target.id,
                    }),
                )
                .map_err(|_| "Panel workspace could not be delivered".to_string())?;
            delivered += 1;
        }
    }
    Ok(json!({"status": "published", "delivered": delivered}))
}

#[tauri::command]
pub fn panel_window_message(
    window: WebviewWindow,
    state: State<'_, PanelWindows>,
    context: PanelContext,
    payload: Value,
) -> Result<Value, String> {
    validate_context(&context)?;
    validate_payload(&payload)?;
    let sender = registered_child(&window, &state, &context)?;
    emit_main(
        window.app_handle(),
        "panel-window-message",
        json!({"context": context, "window_id": sender.id, "payload": payload}),
    )?;
    Ok(json!({"status": "forwarded"}))
}

#[tauri::command]
pub fn panel_window_reply(
    window: WebviewWindow,
    state: State<'_, PanelWindows>,
    context: PanelContext,
    window_id: String,
    request_id: String,
    result: Option<Value>,
    error: Option<String>,
) -> Result<Value, String> {
    trusted_main(&window)?;
    validate_context(&context)?;
    validate_simple_id(&window_id, "Panel window ID")?;
    validate_simple_id(&request_id, "Panel request ID")?;
    validate_reply_parts(&result, &error)?;
    if error.as_ref().is_some_and(|message| message.len() > 4096) {
        return Err("Panel reply error is too large".to_string());
    }
    let envelope = json!({
        "context": context,
        "request_id": request_id,
        "result": result,
        "error": error,
    });
    validate_payload(&envelope)?;
    let target = {
        let registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        if registry.context.as_ref() != Some(&context) {
            return Err("Panel context is no longer active".to_string());
        }
        registry
            .windows
            .get(&window_id)
            .filter(|entry| entry.context == context)
            .cloned()
    }
    .ok_or_else(|| "Panel reply target is unavailable".to_string())?;
    let panel = window
        .app_handle()
        .get_webview_window(&target.label)
        .ok_or_else(|| "Panel reply target is unavailable".to_string())?;
    panel
        .emit("panel-window-reply", envelope)
        .map_err(|_| "Panel reply could not be delivered".to_string())?;
    Ok(json!({"status": "replied"}))
}

// Clears registration before destruction so app/session closure never looks like a manual redock.
pub fn close_all(app: &AppHandle, state: &PanelWindows) -> Result<(), String> {
    let labels = {
        let mut registry = state
            .registry
            .lock()
            .map_err(|_| "Panel window registry is unavailable".to_string())?;
        registry.context = None;
        registry
            .windows
            .drain()
            .map(|(_, entry)| entry.label)
            .collect::<Vec<_>>()
    };
    for label in labels {
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.destroy();
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{recover_placement, validate_reply_parts, MonitorBounds, WindowPlacement};
    use serde_json::Value;

    #[test]
    fn dropped_geometry_uses_destination_scale_and_negative_desktop_coordinates() {
        let point = crate::panel_drag::Point { x: -1800, y: 240 };
        let geometry = super::drop_geometry(point, 300.0, 420.0, 1.5);
        assert_eq!(
            geometry,
            WindowPlacement {
                x: -1848,
                y: 210,
                width: 600,
                height: 630
            }
        );
        let recovered = recover_placement(
            Some(&geometry),
            &[
                MonitorBounds {
                    x: -1920,
                    y: 0,
                    width: 1920,
                    height: 1080,
                },
                MonitorBounds {
                    x: 0,
                    y: 0,
                    width: 1920,
                    height: 1080,
                },
            ],
        )
        .unwrap();
        assert_eq!(recovered, geometry);
    }

    #[test]
    fn drop_near_screen_edge_recovers_without_changing_the_requested_geometry() {
        let point = crate::panel_drag::Point { x: 1900, y: 1070 };
        let geometry = super::drop_geometry(point, 600.0, 400.0, 1.0);
        let recovered = recover_placement(
            Some(&geometry),
            &[MonitorBounds {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            }],
        )
        .unwrap();
        assert!(recovered.x >= 0 && recovered.y >= 0);
        assert!(i64::from(recovered.x) + i64::from(recovered.width) <= 1920);
        assert!(i64::from(recovered.y) + i64::from(recovered.height) <= 1080);
        assert_eq!(geometry.x, 1868);
    }

    #[test]
    fn replacement_does_not_reuse_a_still_closing_webview_label() {
        let closing = super::panel_label("saved-map-window");
        let replacement = super::panel_label("saved-map-window");
        assert_ne!(
            closing, replacement,
            "Tauri teardown can outlive the destroy request"
        );
    }

    #[test]
    fn null_result_is_a_valid_success_reply() {
        assert!(validate_reply_parts(&None, &None).is_ok());
        assert!(validate_reply_parts(&Some(Value::Null), &None).is_ok());
        assert!(validate_reply_parts(&Some(Value::Null), &Some("failed".to_string())).is_err());
    }

    #[test]
    fn unreachable_saved_geometry_recovers_inside_an_available_monitor() {
        let saved = WindowPlacement {
            x: 3000,
            y: 100,
            width: 1200,
            height: 900,
        };
        let recovered = recover_placement(
            Some(&saved),
            &[MonitorBounds {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            }],
        )
        .unwrap();
        assert_eq!(recovered.x, 696);
        assert_eq!(recovered.y, 100);
        assert_eq!(recovered.width, 1200);
        assert_eq!(recovered.height, 900);
        assert_eq!(saved.x, 3000, "preferred geometry remains untouched");
    }

    #[test]
    fn oversized_geometry_is_fitted_to_the_selected_monitor() {
        let recovered = recover_placement(
            Some(&WindowPlacement {
                x: -1800,
                y: -400,
                width: 4000,
                height: 2000,
            }),
            &[MonitorBounds {
                x: -1920,
                y: 0,
                width: 1920,
                height: 1080,
            }],
        )
        .unwrap();
        assert_eq!(
            recovered,
            WindowPlacement {
                x: -1920,
                y: 0,
                width: 1896,
                height: 1000,
            }
        );
    }
}
