//! `sylvia_base::winreg` against `vectors/generate_winreg_vectors.py` (Windows only): the
//! same raw values are written with `reg import` into a scratch current-user key, then read
//! through the real registry calls, and each result must be Python's `winreg.QueryValueEx`.

#![cfg(windows)]

use serde_json::Value as J;
use sylvia_base::winreg::{query_value_ex, Error, Key, Root, Value, View, REG_DWORD, REG_EXPAND_SZ, REG_SZ};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/winreg_vectors.json")).expect("valid JSON")
}

struct Scratch(String);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ =
            std::process::Command::new("reg").args(["delete", &format!(r"HKCU\{}", self.0), "/f"]).output();
    }
}

fn import(subkey: &str, values: &[J]) {
    let mut lines = vec![
        "Windows Registry Editor Version 5.00".to_string(),
        String::new(),
        format!(r"[HKEY_CURRENT_USER\{subkey}]"),
    ];
    for case in values.iter().filter(|case| !case["kind"].is_null()) {
        let name = case["name"].as_str().unwrap();
        let label = if name.is_empty() { "@".to_string() } else { J::String(name.into()).to_string() };
        let data = case["data"].as_str().unwrap();
        let bytes: Vec<&str> = (0..data.len()).step_by(2).map(|index| &data[index..index + 2]).collect();
        lines.push(format!("{label}=hex({:x}):{}", case["kind"].as_u64().unwrap(), bytes.join(",")));
    }
    let text = lines.join("\r\n") + "\r\n";
    let file = std::env::temp_dir().join(format!("sylvia-winreg-{}.reg", std::process::id()));
    let encoded: Vec<u8> =
        [0xFF, 0xFE].into_iter().chain(text.encode_utf16().flat_map(u16::to_le_bytes)).collect();
    std::fs::write(&file, encoded).unwrap();
    let imported = std::process::Command::new("reg").arg("import").arg(&file).output().unwrap();
    let _ = std::fs::remove_file(&file);
    assert!(imported.status.success(), "{}", String::from_utf8_lossy(&imported.stderr));
}

/// Python's result as the port reports it: strings and numbers of the types Sylvia reads,
/// other types refused by type, an `OSError` by its `winerror`.
fn expected(result: &J) -> Result<Value, Error> {
    if let Some(code) = result.get("error") {
        return Err(Error::Os(code.as_i64().unwrap() as i32));
    }
    let kind = result["kind"].as_u64().unwrap() as u32;
    let units =
        || result["units"].as_array().unwrap().iter().map(|unit| unit.as_u64().unwrap() as u16).collect();
    match kind {
        REG_SZ => Ok(Value::Sz(units())),
        REG_EXPAND_SZ => Ok(Value::ExpandSz(units())),
        REG_DWORD => Ok(Value::Dword(result["number"].as_u64().unwrap() as u32)),
        other => Err(Error::OtherType(other)),
    }
}

#[test]
fn values_read_like_python_winreg() {
    let vectors = vectors();
    let subkey = format!(r"Software\sylvia-gm-port-tests\winreg-test-{}", std::process::id());
    let _scratch = Scratch(subkey.clone());
    let values = vectors["values"].as_array().unwrap();
    import(&subkey, values);
    for view in [View::Default, View::Wow64_64] {
        let key = Key::open(Root::CurrentUser, &subkey, view).unwrap();
        for case in values {
            let name = case["name"].as_str().unwrap();
            assert_eq!(key.query_value_ex(name), expected(&case["result"]), "{name}");
            if let (Some(kind), Some(data)) = (case["kind"].as_u64(), case["data"].as_str()) {
                // The stored bytes come back first; Windows adds a NUL to a string stored
                // without one, which CPython sees the same way.
                let (returned_kind, returned) = key.query_raw(name).unwrap();
                assert_eq!(returned_kind, kind as u32, "{name}");
                assert!(returned.starts_with(&hex::decode(data).unwrap()), "{name}: {returned:?}");
            }
        }
    }
    assert_eq!(query_value_ex(Root::CurrentUser, &subkey, "ProxyEnable"), Ok(Value::Dword(1)));
    let missing = query_value_ex(Root::CurrentUser, &format!(r"{subkey}\absent"), "");
    assert_eq!(missing, expected(&vectors["missing_key"]));
    assert!(missing.unwrap_err().is_not_found());
}
