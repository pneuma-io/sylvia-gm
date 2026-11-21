//! Compare sylvia_app::character_profiles and reusable_library_deletion with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_character_profile_vectors.py` (needs the snapshot's
//! Python). One file serves Windows and Linux: links are junctions on Windows and symbolic links
//! elsewhere, and paths in error text are masked as `<ROOT>` with `/` separators.

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::character_profiles::{
    character_profile_catalog, load_character_profile, profile_from_snapshot, save_character_profile,
    validate_character_profile, validate_source_profile,
};
use sylvia_app::reusable_library_deletion::delete_exact_library_entry;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json};
use sylvia_domain::error::DomainError;
use sylvia_domain::package_library::canonical_json_bytes;

const VECTORS: &str = include_str!("vectors/character_profile_vectors.json");
const BACKSLASH: char = 92u8 as char;

fn apply(document: &Json, edits: &Json) -> Json {
    let mut document = document.clone();
    for edit in edits.as_array().into_iter().flatten() {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let mut parent = &mut document;
        for part in &path[..path.len() - 1] {
            parent = step(parent, part);
        }
        let last = &path[path.len() - 1];
        match op {
            "delete" => match last {
                Json::String(key) => {
                    parent.as_object_mut().unwrap().shift_remove(key.as_str());
                }
                index => {
                    parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
                }
            },
            "append" => step(parent, last).as_array_mut().unwrap().push(value.clone()),
            _ => *step(parent, last) = value.clone(),
        }
    }
    document
}

fn step<'a>(value: &'a mut Json, part: &Json) -> &'a mut Json {
    match part {
        Json::String(key) => value.get_mut(key.as_str()).unwrap(),
        index => value.get_mut(index.as_u64().unwrap() as usize).unwrap(),
    }
}

/// The generator's `source_bytes(spec)`.
fn source_bytes(spec: &Json, bases: &Json) -> Vec<u8> {
    if let Some(base) = spec.get("base") {
        let edits = spec.get("edits").cloned().unwrap_or(Json::Null);
        return canonical_json_bytes(&apply(&bases[base.as_str().unwrap()], &edits));
    }
    if let Some(text) = spec.get("text") {
        return text.as_str().unwrap().as_bytes().to_vec();
    }
    if let Some(text) = spec.get("latin1") {
        return text.as_str().unwrap().chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect();
    }
    let parts = spec["repeat"].as_array().unwrap();
    let mut text = parts[0].as_str().unwrap().to_owned();
    text.push_str(&parts[1].as_str().unwrap().repeat(parts[2].as_u64().unwrap() as usize));
    text.push_str(parts[3].as_str().unwrap());
    text.into_bytes()
}

fn digest(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

/// The generator's `error_outcome(error)`: class, `str(error)`, diagnostics and `code`.
fn error_outcome(error: &DomainError) -> Json {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), error.message(), diagnostics, error.code()])
}

/// The generator's `Masker`: the storage location as `<ROOT>`, then `/` for every separator.
struct Masker(String);

impl Masker {
    fn text(&self, text: &str) -> String {
        let doubled: String =
            self.0.chars().flat_map(|c| if c == BACKSLASH { vec![c, c] } else { vec![c] }).collect();
        let double_separator: String = [BACKSLASH, BACKSLASH].iter().collect();
        text.replace(&doubled, "<ROOT>")
            .replace(&self.0, "<ROOT>")
            .replace(&double_separator, "/")
            .replace(BACKSLASH, "/")
    }

    fn json(&self, value: &Json) -> Json {
        match value {
            Json::String(text) => Json::String(self.text(text)),
            Json::Array(items) => Json::Array(items.iter().map(|item| self.json(item)).collect()),
            Json::Object(map) => {
                Json::Object(map.iter().map(|(key, item)| (key.clone(), self.json(item))).collect())
            }
            other => other.clone(),
        }
    }
}

#[cfg(windows)]
fn make_link(path: &Path, target: &Path) {
    let status = std::process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(path)
        .arg(target)
        .stdout(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(status.success(), "mklink /J failed");
}

#[cfg(not(windows))]
fn make_link(path: &Path, target: &Path) {
    std::os::unix::fs::symlink(target, path).unwrap();
}

#[cfg(windows)]
fn remove_link(path: &Path) {
    std::fs::remove_dir(path).unwrap();
}

#[cfg(not(windows))]
fn remove_link(path: &Path) {
    std::fs::remove_file(path).unwrap();
}

fn copy_tree(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to.join(entry.file_name()));
        } else {
            std::fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
        }
    }
}

fn relative(base: &Path, text: &str) -> PathBuf {
    text.split('/').fold(base.to_path_buf(), |path, part| path.join(part))
}

/// The generator's `run_step`: an operation's outcome, or `None` for a setup step.
fn run_step(base: &Path, step: &Json, bases: &Json, mask: &Masker) -> Json {
    let storage = base.join(step.get("storage").and_then(Json::as_str).unwrap_or("storage"));
    let text = |key: &str| step[key].as_str().unwrap();
    let result = match text("op") {
        "save" => save_character_profile(
            &storage,
            text("operation_id"),
            &source_bytes(&step["source"], bases),
        )
        .map(|profile| json!(["ok", profile.artifact_id, profile.content_sha256, profile.catalog_payload()])),
        "load" => {
            load_character_profile(&storage, text("artifact_id"), step.get("expected").and_then(Json::as_str))
                .map(|profile| {
                    json!(["ok", profile.artifact_id, profile.content_sha256, profile.provenance()])
                })
        }
        "catalog" => {
            let mut invalid = Vec::new();
            character_profile_catalog(&storage, &mut |name: &str, error: &DomainError| {
                let mut entry = vec![Json::from(name)];
                entry.extend(mask.json(&error_outcome(error)).as_array().unwrap().iter().cloned());
                invalid.push(Json::Array(entry));
            })
            .map(|found| {
                let ids: Vec<&str> = found.iter().map(|profile| profile.artifact_id.as_str()).collect();
                json!(["ok", ids, invalid])
            })
        }
        "delete" => {
            let owned: Vec<&str> =
                step["owned"].as_array().unwrap().iter().map(|name| name.as_str().unwrap()).collect();
            delete_exact_library_entry(&storage, text("library"), text("artifact_id"), &owned)
                .map(|deleted| json!(["ok", deleted]))
        }
        setup => {
            let path = relative(base, text("path"));
            match setup {
                "write" => {
                    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                    std::fs::write(&path, source_bytes(&step["source"], bases)).unwrap();
                }
                "mkdir" => std::fs::create_dir_all(&path).unwrap(),
                "remove" if path.is_dir() => std::fs::remove_dir_all(&path).unwrap(),
                "remove" => std::fs::remove_file(&path).unwrap(),
                "copy" => copy_tree(&relative(base, text("from")), &path),
                "link" => {
                    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                    make_link(&path, &relative(base, text("target")));
                }
                "unlink" => remove_link(&path),
                "fill" => {
                    std::fs::create_dir_all(&path).unwrap();
                    for index in 0..step["count"].as_u64().unwrap() {
                        std::fs::create_dir(path.join(format!("filler-{index:03}"))).unwrap();
                    }
                }
                other => panic!("unknown step {other}"),
            }
            return Json::Null;
        }
    };
    result.unwrap_or_else(|error| mask.json(&error_outcome(&error)))
}

#[test]
fn character_profiles_match_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let bases = &vectors["bases"];
    let mut failures = Vec::new();

    let validate = vectors["validate"].as_array().unwrap();
    for case in validate {
        let actual = match validate_character_profile(&source_bytes(&case["source"], bases)) {
            Ok(value) => json!(["ok", digest(&value)]),
            Err(error) => error_outcome(&error),
        };
        let expected = match case.get("row").and_then(Json::as_u64) {
            // Differences row 4: nesting of 128 levels or more gets the walk's depth correction.
            Some(4) => "profile_depth_invalid",
            // Rows 1 and 3: a wide integer or a lone surrogate is refused as malformed JSON.
            Some(_) => "profile_json_invalid",
            None => {
                if actual != case["outcome"] {
                    failures.push(format!(
                        "validate {}: python {}, rust {actual}",
                        case["source"], case["outcome"]
                    ));
                }
                continue;
            }
        };
        if actual[2][0]["code"] != expected {
            failures.push(format!("validate row {}: expected {expected}, rust {actual}", case["row"]));
        }
    }

    let snapshots = vectors["snapshot"].as_array().unwrap();
    for case in snapshots {
        let value = &case["value"];
        let actual = match profile_from_snapshot(value) {
            Ok(profile) => json!([
                "ok",
                profile.artifact_id,
                profile.content_sha256,
                digest(&profile.profile),
                profile.provenance(),
                profile.catalog_payload(),
                profile.snapshot() == *value
            ]),
            Err(error) => error_outcome(&error),
        };
        if actual != case["outcome"] {
            failures.push(format!("snapshot {value}: python {}, rust {actual}", case["outcome"]));
        }
    }

    let sources = vectors["source"].as_array().unwrap();
    for case in sources {
        let actual = match validate_source_profile(&case["value"]) {
            Ok(value) => json!(["ok", value.map(Json::Object)]),
            Err(error) => error_outcome(&error),
        };
        if actual != case["outcome"] {
            failures.push(format!("source {}: python {}, rust {actual}", case["value"], case["outcome"]));
        }
    }

    let mut steps = 0;
    for (name, script) in vectors["storage"].as_object().unwrap() {
        let base = std::env::temp_dir().join(format!("sylvia-profiles-rust-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let mask = Masker(base.to_string_lossy().into_owned());
        for (index, step) in script.as_array().unwrap().iter().enumerate() {
            steps += 1;
            let actual = run_step(&base, step, bases, &mask);
            if actual != step["outcome"] {
                failures.push(format!(
                    "storage {name}[{index}] {}: python {}, rust {actual}",
                    step["op"], step["outcome"]
                ));
            }
        }
        let _ = std::fs::remove_dir_all(&base);
    }

    assert!(
        failures.is_empty(),
        "{} of {} cases differ: {:#?}",
        failures.len(),
        validate.len() + snapshots.len() + sources.len() + steps,
        &failures[..failures.len().min(8)]
    );
}
