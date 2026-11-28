//! Compare `sylvia_app::character_card_import` with the frozen Python: card previews, and the
//! desktop import lifecycle on one storage folder.
//!
//! Regenerate with `tests/vectors/generate_character_card_vectors.py` (needs the snapshot's
//! Python).

use std::path::Path;
use sylvia_app::character_card_import::{
    confirm_card_preview, load_import_original, preview_character_card, save_character_card_import,
    stage_card_preview,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::json_difference;
use sylvia_base::{pybase64, pyjson, Json};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/character_card/card_vectors.json"))
        .expect("vector file is valid JSON")
}

fn decode(text: &Json) -> Vec<u8> {
    pybase64::a2b_base64(text.as_str().unwrap().as_bytes(), true).unwrap()
}

/// The generator's `outcome`: an `OSError` by its class only.
fn outcome(result: DomainResult<Json>) -> Json {
    match result {
        Ok(value) => serde_json::json!({ "ok": value }),
        Err(error) => failure(&error),
    }
}

fn failure(error: &DomainError) -> Json {
    let class = error.python_class();
    if matches!(
        class,
        "OSError"
            | "FileNotFoundError"
            | "FileExistsError"
            | "PermissionError"
            | "IsADirectoryError"
            | "NotADirectoryError"
    ) {
        return serde_json::json!({ "error": class });
    }
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    serde_json::json!({"error": class, "message": error.message(), "diagnostics": diagnostics})
}

/// `zlib.crc32`, for the described PNG chunk.
fn crc32(data: &[u8]) -> u32 {
    let mut crc = flate2::Crc::new();
    crc.update(data);
    crc.sum()
}

/// The generator's `long_metadata()`, from its recipe and the shared chunks.
fn png_text(record: &Json, recipe: &Json) -> Vec<u8> {
    let chunks = &record["png_chunks"];
    let mut text = recipe["keyword"].as_str().unwrap().as_bytes().to_vec();
    text.push(0);
    let byte = recipe["byte"].as_str().unwrap().as_bytes()[0];
    text.extend(std::iter::repeat_n(byte, recipe["count"].as_u64().unwrap() as usize));
    let mut source = decode(&chunks["signature"]);
    source.extend(decode(&chunks["ihdr"]));
    source.extend((text.len() as u32).to_be_bytes());
    let mut kind_and_data = b"tEXt".to_vec();
    kind_and_data.extend(&text);
    source.extend(&kind_and_data);
    source.extend(crc32(&kind_and_data).to_be_bytes());
    source.extend(decode(&chunks["idat"]));
    source.extend(decode(&chunks["iend"]));
    source
}

#[test]
fn previews_convert_cards_as_python_does() {
    let record = vectors();
    let mut failures = Vec::new();
    for case in record["previews"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let source = if let Some(repeat) = case.get("repeat") {
            repeat[0].as_str().unwrap().repeat(repeat[1].as_u64().unwrap() as usize).into_bytes()
        } else if let Some(recipe) = case.get("png_text") {
            let source = png_text(&record, recipe);
            assert_eq!(sha256_hex(&source), case["sha256"], "{name}: the described source");
            source
        } else {
            decode(&case["source"])
        };
        let got = outcome(preview_character_card(&source).map(|preview| {
            serde_json::json!({
                "profile": preview.profile,
                "source_sha256": preview.source_sha256,
                "report": preview.report(),
            })
        }));
        if let Some(difference) = json_difference(&got, &case["outcome"]) {
            failures.push(format!("{name}: {difference}"));
        }
    }
    assert!(failures.is_empty(), "{} previews differ:\n{}", failures.len(), failures.join("\n"));
}

/// The generator's `tree`: every file under the storage folder, by path, with its SHA-256.
fn tree(storage: &Path) -> Vec<(String, String)> {
    fn walk(folder: &Path, root: &Path, found: &mut Vec<(Vec<String>, String)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(&path, root, found);
            } else {
                let parts = path
                    .strip_prefix(root)
                    .unwrap()
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy().into_owned())
                    .collect();
                found.push((parts, sha256_hex(std::fs::read(&path).unwrap())));
            }
        }
    }
    let mut found = Vec::new();
    walk(storage, storage, &mut found);
    found.sort();
    found.into_iter().map(|(parts, digest)| (parts.join("/"), digest)).collect()
}

#[test]
fn the_import_lifecycle_keeps_files_as_python_does() {
    let record = vectors();
    let scratch = std::env::temp_dir().join(format!("sgcc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    let (storage, folder) = (scratch.join("storage"), scratch.join("files"));
    std::fs::create_dir_all(&storage).unwrap();
    std::fs::create_dir_all(folder.join("folder.json")).unwrap();
    for (name, data) in record["files"].as_object().unwrap() {
        std::fs::write(folder.join(name), decode(data)).unwrap();
    }
    let text = |step: &Json, key: &str| step[key].as_str().unwrap().to_owned();
    let mut failures = Vec::new();
    for (index, step) in record["lifecycle"].as_array().unwrap().iter().enumerate() {
        let op = text(step, "op");
        let got = match op.as_str() {
            "stage" => {
                outcome(stage_card_preview(&storage, &folder.join(text(step, "file"))).map(Json::Object))
            }
            "confirm" => outcome(confirm_card_preview(&storage, &text(step, "digest")).map(Json::Object)),
            "save" => {
                let source = std::fs::read(folder.join(text(step, "file"))).unwrap();
                outcome(
                    save_character_card_import(&storage, &text(step, "operation_id"), &source)
                        .map(Json::Object),
                )
            }
            "load_original" => outcome(
                load_import_original(&storage, &text(step, "digest")).map(|source| sha256_hex(source).into()),
            ),
            "tamper" => {
                std::fs::write(storage.join(text(step, "path")), decode(&step["data"])).unwrap();
                serde_json::json!({ "ok": null })
            }
            other => panic!("unknown step {other}"),
        };
        let label = format!(
            "#{index} {op} {}",
            step.get("file").or(step.get("digest")).cloned().unwrap_or(Json::Null)
        );
        if let Some(difference) = json_difference(&got, &step["outcome"]) {
            failures.push(format!("{label}: {difference}"));
        }
        let files = tree(&storage);
        if let Some(size) = step.get("tree_size") {
            if files.len() as u64 != size.as_u64().unwrap() {
                failures.push(format!("{label}: {} files, Python {size}", files.len()));
            }
        } else {
            let got: Vec<Json> =
                files.into_iter().map(|(path, digest)| serde_json::json!([path, digest])).collect();
            if let Some(difference) = json_difference(&Json::Array(got), &step["tree"]) {
                failures.push(format!("{label} files: {difference}"));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&scratch);
    assert!(failures.is_empty(), "{} lifecycle steps differ:\n{}", failures.len(), failures.join("\n"));
}
