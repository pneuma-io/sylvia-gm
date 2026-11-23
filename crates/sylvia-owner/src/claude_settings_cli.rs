//! `sylvia-gm-owner claude-settings`: `config/claude_settings.py`'s `main()`, the headless set-up
//! of Sylvia's own Claude Code copy that SylviaBench runs (`python -m
//! sylvia_gm.config.claude_settings --config OWNER/config.yaml ...`).
//!
//! The arguments follow the `argparse` parser Python builds: the same options, prefixes of long
//! options (`allow_abbrev`), `--option=value`, the order errors are found in, and the exact
//! error texts, usage and help. The program name in them is `sylvia-gm-owner claude-settings`
//! where Python prints `claude_settings.py` (differences row NEXT), and the usage is wrapped for
//! 80 columns, which Python uses when `COLUMNS` is unset. The actions, their output (`json.dumps
//! (result, indent=2)` on stdout, progress and messages on stderr) and the exit codes (0, 1 for
//! a refused action or a failed install, 2 for a usage error) are Python's.

use std::io::Write;
use std::path::{Path, PathBuf};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::config::claude_settings::{claude_settings_action, ClaudeSettingsError};

/// The program name in the usage and errors.
pub const PROG: &str = "sylvia-gm-owner claude-settings";

/// `parser.format_usage()` for [`PROG`] at 80 columns.
pub const USAGE: &str = "usage: sylvia-gm-owner claude-settings [-h] --config CONFIG [--install]
                                       [--login] [--console] [--logout]
                                       [--model MODEL]
                                       [--context-window CONTEXT_WINDOW]
";

/// `parser.format_help()` after the usage.
pub const HELP: &str = "
Set up Sylvia's own copy of Claude Code in a Sylvia data directory

options:
  -h, --help            show this help message and exit
  --config CONFIG       Sylvia config.yaml in its owner data directory
  --install             download or update Sylvia's copy of Claude Code
  --login               sign in through Claude Code's browser page
  --console             with --login, use an Anthropic Console account
  --logout              sign Sylvia's copy out
  --model MODEL
  --context-window CONTEXT_WINDOW
";

/// The parser's options, in the order `add_argument` declared them: their strings, the
/// error name `argparse` gives them, and whether they take a value.
const OPTIONS: [(&[&str], &str, bool); 8] = [
    (&["-h", "--help"], "-h/--help", false),
    (&["--config"], "--config", true),
    (&["--install"], "--install", false),
    (&["--login"], "--login", false),
    (&["--console"], "--console", false),
    (&["--logout"], "--logout", false),
    (&["--model"], "--model", true),
    (&["--context-window"], "--context-window", true),
];

/// What `parse_args` produced.
#[derive(Debug, Default, PartialEq)]
pub struct Arguments {
    pub config: Option<String>,
    pub install: bool,
    pub login: bool,
    pub console: bool,
    pub logout: bool,
    pub model: Option<String>,
    pub context_window: Option<i64>,
}

/// How parsing ended without arguments to run.
#[derive(Debug, PartialEq)]
pub enum Stop {
    /// `-h`: the help on stdout, exit 0.
    Help,
    /// `parser.error(message)`: the usage and `PROG: error: message` on stderr, exit 2.
    Error(String),
}

/// `_negative_number_matcher`: `^-\d+$|^-\d*\.\d+$`. No option looks like a negative number,
/// so such a string is an argument, not an option.
fn negative_number(text: &str) -> bool {
    let Some(rest) = text.strip_prefix('-') else { return false };
    let digits = |part: &str| part.chars().all(|c| c.is_ascii_digit());
    if !rest.is_empty() && digits(rest) {
        return true;
    }
    match rest.split_once('.') {
        Some((whole, fraction)) => digits(whole) && !fraction.is_empty() && digits(fraction),
        None => false,
    }
}

/// One classified command-line word: an option (its index in [`OPTIONS`], any value given with
/// it, and whether that value was attached to a single-dash option without `=`), an unknown
/// option, or an argument.
#[derive(Clone)]
enum Word {
    Option(usize, Option<String>, bool),
    /// A prefix of more than one long option: an error when parsing reaches it.
    Ambiguous(String),
    Unknown,
    Argument,
}

/// `_parse_optional(arg_string)`.
fn classify(word: &str) -> Result<Word, Stop> {
    if !word.starts_with('-') || word == "-" {
        return Ok(Word::Argument);
    }
    let exact = |text: &str| OPTIONS.iter().position(|(strings, _, _)| strings.contains(&text));
    if let Some(index) = exact(word) {
        return Ok(Word::Option(index, None, false));
    }
    if word.len() == 1 {
        return Ok(Word::Argument);
    }
    if let Some((name, value)) = word.split_once('=') {
        if let Some(index) = exact(name) {
            return Ok(Word::Option(index, Some(value.to_owned()), false));
        }
    }
    // `_get_option_tuples`: a long option's unique prefix (with or without `=value`).
    if word.starts_with("--") {
        let (prefix, value) = match word.split_once('=') {
            Some((prefix, value)) => (prefix, Some(value.to_owned())),
            None => (word, None),
        };
        let matches: Vec<(usize, &str)> = OPTIONS
            .iter()
            .enumerate()
            .flat_map(|(index, (strings, _, _))| strings.iter().map(move |text| (index, *text)))
            .filter(|(_, text)| text.starts_with(prefix))
            .collect();
        if matches.len() > 1 {
            let names: Vec<&str> = matches.iter().map(|(_, text)| *text).collect();
            return Ok(Word::Ambiguous(format!("ambiguous option: {word} could match {}", names.join(", "))));
        }
        if let Some((index, _)) = matches.first() {
            return Ok(Word::Option(*index, value, false));
        }
    } else if let Some(index) = exact(&word[..2]) {
        // A single-dash option with its value attached (`-hX`).
        return Ok(Word::Option(index, Some(word[2..].to_owned()), true));
    }
    if negative_number(word) || word.contains(' ') {
        return Ok(Word::Argument);
    }
    Ok(Word::Unknown)
}

/// `parser.parse_args(words)`.
pub fn parse(words: &[String]) -> Result<Arguments, Stop> {
    // The first pass classifies every word; an ambiguous prefix is reported when it is reached.
    let mut classes = Vec::with_capacity(words.len());
    let mut rest = false;
    for word in words {
        if rest {
            classes.push(Word::Argument);
        } else if word == "--" {
            rest = true;
            classes.push(Word::Argument);
        } else {
            classes.push(classify(word)?);
        }
    }
    let mut parsed = Arguments::default();
    let mut extras: Vec<String> = Vec::new();
    let mut index = 0;
    while index < words.len() {
        let word = &words[index];
        match &classes[index] {
            Word::Ambiguous(message) => return Err(Stop::Error(message.clone())),
            // With no positional arguments, `--` and everything else stays unrecognized.
            Word::Argument | Word::Unknown => {
                extras.push(word.clone());
                index += 1;
            }
            Word::Option(option, explicit, attached) => {
                let (_, name, takes_value) = OPTIONS[*option];
                index += 1;
                let value = if takes_value {
                    match explicit {
                        Some(value) => value.clone(),
                        None => match (words.get(index), classes.get(index)) {
                            (Some(next), Some(Word::Argument)) if next != "--" => {
                                index += 1;
                                next.clone()
                            }
                            _ => return Err(Stop::Error(format!("argument {name}: expected one argument"))),
                        },
                    }
                } else {
                    // `-hX` runs `-h` (CPython 3.12 tries `-X` next and gives up silently);
                    // `-h=X`, `-h-X` and `--flag=X` are refused.
                    if let (Some(value), true) = (explicit, *attached) {
                        if !value.is_empty() && !value.starts_with('-') && name == "-h/--help" {
                            return Err(Stop::Help);
                        }
                    }
                    if let Some(value) = explicit {
                        return Err(Stop::Error(format!(
                            "argument {name}: ignored explicit argument {}",
                            sylvia_base::pystr::repr(value)
                        )));
                    }
                    String::new()
                };
                match name {
                    "-h/--help" => return Err(Stop::Help),
                    "--config" => parsed.config = Some(value),
                    "--install" => parsed.install = true,
                    "--login" => parsed.login = true,
                    "--console" => parsed.console = true,
                    "--logout" => parsed.logout = true,
                    "--model" => parsed.model = Some(value),
                    "--context-window" => match sylvia_base::pystr::parse_int(&value) {
                        Ok(number) => parsed.context_window = Some(number),
                        Err(sylvia_base::pystr::IntParseError::OutOfRange { .. }) => {
                            return Err(Stop::Error(format!(
                                "argument --context-window: {} is beyond the Rust owner's 64-bit integer limit",
                                sylvia_base::pystr::repr(&value)
                            )))
                        }
                        Err(_) => {
                            return Err(Stop::Error(format!(
                                "argument --context-window: invalid int value: {}",
                                sylvia_base::pystr::repr(&value)
                            )))
                        }
                    },
                    _ => unreachable!("every option is handled"),
                }
            }
        }
    }
    if parsed.config.is_none() {
        return Err(Stop::Error("the following arguments are required: --config".into()));
    }
    if !extras.is_empty() {
        return Err(Stop::Error(format!("unrecognized arguments: {}", extras.join(" "))));
    }
    Ok(parsed)
}

/// `claude_settings_action(root, action, options)` with the owner's configuration sources and
/// Sylvia's installer.
fn action(root: &Path, action: &str, options: Json) -> Result<JsonMap, ClaudeSettingsError> {
    let default_config = sylvia_provider::config::default_config_path();
    let env = sylvia_provider::config::env::Environment::current();
    let sources = sylvia_provider::config::provider_settings::ConfigSources {
        default_config: &default_config,
        seal: &sylvia_provider::config::protection::PlatformSeal,
        env: &env,
    };
    claude_settings_action(
        sources,
        root,
        &Json::from(action),
        &options,
        &crate::commands::providers::claude_install_system,
        &sylvia_provider::config::provider_settings::now_isoformat,
    )
}

/// Text as Python's `sys.stdout` or `sys.stderr` writes it: `\n` becomes `\r\n` on Windows,
/// and stderr is flushed at once.
fn translated(text: &str) -> std::borrow::Cow<'_, str> {
    if cfg!(windows) {
        std::borrow::Cow::Owned(text.replace('\n', "\r\n"))
    } else {
        std::borrow::Cow::Borrowed(text)
    }
}

/// `print(..., end="")` on stdout.
fn out(text: &str) {
    let mut output = std::io::stdout();
    let _ = output.write_all(translated(text).as_bytes());
    let _ = output.flush();
}

/// `print(..., end="", file=sys.stderr)`.
fn err(text: &str) {
    let mut error = std::io::stderr();
    let _ = error.write_all(translated(text).as_bytes());
    let _ = error.flush();
}

/// `int(value or 0)` for a progress count.
fn count(value: Option<&Json>) -> i64 {
    value.and_then(Json::as_i64).unwrap_or(0)
}

/// `_wait(storage_root, step)`: poll the status until an install or a sign-in has finished,
/// showing the download's progress on stderr.
fn wait(root: &Path, step: &str) -> Result<JsonMap, ClaudeSettingsError> {
    loop {
        let status = action(root, "status", Json::Null)?;
        let install = status.get("install").and_then(Json::as_object).cloned().unwrap_or_default();
        let state = install.get("state").and_then(Json::as_str).unwrap_or_default();
        if step == "install" && ["checking", "downloading", "verifying"].contains(&state) {
            let version = match install.get("version") {
                Some(Json::String(version)) if !version.is_empty() => version.clone(),
                _ => String::new(),
            };
            let megabytes = |key: &str| count(install.get(key)).div_euclid(1024 * 1024);
            err(&format!(
                "\rClaude Code {version}: {state}, {} of {} MB   ",
                megabytes("received"),
                megabytes("total")
            ));
        } else if step != "login" || !status.get("login_pending").is_some_and(sylvia_provider::pyobj::truthy)
        {
            return Ok(status);
        }
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
}

/// `str(value)` for the message a failed install reports.
fn message_text(value: Option<&Json>) -> String {
    match value {
        Some(Json::String(text)) => text.clone(),
        None | Some(Json::Null) => "None".into(),
        Some(other) => sylvia_provider::pyobj::py_str(other),
    }
}

/// Run the set-up the arguments ask for and return the exit code.
fn run(arguments: Arguments) -> i32 {
    let config = PathBuf::from(arguments.config.unwrap_or_default());
    if config.file_name().and_then(|name| name.to_str()) != Some("config.yaml") {
        err(&format!(
            "{USAGE}{PROG}: error: --config must name config.yaml in the selected owner directory\n"
        ));
        return 2;
    }
    let root = match config.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent.to_path_buf(),
        _ => PathBuf::from("."),
    };
    let outcome = (|| -> Result<Result<JsonMap, i32>, ClaudeSettingsError> {
        if arguments.install {
            action(&root, "install", Json::Null)?;
            let status = wait(&root, "install")?;
            let install = status.get("install").and_then(Json::as_object).cloned().unwrap_or_default();
            err("\n");
            if install.get("state").and_then(Json::as_str) != Some("ready") {
                err(&format!("{}\n", message_text(install.get("message"))));
                return Ok(Err(1));
            }
        }
        if arguments.logout {
            action(&root, "logout", Json::Null)?;
        }
        if arguments.login {
            let account = if arguments.console { "api" } else { "subscription" };
            action(&root, "login", serde_json::json!({ "account_type": account }))?;
            err("Finish sign-in in the browser page Claude Code opened.\n");
            wait(&root, "login")?;
        }
        match arguments.model {
            Some(model) if !model.is_empty() => {
                let model = serde_json::json!({
                    "id": model,
                    "context_window": arguments.context_window,
                    "max_output_tokens": null,
                });
                Ok(Ok(action(&root, "add_model", serde_json::json!({ "model": model }))?))
            }
            _ => Ok(Ok(action(&root, "status", Json::Null)?)),
        }
    })();
    let code = match outcome {
        Ok(Ok(result)) => {
            let options = sylvia_base::pyjson::Dump {
                item_separator: ",",
                key_separator: ": ",
                indent: Some(2),
                ..sylvia_base::pyjson::Dump::DEFAULT
            };
            out(&format!("{}\n", sylvia_base::pyjson::dumps(&Json::Object(result), options)));
            0
        }
        Ok(Err(code)) => code,
        Err(ClaudeSettingsError::Connection(message)) => {
            err(&format!("{message}\n"));
            1
        }
        // Python prints the uncaught exception's traceback and exits 1.
        Err(ClaudeSettingsError::Config(failure)) => {
            err(&format!("Sylvia GM: the Claude settings could not be saved: {failure:?}\n"));
            1
        }
        Err(ClaudeSettingsError::Internal(message)) => {
            err(&format!("Sylvia GM: {message}\n"));
            1
        }
    };
    // Python's `atexit` hook: a sign-in still pending is cancelled.
    sylvia_provider::config::claude_settings::cancel_all();
    code
}

/// `main()` for the words after `claude-settings`; returns the exit code.
pub fn main(words: &[std::ffi::OsString]) -> i32 {
    let words: Vec<String> = words.iter().map(|word| word.to_string_lossy().into_owned()).collect();
    match parse(&words) {
        Ok(arguments) => run(arguments),
        Err(Stop::Help) => {
            out(&format!("{USAGE}{HELP}"));
            0
        }
        Err(Stop::Error(message)) => {
            err(&format!("{USAGE}{PROG}: error: {message}\n"));
            2
        }
    }
}
