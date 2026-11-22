//! The Windows registry proxy read against `vectors/generate_registry_proxy_vectors.py` (Windows
//! only). Each case's `ProxyEnable` and `ProxyServer`, of any registry type, are written with
//! `reg import` into a subkey of a scratch current-user key, read through the real registry calls,
//! and the proxies must be the ones Python's `urllib.request.getproxies_registry()` returned. The
//! scratch key is deleted in both registry views, and the deletion is checked. Nothing reads or
//! changes the real Internet Settings.

#![cfg(windows)]

use serde_json::Value as J;
use sylvia_provider::proxy::{getproxies_registry, read_registry};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/registry_proxy_vectors.json")).expect("valid JSON")
}

/// Delete a scratch current-user key in both registry views; `true` once it is gone in both.
fn delete_scratch_key(subkey: &str) -> bool {
    let key = format!(r"HKCU\{subkey}");
    for view in ["/reg:64", "/reg:32"] {
        // A view where the key is already gone answers 1; the query below is the check.
        let _ = std::process::Command::new("reg").args(["delete", &key, "/f", view]).output();
    }
    ["/reg:64", "/reg:32"].iter().all(|view| {
        std::process::Command::new("reg")
            .args(["query", &key, view])
            .output()
            .is_ok_and(|output| !output.status.success())
    })
}

/// Removes the scratch key even when the test fails part-way; the test also checks it is gone.
struct Scratch(String);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = delete_scratch_key(&self.0);
    }
}

fn import(root: &str, cases: &[J]) {
    let mut lines = vec!["Windows Registry Editor Version 5.00".to_string(), String::new()];
    for (index, case) in cases.iter().enumerate() {
        lines.push(format!(r"[HKEY_CURRENT_USER\{root}\case{index}]"));
        for value in case["values"].as_array().unwrap() {
            let data = value["data"].as_str().unwrap();
            let bytes: Vec<&str> = (0..data.len()).step_by(2).map(|at| &data[at..at + 2]).collect();
            lines.push(format!(
                "\"{}\"=hex({:x}):{}",
                value["name"].as_str().unwrap(),
                value["kind"].as_u64().unwrap(),
                bytes.join(",")
            ));
        }
        lines.push(String::new());
    }
    let text = lines.join("\r\n") + "\r\n";
    let file = std::env::temp_dir().join(format!("sylvia-registry-proxies-{}.reg", std::process::id()));
    let encoded: Vec<u8> =
        [0xFF, 0xFE].into_iter().chain(text.encode_utf16().flat_map(u16::to_le_bytes)).collect();
    std::fs::write(&file, encoded).unwrap();
    let imported = std::process::Command::new("reg").arg("import").arg(&file).output().unwrap();
    let _ = std::fs::remove_file(&file);
    assert!(imported.status.success(), "{}", String::from_utf8_lossy(&imported.stderr));
}

fn pairs(value: &J) -> Vec<(String, String)> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned()))
        .collect()
}

#[test]
fn registry_proxies_of_every_value_type_match_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    // A key of its own directly under Software, so deleting it leaves no parent behind.
    let root = format!(r"Software\sylvia-gm-port-test-registry-proxies-{}", std::process::id());
    let scratch = Scratch(root.clone());
    import(&root, cases);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let read = read_registry(&format!(r"{root}\case{index}"));
        let proxies = getproxies_registry(read.as_ref());
        if proxies != pairs(&case["proxies"]) {
            failures.push(format!(
                "{}\n  expected {}\n  got      {proxies:?} (read {read:?})",
                case["values"], case["proxies"]
            ));
        }
    }
    let missing = getproxies_registry(read_registry(&format!(r"{root}\absent")).as_ref());
    assert_eq!(missing, pairs(&vectors["missing_key"]));
    drop(scratch);
    assert!(delete_scratch_key(&root), "the scratch key HKCU\\{root} is still there");
    assert!(failures.is_empty(), "{} of {} differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
}
