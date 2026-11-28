//! `interfaces/ruleset_test_worker.py`: the dedicated local worker for disposable Ruleset
//! scenarios, without application credentials. The owner executable runs it for
//! `--ruleset-test-worker --archive X --result Y` (lane 1's `sylvia_app::ruleset_testing` starts
//! it). It admits the exact package, runs its own saved cases in fresh campaign stores through
//! lane 1's `run_scenario`, and writes only a structured result.

use std::collections::BTreeSet;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use sylvia_agent::builder::ruleset_tests::{run_scenario, summarize_coverage};
use sylvia_app::package_runtime::{ruleset_test_runtime_facts, PackageRuntimeFailure, PACKAGE_RUNTIME_PATH};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::PackageRole;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::closure_sha256;
use sylvia_domain::pyops::py_truthy;
use sylvia_domain::ruleset_tests::{parse_ruleset_tests, RULESET_TESTS_PATH};

/// `args.archive.stat().st_size > 128 * 1024 * 1024`.
const ARCHIVE_LIMIT: u64 = 128 * 1024 * 1024;

/// The coverage keys whose any entry makes passing scenarios `incomplete_coverage`.
const COVERAGE_GAPS: [&str; 6] = [
    "missing_actions",
    "unproven_claims",
    "missing_rejections",
    "missing_interaction",
    "missing_preconditions",
    "missing_handoff",
];

/// An exception `run_archive` lets escape: only its class (`type(error).__name__`) is reported.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Raised(pub &'static str);

impl From<sylvia_domain::DomainError> for Raised {
    fn from(error: sylvia_domain::DomainError) -> Self {
        Self(error.python_class())
    }
}

impl From<PackageRuntimeFailure> for Raised {
    fn from(failure: PackageRuntimeFailure) -> Self {
        Self(match failure {
            PackageRuntimeFailure::Rejected(_) => "PackageRuntimeError",
            PackageRuntimeFailure::Uncaught { class, .. } => class,
        })
    }
}

/// `type(error).__name__` of the `OSError` Python raises for this failure.
fn os_class(error: &std::io::Error) -> &'static str {
    match error.kind() {
        std::io::ErrorKind::NotFound => "FileNotFoundError",
        std::io::ErrorKind::PermissionDenied => "PermissionError",
        std::io::ErrorKind::AlreadyExists => "FileExistsError",
        std::io::ErrorKind::IsADirectory => "IsADirectoryError",
        std::io::ErrorKind::NotADirectory => "NotADirectoryError",
        _ => "OSError",
    }
}

impl From<std::io::Error> for Raised {
    fn from(error: std::io::Error) -> Self {
        Self(os_class(&error))
    }
}

/// `tempfile.TemporaryDirectory(prefix="scenario-", dir=root)`: a fresh private folder, removed
/// by [`ScenarioFolder::cleanup`] (whose failure Python raises).
struct ScenarioFolder(PathBuf);

impl ScenarioFolder {
    fn new(root: &Path) -> Result<Self, Raised> {
        const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
        for _ in 0..100 {
            let mut random = [0u8; 8];
            getrandom::getrandom(&mut random).expect("the system random source");
            let name: String =
                random.iter().map(|byte| CHARACTERS[usize::from(*byte) % CHARACTERS.len()] as char).collect();
            let path = root.join(format!("scenario-{name}"));
            #[cfg_attr(not(unix), allow(unused_mut))]
            let mut builder = std::fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&path) {
                Ok(()) => return Ok(Self(path)),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(Raised("FileExistsError"))
    }

    fn cleanup(self) -> Result<(), Raised> {
        std::fs::remove_dir_all(&self.0).map_err(Raised::from)
    }
}

/// `{"status": ..., "scenarios": [], "message": ...}`.
fn missing_tests() -> JsonMap {
    let mut result = JsonMap::new();
    result.insert("status".into(), "missing_tests".into());
    result.insert("scenarios".into(), Json::Array(Vec::new()));
    result.insert("message".into(), "The Ruleset has no saved test scenarios.".into());
    result
}

/// `run_archive(archive, root)`: admit the exact package and run its own saved cases in fresh
/// campaign stores under `root`.
pub async fn run_archive(archive: &[u8], root: &Path) -> Result<JsonMap, Raised> {
    let admitted = admit_package_archive(archive)?;
    // `next(manifest for manifest in admitted.manifests if manifest.role.value == "primary")`.
    let Some(primary_index) =
        admitted.manifests.iter().position(|manifest| manifest.role == PackageRole::Primary)
    else {
        return Err(Raised("StopIteration"));
    };
    let primary = &admitted.manifests[primary_index];
    let files: Vec<_> = admitted
        .files
        .iter()
        .filter(|item| {
            admitted.manifests[item.manifest].package_id == primary.package_id
                && item.file.path() == RULESET_TESTS_PATH
        })
        .collect();
    let [tests] = files.as_slice() else {
        return Ok(missing_tests());
    };
    let suite = parse_ruleset_tests(&admitted.read_file(tests)?)?;
    let identity = closure_sha256(&admitted.package_lock);
    let mut tool_names: Vec<&str> = Vec::new();
    for tool in &primary.tools {
        if !tool_names.contains(&tool.name.as_str()) {
            tool_names.push(&tool.name);
        }
    }
    let required: BTreeSet<String> = tool_names.iter().map(|name| (*name).to_owned()).collect();
    let required_rejections: BTreeSet<String> = primary
        .tools
        .iter()
        .filter(|tool| tool.input_schema.get("properties").is_some_and(py_truthy))
        .map(|tool| tool.name.clone())
        .collect();
    let Some(runtime_file) = admitted.files.iter().find(|item| {
        admitted.manifests[item.manifest].package_id == primary.package_id
            && item.file.path() == PACKAGE_RUNTIME_PATH
    }) else {
        return Err(Raised("StopIteration"));
    };
    let runtime_bytes = admitted.read_file(runtime_file)?;
    let facts = ruleset_test_runtime_facts(&primary.package_id, &runtime_bytes, &tool_names)?;
    let required_preconditions: BTreeSet<String> = facts.required_preconditions.into_iter().collect();
    // `suite["scenarios"]`: a parsed suite always has its scenario list.
    let scenarios: Vec<JsonMap> = match suite.get("scenarios") {
        Some(Json::Array(items)) => items.iter().filter_map(|item| item.as_object().cloned()).collect(),
        _ => return Err(Raised("KeyError")),
    };
    let mut results = Vec::with_capacity(scenarios.len());
    for scenario in &scenarios {
        let folder = ScenarioFolder::new(root)?;
        let result = run_scenario(archive, scenario, &folder.0).await;
        folder.cleanup()?;
        results.push(result);
    }
    let coverage = summarize_coverage(
        &required,
        &results,
        &scenarios,
        Some(&required_rejections),
        Some(&required_preconditions),
        facts.requires_handoff,
    );
    let all_passed = results.iter().all(|item| item.get("status").and_then(Json::as_str) == Some("passed"));
    let mut status = if all_passed { "passed" } else { "failed" };
    if status == "passed" && COVERAGE_GAPS.iter().any(|key| coverage.get(*key).is_some_and(py_truthy)) {
        status = "incomplete_coverage";
    }
    let mut reply = JsonMap::new();
    reply.insert("status".into(), status.into());
    reply.insert("closure_sha256".into(), identity.into());
    reply.insert("scenarios".into(), Json::Array(results.into_iter().map(Json::Object).collect()));
    reply.insert("coverage".into(), Json::Object(coverage));
    Ok(reply)
}

/// `argparse`'s refusal: the usage and the message on stderr, exit 2.
fn usage_error(message: &str) -> i32 {
    eprintln!(
        "usage: sylvia-gm-owner [-h] --archive ARCHIVE --result RESULT\nsylvia-gm-owner: error: {message}"
    );
    2
}

/// `main(argv)`: write only structured scenario results; raw execution diagnostics stay out of
/// the UI. Every exception becomes `{"status": "error", "scenarios": [], "error_type": class}`.
pub fn main(arguments: &[OsString]) -> i32 {
    let (mut archive, mut result): (Option<PathBuf>, Option<PathBuf>) = (None, None);
    let mut index = 0;
    while index < arguments.len() {
        let argument = arguments[index].to_string_lossy().into_owned();
        let (flag, inline) = match argument.split_once('=') {
            Some((flag, value)) if flag.starts_with("--") => (flag.to_owned(), Some(PathBuf::from(value))),
            _ => (argument.clone(), None),
        };
        let slot = match flag.as_str() {
            "--archive" => &mut archive,
            "--result" => &mut result,
            "-h" | "--help" => {
                println!("usage: sylvia-gm-owner [-h] --archive ARCHIVE --result RESULT");
                return 0;
            }
            _ => return usage_error(&format!("unrecognized arguments: {argument}")),
        };
        *slot = match inline {
            Some(value) => Some(value),
            None => {
                index += 1;
                match arguments.get(index) {
                    Some(value) => Some(PathBuf::from(value)),
                    None => return usage_error(&format!("argument {flag}: expected one argument")),
                }
            }
        };
        index += 1;
    }
    let (archive, result) = match (archive, result) {
        (Some(archive), Some(result)) => (archive, result),
        (None, None) => return usage_error("the following arguments are required: --archive, --result"),
        (None, _) => return usage_error("the following arguments are required: --archive"),
        (_, None) => return usage_error("the following arguments are required: --result"),
    };
    let runtime =
        tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("the worker runtime");
    let outcome = runtime.block_on(async {
        let size = std::fs::metadata(&archive)?.len();
        if size > ARCHIVE_LIMIT {
            return Err(Raised("ValueError"));
        }
        let bytes = std::fs::read(&archive)?;
        // `args.result.parent`.
        let root = result.parent().map_or_else(|| PathBuf::from("."), Path::to_path_buf);
        let root = if root.as_os_str().is_empty() { PathBuf::from(".") } else { root };
        run_archive(&bytes, &root).await
    });
    let value = match outcome {
        Ok(value) => value,
        Err(Raised(class)) => {
            let mut value = JsonMap::new();
            value.insert("status".into(), "error".into());
            value.insert("scenarios".into(), Json::Array(Vec::new()));
            value.insert("error_type".into(), class.into());
            value
        }
    };
    // `args.result.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")`: a
    // failed write escapes, as in Python (a traceback and exit 1).
    if let Err(error) = std::fs::write(&result, pyjson::dumps(&Json::Object(value), Dump::DEFAULT)) {
        eprintln!("{}: {error}", os_class(&error));
        return 1;
    }
    0
}
