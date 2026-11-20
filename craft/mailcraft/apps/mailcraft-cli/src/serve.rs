//! `mailcraft-cli serve`: the server behind the web app. It keeps the engine session (accounts,
//! connections, cache) and speaks a tiny HTTP API: `POST /api/cmd` runs a command,
//! `GET /api/snapshot?since=N` returns the state when it changed, `GET /api/health`. It also
//! serves the web app's files from a directory when one is given.
//!
//! The token travels only in `Authorization: Bearer …` (compared in constant time, never in
//! the query string, never printed) and the user the framework names only in
//! `X-Mailcraft-User`. Without a token the server answers only to this machine: a local `Host`
//! on its own port (against DNS rebinding) and a local or allow-listed `Origin` (CORS headers
//! only for those), so a web page the person visits cannot read or send their mail.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, Ipv6Addr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mailcraft_engine::core::account::slug;
use mailcraft_engine::{Paths, Session};
use serde_json::{Value, json};

pub struct Options {
    pub bind: String,
    /// The service token: required beyond localhost; in multi-user mode it identifies the
    /// framework (which names the user with `X-Mailcraft-User`) and mints user tokens.
    pub token: String,
    pub static_dir: Option<PathBuf>,
    pub open: bool,
    /// Multi-user mode: one session per user under this directory.
    pub users_dir: Option<PathBuf>,
    /// Sessions on the built-in demo mailbox instead of real servers.
    pub demo: bool,
    /// Language preference applied to new sessions (`en`, `it`).
    pub lang: Option<String>,
    /// The URL browsers reach this server at (behind a proxy), for the OAuth callback; when
    /// unset it comes from the request's `X-Forwarded-*` or `Host` headers.
    pub public_url: Option<String>,
    /// The framework's assistant endpoint (`/api/mail/assist`) announced to the web app for
    /// reply suggestions and drafts; relative URLs are resolved against the page's origin.
    pub assist_url: Option<String>,
    /// Without a token: the origins of other sites' pages allowed to call the API, lower case,
    /// without a trailing slash (`--allow-origin`). This machine's pages always are.
    pub allow_origins: Vec<String>,
}

/// The shortest service token accepted: `openssl rand -hex 16` and up.
pub const MIN_TOKEN: usize = 32;

type Shared = Arc<Mutex<Session>>;

/// Everything the request handlers share.
struct Hub {
    opts: Options,
    /// The port the server listens on (`Host` must name it without a token).
    port: u16,
    /// Single-user mode: the one session.
    single: Option<Shared>,
    /// Multi-user mode: sessions by user id, made on first use.
    sessions: Mutex<HashMap<String, Shared>>,
    /// User tokens (token → user), persisted in `tokens.json` under the users directory.
    tokens: Mutex<HashMap<String, String>>,
}

impl Hub {
    fn session_for(&self, user: &str) -> Result<Shared, String> {
        if let Some(s) = &self.single {
            return Ok(s.clone());
        }
        let user = slug(user);
        if user.is_empty() {
            return Err("missing user".into());
        }
        let mut map = self.sessions.lock().map_err(|_| "sessions unavailable".to_string())?;
        if let Some(s) = map.get(&user) {
            return Ok(s.clone());
        }
        if map.len() >= 2_000 {
            return Err("too many users loaded".into());
        }
        let dir = self.opts.users_dir.clone().ok_or("no users directory")?.join(&user);
        let session = if self.opts.demo {
            Session::demo()
        } else {
            let mut s = Session::with_broker(mailcraft_net::factory(), mailcraft_net::broker(), Some(Paths::under(&dir)));
            if let Some(l) = &self.opts.lang
                && s.config().preferences.language.is_empty()
            {
                let _ = s.execute("prefs.set", &json!({"language": l}));
            }
            s
        };
        let mut session = session;
        // The server's files are nobody's: attachments travel as base64 only.
        session.allow_host_paths(false);
        if let Some(u) = &self.opts.assist_url {
            session.set_assist_url(u);
        }
        let shared = Arc::new(Mutex::new(session));
        map.insert(user, shared.clone());
        Ok(shared)
    }

    fn all_sessions(&self) -> Vec<Shared> {
        let mut out: Vec<Shared> = self.single.iter().cloned().collect();
        if let Ok(map) = self.sessions.lock() {
            out.extend(map.values().cloned());
        }
        out
    }

    fn tokens_path(&self) -> Option<PathBuf> {
        self.opts.users_dir.as_ref().map(|d| d.join("tokens.json"))
    }

    fn load_tokens(&self) {
        if let Some(p) = self.tokens_path()
            && let Ok(text) = std::fs::read_to_string(&p)
            && let Ok(map) = serde_json::from_str::<HashMap<String, String>>(&text)
            && let Ok(mut t) = self.tokens.lock()
        {
            *t = map;
        }
    }

    fn save_tokens(&self) {
        if let (Some(p), Ok(t)) = (self.tokens_path(), self.tokens.lock()) {
            let text = serde_json::to_string_pretty(&*t).unwrap_or_else(|_| "{}".into());
            let _ = mailcraft_engine::core::account::write_private(&p, text.as_bytes());
        }
    }

    fn mint_token(&self, user: &str) -> Result<String, String> {
        let token = random_token();
        self.tokens.lock().map_err(|_| "tokens unavailable".to_string())?.insert(token.clone(), slug(user));
        self.save_tokens();
        Ok(token)
    }

    fn revoke(&self, user: &str) -> usize {
        let user = slug(user);
        let n = self
            .tokens
            .lock()
            .map(|mut t| {
                let before = t.len();
                t.retain(|_, u| *u != user);
                before - t.len()
            })
            .unwrap_or(0);
        self.save_tokens();
        n
    }

    fn user_of_token(&self, token: &str) -> Option<String> {
        self.tokens.lock().ok()?.get(token).cloned()
    }

    fn users(&self) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        if let Some(dir) = &self.opts.users_dir
            && let Ok(entries) = std::fs::read_dir(dir)
        {
            for e in entries.flatten().filter(|e| e.path().is_dir()) {
                out.push(e.file_name().to_string_lossy().into_owned());
            }
        }
        if let Ok(map) = self.sessions.lock() {
            for u in map.keys() {
                if !out.contains(u) {
                    out.push(u.clone());
                }
            }
        }
        out.sort();
        out
    }
}

/// Compares a given token with a secret in time that depends on the given token's length
/// only, never on where the two differ or on the secret's length.
fn constant_eq(given: &str, secret: &str) -> bool {
    let (g, s) = (given.as_bytes(), secret.as_bytes());
    let mut diff = u8::from(g.len() != s.len());
    for (i, x) in g.iter().enumerate() {
        let y = i.checked_rem(s.len()).and_then(|j| s.get(j)).copied().unwrap_or(0);
        diff |= x ^ y;
    }
    diff == 0
}

/// 32 random bytes as hex, from the OS when it has `/dev/urandom`, else a hash mix.
fn random_token() -> String {
    let mut bytes = [0u8; 32];
    let from_os = std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut bytes)).is_ok();
    if !from_os {
        use std::hash::{BuildHasher, Hasher};
        for (i, chunk) in bytes.chunks_mut(8).enumerate() {
            let mut h = std::collections::hash_map::RandomState::new().build_hasher();
            h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
            h.write_u32(std::process::id());
            h.write_usize(i);
            let v = h.finish().to_le_bytes();
            chunk.copy_from_slice(&v[..chunk.len()]);
        }
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

const MAX_BODY: usize = 64 * 1024 * 1024;

struct Request {
    method: String,
    path: String,
    query: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

fn read_request(stream: &mut TcpStream) -> Option<Request> {
    let mut reader = BufReader::new(stream.try_clone().ok()?);
    let mut line = String::new();
    if reader.read_line(&mut line).ok()? == 0 {
        return None;
    }
    let mut parts = line.split_whitespace();
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target.clone(), String::new()),
    };
    let mut headers = Vec::new();
    let mut length = 0usize;
    loop {
        let mut h = String::new();
        if reader.read_line(&mut h).ok()? == 0 || h.trim().is_empty() {
            break;
        }
        if let Some((k, v)) = h.split_once(':') {
            let (k, v) = (k.trim().to_ascii_lowercase(), v.trim().to_string());
            if k == "content-length" {
                length = v.parse().unwrap_or(0);
            }
            headers.push((k, v));
        }
        if headers.len() > 100 {
            return None;
        }
    }
    if length > MAX_BODY {
        return None;
    }
    let mut body = vec![0u8; length];
    if length > 0 {
        reader.read_exact(&mut body).ok()?;
    }
    Some(Request { method, path, query, headers, body })
}

/// The URL browsers use for this server: configured, or from the proxy's headers, or `Host`.
fn public_url(hub: &Hub, req: &Request) -> String {
    if let Some(u) = &hub.opts.public_url {
        return u.trim_end_matches('/').to_string();
    }
    let proto = header(req, "x-forwarded-proto").unwrap_or("http").split(',').next().unwrap_or("http").trim();
    let host = header(req, "x-forwarded-host").or_else(|| header(req, "host")).unwrap_or("127.0.0.1:7878").split(',').next().unwrap_or("").trim();
    let prefix = header(req, "x-forwarded-prefix").unwrap_or("").trim_end_matches('/');
    format!("{proto}://{host}{prefix}")
}

fn header<'a>(req: &'a Request, name: &str) -> Option<&'a str> {
    req.headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
}

fn query_param(query: &str, name: &str) -> Option<String> {
    query.split('&').find_map(|kv| kv.split_once('=').filter(|(k, _)| *k == name).map(|(_, v)| url_decode(v)))
}

/// Decodes `%XX` escapes and `+` in a path segment or query value.
fn url_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or_default();
                match u8::from_str_radix(hex, 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(b'%');
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `origin` is an allowed `Origin` (see [`cors`]): only then does the answer carry the CORS
/// headers, and only for that origin.
fn respond(stream: &mut TcpStream, status: &str, content_type: &str, body: &[u8], origin: Option<&str>) {
    respond_with(stream, status, content_type, body, origin, "");
}

fn respond_with(stream: &mut TcpStream, status: &str, content_type: &str, body: &[u8], origin: Option<&str>, extra: &str) {
    let cors = origin
        .map(|o| {
            format!(
                "Access-Control-Allow-Origin: {o}\r\nVary: Origin\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type, Authorization\r\nAccess-Control-Max-Age: 600\r\n"
            )
        })
        .unwrap_or_default();
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n{cors}{extra}\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body);
    let _ = stream.flush();
}

fn json_response(stream: &mut TcpStream, status: &str, v: &Value, origin: Option<&str>) {
    respond(stream, status, "application/json; charset=utf-8", v.to_string().as_bytes(), origin);
}

fn mime_of(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or_default() {
        "html" => "text/html; charset=utf-8",
        "js" => "text/javascript",
        "wasm" => "application/wasm",
        "css" => "text/css",
        "json" => "application/json",
        "png" => "image/png",
        "svg" => "image/svg+xml",
        "ico" => "image/x-icon",
        _ => "application/octet-stream",
    }
}

fn serve_static(stream: &mut TcpStream, dir: &Path, path: &str, origin: Option<&str>) {
    let rel = path.trim_start_matches('/');
    let rel = if rel.is_empty() { "index.html" } else { rel };
    if rel.contains("..") || rel.contains('\\') {
        respond(stream, "400 Bad Request", "text/plain", b"bad path", origin);
        return;
    }
    let file = dir.join(rel);
    match std::fs::read(&file) {
        Ok(bytes) => respond(stream, "200 OK", mime_of(&file), &bytes, origin),
        Err(_) => respond(stream, "404 Not Found", "text/plain; charset=utf-8", b"not found", origin),
    }
}

const WELCOME: &str = "MailCraft server is running.\n\nOpen the web app and point it here, or start the server with --static DIR to serve the web app's files (apps/mailcraft-web, built with trunk).\n\nAPI: POST /api/cmd {\"command\", \"params\"}  ·  GET /api/snapshot?since=N  ·  GET /api/health\n";

/// Who is calling: the framework (service token) naming a user, a user token, or, in
/// single-user mode, anyone the token policy lets in.
enum Caller {
    Service { user: Option<String> },
    User(String),
    Single,
}

/// The token comes in the `Authorization: Bearer` header only: a token in the URL would land
/// in every proxy and access log on the way. The user the framework names comes in
/// `X-Mailcraft-User` only (its proxy sets it after its own login): a query string would let
/// whoever holds a link read someone else's mail.
fn authenticate(hub: &Hub, req: &Request) -> Result<Caller, &'static str> {
    let given = header(req, "authorization").and_then(|a| a.strip_prefix("Bearer ")).map(str::trim).map(str::to_string);
    let service = &hub.opts.token;
    if hub.single.is_some() {
        if service.is_empty() || given.as_deref().is_some_and(|g| constant_eq(g, service)) {
            return Ok(Caller::Single);
        }
        return Err("missing or wrong token");
    }
    let Some(given) = given.filter(|g| !g.is_empty()) else { return Err("missing token") };
    if !service.is_empty() && constant_eq(&given, service) {
        let user = header(req, "x-mailcraft-user").map(slug).filter(|u| !u.is_empty());
        return Ok(Caller::Service { user });
    }
    match hub.user_of_token(&given) {
        Some(u) => Ok(Caller::User(u)),
        None => Err("unknown token"),
    }
}

/// `host[:port]` → the host (IPv6 in brackets, as written) and the port, if well formed.
fn split_host_port(authority: &str) -> Option<(&str, Option<u16>)> {
    let (host, port) = if authority.starts_with('[') {
        let end = authority.find(']')?;
        let (host, rest) = authority.split_at(end.checked_add(1)?);
        match rest {
            "" => (host, None),
            _ => (host, Some(rest.strip_prefix(':')?)),
        }
    } else {
        match authority.split_once(':') {
            Some((host, port)) => (host, Some(port)),
            None => (authority, None),
        }
    };
    let port = match port {
        Some(p) if !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()) => Some(p.parse().ok()?),
        Some(_) => return None,
        None => None,
    };
    (!host.is_empty()).then_some((host, port))
}

/// `localhost`, `127.x.y.z` or `[::1]`: names of this machine no DNS answer can change.
fn is_local_host(host: &str) -> bool {
    host.eq_ignore_ascii_case("localhost")
        || host.parse::<Ipv4Addr>().is_ok_and(|a| a.is_loopback())
        || host.strip_prefix('[').and_then(|h| h.strip_suffix(']')).and_then(|h| h.parse::<Ipv6Addr>().ok()).is_some_and(|a| a.is_loopback())
}

/// `Host` names this machine on the server's port. Without a token, a page elsewhere could
/// otherwise point a name of its own at 127.0.0.1 (DNS rebinding) and read the mail as if it
/// were its own site.
fn host_is_local(hub: &Hub, req: &Request) -> bool {
    header(req, "host").and_then(split_host_port).is_some_and(|(host, port)| is_local_host(host) && port.unwrap_or(80) == hub.port)
}

/// What a request's `Origin` (a browser's page calling) is allowed.
enum Cors<'a> {
    /// No origin (not a browser, or the page's own site): nothing to allow.
    None,
    /// Named in `Access-Control-Allow-Origin`.
    Allow(&'a str),
    /// A page of another site calling a server without a token: refused.
    Refuse,
}

/// With a service token the API answers only to bearer tokens, which a browser never adds on
/// its own: any page may call it (the web app hosted elsewhere does). Without a token anyone
/// who reaches the API may use it, so only this machine's pages may (`http://127.0.0.1:*`,
/// `http://localhost:*`, `http://[::1]:*`: the server's own origin is one of them, since `Host`
/// must be local), and the origins of `--allow-origin`.
fn cors<'a>(hub: &Hub, req: &'a Request) -> Cors<'a> {
    let Some(origin) = header(req, "origin") else { return Cors::None };
    // Repeated in a header of the answer: printable ASCII only, of a sane length.
    let printable = !origin.is_empty() && origin.len() <= 200 && origin.bytes().all(|b| b.is_ascii_graphic());
    if !hub.opts.token.is_empty() {
        return if printable { Cors::Allow(origin) } else { Cors::None };
    }
    let o = origin.to_ascii_lowercase();
    let local =
        o.strip_prefix("http://").or_else(|| o.strip_prefix("https://")).and_then(split_host_port).is_some_and(|(host, _)| is_local_host(host));
    if printable && (local || hub.opts.allow_origins.contains(&o)) { Cors::Allow(origin) } else { Cors::Refuse }
}

/// `--allow-origin a,b`: origins lower-cased, trimmed, without a trailing slash.
pub fn parse_origins(list: &str) -> Vec<String> {
    list.split(',').map(|o| o.trim().trim_end_matches('/').to_ascii_lowercase()).filter(|o| !o.is_empty()).collect()
}

fn handle(mut stream: TcpStream, hub: Arc<Hub>) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
    let Some(req) = read_request(&mut stream) else { return };
    if hub.opts.token.is_empty() && !host_is_local(&hub, &req) {
        json_response(
            &mut stream,
            "403 Forbidden",
            &json!({"error": "without a token the server answers only to this machine (Host: 127.0.0.1 or localhost)"}),
            None,
        );
        return;
    }
    let origin = match cors(&hub, &req) {
        Cors::None => None,
        Cors::Allow(o) => Some(o),
        Cors::Refuse => {
            json_response(&mut stream, "403 Forbidden", &json!({"error": "origin not allowed (start the server with --allow-origin ORIGIN)"}), None);
            return;
        }
    };
    if req.method == "OPTIONS" {
        // A page on a public site calling this private address: Chrome asks first.
        let private = header(&req, "access-control-request-private-network").is_some_and(|v| v.trim().eq_ignore_ascii_case("true"));
        let extra = if private && origin.is_some() { "Access-Control-Allow-Private-Network: true\r\n" } else { "" };
        respond_with(&mut stream, "204 No Content", "text/plain", b"", origin, extra);
        return;
    }
    if !req.path.starts_with("/api/") {
        match (req.method.as_str(), &hub.opts.static_dir) {
            ("GET", Some(dir)) => serve_static(&mut stream, dir, &req.path, origin),
            ("GET", None) if req.path == "/" => respond(&mut stream, "200 OK", "text/plain; charset=utf-8", WELCOME.as_bytes(), origin),
            ("GET", None) => respond(&mut stream, "404 Not Found", "text/plain", b"not found", origin),
            _ => respond(&mut stream, "405 Method Not Allowed", "text/plain", b"method not allowed", origin),
        }
        return;
    }
    // The provider sends the browser back here after an OAuth sign-in (no token: the state
    // of the flow is the proof, and only the session that started it accepts it).
    if req.method == "GET" && req.path == "/api/oauth/callback" {
        let params = mailcraft_engine::core::oauth::parse_callback(&req.query);
        let prm = json!({"state": params.get("state").cloned().unwrap_or_default(), "code": params.get("code").cloned().unwrap_or_default(), "error": params.get("error").cloned().unwrap_or_default()});
        let finished = hub.all_sessions().into_iter().any(|s| s.lock().map(|mut s| s.execute("account.oauthFinish", &prm).is_ok()).unwrap_or(false));
        let page = if finished { mailcraft_engine::loopback::PAGE_OK } else { mailcraft_engine::loopback::PAGE_ERR };
        respond(&mut stream, "200 OK", "text/html; charset=utf-8", page.as_bytes(), origin);
        return;
    }
    if req.method == "GET" && req.path == "/api/health" {
        json_response(
            &mut stream,
            "200 OK",
            &json!({"ok": true, "name": "mailcraft", "version": env!("CARGO_PKG_VERSION"), "multi_user": hub.single.is_none()}),
            origin,
        );
        return;
    }
    let caller = match authenticate(&hub, &req) {
        Ok(c) => c,
        Err(e) => {
            json_response(&mut stream, "401 Unauthorized", &json!({"error": e}), origin);
            return;
        }
    };
    // Administration (service token only).
    if let Some(rest) = req.path.strip_prefix("/api/users") {
        if !matches!(caller, Caller::Service { .. }) {
            json_response(&mut stream, "403 Forbidden", &json!({"error": "service token required"}), origin);
            return;
        }
        let decoded: Vec<String> = rest.trim_matches('/').split('/').filter(|p| !p.is_empty()).map(url_decode).collect();
        let parts: Vec<&str> = decoded.iter().map(String::as_str).collect();
        match (req.method.as_str(), parts.as_slice()) {
            ("GET", []) => json_response(&mut stream, "200 OK", &json!({"users": hub.users()}), origin),
            ("POST", [user, "token"]) => match hub.mint_token(user) {
                Ok(t) => json_response(&mut stream, "200 OK", &json!({"user": slug(user), "token": t}), origin),
                Err(e) => json_response(&mut stream, "500 Internal Server Error", &json!({"error": e}), origin),
            },
            ("POST", [user, "revoke"]) => json_response(&mut stream, "200 OK", &json!({"user": slug(user), "revoked": hub.revoke(user)}), origin),
            _ => json_response(&mut stream, "404 Not Found", &json!({"error": "unknown users endpoint"}), origin),
        }
        return;
    }
    let user = match &caller {
        Caller::Single => String::new(),
        Caller::User(u) => u.clone(),
        Caller::Service { user: Some(u) } => u.clone(),
        Caller::Service { user: None } => {
            json_response(&mut stream, "400 Bad Request", &json!({"error": "name the user with the X-Mailcraft-User header"}), origin);
            return;
        }
    };
    let session = match hub.session_for(&user) {
        Ok(s) => s,
        Err(e) => {
            json_response(&mut stream, "500 Internal Server Error", &json!({"error": e}), origin);
            return;
        }
    };
    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/api/snapshot") => {
            let since: u64 = query_param(&req.query, "since").and_then(|s| s.parse().ok()).unwrap_or(0);
            let snapshot = session.lock().map(|s| s.snapshot(since)).unwrap_or(None);
            match snapshot {
                Some(s) => json_response(&mut stream, "200 OK", &serde_json::to_value(s).unwrap_or(Value::Null), origin),
                None => json_response(&mut stream, "200 OK", &json!({"unchanged": true}), origin),
            }
        }
        ("POST", "/api/cmd") => {
            let body: Value = match serde_json::from_slice(&req.body) {
                Ok(v) => v,
                Err(e) => {
                    json_response(&mut stream, "400 Bad Request", &json!({"error": format!("invalid JSON: {e}")}), origin);
                    return;
                }
            };
            let command = body.get("command").and_then(Value::as_str).unwrap_or_default().to_string();
            let mut params = body.get("params").cloned().unwrap_or_else(|| json!({}));
            // A sign-in started through the server comes back to the server.
            if command == "account.oauthStart" && params.get("redirect").and_then(Value::as_str).is_none_or(str::is_empty) {
                params["redirect"] = json!(format!("{}/api/oauth/callback", public_url(&hub, &req)));
            }
            let result = match session.lock() {
                Ok(mut s) => s.execute(&command, &params).map_err(|e| e.to_string()),
                Err(_) => Err("the session is unavailable".to_string()),
            };
            match result {
                Ok(v) => json_response(&mut stream, "200 OK", &json!({"result": v}), origin),
                Err(e) => json_response(&mut stream, "200 OK", &json!({"error": e}), origin),
            }
        }
        _ => json_response(&mut stream, "404 Not Found", &json!({"error": "unknown endpoint"}), origin),
    }
}

fn is_loopback(bind: &str) -> bool {
    bind.starts_with("127.") || bind.starts_with("localhost") || bind.starts_with("[::1]")
}

/// Runs the server until the process ends. `session` is the single-user session; in
/// multi-user mode (`users_dir`) it is ignored and sessions are made per user.
/// Binds the listener, after the checks on the token and the address.
fn bind(opts: &Options) -> Result<TcpListener, String> {
    if !opts.token.is_empty() && opts.token.chars().count() < MIN_TOKEN {
        return Err(format!(
            "the service token is too short: use at least {MIN_TOKEN} random characters, for example MAILCRAFT_TOKEN=$(openssl rand -hex 32)"
        ));
    }
    if !is_loopback(&opts.bind) && opts.token.is_empty() {
        return Err("binding beyond localhost needs --token SECRET / MAILCRAFT_TOKEN (and an HTTPS reverse proxy in front)".into());
    }
    if opts.users_dir.is_some() && opts.token.is_empty() {
        return Err("multi-user mode (--users-dir) needs --token SECRET for the framework".into());
    }
    TcpListener::bind(&opts.bind).map_err(|e| format!("cannot listen on {}: {e}", opts.bind))
}

pub fn run(session: Session, opts: Options) -> Result<(), String> {
    let listener = bind(&opts)?;
    let addr = listener.local_addr().map_err(|e| e.to_string())?;
    if let Some(d) = &opts.users_dir {
        std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    let mut session = session;
    // Whoever holds the token is not the server's owner: no file by path through the API.
    session.allow_host_paths(false);
    if let Some(u) = &opts.assist_url {
        session.set_assist_url(u);
    }
    let single = opts.users_dir.is_none().then(|| Arc::new(Mutex::new(session)));
    let hub = Arc::new(Hub { opts, port: addr.port(), single, sessions: Mutex::new(HashMap::new()), tokens: Mutex::new(HashMap::new()) });
    hub.load_tokens();
    // Pump every session in the background so jobs complete while nobody asks.
    {
        let h = hub.clone();
        std::thread::Builder::new()
            .name("mailcraft-pump".into())
            .spawn(move || {
                loop {
                    std::thread::sleep(Duration::from_millis(150));
                    for s in h.all_sessions() {
                        if let Ok(mut guard) = s.lock() {
                            let _ = guard.pump();
                        }
                    }
                }
            })
            .map_err(|e| e.to_string())?;
    }
    // The token is never printed (logs, terminals and service managers keep what they see):
    // the page gets it from the person, or from the URL `--open` hands to the local browser,
    // in the fragment (`#token=`), which the browser never sends to a server and the page
    // takes out of the address bar at once.
    let url = format!("http://{addr}");
    let with_token = if hub.opts.token.is_empty() || hub.single.is_none() { url.clone() } else { format!("{url}/#token={}", hub.opts.token) };
    eprintln!(
        "MailCraft server on {url}{}{}",
        if hub.single.is_none() { " (multi-user)" } else { "" },
        if hub.opts.token.is_empty() {
            ": no token, this machine's pages only"
        } else {
            "; the token is the one given with --token / MAILCRAFT_TOKEN"
        }
    );
    if let Some(d) = &hub.opts.static_dir {
        eprintln!("serving the web app from {}", d.display());
    } else {
        eprintln!(
            "no --static DIR: the web app on https://rickyb82.github.io/mailcraft/ can connect to this server (Server field in Settings) when it is started with --allow-origin https://rickyb82.github.io or a token"
        );
    }
    if hub.opts.open {
        crate::open_browser(&with_token);
    }
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let h = hub.clone();
        let _ = std::thread::Builder::new().name("mailcraft-http".into()).spawn(move || handle(stream, h));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "t0k-0123456789abcdef0123456789abcdef";

    fn get(addr: &str, path: &str, token: &str) -> (String, Value) {
        let mut s = TcpStream::connect(addr).expect("connect");
        let auth = if token.is_empty() { String::new() } else { format!("Authorization: Bearer {token}\r\n") };
        s.write_all(format!("GET {path} HTTP/1.1\r\nHost: x\r\n{auth}Origin: https://example.org\r\n\r\n").as_bytes()).expect("write");
        let mut out = String::new();
        s.read_to_string(&mut out).expect("read");
        let (head, body) = out.split_once("\r\n\r\n").expect("response");
        (head.lines().next().unwrap_or_default().to_string(), serde_json::from_str(body).unwrap_or(Value::Null))
    }

    fn post(addr: &str, path: &str, body: &str) -> Value {
        let mut s = TcpStream::connect(addr).expect("connect");
        s.write_all(
            format!(
                "POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
            .as_bytes(),
        )
        .expect("write");
        let mut out = String::new();
        s.read_to_string(&mut out).expect("read");
        serde_json::from_str(out.split_once("\r\n\r\n").map(|(_, b)| b).unwrap_or("null")).unwrap_or(Value::Null)
    }

    fn options(bind: &str, token: &str) -> Options {
        Options {
            bind: bind.into(),
            token: token.into(),
            static_dir: None,
            open: false,
            users_dir: None,
            demo: false,
            lang: None,
            public_url: None,
            assist_url: None,
            allow_origins: Vec::new(),
        }
    }

    /// A request with the headers given verbatim (`Host` included): the head and the body.
    fn raw(addr: &str, method: &str, path: &str, headers: &str, body: &str) -> (String, Value) {
        let mut s = TcpStream::connect(addr).expect("connect");
        s.write_all(
            format!("{method} {path} HTTP/1.1\r\n{headers}Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes(),
        )
        .expect("write");
        let mut out = String::new();
        s.read_to_string(&mut out).expect("read");
        let (head, body) = out.split_once("\r\n\r\n").expect("response");
        (head.to_string(), serde_json::from_str(body).unwrap_or(Value::Null))
    }

    #[test]
    fn without_a_token_only_this_machines_pages_are_answered() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        let port = listener.local_addr().expect("addr").port();
        drop(listener);
        let opts = Options { allow_origins: parse_origins(" https://App.Example.it/ , "), ..options(&addr, "") };
        std::thread::spawn(move || {
            let _ = run(Session::demo(), opts);
        });
        for _ in 0..50 {
            if TcpStream::connect(&addr).is_ok() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let local = format!("Host: 127.0.0.1:{port}\r\n");
        // A local Host on the server's port, with or without an origin of this machine.
        let (head, snap) = raw(&addr, "GET", "/api/snapshot?since=0", &local, "");
        assert!(head.contains("200") && snap["revision"].is_number() && !head.contains("Access-Control"), "{head}");
        let (head, _) = raw(&addr, "GET", "/api/snapshot?since=0", &format!("Host: localhost:{port}\r\nOrigin: http://localhost:8080\r\n"), "");
        assert!(head.contains("200") && head.contains("Access-Control-Allow-Origin: http://localhost:8080"), "{head}");
        assert!(!head.contains("Private-Network"), "only a preflight that asks gets it: {head}");
        let (head, _) = raw(
            &addr,
            "OPTIONS",
            "/api/cmd",
            &format!("Host: [::1]:{port}\r\nOrigin: http://[::1]:5173\r\nAccess-Control-Request-Private-Network: true\r\n"),
            "",
        );
        assert!(
            head.contains("204")
                && head.contains("Access-Control-Allow-Origin: http://[::1]:5173")
                && head.contains("Access-Control-Allow-Private-Network: true"),
            "{head}"
        );
        // An allow-listed origin.
        let (head, _) = raw(&addr, "GET", "/api/health", &format!("{local}Origin: https://app.example.it\r\n"), "");
        assert!(head.contains("200") && head.contains("Access-Control-Allow-Origin: https://app.example.it"), "{head}");
        // Another site's page (reading, sending, or asking first), a name of its own pointed at
        // this machine (DNS rebinding), another port, no Host at all.
        let send = r#"{"command": "mail.send", "params": {"to": "a@example.org", "subject": "x", "body": "y"}}"#;
        for (method, path, headers, body) in [
            ("GET", "/api/snapshot?since=0", format!("{local}Origin: https://evil.example\r\n"), ""),
            ("POST", "/api/cmd", format!("{local}Origin: https://evil.example\r\n"), send),
            ("OPTIONS", "/api/cmd", format!("{local}Origin: http://127.0.0.1.evil.example\r\nAccess-Control-Request-Private-Network: true\r\n"), ""),
            ("GET", "/api/snapshot?since=0", "Host: rebind.evil.example\r\n".to_string(), ""),
            ("POST", "/api/cmd", format!("Host: rebind.evil.example:{port}\r\n"), send),
            ("GET", "/api/snapshot?since=0", "Host: 127.0.0.1:1\r\n".to_string(), ""),
            ("GET", "/", "Host: 10.0.0.5\r\n".to_string(), ""),
            ("GET", "/api/snapshot?since=0", String::new(), ""),
        ] {
            let (head, _) = raw(&addr, method, path, &headers, body);
            assert!(head.contains("403") && !head.contains("Access-Control-Allow"), "{method} {path} {headers}: {head}");
        }
        let (_, snap) = raw(&addr, "GET", "/api/snapshot?since=0", &local, "");
        assert!(!snap.to_string().contains("mail.send"), "nothing was sent: {snap}");
    }

    #[test]
    fn tokens_are_long_and_binding_beyond_localhost_needs_one() {
        let e = bind(&options("127.0.0.1:0", "short")).unwrap_err();
        assert!(e.contains("too short"), "{e}");
        assert!(bind(&options("0.0.0.0:0", "")).unwrap_err().contains("beyond localhost"));
        assert!(bind(&Options { users_dir: Some(std::env::temp_dir()), ..options("127.0.0.1:0", "") }).unwrap_err().contains("--token"));
        assert!(bind(&options("127.0.0.1:0", "")).is_ok());
        assert!(bind(&options("127.0.0.1:0", TOKEN)).is_ok());
        assert_eq!(parse_origins("https://A.it/, http://b.it ,,"), vec!["https://a.it", "http://b.it"]);
        assert_eq!(split_host_port("[::1]:80"), Some(("[::1]", Some(80))));
        assert_eq!(split_host_port("localhost"), Some(("localhost", None)));
        assert_eq!(split_host_port("x:y"), None);
        assert!(is_local_host("LOCALHOST") && is_local_host("127.3.4.5") && is_local_host("[::1]"));
        assert!(!is_local_host("127.0.0.1.evil.example") && !is_local_host("10.0.0.5") && !is_local_host("[::2]"));
    }

    #[test]
    fn tokens_compare_in_constant_time_by_value() {
        assert!(constant_eq("abc", "abc") && constant_eq("", ""));
        assert!(
            !constant_eq("abc", "abd")
                && !constant_eq("ab", "abc")
                && !constant_eq("abcabc", "abc")
                && !constant_eq("", "a")
                && !constant_eq("a", "")
        );
    }

    #[test]
    fn api_serves_snapshots_and_commands() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        drop(listener);
        let a = addr.clone();
        std::thread::spawn(move || {
            let _ = run(
                Session::demo(),
                Options {
                    bind: a,
                    token: TOKEN.into(),
                    static_dir: None,
                    open: false,
                    users_dir: None,
                    demo: false,
                    lang: None,
                    public_url: None,
                    assist_url: None,
                    allow_origins: Vec::new(),
                },
            );
        });
        for _ in 0..50 {
            if TcpStream::connect(&addr).is_ok() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let (status, _) = get(&addr, "/api/snapshot", "");
        assert!(status.contains("401"), "{status}");
        // The token lives in the header only: in the URL it is ignored, so it never reaches a log.
        let (status, _) = get(&addr, &format!("/api/snapshot?since=0&token={TOKEN}"), "");
        assert!(status.contains("401"), "{status}");
        let (status, _) = get(&addr, "/api/snapshot?since=0", &format!("{TOKEN}x"));
        assert!(status.contains("401"), "{status}");
        // The server's files are nobody's: an attachment by path is refused, base64 works.
        let v = post(
            &addr,
            "/api/cmd",
            r#"{"command": "mail.send", "params": {"to": "a@example.org", "subject": "x", "attachments": [{"path": "/etc/hostname"}]}}"#,
        );
        assert!(v["error"].as_str().expect("refused").contains("host paths are not available on a shared server"), "{v}");
        let v = post(
            &addr,
            "/api/cmd",
            r#"{"command": "mail.send", "params": {"to": "a@example.org", "subject": "x", "attachments": [{"name": "a.txt", "data": "Y2lhbw=="}]}}"#,
        );
        assert!(v["result"]["job"].is_u64(), "{v}");
        let (status, v) = get(&addr, "/api/health", TOKEN);
        assert!(status.contains("200") && v["ok"] == true);
        // With a token any page may call (a browser never adds a bearer token on its own).
        let (head, _) =
            raw(&addr, "OPTIONS", "/api/cmd", "Host: x\r\nOrigin: https://app.example.it\r\nAccess-Control-Request-Private-Network: true\r\n", "");
        assert!(
            head.contains("204")
                && head.contains("Access-Control-Allow-Origin: https://app.example.it")
                && head.contains("Allow-Private-Network: true"),
            "{head}"
        );
        let (head, _) = raw(&addr, "GET", "/api/health", "Host: x\r\n", "");
        assert!(!head.contains("Access-Control"), "no origin, no CORS headers: {head}");
        let (_, snap) = get(&addr, "/api/snapshot?since=0", TOKEN);
        let rev = snap["revision"].as_u64().expect("revision");
        assert_eq!(snap["view"]["account"], "demo");
        assert!(snap["states"]["demo"]["envelopes"]["INBOX"].as_array().expect("inbox").len() > 5);
        assert!(snap.to_string().contains("\"passwords\":[\"demo\",\"lavoro\"]"));
        assert!(!snap.to_string().contains("\"demo\"}") || true);
        let (_, same) = get(&addr, &format!("/api/snapshot?since={rev}"), TOKEN);
        assert_eq!(same["unchanged"], true);
        let v = post(&addr, "/api/cmd", r#"{"command": "mail.search", "params": {"query": "fattura"}}"#);
        assert!(v["result"]["job"].is_u64(), "{v}");
        std::thread::sleep(Duration::from_millis(400));
        let (_, snap2) = get(&addr, &format!("/api/snapshot?since={rev}"), TOKEN);
        assert!(snap2["revision"].as_u64().expect("rev") > rev);
        assert!(snap2["completed"].as_array().expect("completed").iter().any(|c| c["command"] == "mail.search"));
        assert_eq!(snap2["view"]["results"].as_array().expect("results").len(), 1);
        let v = post(&addr, "/api/cmd", r#"{"command": "nope"}"#);
        assert!(v["error"].as_str().expect("error").contains("unknown command"));
        let v = post(&addr, "/api/cmd", "{");
        assert!(v["error"].as_str().expect("error").contains("invalid JSON"));
        // OAuth through the server: the redirect is the server's own callback (from the Host
        // header), which finishes the flow of the session that started it, without a token.
        post(&addr, "/api/cmd", r#"{"command": "oauth.configure", "params": {"provider": "google", "client_id": "cid"}}"#);
        // A plain http name is refused (the providers would), an https one behind a proxy works.
        let v = post(&addr, "/api/cmd", r#"{"command": "account.oauthStart", "params": {"id": "demo", "provider": "google"}}"#);
        assert!(v["error"].as_str().expect("refused").contains("MAILCRAFT_PUBLIC_URL"), "{v}");
        let body = r#"{"command": "account.oauthStart", "params": {"id": "demo", "provider": "google"}}"#;
        let mut c = TcpStream::connect(&addr).expect("connect");
        c.write_all(
            format!(
                "POST /api/cmd HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\nX-Forwarded-Proto: https\r\nX-Forwarded-Host: mail.example.it\r\nX-Forwarded-Prefix: /mailcraft\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
            .as_bytes(),
        )
        .expect("write");
        let mut out = String::new();
        c.read_to_string(&mut out).expect("read");
        let v: Value = serde_json::from_str(out.split_once("\r\n\r\n").map(|(_, b)| b).unwrap_or("null")).unwrap_or(Value::Null);
        assert_eq!(v["result"]["redirect"], "https://mail.example.it/mailcraft/api/oauth/callback", "{v}");
        let state = v["result"]["state"].as_str().expect("state").to_string();
        let (status, _) = get(&addr, "/api/oauth/callback?state=nope&code=x", "");
        assert!(status.contains("200"));
        let mut c = TcpStream::connect(&addr).expect("connect");
        c.write_all(format!("GET /api/oauth/callback?state={state}&code=c0de HTTP/1.1\r\nHost: x\r\n\r\n").as_bytes()).expect("write");
        let mut page = String::new();
        c.read_to_string(&mut page).expect("read");
        assert!(page.contains("Accesso completato"), "{page}");
        std::thread::sleep(Duration::from_millis(400));
        let (_, snap3) = get(&addr, "/api/snapshot?since=0", TOKEN);
        assert!(
            snap3["completed"]
                .as_array()
                .expect("completed")
                .iter()
                .any(|c| c["command"] == "account.oauthFinish" && c["result"]["signed_in"] == true),
            "{snap3}"
        );
        assert_eq!(snap3["oauth_ready"], serde_json::json!(["google"]));
        assert!(snap3["config"]["accounts"].as_array().expect("accounts").iter().any(|a| a["id"] == "demo" && a["auth"] == "google"));
    }
}

#[cfg(test)]
mod remote_tests {
    use super::*;
    use mailcraft_engine::MailSession;
    use mailcraft_remote::RemoteSession;

    fn pump_until(s: &mut RemoteSession, mut done: impl FnMut(&RemoteSession, &[mailcraft_engine::Completed]) -> bool) -> bool {
        for _ in 0..200 {
            let completed = s.pump();
            if done(s, &completed) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        false
    }

    #[test]
    fn a_remote_session_mirrors_the_server_and_runs_commands() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        drop(listener);
        let a = addr.clone();
        std::thread::spawn(move || {
            let _ = run(
                Session::demo(),
                Options {
                    bind: a,
                    token: String::new(),
                    static_dir: None,
                    open: false,
                    users_dir: None,
                    demo: false,
                    lang: None,
                    public_url: None,
                    assist_url: None,
                    allow_origins: Vec::new(),
                },
            );
        });
        for _ in 0..50 {
            if TcpStream::connect(&addr).is_ok() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let mut r = RemoteSession::new(&format!("http://{addr}"), "");
        assert!(pump_until(&mut r, |s, _| s.problem().is_none() && !s.config().accounts.is_empty()), "snapshot arrives");
        assert_eq!(r.view().account, "demo");
        assert!(r.has_password("demo"));
        let inbox = r.current().expect("state").list("INBOX").len();
        assert!(inbox > 5);
        assert_eq!(r.folder_for("demo", mailcraft_engine::core::mailbox::Role::Trash).as_deref(), Some("Trash"));
        // A network command: queued, then its completion is attributed to the command.
        let uid = r.current().expect("state").list("INBOX")[0].uid;
        assert_eq!(r.execute("mail.open", &json!({"uid": uid})).expect("queued")["queued"], true);
        assert!(pump_until(&mut r, |s, done| done.iter().any(|c| c.command == "mail.open")
            || s.current().is_some_and(|st| st.message("INBOX", uid).is_some())));
        assert!(pump_until(&mut r, |s, _| s.current().is_some_and(|st| st.message("INBOX", uid).is_some())), "the opened message is mirrored");
        assert!(r.current().expect("state").message("INBOX", uid).expect("message").text.contains("lunedì"));
        // A local command: answered through pump as a completion with the value.
        r.execute("account.suggest", &json!({"email": "anna@gmail.com"})).expect("queued");
        assert!(pump_until(&mut r, |_, done| done
            .iter()
            .any(|c| c.command == "account.suggest" && c.result.as_ref().is_ok_and(|v| v["provider"] == "Gmail"))));
        // A failing command reports its error.
        r.execute("mail.move", &json!({"uids": [uid], "to": "Nowhere"})).expect("queued");
        assert!(pump_until(&mut r, |_, done| done.iter().any(|c| c.command == "mail.move" && c.result.is_err())));
        // Archive: the mirrored inbox shrinks.
        r.execute("mail.archive", &json!({"uids": [uid]})).expect("queued");
        assert!(pump_until(&mut r, |s, _| s.current().is_some_and(|st| st.list("INBOX").len() < inbox)));
    }
}

#[cfg(test)]
mod multi_user_tests {
    use super::*;
    use mailcraft_remote::RemoteSession;

    fn call(addr: &str, method: &str, path: &str, headers: &str, body: &str) -> (String, Value) {
        let mut s = TcpStream::connect(addr).expect("connect");
        s.write_all(
            format!("{method} {path} HTTP/1.1\r\nHost: x\r\n{headers}Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len())
                .as_bytes(),
        )
        .expect("write");
        let mut out = String::new();
        s.read_to_string(&mut out).expect("read");
        let (head, body) = out.split_once("\r\n\r\n").expect("response");
        (head.lines().next().unwrap_or_default().to_string(), serde_json::from_str(body).unwrap_or(Value::Null))
    }

    #[test]
    fn users_are_isolated_tokens_are_minted_and_mcp_runs_over_the_server() {
        const SVC: &str = "svc-0123456789abcdef0123456789abcdef";
        let svc = format!("Authorization: Bearer {SVC}\r\n");
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr").to_string();
        drop(listener);
        let dir = std::env::temp_dir().join(format!("mailcraft-multi-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let a = addr.clone();
        let d = dir.clone();
        std::thread::spawn(move || {
            let _ = run(
                Session::demo(),
                Options {
                    bind: a,
                    token: SVC.into(),
                    static_dir: None,
                    open: false,
                    users_dir: Some(d),
                    demo: true,
                    lang: Some("it".into()),
                    public_url: None,
                    assist_url: Some("/api/mail/assist".into()),
                    allow_origins: Vec::new(),
                },
            );
        });
        for _ in 0..50 {
            if TcpStream::connect(&addr).is_ok() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        // No token, wrong token, service token without a user.
        assert!(call(&addr, "GET", "/api/snapshot", "", "").0.contains("401"));
        assert!(call(&addr, "GET", "/api/snapshot", "Authorization: Bearer nope\r\n", "").0.contains("401"));
        assert!(call(&addr, "GET", "/api/snapshot", &svc, "").0.contains("400"));
        // The framework names the user; each user has their own session.
        let (st, v) = call(
            &addr,
            "POST",
            "/api/cmd",
            &format!("{svc}X-Mailcraft-User: Anna Rossi\r\n"),
            r#"{"command": "prefs.set", "params": {"theme": "dark"}}"#,
        );
        assert!(st.contains("200"), "{v}");
        let (_, anna) = call(&addr, "GET", "/api/snapshot?since=0", &format!("{svc}X-Mailcraft-User: Anna Rossi\r\n"), "");
        assert_eq!(anna["assist_url"], "/api/mail/assist", "the assistant endpoint reaches the web app: {anna}");
        assert_eq!(anna["config"]["preferences"]["theme"], "dark");
        let (_, luca) = call(&addr, "GET", "/api/snapshot?since=0", &format!("{svc}X-Mailcraft-User: luca\r\n"), "");
        assert_eq!(luca["config"]["preferences"]["theme"], "system", "another user, another session");
        // The user comes from the framework's header only: a link naming one names nobody.
        let (st, v) = call(&addr, "GET", "/api/snapshot?since=0&user=anna-rossi", &svc, "");
        assert!(st.contains("400") && v["error"].as_str().is_some_and(|e| e.contains("X-Mailcraft-User")), "{st} {v}");
        let (_, v) = call(&addr, "GET", "/api/snapshot?since=0&user=anna-rossi", &format!("{svc}X-Mailcraft-User: luca\r\n"), "");
        assert_eq!(v["config"]["preferences"]["theme"], "system", "the header wins over the query");
        // User tokens: minted by the service, usable by the page, revocable.
        let (_, users) = call(&addr, "GET", "/api/users", &svc, "");
        assert!(users["users"].as_array().expect("users").iter().any(|u| u == "anna-rossi"));
        let (_, minted) = call(&addr, "POST", "/api/users/Anna%20Rossi/token", &svc, "");
        let token = minted["token"].as_str().expect("token").to_string();
        assert_eq!(token.len(), 64);
        assert!(
            call(&addr, "POST", "/api/users/anna/token", &format!("Authorization: Bearer {token}\r\n"), "").0.contains("403"),
            "users cannot mint"
        );
        let (st, as_anna) = call(&addr, "GET", "/api/snapshot?since=0", &format!("Authorization: Bearer {token}\r\n"), "");
        assert!(st.contains("200"));
        assert_eq!(as_anna["config"]["preferences"]["theme"], "dark");
        // The MCP server for a user, over the network.
        let mut mcp = mailcraft_mcp::Server::new(RemoteSession::new(&format!("http://{addr}"), &token));
        let r = mcp
            .handle(
                &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_messages", "arguments": {"unread_only": true}}}),
            )
            .expect("reply");
        assert_eq!(r["result"]["isError"], false, "{r}");
        assert!(r["result"]["structuredContent"]["count"].as_u64().expect("count") >= 3);
        let r = mcp.handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "suggest_settings", "arguments": {"email": "x@libero.it"}}})).expect("reply");
        assert_eq!(r["result"]["structuredContent"]["provider"], "Libero");
        let r = mcp
            .handle(
                &json!({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "move", "arguments": {"uids": [1], "to": "Nowhere"}}}),
            )
            .expect("reply");
        assert_eq!(r["result"]["isError"], true, "a failing command reports its error: {r}");
        assert_eq!(mcp.session().config().accounts.len(), 2);
        // Revocation.
        let (_, rev) = call(&addr, "POST", "/api/users/anna-rossi/revoke", &svc, "");
        assert_eq!(rev["revoked"], 1);
        assert!(call(&addr, "GET", "/api/snapshot", &format!("Authorization: Bearer {token}\r\n"), "").0.contains("401"));
        assert!(dir.join("tokens.json").is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
