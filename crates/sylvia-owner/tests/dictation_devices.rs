//! The device-id migration on this machine's own devices: every id the Python worker lists
//! (`vectors/list_portaudio_devices.py`, written outside the checkout) against the Rust worker's
//! cpal list. Metadata only: no stream is opened and nothing is recorded. Windows, `dictation`
//! feature, never part of check.sh (see the dictation check in docs/rust-port/README.md).
//!
//! Needs `SYLVIA_DICTATION_PORTAUDIO_DEVICES`, the generator's output. Each PortAudio input
//! under MME, DirectSound or WASAPI must select the cpal endpoint with the same name (MME's cut
//! to 31 characters), PortAudio's own default entries must follow the system default, and no
//! WDM-KS id may select anything (those players choose again).

use serde_json::Value as J;

#[cfg(windows)]
fn check(listed: &[J]) -> Vec<String> {
    use sylvia_owner::dictation::capture::CpalSound;
    use sylvia_owner::dictation::{input_devices, migrated};

    let mut sound = CpalSound::default();
    let options = match input_devices(&mut sound) {
        Ok(options) => options,
        Err(failure) => return vec![format!("cpal list: {failure:?}")],
    };
    for option in &options {
        println!("rust  {} {} ({} previous)", option.id, option.label, option.previous.len());
    }
    let label_name = |label: &str| label.strip_suffix(" (Windows WASAPI)").map(str::to_string);
    let mut failures = Vec::new();
    let mut checked = 0;
    for device in listed {
        let id = device["id"].as_str().unwrap();
        if id == "default" {
            continue;
        }
        let name = device["name"].as_str().unwrap_or("");
        let host = device["host"].as_str().unwrap_or("");
        let found = migrated(&options, id);
        let expected: Vec<&str> = match host {
            _ if name == "Microsoft Sound Mapper - Input" || name == "Primary Sound Capture Driver" => {
                vec!["default"]
            }
            "MME" => options
                .iter()
                .filter(|option| {
                    label_name(&option.label)
                        .is_some_and(|full| full == name || full.chars().take(31).collect::<String>() == name)
                })
                .map(|option| option.id.as_str())
                .collect(),
            "Windows DirectSound" | "Windows WASAPI" => options
                .iter()
                .filter(|option| label_name(&option.label).as_deref() == Some(name))
                .map(|option| option.id.as_str())
                .collect(),
            _ => Vec::new(),
        };
        let got = found.map(|option| option.id.as_str());
        let ok = match expected.as_slice() {
            [one] => got == Some(*one),
            // Not listed by cpal (a disabled endpoint) or ambiguous: nothing is selected.
            _ => got.is_none(),
        };
        println!("{} {host}: {id} -> {}", if ok { "ok  " } else { "FAIL" }, got.unwrap_or("(choose again)"));
        if !ok {
            failures.push(format!("{host} {id}: expected {expected:?}, got {got:?}"));
        }
        checked += 1;
    }
    if checked == 0 {
        failures.push("the PortAudio list has no inputs".into());
    }
    failures
}

fn main() {
    let Some(path) = std::env::var_os("SYLVIA_DICTATION_PORTAUDIO_DEVICES") else {
        eprintln!("dictation_devices: set SYLVIA_DICTATION_PORTAUDIO_DEVICES");
        std::process::exit(1)
    };
    let listed: Vec<J> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    #[cfg(not(windows))]
    {
        let _ = listed;
        println!("dictation_devices: Windows only");
    }
    #[cfg(windows)]
    {
        let failures = check(&listed);
        for failure in &failures {
            println!("FAIL {failure}");
        }
        println!("devices: {}", if failures.is_empty() { "ok" } else { "FAILED" });
        std::process::exit(i32::from(!failures.is_empty()));
    }
}
