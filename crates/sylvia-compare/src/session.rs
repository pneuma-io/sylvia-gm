//! Run one scripted session against an owner and record everything observable.

use crate::dbdump;
use crate::diff::Rules;
use crate::owner::{Owner, OwnerCommand};
use crate::standin::{self, Standin};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;
use std::time::Duration;
use sylvia_base::{Json, JsonMap};

/// A scripted session: the same requests are sent to whichever owner is under test.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Session {
    /// Short name, also the recording's file name.
    pub name: String,
    /// What the session covers, for people.
    pub description: String,
    /// Start the stand-in model service and expose `${standin_url}`, `${standin_key}` and
    /// `${standin_model}` to the steps.
    #[serde(default)]
    pub standin: bool,
    /// What the stand-in narrates: `fixed` (default) or `journey`, which answers "The Game
    /// Master answers: " and the player's message, as the Stop and resume fixtures do.
    #[serde(default)]
    pub standin_narration: standin::Narration,
    /// Seconds to wait for the owner to report ready. A first start installs the bundled
    /// packages, which takes the Python owner well over a minute.
    #[serde(default = "default_ready_timeout")]
    pub ready_timeout_s: u64,
    /// Regular expressions for tables recorded as a row count and one hash of their rows
    /// instead of every row. Use it for bulk content another session already records in
    /// full (the installed packages), and only for tables without run-specific values.
    #[serde(default)]
    pub summarise_tables: Vec<String>,
    /// The requests, in order.
    pub steps: Vec<Step>,
    /// What may differ between runs of this session, on top of the built-in defaults.
    #[serde(default)]
    pub rules: Rules,
}

fn default_ready_timeout() -> u64 {
    600
}

fn default_step_timeout() -> u64 {
    300
}

/// One request, a wait for an earlier background request, a stand-in control, or a fresh
/// launch of the owner on the same data folder.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    /// Owner method. Omit when `await` is used.
    #[serde(default)]
    pub method: Option<String>,
    /// Parameters. `${name}` is replaced by a saved or built-in value.
    #[serde(default)]
    pub params: Json,
    /// Request id; defaults to `s<index>`.
    #[serde(default)]
    pub id: Option<String>,
    /// Send without waiting; a later step with `await` collects the reply.
    #[serde(default)]
    pub background: bool,
    /// Wait for the reply to the background step with this id.
    #[serde(default, rename = "await")]
    pub await_id: Option<String>,
    /// Control the stand-in: `wait_held` blocks until it holds one more request than earlier
    /// `wait_held` steps waited for (the owner is then inside that model stream); `release`
    /// lets every `[late]` request being held answer.
    #[serde(default)]
    pub standin: Option<StandinControl>,
    /// Start the owner again on the same data folder. Must follow a `shutdown` step. The
    /// recording keeps the previous launch's exit code and the new launch's ready message.
    #[serde(default)]
    pub relaunch: bool,
    /// Values to keep from the reply: variable name to JSON pointer into the reply.
    #[serde(default)]
    pub save: HashMap<String, String>,
    /// Stop the session unless the reply's `ok` equals this. Unset records either outcome.
    #[serde(default)]
    pub expect_ok: Option<bool>,
    /// Seconds to wait for the reply.
    #[serde(default = "default_step_timeout")]
    pub timeout_s: u64,
}

/// A stand-in control step.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StandinControl {
    /// Wait until the stand-in holds a new request.
    WaitHeld,
    /// Let held `[late]` requests answer.
    Release,
}

/// Load a session file.
pub fn load(path: &Path) -> Result<Session, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("could not read {}: {error}", path.display()))?;
    serde_json::from_str(&text).map_err(|error| format!("{} is not a valid session: {error}", path.display()))
}

/// Run `session` against `command` in a fresh data folder under `work` and return the
/// recording. `work` must not exist yet; it is left in place for inspection.
/// Recordings are per platform: paths inside `<work>` keep the platform's separators.
pub fn record(session: &Session, command: &OwnerCommand, work: &Path) -> Result<Json, String> {
    if work.exists() {
        return Err(format!("{} already exists; a recording needs a fresh folder", work.display()));
    }
    // The owner gets its own data folder and its own empty user profile, so a run never reads
    // or writes the real profile and both owners start from the same nothing.
    let data_root = work.join("data");
    let home = work.join("home");
    for folder in [&data_root, &home.join("AppData/Roaming"), &home.join("AppData/Local"), &home.join("xdg")]
    {
        std::fs::create_dir_all(folder)
            .map_err(|error| format!("could not create {}: {error}", folder.display()))?;
    }
    // Without the `\\?\` prefix: the owner should see the path the desktop would give it.
    let work = std::path::PathBuf::from(plain_path(&work.canonicalize().map_err(|error| error.to_string())?));
    let (data_root, home) = (work.join("data"), work.join("home"));
    let database = data_root.join("authority.sqlite3");
    let mut command = command.clone();
    let profile = |path: std::path::PathBuf| plain_path(&path);
    for (key, value) in [
        ("USERPROFILE", profile(home.clone())),
        ("HOME", profile(home.clone())),
        ("APPDATA", profile(home.join("AppData/Roaming"))),
        ("LOCALAPPDATA", profile(home.join("AppData/Local"))),
        ("XDG_STATE_HOME", profile(home.join("xdg/state"))),
        ("XDG_CACHE_HOME", profile(home.join("xdg/cache"))),
        ("XDG_CONFIG_HOME", profile(home.join("xdg/config"))),
        ("XDG_DATA_HOME", profile(home.join("xdg/data"))),
    ] {
        // An explicit --env on the command line wins.
        if !command.env.iter().any(|(existing, _)| existing == key) {
            command.env.push((key.to_owned(), value));
        }
    }
    let command = &command;

    let standin = if session.standin { Some(Standin::start(session.standin_narration)?) } else { None };
    let mut variables: HashMap<String, Json> = HashMap::new();
    let plain_work = plain_path(&work);
    variables.insert("data_root".into(), plain_path(&data_root).into());
    if let Some(standin) = &standin {
        variables.insert("standin_url".into(), standin.url.clone().into());
        variables.insert("standin_key".into(), standin::KEY.into());
        variables.insert("standin_model".into(), standin::MODEL.into());
    }

    let outcome = run_steps(session, command, &database, &mut variables, standin.as_ref());
    let model_requests = standin.map(Standin::finish).unwrap_or_default();
    let (ready, steps, exit_code, stopped) = outcome?;

    let mut recording = JsonMap::new();
    recording.insert("session".into(), session.name.clone().into());
    // Present only when the session did not run to the end; a reference never has it.
    if !stopped.is_null() {
        recording.insert("stopped".into(), stopped);
    }
    recording.insert("ready".into(), ready);
    recording.insert("steps".into(), Json::Array(steps));
    recording.insert("exit_code".into(), exit_code.map_or(Json::Null, Json::from));
    recording.insert("model_requests".into(), Json::Array(model_requests));
    let mut dumped = if database.exists() { dbdump::dump(&database)? } else { Json::Null };
    summarise_tables(&mut dumped, &session.summarise_tables)?;
    recording.insert("database".into(), dumped);
    recording.insert("files".into(), list_files(&data_root)?);

    // Run-specific locations become fixed words so recordings from different machines and
    // different owners line up.
    let mut text = Json::Object(recording).to_string();
    let escaped_work = serde_json::to_string(&plain_work).expect("a path is a string");
    let escaped_work = &escaped_work[1..escaped_work.len() - 1];
    text = text.replace(escaped_work, "<work>");
    text = text.replace(&escaped_work.replace("\\\\", "/"), "<work>");
    if let Some(Json::String(url)) = variables.get("standin_url") {
        text = text.replace(url.as_str(), "<standin_url>");
        text = text.replace(url.trim_end_matches("/v1"), "<standin_origin>");
    }
    serde_json::from_str(&text)
        .map_err(|error| format!("recording is not valid JSON after cleaning: {error}"))
}

/// Replace the rows of matching tables with one hash over them.
fn summarise_tables(database: &mut Json, patterns: &[String]) -> Result<(), String> {
    let patterns = patterns
        .iter()
        .map(|pattern| {
            regex::Regex::new(pattern).map_err(|error| format!("bad table pattern {pattern}: {error}"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let Some(tables) = database.get_mut("tables").and_then(Json::as_object_mut) else {
        return Ok(());
    };
    for (name, table) in tables.iter_mut() {
        if !patterns.iter().any(|pattern| pattern.is_match(name)) {
            continue;
        }
        if let Some(entry) = table.as_object_mut() {
            if let Some(rows) = entry.shift_remove("rows") {
                entry.insert("rows_sha256".into(), sylvia_base::hash::sha256_hex(rows.to_string()).into());
            }
        }
    }
    Ok(())
}

/// The ready message, the recorded steps, the owner's exit code, and why the session stopped
/// early (`null` when it ran to the end).
type StepsOutcome = (Json, Vec<Json>, Option<i32>, Json);

fn run_steps(
    session: &Session,
    command: &OwnerCommand,
    database: &Path,
    variables: &mut HashMap<String, Json>,
    standin: Option<&Standin>,
) -> Result<StepsOutcome, String> {
    let ready_timeout = Duration::from_secs(session.ready_timeout_s);
    let mut owner = Owner::launch(command, database, "sylvia-compare", ready_timeout)?;
    let ready = owner.ready.clone();
    let mut recorded = Vec::new();
    let mut closed_by_shutdown = false;
    let mut stopped = Json::Null;
    for (index, step) in session.steps.iter().enumerate() {
        let id = step.id.clone().unwrap_or_else(|| format!("s{index}"));
        let mut entry = JsonMap::new();
        if step.relaunch {
            if !closed_by_shutdown {
                return Err(format!("step {index} relaunches the owner without a shutdown before it"));
            }
            let exit_code = owner.close(Duration::from_secs(60))?;
            owner = Owner::launch(command, database, "sylvia-compare", ready_timeout)?;
            closed_by_shutdown = false;
            entry.insert("relaunch".into(), true.into());
            entry.insert("exit_code".into(), exit_code.map_or(Json::Null, Json::from));
            entry.insert("ready".into(), owner.ready.clone());
            recorded.push(Json::Object(entry));
            continue;
        }
        if let Some(control) = step.standin {
            let standin =
                standin.ok_or(format!("step {index} controls the stand-in, which is not started"))?;
            let name = match control {
                StandinControl::WaitHeld => "wait_held",
                StandinControl::Release => "release",
            };
            entry.insert("standin".into(), name.into());
            recorded.push(Json::Object(entry));
            match control {
                StandinControl::WaitHeld => {
                    if let Err(reason) = standin.wait_held(Duration::from_secs(step.timeout_s)) {
                        stopped = Json::String(format!(
                            "step {index} (wait_held): {reason}; {} of {} steps ran",
                            index + 1,
                            session.steps.len()
                        ));
                        break;
                    }
                }
                StandinControl::Release => standin.release(),
            }
            continue;
        }
        let reply = if let Some(await_id) = &step.await_id {
            entry.insert("await".into(), await_id.clone().into());
            Some(owner.reply(await_id, Duration::from_secs(step.timeout_s))?)
        } else {
            let method =
                step.method.as_deref().ok_or(format!("step {index} has neither method nor await"))?;
            let empty = Json::Object(JsonMap::new());
            let params = substitute(if step.params.is_null() { &empty } else { &step.params }, variables)?;
            entry.insert("id".into(), id.clone().into());
            entry.insert("method".into(), method.into());
            entry.insert("params".into(), params.clone());
            owner.request(&id, method, &params)?;
            if method == "shutdown" {
                closed_by_shutdown = true;
            }
            if step.background {
                entry.insert("background".into(), true.into());
                None
            } else {
                Some(owner.reply(&id, Duration::from_secs(step.timeout_s))?)
            }
        };
        // A step the session depends on that goes wrong stops the session. What was recorded
        // up to there is kept, so a check still shows how far an owner gets.
        let mut stop = None;
        if let Some(reply) = reply {
            let ok = reply.get("ok").and_then(Json::as_bool);
            let method = step.method.as_deref().unwrap_or("await");
            if let Some(expected) = step.expect_ok.filter(|expected| ok != Some(*expected)) {
                let code = reply.pointer("/error/code").and_then(Json::as_str).unwrap_or("no error code");
                stop = Some(format!("step {index} ({method}) needed ok={expected} and got {code}"));
            }
            for (name, pointer) in &step.save {
                match reply.pointer(pointer) {
                    Some(value) => {
                        variables.insert(name.clone(), value.clone());
                    }
                    None if stop.is_none() => {
                        stop = Some(format!("step {index} ({method}) has nothing at {pointer} to save"));
                    }
                    None => {}
                }
            }
            entry.insert("reply".into(), reply);
        }
        recorded.push(Json::Object(entry));
        if let Some(reason) = stop {
            stopped = Json::String(format!("{reason}; {} of {} steps ran", index + 1, session.steps.len()));
            break;
        }
        // After a shutdown only a relaunch may follow; anything else ends the session.
        if closed_by_shutdown && !session.steps.get(index + 1).is_some_and(|next| next.relaunch) {
            break;
        }
    }
    let exit_code = owner.close(Duration::from_secs(60))?;
    Ok((ready, recorded, exit_code, stopped))
}

/// Replace `${name}` in every string of `value`. A string that is exactly one placeholder
/// takes the saved value's own type; otherwise the value is spliced in as text.
fn substitute(value: &Json, variables: &HashMap<String, Json>) -> Result<Json, String> {
    Ok(match value {
        Json::String(text) => {
            if let Some(name) = text.strip_prefix("${").and_then(|rest| rest.strip_suffix('}')) {
                if !name.contains("${") {
                    return variables.get(name).cloned().ok_or(format!("no saved value named {name}"));
                }
            }
            let mut out = String::new();
            let mut rest = text.as_str();
            while let Some(start) = rest.find("${") {
                let end = rest[start..].find('}').ok_or(format!("unclosed placeholder in {text}"))? + start;
                let name = &rest[start + 2..end];
                out.push_str(&rest[..start]);
                match variables.get(name).ok_or(format!("no saved value named {name}"))? {
                    Json::String(found) => out.push_str(found),
                    other => out.push_str(&other.to_string()),
                }
                rest = &rest[end + 1..];
            }
            out.push_str(rest);
            Json::String(out)
        }
        Json::Array(items) => {
            Json::Array(items.iter().map(|item| substitute(item, variables)).collect::<Result<_, _>>()?)
        }
        Json::Object(map) => {
            let mut out = JsonMap::new();
            for (key, item) in map {
                out.insert(key.clone(), substitute(item, variables)?);
            }
            Json::Object(out)
        }
        other => other.clone(),
    })
}

/// A path without Windows' `\\?\` prefix, as the owner itself would print it.
fn plain_path(path: &Path) -> String {
    let text = path.to_string_lossy().into_owned();
    text.strip_prefix(r"\\?\").map(str::to_owned).unwrap_or(text)
}

/// Text files up to this size are recorded whole; larger files by hash.
const INLINE_TEXT_BYTES: usize = 16 * 1024;

/// Every file under the data folder, in path order.
///
/// Database files are listed by name only: their contents are in the dump and their sizes vary
/// with page reuse. JSON-lines logs are recorded as parsed lines so rules can reach inside.
/// Small text files are recorded whole and everything else by size and SHA-256, so a file the
/// owner writes is compared byte for byte.
fn list_files(root: &Path) -> Result<Json, String> {
    let mut found = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        let entries = std::fs::read_dir(&folder)
            .map_err(|error| format!("could not list {}: {error}", folder.display()))?;
        for entry in entries {
            let entry = entry.map_err(|error| error.to_string())?;
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
                continue;
            }
            let relative = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().replace('\\', "/");
            let mut item = JsonMap::new();
            item.insert("path".into(), relative.clone().into());
            if relative.contains(".sqlite") {
                found.push((relative, item));
                continue;
            }
            let bytes = std::fs::read(&path)
                .map_err(|error| format!("could not read {}: {error}", path.display()))?;
            let text = std::str::from_utf8(&bytes).ok();
            if let (true, Some(text)) = (relative.ends_with(".jsonl"), text) {
                let lines = text
                    .lines()
                    .map(|line| serde_json::from_str(line).unwrap_or_else(|_| Json::String(line.to_owned())))
                    .collect();
                item.insert("lines".into(), Json::Array(lines));
            } else {
                item.insert("bytes".into(), bytes.len().into());
                // Inline text is compared character for character, and scrub rules can reach
                // into it (a protected credential differs on every save); a hash could not.
                match text {
                    Some(text) if bytes.len() <= INLINE_TEXT_BYTES => {
                        item.insert("text".into(), text.into());
                    }
                    _ => {
                        item.insert("sha256".into(), sylvia_base::hash::sha256_hex(&bytes).into());
                    }
                }
            }
            found.push((relative, item));
        }
    }
    found.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(found.into_iter().map(|(_, item)| Json::Object(item)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn placeholders_keep_types_or_splice_text() {
        let mut variables = HashMap::new();
        variables.insert("revision".to_owned(), json!(3));
        variables.insert("name".to_owned(), json!("Rift"));
        let value =
            json!({"expected": "${revision}", "label": "${name}-draft-${revision}", "n": [1, "${name}"]});
        assert_eq!(
            substitute(&value, &variables).unwrap(),
            json!({"expected": 3, "label": "Rift-draft-3", "n": [1, "Rift"]})
        );
        assert!(substitute(&json!("${missing}"), &variables).is_err());
    }
}
