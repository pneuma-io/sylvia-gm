//! `sylvia-compare`: prove that two Sylvia owners behave the same.
//!
//! One scripted session is run against an owner executable. Everything observable is recorded:
//! every reply on the command protocol, every request sent to the model (through a stand-in
//! service), the database afterwards and the files left in the data folder. A recording from
//! the frozen Python owner is the reference; the Rust owner must produce the same recording
//! apart from the differences a session declares (times, run-specific identifiers).
//!
//! ```text
//! sylvia-compare record --session FILE --out FOLDER --owner PROGRAM [--arg A]... [--env K=V]...
//! sylvia-compare check  --session FILE --reference RECORDING --owner PROGRAM [--arg A]... [--env K=V]...
//!                       [--work FOLDER] [--limit N]
//! sylvia-compare diff   --session FILE REFERENCE CANDIDATE [--limit N]
//! ```
//!
//! `record` writes `FOLDER/<session name>.recording.json` and leaves the owner's data in
//! `FOLDER/<session name>.work`. `check` records into a scratch folder and compares with the
//! reference; it exits 1 when they differ. See `docs/rust-port/comparison-tool.md`.

mod owner;
mod session;
mod standin;
mod webui;

use owner::OwnerCommand;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_compare::diff;

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match run(&arguments) {
        Ok(code) => code,
        Err(message) => {
            eprintln!("sylvia-compare: {message}");
            ExitCode::from(2)
        }
    }
}

#[derive(Default)]
struct Options {
    session: Option<PathBuf>,
    out: Option<PathBuf>,
    reference: Option<PathBuf>,
    work: Option<PathBuf>,
    owner: OwnerCommand,
    limit: usize,
    positional: Vec<PathBuf>,
}

fn parse(arguments: &[String]) -> Result<Options, String> {
    let mut options = Options { limit: 40, ..Options::default() };
    let mut index = 0;
    let value = |index: &mut usize| -> Result<String, String> {
        *index += 1;
        arguments.get(*index).cloned().ok_or(format!("{} needs a value", arguments[*index - 1]))
    };
    while index < arguments.len() {
        match arguments[index].as_str() {
            "--session" => options.session = Some(value(&mut index)?.into()),
            "--out" => options.out = Some(value(&mut index)?.into()),
            "--reference" => options.reference = Some(value(&mut index)?.into()),
            "--work" => options.work = Some(value(&mut index)?.into()),
            "--owner" => options.owner.program = value(&mut index)?,
            "--arg" => options.owner.args.push(value(&mut index)?),
            "--env" => {
                let pair = value(&mut index)?;
                let (key, item) = pair.split_once('=').ok_or(format!("--env needs KEY=VALUE, got {pair}"))?;
                options.owner.env.push((key.to_owned(), item.to_owned()));
            }
            "--limit" => {
                options.limit =
                    value(&mut index)?.parse().map_err(|_| "--limit needs a number".to_owned())?;
            }
            flag if flag.starts_with("--") => return Err(format!("unknown option {flag}")),
            path => options.positional.push(path.into()),
        }
        index += 1;
    }
    Ok(options)
}

fn run(arguments: &[String]) -> Result<ExitCode, String> {
    let Some(command) = arguments.first() else {
        return Err("expected record, check or diff (see docs/rust-port/comparison-tool.md)".into());
    };
    let options = parse(&arguments[1..])?;
    let session_path = options.session.clone().ok_or("--session is required")?;
    let session = session::load(&session_path)?;
    let rules = diff::Rules::defaults().merged(session.rules.clone());
    match command.as_str() {
        "record" => {
            require_owner(&options)?;
            let out = options.out.clone().ok_or("--out is required")?;
            std::fs::create_dir_all(&out)
                .map_err(|error| format!("could not create {}: {error}", out.display()))?;
            let recording =
                session::record(&session, &options.owner, &out.join(format!("{}.work", session.name)))?;
            let recording = diff::scrub(&recording, &session.rules)?;
            let path = out.join(format!("{}.recording.json", session.name));
            write_recording(&path, &recording)?;
            if let Some(stopped) = recording.get("stopped").and_then(Json::as_str) {
                return Err(format!("{} did not run to the end: {stopped}", session.name));
            }
            println!("{}: {}", session.name, session.description);
            println!("recorded {} ({})", path.display(), summary(&recording));
            Ok(ExitCode::SUCCESS)
        }
        "check" => {
            require_owner(&options)?;
            let reference_path = options.reference.clone().ok_or("--reference is required")?;
            let reference = read_recording(&reference_path)?;
            let work = options.work.clone().unwrap_or_else(|| {
                std::env::temp_dir().join(format!("sylvia-compare-{}-{}", session.name, std::process::id()))
            });
            let candidate = session::record(&session, &options.owner, &work)?;
            write_recording(&work.join("candidate.recording.json"), &candidate)?;
            report(&session.name, &reference, &candidate, &rules, options.limit)
        }
        "diff" => {
            let [reference, candidate] = options.positional.as_slice() else {
                return Err("diff needs a reference recording and a candidate recording".into());
            };
            report(
                &session.name,
                &read_recording(reference)?,
                &read_recording(candidate)?,
                &rules,
                options.limit,
            )
        }
        other => Err(format!("unknown command {other}")),
    }
}

/// Differences are counted up to this many; only `--limit` of them are printed.
const COUNT_LIMIT: usize = 100_000;

fn require_owner(options: &Options) -> Result<(), String> {
    if options.owner.program.is_empty() {
        return Err("--owner is required".into());
    }
    Ok(())
}

fn summary(recording: &Json) -> String {
    let count = |key: &str| recording.get(key).and_then(Json::as_array).map_or(0, Vec::len);
    let tables =
        recording.pointer("/database/tables").and_then(Json::as_object).map_or(0, serde_json::Map::len);
    format!("{} steps, {} model requests, {} tables", count("steps"), count("model_requests"), tables)
}

fn report(
    name: &str,
    reference: &Json,
    candidate: &Json,
    rules: &diff::Rules,
    limit: usize,
) -> Result<ExitCode, String> {
    let reference = diff::normalise(reference, rules)?;
    let candidate = diff::normalise(candidate, rules)?;
    // Count far past what is printed, so the number is the real amount of work left.
    let differences = diff::compare(&reference, &candidate, COUNT_LIMIT);
    if differences.is_empty() {
        println!("{name}: same ({})", summary(&candidate));
        return Ok(ExitCode::SUCCESS);
    }
    if let Some(stopped) = candidate.get("stopped").and_then(Json::as_str) {
        println!("{name}: STOPPED EARLY, {stopped}");
    }
    let mut sections = std::collections::BTreeMap::<String, usize>::new();
    for difference in &differences {
        let section = difference.pointer.split('/').nth(1).unwrap_or("").to_owned();
        *sections.entry(section).or_default() += 1;
    }
    let more = if differences.len() >= COUNT_LIMIT {
        format!("at least {COUNT_LIMIT}")
    } else {
        differences.len().to_string()
    };
    println!("{name}: DIFFERENT, {more} differences {sections:?}");
    for difference in differences.iter().take(limit) {
        println!(
            "  {}\n    reference: {}\n    candidate: {}",
            difference.pointer, difference.reference, difference.candidate
        );
    }
    Ok(ExitCode::from(1))
}

fn read_recording(path: &Path) -> Result<Json, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("could not read {}: {error}", path.display()))?;
    serde_json::from_str(&text).map_err(|error| format!("{} is not a recording: {error}", path.display()))
}

fn write_recording(path: &Path, recording: &Json) -> Result<(), String> {
    // Indented by one space, UTF-8, stable: recordings are reviewed in pull requests, and a
    // wider indent triples their size.
    let options = Dump { indent: Some(1), ..Dump::INDENT2.ascii(false) };
    let mut text = pyjson::dumps(recording, options);
    text.push('\n');
    std::fs::write(path, text).map_err(|error| format!("could not write {}: {error}", path.display()))
}
