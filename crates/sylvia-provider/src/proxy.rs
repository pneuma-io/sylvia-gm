//! Which proxy a provider request uses, as httpx decides it with `trust_env=True`.
//!
//! Python reads the proxies once, when the OpenAI SDK builds its httpx client:
//! `urllib.request.getproxies()` (the `*_proxy` environment variables; on Windows, when there are
//! none, the Internet Settings registry values), then `httpx._utils.get_environment_proxies()`
//! turns them into URL patterns (`http://`, `https://`, `all://`, and one exclusion per `NO_PROXY`
//! entry), and each request takes the first pattern, in httpx's priority order, that matches its
//! URL. This module reproduces those steps from an explicit snapshot of the inputs, so tests can
//! give any environment or registry values without touching the real ones.
//!
//! Two Python behaviours worth knowing are kept: proxies found only in the Windows registry come
//! without any exclusion (so loopback model servers go through the system proxy too), and a
//! `NO_PROXY` network such as `192.168.0.0/16` excludes only its network address.

use crate::httpx_url::{HttpxUrl, InvalidUrl};

/// Where the proxies are read from.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProxySource {
    /// The process environment, in its own order.
    pub environment: Vec<(String, String)>,
    /// Windows: Python uppercases environment names and falls back to the registry.
    pub windows: bool,
    /// `ProxyEnable` and `ProxyServer` from `HKCU\...\Internet Settings`, or `None` when the key
    /// or a value is missing.
    pub registry: Option<(u32, String)>,
}

impl ProxySource {
    /// The current process's environment and, on Windows, its registry settings.
    pub fn from_system() -> Self {
        Self { environment: std::env::vars().collect(), windows: cfg!(windows), registry: system_registry() }
    }
}

/// A registry value as CPython 3.12's `winreg.QueryValueEx` (`Reg2Py`) gives it to Python.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RegistryValue {
    /// `REG_DWORD` and `REG_QWORD`: an `int` (no data reads as 0).
    Int(u64),
    /// `REG_SZ` and `REG_EXPAND_SZ`: a `str` (`sylvia_base::winreg::sz_units`). A lone surrogate,
    /// which Python keeps, becomes U+FFFD here; a proxy address never holds one.
    Str(String),
    /// `REG_MULTI_SZ` with data: a `list` of `str`.
    List(Vec<String>),
    /// Any other type with data: `bytes`.
    Bytes(Vec<u8>),
    /// `None`: an empty `REG_MULTI_SZ`, `REG_BINARY` or other type.
    None,
}

const REG_SZ: u32 = 1;
const REG_EXPAND_SZ: u32 = 2;
const REG_DWORD: u32 = 4;
const REG_MULTI_SZ: u32 = 7;
const REG_QWORD: u32 = 11;

/// `Reg2Py(data, size, kind)` for the bytes Windows returned.
pub fn reg2py(kind: u32, data: &[u8]) -> RegistryValue {
    let number = |width: usize| {
        let mut bytes = [0u8; 8];
        let taken = data.len().min(width);
        bytes[..taken].copy_from_slice(&data[..taken]);
        RegistryValue::Int(u64::from_le_bytes(bytes))
    };
    match kind {
        REG_DWORD => number(4),
        REG_QWORD => number(8),
        REG_SZ | REG_EXPAND_SZ => {
            RegistryValue::Str(String::from_utf16_lossy(&sylvia_base::winreg::sz_units(data)))
        }
        _ if data.is_empty() => RegistryValue::None,
        REG_MULTI_SZ => {
            // `countStrings` and `fixupMultiSZ`: one final NUL ends the list; each string runs to
            // its NUL or the end of the data. An odd trailing byte is dropped.
            let units: Vec<u16> =
                data.as_chunks::<2>().0.iter().map(|pair| u16::from_le_bytes(*pair)).collect();
            let stop = if units.last() == Some(&0) { units.len() - 1 } else { units.len() };
            let mut strings = Vec::new();
            let mut index = 0;
            while index < stop {
                let start = index;
                while index < stop && units[index] != 0 {
                    index += 1;
                }
                let end =
                    units[start..].iter().position(|unit| *unit == 0).map_or(units.len(), |at| start + at);
                strings.push(String::from_utf16_lossy(&units[start..end]));
                index += 1;
            }
            RegistryValue::List(strings)
        }
        _ => RegistryValue::Bytes(data.to_vec()),
    }
}

impl RegistryValue {
    /// `bool(value)`.
    pub fn truthy(&self) -> bool {
        match self {
            Self::Int(number) => *number != 0,
            Self::Str(text) => !text.is_empty(),
            Self::List(items) => !items.is_empty(),
            Self::Bytes(bytes) => !bytes.is_empty(),
            Self::None => false,
        }
    }

    /// `str(value)`.
    pub fn py_str(&self) -> String {
        match self {
            Self::Int(number) => number.to_string(),
            Self::Str(text) => text.clone(),
            Self::List(items) => {
                let items: Vec<String> = items.iter().map(|item| sylvia_base::pystr::repr(item)).collect();
                format!("[{}]", items.join(", "))
            }
            Self::Bytes(bytes) => crate::config::pyvalue::bytes_repr(bytes),
            Self::None => "None".into(),
        }
    }
}

/// `ProxyEnable` and `ProxyServer` as `urllib.request.getproxies_registry` reads them from one
/// key, given each value's registry type and bytes: only the truth of `ProxyEnable` counts, and
/// `ProxyServer` is `str()` of its value, read only when proxies are enabled. `None` stands for
/// Python's caught `OSError` (a missing key or value included), which gives no registry proxies.
pub fn proxy_values(read: impl Fn(&str) -> Option<(u32, Vec<u8>)>) -> Option<(u32, String)> {
    let (kind, data) = read("ProxyEnable")?;
    if !reg2py(kind, &data).truthy() {
        return Some((0, String::new()));
    }
    let (kind, data) = read("ProxyServer")?;
    Some((1, reg2py(kind, &data).py_str()))
}

/// The current user's registry proxy settings, as [`ProxySource::from_system`] reads them.
pub fn system_registry() -> Option<(u32, String)> {
    read_registry(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
}

/// The proxy values of one current-user key, read through `sylvia_base::winreg` (CPython's
/// `winreg.OpenKey` and `QueryValueEx`).
#[cfg(windows)]
pub fn read_registry(subkey: &str) -> Option<(u32, String)> {
    use sylvia_base::winreg::{Key, Root, View};
    let key = Key::open(Root::CurrentUser, subkey, View::Default).ok()?;
    proxy_values(|name| key.query_raw(name).ok())
}

/// No registry outside Windows.
#[cfg(not(windows))]
pub fn read_registry(_subkey: &str) -> Option<(u32, String)> {
    None
}

/// An ordered `dict[str, str]`: a repeated key keeps its first place and takes the new value.
type OrderedMap = Vec<(String, String)>;

fn dict_set(map: &mut OrderedMap, key: &str, value: &str) {
    match map.iter_mut().find(|(existing, _)| existing == key) {
        Some(slot) => slot.1 = value.to_owned(),
        None => map.push((key.to_owned(), value.to_owned())),
    }
}

fn dict_pop(map: &mut OrderedMap, key: &str) {
    map.retain(|(existing, _)| existing != key);
}

fn dict_get<'a>(map: &'a OrderedMap, key: &str) -> Option<&'a str> {
    map.iter().find(|(existing, _)| existing == key).map(|(_, value)| value.as_str())
}

/// `urllib.request.getproxies_environment()`.
pub fn getproxies_environment(source: &ProxySource) -> OrderedMap {
    let names: Vec<(String, &str)> = source
        .environment
        .iter()
        .map(|(name, value)| {
            (if source.windows { name.to_uppercase() } else { name.clone() }, value.as_str())
        })
        .collect();
    let mut proxies = OrderedMap::new();
    for (name, value) in &names {
        let lower = name.to_lowercase();
        if !value.is_empty() && lower.ends_with("_proxy") {
            dict_set(&mut proxies, &lower[..lower.len() - 6], value);
        }
    }
    if names.iter().any(|(name, _)| name == "REQUEST_METHOD") {
        dict_pop(&mut proxies, "http");
    }
    for (name, value) in &names {
        if name.ends_with("_proxy") {
            let lower = name.to_lowercase();
            let scheme = &lower[..lower.len() - 6];
            if value.is_empty() {
                dict_pop(&mut proxies, scheme);
            } else {
                dict_set(&mut proxies, scheme, value);
            }
        }
    }
    proxies
}

/// `re.match('(?:[^/:]+)://', address)`.
fn has_scheme_prefix(address: &str) -> bool {
    let end = address.find(['/', ':']).unwrap_or(address.len());
    end > 0 && address[end..].starts_with("://")
}

/// `urllib.request.getproxies_registry()` for the given `(ProxyEnable, ProxyServer)`.
pub fn getproxies_registry(registry: Option<&(u32, String)>) -> OrderedMap {
    let mut proxies = OrderedMap::new();
    let Some((enable, server)) = registry else { return proxies };
    if *enable == 0 {
        return proxies;
    }
    let server = if !server.contains('=') && !server.contains(';') {
        format!("http={server};https={server};ftp={server}")
    } else {
        server.clone()
    };
    for part in server.split(';') {
        // `protocol, address = p.split('=', 1)`: a part without '=' ends the loop with the
        // proxies found so far (Python's ValueError is caught around it).
        let Some((protocol, address)) = part.split_once('=') else { return proxies };
        let mut address = address.to_owned();
        if !has_scheme_prefix(&address) {
            if matches!(protocol, "http" | "https" | "ftp") {
                address = format!("http://{address}");
            } else if protocol == "socks" {
                address = format!("socks://{address}");
            }
        }
        dict_set(&mut proxies, protocol, &address);
    }
    if let Some(socks) = dict_get(&proxies, "socks").filter(|socks| !socks.is_empty()) {
        let socks4 = match socks.strip_prefix("socks://") {
            Some(rest) => format!("socks4://{rest}"),
            None => socks.to_owned(),
        };
        let http = dict_get(&proxies, "http").filter(|value| !value.is_empty()).unwrap_or(&socks4).to_owned();
        let https =
            dict_get(&proxies, "https").filter(|value| !value.is_empty()).unwrap_or(&socks4).to_owned();
        dict_set(&mut proxies, "http", &http);
        dict_set(&mut proxies, "https", &https);
    }
    proxies
}

/// `urllib.request.getproxies()`: the environment, or on Windows the registry when the
/// environment has none.
pub fn getproxies(source: &ProxySource) -> OrderedMap {
    let environment = getproxies_environment(source);
    if !environment.is_empty() || !source.windows {
        return environment;
    }
    getproxies_registry(source.registry.as_ref())
}

fn is_ipv4_hostname(hostname: &str) -> bool {
    hostname.split('/').next().is_some_and(|host| host.parse::<std::net::Ipv4Addr>().is_ok())
}

fn is_ipv6_hostname(hostname: &str) -> bool {
    hostname.split('/').next().is_some_and(|host| host.parse::<std::net::Ipv6Addr>().is_ok())
}

/// `httpx._utils.get_environment_proxies()`: pattern to proxy URL, `None` for an exclusion.
pub fn environment_mounts(proxies: &OrderedMap) -> Vec<(String, Option<String>)> {
    let mut mounts: Vec<(String, Option<String>)> = Vec::new();
    let mut set =
        |key: String, value: Option<String>| match mounts.iter_mut().find(|(existing, _)| *existing == key) {
            Some(slot) => slot.1 = value,
            None => mounts.push((key, value)),
        };
    for scheme in ["http", "https", "all"] {
        if let Some(hostname) = dict_get(proxies, scheme).filter(|value| !value.is_empty()) {
            let url =
                if hostname.contains("://") { hostname.to_owned() } else { format!("http://{hostname}") };
            set(format!("{scheme}://"), Some(url));
        }
    }
    let no_proxy = dict_get(proxies, "no").unwrap_or("");
    for hostname in no_proxy.split(',').map(sylvia_base::pystr::strip) {
        if hostname == "*" {
            return Vec::new();
        }
        if hostname.is_empty() {
            continue;
        }
        let key = if hostname.contains("://") {
            hostname.to_owned()
        } else if is_ipv4_hostname(hostname) {
            format!("all://{hostname}")
        } else if is_ipv6_hostname(hostname) {
            format!("all://[{hostname}]")
        } else if hostname.to_lowercase() == "localhost" {
            format!("all://{hostname}")
        } else {
            format!("all://*{hostname}")
        };
        set(key, None);
    }
    mounts
}

/// `URLPattern`: which URLs a mount applies to.
#[derive(Debug, Clone, PartialEq, Eq)]
struct UrlPattern {
    scheme: String,
    host: String,
    port: Option<u32>,
}

enum HostMatch<'a> {
    Any,
    /// `*.example.com`: subdomains only.
    Subdomains(&'a str),
    /// `*example.com`: the domain and its subdomains.
    DomainAndSubdomains(&'a str),
    Exact(&'a str),
}

impl UrlPattern {
    fn parse(pattern: &str) -> Result<Self, ProxyError> {
        if !pattern.is_empty() && !pattern.contains(':') {
            return Err(ProxyError::Value(format!(
                "Proxy keys should use proper URL forms rather than plain scheme strings. Instead of \"{pattern}\", use \"{pattern}://\""
            )));
        }
        let url = HttpxUrl::parse(pattern).map_err(ProxyError::from)?;
        Ok(Self {
            scheme: if url.scheme == "all" { String::new() } else { url.scheme },
            host: if url.host == "*" { String::new() } else { url.host },
            port: url.port,
        })
    }

    fn host_match(&self) -> HostMatch<'_> {
        if self.host.is_empty() {
            HostMatch::Any
        } else if let Some(domain) = self.host.strip_prefix("*.") {
            HostMatch::Subdomains(domain)
        } else if let Some(domain) = self.host.strip_prefix('*') {
            HostMatch::DomainAndSubdomains(domain)
        } else {
            HostMatch::Exact(&self.host)
        }
    }

    fn matches(&self, url: &HttpxUrl) -> bool {
        if !self.scheme.is_empty() && url.scheme != self.scheme {
            return false;
        }
        let host = url.host.as_str();
        let host_ok = match self.host_match() {
            HostMatch::Any => true,
            // ^.+\.{domain}$
            HostMatch::Subdomains(domain) => {
                host.len() > domain.len() + 1
                    && host.ends_with(domain)
                    && host[..host.len() - domain.len()].ends_with('.')
            }
            // ^(.+\.)?{domain}$
            HostMatch::DomainAndSubdomains(domain) => {
                host == domain
                    || (host.len() > domain.len() + 1
                        && host.ends_with(domain)
                        && host[..host.len() - domain.len()].ends_with('.'))
            }
            HostMatch::Exact(domain) => host == domain,
        };
        if !host_ok {
            return false;
        }
        self.port.is_none() || url.port == self.port
    }

    /// `URLPattern.priority`, lower first.
    fn priority(&self) -> (u8, isize, isize) {
        (
            u8::from(self.port.is_none()),
            -(self.host.chars().count() as isize),
            -(self.scheme.chars().count() as isize),
        )
    }
}

/// Why httpx refuses to build its client from these proxies.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProxyError {
    /// `ValueError` (the owner answers `command_rejected` with this text).
    Value(String),
    /// `httpx.InvalidURL` or the `ImportError` for SOCKS without `socksio` (an internal error).
    Internal(String),
}

impl From<InvalidUrl> for ProxyError {
    fn from(error: InvalidUrl) -> Self {
        Self::Internal(error.0)
    }
}

/// The proxy a request goes through: its URL without credentials, and those credentials.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProxyTarget {
    /// `httpx.Proxy(url).url`, as text.
    pub url: String,
    /// `(username, password)` from the proxy URL, percent-decoded, sent as basic authentication.
    pub auth: Option<(String, String)>,
}

/// `repr(httpx.URL(...))`, with a password shown as `[secure]`.
fn url_repr(url: &HttpxUrl) -> String {
    let userinfo = match url.userinfo.split_once(':') {
        Some((user, _)) => format!("{user}:[secure]"),
        None => url.userinfo.clone(),
    };
    let shown = HttpxUrl { userinfo, ..url.clone() };
    format!("URL({})", crate::pyobj::py_repr_str(&shown.to_string()))
}

/// `urllib.parse.unquote`: `%XX` escapes decoded as UTF-8 (invalid bytes replaced), anything
/// else kept.
fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let escape = bytes[index] == b'%'
            && bytes.get(index + 1).is_some_and(u8::is_ascii_hexdigit)
            && bytes.get(index + 2).is_some_and(u8::is_ascii_hexdigit);
        if escape {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("00");
            out.push(u8::from_str_radix(hex, 16).unwrap_or(0));
            index += 3;
        } else {
            out.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `httpx.Proxy(url)`: the scheme must be HTTP(S) or SOCKS5, and credentials move out of the URL.
fn proxy_target(url: &str) -> Result<(String, ProxyTarget), ProxyError> {
    let parsed = HttpxUrl::parse(url)?;
    if !matches!(parsed.scheme.as_str(), "http" | "https" | "socks5" | "socks5h") {
        return Err(ProxyError::Value(format!("Unknown scheme for proxy URL {}", url_repr(&parsed))));
    }
    let auth = if parsed.userinfo.is_empty() {
        None
    } else {
        let (user, password) = parsed.userinfo.split_once(':').unwrap_or((&parsed.userinfo, ""));
        Some((percent_decode(user), percent_decode(password)))
    };
    // httpcore's proxy URL: scheme, host and port with the raw path as its target (`/` when empty).
    let bare = HttpxUrl { userinfo: String::new(), ..parsed.clone() };
    let url = format!("{}://{}{}", bare.scheme, bare.authority(), bare.raw_path());
    Ok((parsed.scheme, ProxyTarget { url, auth }))
}

/// The proxies of one httpx client.
#[derive(Debug, Clone, Default)]
pub struct ProxyMap {
    mounts: Vec<(UrlPattern, Option<ProxyTarget>)>,
}

impl ProxyMap {
    /// Build the map httpx builds for `trust_env=True`, failing where httpx's client fails.
    pub fn from_source(source: &ProxySource) -> Result<Self, ProxyError> {
        let mounts = environment_mounts(&getproxies(source));
        // httpx makes every Proxy first (scheme checks), then the transports (SOCKS needs socksio).
        let mut built = Vec::with_capacity(mounts.len());
        for (key, url) in &mounts {
            let target = url.as_deref().map(proxy_target).transpose()?;
            built.push((key, target));
        }
        if built
            .iter()
            .any(|(_, target)| target.as_ref().is_some_and(|(scheme, _)| scheme.starts_with("socks")))
        {
            return Err(ProxyError::Internal(
                "Using SOCKS proxy, but the 'socksio' package is not installed. Make sure to install httpx using `pip install httpx[socks]`."
                    .into(),
            ));
        }
        let mut patterns = Vec::with_capacity(built.len());
        for (key, target) in built {
            patterns.push((UrlPattern::parse(key)?, target.map(|(_, target)| target)));
        }
        // dict(sorted(...)): stable, so equal priorities keep their order.
        patterns.sort_by_key(|(pattern, _)| pattern.priority());
        Ok(Self { mounts: patterns })
    }

    /// `_transport_for_url(url)`: the proxy for a request URL, or `None` to connect directly.
    pub fn proxy_for(&self, url: &HttpxUrl) -> Option<&ProxyTarget> {
        self.mounts.iter().find(|(pattern, _)| pattern.matches(url)).and_then(|(_, target)| target.as_ref())
    }
}
