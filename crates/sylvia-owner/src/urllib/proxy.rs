//! `urllib.request.ProxyHandler` for an https request, as CPython 3.12 runs it on Windows:
//! `getproxies()` (lane 4's `sylvia_provider::proxy::getproxies`), then for the request's host
//! `proxy_bypass(host)`: the environment's `no_proxy` when any `*_proxy` variable is set
//! (`proxy_bypass_environment`), otherwise the registry's `ProxyEnable` and `ProxyOverride`
//! (`proxy_bypass_registry`, `_proxy_bypass_winreg_override` with `fnmatch`); then
//! `_parse_proxy` and `unquote` of the credentials and the host and port.

use super::Failure;

use sylvia_base::pyurl;
use sylvia_provider::proxy::{getproxies, getproxies_environment, ProxySource};

/// How `proxy_bypass` decides.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Bypass {
    /// The environment's proxies (`no_proxy` among them).
    Environment(Vec<(String, String)>),
    /// The registry's `ProxyEnable` truth and `str(ProxyOverride)`, or `None` when either value
    /// cannot be read.
    Registry(Option<(bool, String)>),
}

/// The proxies of one opener.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Proxies {
    /// `getproxies()`.
    pub proxies: Vec<(String, String)>,
    /// The bypass rule.
    pub bypass: Bypass,
}

/// Where a request goes through a proxy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Route {
    /// The proxy's `host[:port]`, unquoted.
    pub hostport: String,
    /// `Basic <credentials>` when the proxy URL has a user and a password.
    pub authorization: Option<String>,
}

impl Proxies {
    /// The proxies `ProxyHandler()` reads from `source` (and, for the registry rule, the
    /// override value).
    pub fn from_source(source: &ProxySource, registry_override: Option<(bool, String)>) -> Self {
        let environment = getproxies_environment(source);
        let bypass = if environment.is_empty() {
            Bypass::Registry(registry_override)
        } else {
            Bypass::Environment(environment)
        };
        Self { proxies: getproxies(source), bypass }
    }

    /// This process's settings, as `ProxyHandler()` reads them.
    pub fn system() -> Self {
        Self::from_source(&ProxySource::from_system(), registry_override())
    }

    /// The route for a `scheme` request to `host` (`req.host`): `ProxyHandler` only handles the
    /// schemes `getproxies()` names. `None` for a direct connection.
    pub fn route(&self, scheme: &str, host: &str) -> Result<Option<Route>, Failure> {
        let Some((_, proxy)) = self.proxies.iter().find(|(name, _)| name == scheme) else { return Ok(None) };
        let (_, user, password, hostport) = parse_proxy(proxy)?;
        if !host.is_empty() && self.bypass(host) {
            return Ok(None);
        }
        let authorization = match (user, password) {
            (Some(user), Some(password)) if !user.is_empty() && !password.is_empty() => {
                let pair = format!("{}:{}", unquote(&user), unquote(&password));
                Some(format!("Basic {}", sylvia_base::pybase64::b64encode(pair.as_bytes())))
            }
            _ => None,
        };
        Ok(Some(Route { hostport: unquote(&hostport), authorization }))
    }

    /// `proxy_bypass(host)`.
    pub fn bypass(&self, host: &str) -> bool {
        match &self.bypass {
            Bypass::Environment(proxies) => bypass_environment(host, proxies),
            Bypass::Registry(Some((enabled, overrides))) if *enabled && !overrides.is_empty() => {
                bypass_override(host, overrides)
            }
            Bypass::Registry(_) => false,
        }
    }
}

/// `unquote(text)` (errors="replace", as the default).
fn unquote(text: &str) -> String {
    pyurl::unquote_strict(text).unwrap_or_else(|_| text.to_string())
}

/// `_splitport(host)`: `(.*):([0-9]*)` with the port digits only.
fn split_port(host: &str) -> (&str, Option<&str>) {
    match host.rsplit_once(':') {
        Some((name, port)) if port.bytes().all(|b| b.is_ascii_digit()) => (name, Some(port)),
        _ => (host, None),
    }
}

/// `proxy_bypass_environment(host, proxies)`.
pub fn bypass_environment(host: &str, proxies: &[(String, String)]) -> bool {
    let Some((_, no_proxy)) = proxies.iter().find(|(scheme, _)| scheme == "no") else { return false };
    if no_proxy == "*" {
        return true;
    }
    let host = host.to_lowercase();
    let (hostonly, _) = split_port(&host);
    for name in no_proxy.split(',') {
        let name = sylvia_base::pystr::strip(name);
        if name.is_empty() {
            continue;
        }
        let name = name.trim_start_matches('.').to_lowercase();
        if hostonly == name || host == name {
            return true;
        }
        let suffix = format!(".{name}");
        if hostonly.ends_with(&suffix) || host.ends_with(&suffix) {
            return true;
        }
    }
    false
}

/// `_proxy_bypass_winreg_override(host, override)`.
pub fn bypass_override(host: &str, overrides: &str) -> bool {
    let (host, _) = split_port(host);
    for test in overrides.split(';') {
        let test = sylvia_base::pystr::strip(test);
        if test == "<local>" {
            if !host.contains('.') {
                return true;
            }
        } else if fnmatch(host, test) {
            return true;
        }
    }
    false
}

/// `fnmatch.fnmatch(name, pattern)` on Windows: both sides `os.path.normcase`d (lowercased,
/// `/` as `\`), then `fnmatchcase` (`*`, `?`, `[seq]`, `[!seq]`).
pub fn fnmatch(name: &str, pattern: &str) -> bool {
    let normcase = |text: &str| -> Vec<char> { text.to_lowercase().replace('/', "\\").chars().collect() };
    matches(&normcase(name), &normcase(pattern))
}

fn matches(name: &[char], pattern: &[char]) -> bool {
    match pattern.first() {
        None => name.is_empty(),
        Some('*') => (0..=name.len()).any(|skip| matches(&name[skip..], &pattern[1..])),
        Some('?') => !name.is_empty() && matches(&name[1..], &pattern[1..]),
        Some('[') => match class(&pattern[1..]) {
            Some((set, negate, rest)) => {
                name.first().is_some_and(|c| set_contains(&set, *c) != negate) && matches(&name[1..], rest)
            }
            // An unclosed `[` is a literal.
            None => name.first() == Some(&'[') && matches(&name[1..], &pattern[1..]),
        },
        Some(c) => name.first() == Some(c) && matches(&name[1..], &pattern[1..]),
    }
}

/// A `[...]` class after its `[`: the ranges, whether it is negated, and the pattern after `]`.
type Class<'a> = (Vec<(char, char)>, bool, &'a [char]);

fn class(pattern: &[char]) -> Option<Class<'_>> {
    let mut at = 0;
    let negate = pattern.first() == Some(&'!');
    if negate {
        at = 1;
    }
    // A `]` right after `[` or `[!` is part of the set.
    if pattern.get(at) == Some(&']') {
        at += 1;
    }
    let close = at + pattern[at..].iter().position(|c| *c == ']')?;
    let body = &pattern[usize::from(negate)..close];
    let mut set = Vec::new();
    let mut index = 0;
    while index < body.len() {
        if index + 2 < body.len() && body[index + 1] == '-' {
            if body[index] <= body[index + 2] {
                set.push((body[index], body[index + 2]));
            }
            index += 3;
        } else {
            set.push((body[index], body[index]));
            index += 1;
        }
    }
    Some((set, negate, &pattern[close + 1..]))
}

fn set_contains(set: &[(char, char)], character: char) -> bool {
    set.iter().any(|(low, high)| *low <= character && character <= *high)
}

/// `_parse_proxy`'s `(scheme, user, password, hostport)`.
pub type ParsedProxy = (Option<String>, Option<String>, Option<String>, String);

/// `_parse_proxy(proxy)`.
pub fn parse_proxy(proxy: &str) -> Result<ParsedProxy, Failure> {
    // `_splittype`: `^([^/:]+):(.*)`.
    let (scheme, rest) = match proxy.split_once(':') {
        Some((scheme, rest)) if !scheme.is_empty() && !scheme.contains('/') => {
            (Some(scheme.to_ascii_lowercase()), rest)
        }
        _ => (None, proxy),
    };
    let (scheme, authority) = if !rest.starts_with('/') {
        (None, proxy.to_string())
    } else {
        if !rest.starts_with("//") {
            return Err(Failure::Value(format!(
                "proxy URL with no authority: {}",
                sylvia_base::pystr::repr(proxy)
            )));
        }
        let end = match rest.find('@') {
            Some(at) => rest[at..].find('/').map(|n| at + n),
            None => rest[2..].find('/').map(|n| n + 2),
        };
        (scheme, rest[2..end.unwrap_or(rest.len())].to_string())
    };
    // `_splituser`: the last `@`; `_splitpasswd`: the first `:`.
    let (user, password, hostport) = match authority.rsplit_once('@') {
        Some((userinfo, hostport)) => match userinfo.split_once(':') {
            Some((user, password)) => {
                (Some(user.to_string()), Some(password.to_string()), hostport.to_string())
            }
            None => (Some(userinfo.to_string()), None, hostport.to_string()),
        },
        None => (None, None, authority),
    };
    Ok((scheme, user, password, hostport))
}

/// `ProxyEnable` and `str(ProxyOverride)` from the current user's Internet Settings, as
/// `proxy_bypass_registry` reads them; `None` on any `OSError`.
#[cfg(windows)]
pub fn registry_override() -> Option<(bool, String)> {
    use sylvia_base::winreg::{Key, Root, Value, View};
    let key = Key::open(
        Root::CurrentUser,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        View::Default,
    )
    .ok()?;
    let enabled = match key.query_value_ex("ProxyEnable").ok()? {
        Value::Dword(number) => number != 0,
        Value::Sz(units) | Value::ExpandSz(units) => !units.is_empty(),
    };
    let overrides = match key.query_value_ex("ProxyOverride").ok()? {
        // A lone surrogate cannot be held in a Rust string; an override list never has one.
        Value::Sz(units) | Value::ExpandSz(units) => String::from_utf16_lossy(&units),
        Value::Dword(number) => number.to_string(),
    };
    Some((enabled, overrides))
}

/// Not Windows: urllib has no registry rule.
#[cfg(not(windows))]
pub fn registry_override() -> Option<(bool, String)> {
    None
}
