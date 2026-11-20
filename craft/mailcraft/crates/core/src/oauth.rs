//! OAuth 2.0 for mail providers that no longer take passwords (Google, Microsoft 365): the
//! provider table, the authorization URL with PKCE, the tokens, the XOAUTH2 SASL string and the
//! broker that exchanges a code for tokens and refreshes them (`net` implements it over HTTP;
//! [`MockBroker`] serves the tests).
//!
//! The flow is the one Thunderbird and Outlook use: MailCraft opens the provider's consent page
//! in the browser, the provider sends the browser back to a redirect URL with a `code`, and the
//! code is exchanged for an access token (short-lived, used as the IMAP/SMTP password with the
//! XOAUTH2 mechanism) and a refresh token (kept in the secrets file).
//!
//! Every provider requires an OAuth *client* registered by whoever ships the app. Thunderbird
//! and Outlook carry theirs inside the program; MailCraft does the same when it is built with
//! `MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID` / `..._SECRET` (and the `MICROSOFT` pair) set, so a
//! distribution's users just press "Sign in with Google". Without a built-in client, the
//! person (or the framework operator) creates one in the provider's console and gives
//! MailCraft its id and secret (`oauth.configure`, or the `MAILCRAFT_GOOGLE_CLIENT_ID` /
//! `MAILCRAFT_GOOGLE_CLIENT_SECRET` environment variables at run time). The secret of a
//! "desktop" client is not a secret in the provider's own terms (the app is public), which is
//! why Thunderbird keeps it in its source.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::message::time;
use crate::mime::base64_encode;
use crate::{Error, Result, clip};

/// A provider's OAuth endpoints and the scope that unlocks IMAP and SMTP.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Provider {
    /// `google`, `microsoft`.
    pub id: &'static str,
    pub name: &'static str,
    pub auth_url: &'static str,
    pub token_url: &'static str,
    pub scope: &'static str,
    /// Address domains that always use this provider (custom domains are chosen by hand).
    pub domains: &'static [&'static str],
    /// The provider's mail servers, used for every account that signs in with it (a Google
    /// Workspace or Microsoft 365 custom domain has no servers of its own).
    pub imap: (&'static str, u16),
    pub smtp: (&'static str, u16, bool),
    /// Host suffixes that belong to the provider.
    pub hosts: &'static [&'static str],
    /// Where the client is created and what to pick there.
    pub console: &'static str,
    pub note: &'static str,
    pub note_it: &'static str,
}

pub const PROVIDERS: [Provider; 2] = [
    Provider {
        id: "google",
        name: "Google",
        auth_url: "https://accounts.google.com/o/oauth2/v2/auth",
        token_url: "https://oauth2.googleapis.com/token",
        scope: "https://mail.google.com/",
        domains: &["gmail.com", "googlemail.com"],
        imap: ("imap.gmail.com", 993),
        smtp: ("smtp.gmail.com", 465, false),
        hosts: &["gmail.com", "google.com", "googlemail.com"],
        console: "https://console.cloud.google.com/apis/credentials",
        note: "Google: create an OAuth client ID in Google Cloud Console (APIs & Services › Credentials › Create credentials › OAuth client ID; type Desktop app for the desktop client, Web application with the server's /api/oauth/callback URL for the web app), configure the consent screen with the scope https://mail.google.com/ and add yourself as a test user, then paste the client id and secret in MailCraft and sign in.",
        note_it: "Google: crea un ID client OAuth nella Google Cloud Console (API e servizi › Credenziali › Crea credenziali › ID client OAuth; tipo App desktop per il client desktop, Applicazione web con l'URL /api/oauth/callback del server per l'app web), configura la schermata di consenso con l'ambito https://mail.google.com/ e aggiungiti come utente di test, poi incolla id e segreto del client in MailCraft e accedi.",
    },
    Provider {
        id: "microsoft",
        name: "Microsoft 365",
        auth_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scope: "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send offline_access",
        domains: &["outlook.com", "outlook.it", "hotmail.com", "hotmail.it", "live.com", "live.it", "msn.com"],
        imap: ("outlook.office365.com", 993),
        smtp: ("smtp.office365.com", 587, true),
        hosts: &["office365.com", "outlook.com", "office.com"],
        console: "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        note: "Microsoft: register an app in Entra ID (App registrations › New registration; redirect URI type Mobile and desktop applications with http://127.0.0.1 for the desktop client, Web with the server's /api/oauth/callback URL for the web app), add the API permissions IMAP.AccessAsUser.All, SMTP.Send and offline_access (Office 365 Exchange Online), create a client secret, then paste the application id and the secret in MailCraft and sign in.",
        note_it: "Microsoft: registra un'app in Entra ID (Registrazioni app › Nuova registrazione; URI di reindirizzamento di tipo Applicazioni per dispositivi mobili e desktop con http://127.0.0.1 per il client desktop, Web con l'URL /api/oauth/callback del server per l'app web), aggiungi le autorizzazioni IMAP.AccessAsUser.All, SMTP.Send e offline_access (Office 365 Exchange Online), crea un segreto client, poi incolla id dell'applicazione e segreto in MailCraft e accedi.",
    },
];

impl Provider {
    /// The host belongs to this provider (so an account can keep it).
    pub fn owns_host(&self, host: &str) -> bool {
        let h = host.trim().to_ascii_lowercase();
        self.hosts.iter().any(|s| h == *s || h.ends_with(&format!(".{s}")))
    }

    /// The provider's IMAP and SMTP servers.
    pub fn servers(&self) -> (crate::account::Server, crate::account::Server) {
        use crate::account::{Security, Server};
        (
            Server::new(self.imap.0, self.imap.1, Security::Tls),
            Server::new(self.smtp.0, self.smtp.1, if self.smtp.2 { Security::StartTls } else { Security::Tls }),
        )
    }

    /// Points an account that signs in with this provider at the provider's servers, unless
    /// it already uses them (a custom domain's guessed hosts never work).
    pub fn apply_servers(&self, account: &mut crate::account::Account) -> bool {
        if self.owns_host(&account.imap.host) && self.owns_host(&account.smtp.host) {
            return false;
        }
        let (imap, smtp) = self.servers();
        account.imap = imap;
        account.smtp = smtp;
        true
    }
}

pub fn provider(id: &str) -> Option<&'static Provider> {
    let id = id.trim().to_ascii_lowercase();
    PROVIDERS.iter().find(|p| p.id == id)
}

/// The provider an address always belongs to (`gmail.com` → Google); `None` for custom domains.
pub fn provider_for_email(email: &str) -> Option<&'static Provider> {
    let domain = email.trim().rsplit('@').next()?.to_ascii_lowercase();
    PROVIDERS.iter().find(|p| p.domains.contains(&domain.as_str()))
}

/// The OAuth client MailCraft presents to a provider (created by the person in the provider's
/// console).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClientCredentials {
    pub client_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub client_secret: String,
}

impl ClientCredentials {
    /// The client from the environment (`MAILCRAFT_GOOGLE_CLIENT_ID`…) for servers and Docker,
    /// else the one compiled into this build ([`ClientCredentials::builtin`]).
    pub fn from_env(provider: &str) -> Option<Self> {
        let key = provider.to_ascii_uppercase();
        match std::env::var(format!("MAILCRAFT_{key}_CLIENT_ID")).ok().filter(|v| !v.trim().is_empty()) {
            Some(client_id) => {
                let client_secret = std::env::var(format!("MAILCRAFT_{key}_CLIENT_SECRET")).unwrap_or_default();
                Some(Self { client_id: clip(client_id.trim(), 500).to_string(), client_secret: clip(client_secret.trim(), 500).to_string() })
            }
            None => Self::builtin(provider),
        }
    }

    /// The client compiled into this build (`MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID` and
    /// `MAILCRAFT_BUILTIN_GOOGLE_CLIENT_SECRET` at build time; the same for `MICROSOFT`): what
    /// lets a distribution's users sign in without creating a client themselves.
    pub fn builtin(provider: &str) -> Option<Self> {
        let (id, secret) = match provider {
            "google" => (option_env!("MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID"), option_env!("MAILCRAFT_BUILTIN_GOOGLE_CLIENT_SECRET")),
            "microsoft" => (option_env!("MAILCRAFT_BUILTIN_MICROSOFT_CLIENT_ID"), option_env!("MAILCRAFT_BUILTIN_MICROSOFT_CLIENT_SECRET")),
            _ => (None, None),
        };
        let id = id.map(str::trim).filter(|v| !v.is_empty())?;
        Some(Self { client_id: id.to_string(), client_secret: secret.map(str::trim).unwrap_or_default().to_string() })
    }
}

/// The tokens of one account. The access token is the IMAP/SMTP password for a while; the
/// refresh token renews it.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tokens {
    pub access_token: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub refresh_token: String,
    /// Unix seconds when the access token stops working.
    #[serde(default)]
    pub expires_at: i64,
}

impl Tokens {
    /// Expired, or about to (a minute of slack for the round trip).
    pub fn expired(&self, now: i64) -> bool {
        self.access_token.is_empty() || self.expires_at.saturating_sub(60) <= now
    }

    /// Reads a token endpoint answer; keeps `old_refresh` when the provider sends no new one.
    pub fn from_response(v: &Value, old_refresh: &str, now: i64) -> Result<Self> {
        match v.get("error") {
            Some(Value::String(e)) => {
                let detail = v.get("error_description").and_then(Value::as_str).unwrap_or("");
                return Err(Error::Auth(format!("OAuth: {e} {detail}").trim().to_string()));
            }
            Some(Value::Object(o)) => {
                let message = o.get("message").and_then(Value::as_str).unwrap_or("");
                let status = o.get("status").and_then(Value::as_str).unwrap_or("");
                return Err(Error::Auth(format!("OAuth: {status} {message}").trim().to_string()));
            }
            _ => {}
        }
        // An answer without a token is reported with what it said (no token is in it).
        let access_token = v
            .get("access_token")
            .and_then(Value::as_str)
            .filter(|t| !t.is_empty())
            .ok_or_else(|| Error::Auth(format!("OAuth: no access token in the answer: {}", clip(&v.to_string(), 400))))?;
        let refresh_token = v.get("refresh_token").and_then(Value::as_str).filter(|t| !t.is_empty()).unwrap_or(old_refresh);
        let expires_in = v.get("expires_in").and_then(Value::as_i64).unwrap_or(3600).clamp(60, 86_400 * 30);
        Ok(Self {
            access_token: clip(access_token, 8_000).to_string(),
            refresh_token: clip(refresh_token, 8_000).to_string(),
            expires_at: now.saturating_add(expires_in),
        })
    }
}

/// An authorization in progress: what the callback must match and what the exchange needs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Flow {
    pub provider: &'static Provider,
    pub account: String,
    pub state: String,
    pub verifier: String,
    pub redirect: String,
    pub started: i64,
}

impl Flow {
    /// Ten minutes to finish in the browser.
    pub fn expired(&self, now: i64) -> bool {
        now.saturating_sub(self.started) > 600
    }
}

/// Starts a flow: the state, the PKCE verifier and the URL to open in the browser.
pub fn start(provider: &'static Provider, client: &ClientCredentials, account: &str, email: &str, redirect: &str) -> (Flow, String) {
    let state = random_string(32);
    let verifier = random_string(64);
    let challenge = base64url(&sha256(verifier.as_bytes()));
    let mut params = vec![
        ("client_id", client.client_id.as_str()),
        ("redirect_uri", redirect),
        ("response_type", "code"),
        ("scope", provider.scope),
        ("state", state.as_str()),
        ("code_challenge", challenge.as_str()),
        ("code_challenge_method", "S256"),
        ("access_type", "offline"),
        ("prompt", "consent"),
    ];
    if !email.is_empty() {
        params.push(("login_hint", email));
    }
    let url = format!("{}?{}", provider.auth_url, form(&params));
    let flow = Flow { provider, account: account.to_string(), state, verifier, redirect: redirect.to_string(), started: time::now() };
    (flow, url)
}

/// The body of the token request that turns a code into tokens.
pub fn exchange_body(client: &ClientCredentials, code: &str, verifier: &str, redirect: &str) -> String {
    let mut params = vec![
        ("client_id", client.client_id.as_str()),
        ("code", code),
        ("code_verifier", verifier),
        ("redirect_uri", redirect),
        ("grant_type", "authorization_code"),
    ];
    if !client.client_secret.is_empty() {
        params.push(("client_secret", client.client_secret.as_str()));
    }
    form(&params)
}

/// The body of the token request that renews an access token.
pub fn refresh_body(client: &ClientCredentials, refresh_token: &str) -> String {
    let mut params = vec![("client_id", client.client_id.as_str()), ("refresh_token", refresh_token), ("grant_type", "refresh_token")];
    if !client.client_secret.is_empty() {
        params.push(("client_secret", client.client_secret.as_str()));
    }
    form(&params)
}

/// The SASL XOAUTH2 string (before base64) for IMAP `AUTHENTICATE` and SMTP `AUTH`.
pub fn xoauth2(user: &str, access_token: &str) -> String {
    format!("user={user}\u{1}auth=Bearer {access_token}\u{1}\u{1}")
}

/// Talks to the token endpoint. `net` implements it over HTTPS.
pub trait OAuthBroker: Send + Sync {
    fn exchange(&self, provider: &Provider, client: &ClientCredentials, code: &str, verifier: &str, redirect: &str) -> Result<Tokens>;
    fn refresh(&self, provider: &Provider, client: &ClientCredentials, refresh_token: &str) -> Result<Tokens>;
}

/// A build without network: every OAuth call fails with a clear message.
pub struct NoBroker;

impl OAuthBroker for NoBroker {
    fn exchange(&self, _: &Provider, _: &ClientCredentials, _: &str, _: &str, _: &str) -> Result<Tokens> {
        Err(Error::Auth("OAuth is not available in this build".into()))
    }

    fn refresh(&self, _: &Provider, _: &ClientCredentials, _: &str) -> Result<Tokens> {
        Err(Error::Auth("OAuth is not available in this build".into()))
    }
}

/// A broker for tests: `code` → access token `access-<code>`, refresh → `refreshed-<n>`.
#[derive(Default)]
pub struct MockBroker {
    calls: std::sync::Mutex<Vec<String>>,
}

impl MockBroker {
    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().map(|c| c.clone()).unwrap_or_default()
    }
}

impl OAuthBroker for MockBroker {
    fn exchange(&self, provider: &Provider, client: &ClientCredentials, code: &str, verifier: &str, redirect: &str) -> Result<Tokens> {
        if let Ok(mut c) = self.calls.lock() {
            c.push(format!("exchange {} {} {code} {redirect} verifier={}", provider.id, client.client_id, verifier.len()));
        }
        if code == "bad" {
            return Err(Error::Auth("OAuth: invalid_grant".into()));
        }
        Ok(Tokens { access_token: format!("access-{code}"), refresh_token: format!("refresh-{code}"), expires_at: time::now() + 3600 })
    }

    fn refresh(&self, provider: &Provider, _: &ClientCredentials, refresh_token: &str) -> Result<Tokens> {
        if let Ok(mut c) = self.calls.lock() {
            c.push(format!("refresh {} {refresh_token}", provider.id));
        }
        Ok(Tokens { access_token: format!("refreshed-{refresh_token}"), refresh_token: refresh_token.to_string(), expires_at: time::now() + 3600 })
    }
}

/// The map of configured clients by provider id, as the configuration keeps it.
pub type Clients = BTreeMap<String, ClientCredentials>;

/// Why a provider will refuse this redirect URL, if it will: Google and Microsoft take
/// `http://` only on `localhost`/`127.0.0.1`, and never a private IP address (a framework on a
/// LAN needs a host name with HTTPS and `MAILCRAFT_PUBLIC_URL`).
pub fn redirect_problem(redirect: &str) -> Option<String> {
    let (scheme, rest) = redirect.split_once("://")?;
    let host = rest.split(['/', '?', '#']).next().unwrap_or("").rsplit('@').next().unwrap_or("");
    let host = host.trim_start_matches('[').split([']', ':']).next().unwrap_or("").to_ascii_lowercase();
    let local = host == "localhost" || host == "127.0.0.1" || host == "::1" || host.ends_with(".localhost");
    if local {
        return None;
    }
    let is_ip = host.split('.').count() == 4 && host.split('.').all(|p| p.parse::<u8>().is_ok()) || host.contains(':');
    if is_ip {
        return Some(format!(
            "the providers refuse a redirect on an IP address ({host}): reach the server through a host name with HTTPS and set MAILCRAFT_PUBLIC_URL (or --public-url) to it; only localhost works without"
        ));
    }
    if scheme.eq_ignore_ascii_case("http") {
        return Some(format!(
            "the providers take http:// redirects only on localhost ({host} must be https): put HTTPS in front of the server and set MAILCRAFT_PUBLIC_URL (or --public-url) to https://{host}"
        ));
    }
    None
}

/// `application/x-www-form-urlencoded`.
pub fn form(params: &[(&str, &str)]) -> String {
    params.iter().map(|(k, v)| format!("{}={}", url_encode(k), url_encode(v))).collect::<Vec<_>>().join("&")
}

pub fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Reads the `code`, `state` and `error` of a redirect's query string.
pub fn parse_callback(query: &str) -> BTreeMap<String, String> {
    let query = query.trim_start_matches('?');
    query
        .split('&')
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
            if k.is_empty() { None } else { Some((url_decode(k), url_decode(v))) }
        })
        .collect()
}

pub fn url_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes.get(i) {
            Some(b'+') => out.push(b' '),
            Some(b'%') => {
                let hex = bytes.get(i + 1..i + 3).and_then(|h| std::str::from_utf8(h).ok()).and_then(|h| u8::from_str_radix(h, 16).ok());
                match hex {
                    Some(b) => {
                        out.push(b);
                        i += 2;
                    }
                    None => out.push(b'%'),
                }
            }
            Some(b) => out.push(*b),
            None => break,
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn base64url(bytes: &[u8]) -> String {
    base64_encode(bytes).replace('+', "-").replace('/', "_").trim_end_matches('=').to_string()
}

/// `n` characters of `[A-Za-z0-9-._~]` from the system's randomness (a fixed, poorer fallback
/// only if the system gives none, which the browser build never asks for).
pub fn random_string(n: usize) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    let mut bytes = vec![0u8; n];
    if getrandom::getrandom(&mut bytes).is_err() {
        let seed = sha256(format!("{}-{:p}", time::now(), &bytes).as_bytes());
        for (i, b) in bytes.iter_mut().enumerate() {
            *b = seed.get(i % seed.len()).copied().unwrap_or(0).wrapping_add(i as u8);
        }
    }
    bytes.iter().map(|b| ALPHABET.get(usize::from(*b) % ALPHABET.len()).copied().unwrap_or(b'a') as char).collect()
}

pub fn sha256(data: &[u8]) -> Vec<u8> {
    use sha2::Digest;
    sha2::Sha256::digest(data).to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flows_urls_bodies_and_sasl() {
        let google = provider("google").unwrap();
        assert_eq!(provider_for_email("anna@gmail.com").map(|p| p.id), Some("google"));
        assert_eq!(provider_for_email("luca@hotmail.it").map(|p| p.id), Some("microsoft"));
        assert!(provider_for_email("info@sirius.it").is_none());
        let client = ClientCredentials { client_id: "id-1".into(), client_secret: "s&s".into() };
        let (flow, url) = start(google, &client, "anna", "anna@gmail.com", "http://127.0.0.1:4242/");
        assert!(url.starts_with("https://accounts.google.com/o/oauth2/v2/auth?client_id=id-1&redirect_uri=http%3A%2F%2F127.0.0.1%3A4242%2F"));
        assert!(url.contains("&code_challenge_method=S256&") && url.contains("login_hint=anna%40gmail.com"));
        assert!(url.contains(&format!("state={}", flow.state)));
        assert_eq!(flow.verifier.len(), 64);
        assert_ne!(flow.state, random_string(32));
        assert!(!flow.expired(time::now()));
        let body = exchange_body(&client, "c0de", &flow.verifier, &flow.redirect);
        assert!(body.contains("code=c0de&code_verifier=") && body.ends_with("grant_type=authorization_code&client_secret=s%26s"));
        assert!(refresh_body(&client, "r1").starts_with("client_id=id-1&refresh_token=r1&grant_type=refresh_token"));
        assert_eq!(xoauth2("a@b.c", "tok"), "user=a@b.c\u{1}auth=Bearer tok\u{1}\u{1}");
        let t = Tokens::from_response(&serde_json::json!({"access_token": "x", "expires_in": 3599}), "old", 1000).unwrap();
        assert_eq!((t.access_token.as_str(), t.refresh_token.as_str(), t.expires_at), ("x", "old", 4599));
        assert!(!t.expired(1000) && t.expired(4540));
        assert_eq!(
            Tokens::from_response(&serde_json::json!({"error": "invalid_grant", "error_description": "expired"}), "", 0).unwrap_err().to_string(),
            "login failed: OAuth: invalid_grant expired"
        );
        let e =
            Tokens::from_response(&serde_json::json!({"error": {"status": "PERMISSION_DENIED", "message": "nope"}}), "", 0).unwrap_err().to_string();
        assert_eq!(e, "login failed: OAuth: PERMISSION_DENIED nope");
        let e = Tokens::from_response(&serde_json::json!({"message": "blocked by proxy"}), "", 0).unwrap_err().to_string();
        assert!(e.contains("blocked by proxy"), "{e}");
        let q = parse_callback("?code=4%2FabC&state=s1&scope=https%3A%2F%2Fmail.google.com%2F");
        assert_eq!((q["code"].as_str(), q["state"].as_str()), ("4/abC", "s1"));
        assert_eq!(url_decode("a+b%zz"), "a b%zz");
        assert!(google.owns_host("imap.gmail.com") && !google.owns_host("imap.example.it"));
        let mut a = crate::account::Account { email: "anna@example.it".into(), auth: "google".into(), ..Default::default() };
        a.imap = crate::account::Server::new("imap.example.it", 993, crate::account::Security::Tls);
        assert!(google.apply_servers(&mut a));
        assert_eq!((a.imap.host.as_str(), a.smtp.host.as_str(), a.smtp.port), ("imap.gmail.com", "smtp.gmail.com", 465));
        assert!(!google.apply_servers(&mut a), "already there");
        assert!(redirect_problem("http://127.0.0.1:4242/").is_none());
        assert!(redirect_problem("http://localhost:8080/mailcraft/api/oauth/callback").is_none());
        assert!(redirect_problem("https://mail.example.it/api/oauth/callback").is_none());
        assert!(redirect_problem("http://192.0.2.80:8080/mailcraft/api/oauth/callback").unwrap().contains("IP address"));
        assert!(redirect_problem("http://emily.lan:8080/api/oauth/callback").unwrap().contains("https"));
        let mock = MockBroker::default();
        assert_eq!(mock.exchange(google, &client, "c", "v", "r").unwrap().access_token, "access-c");
        assert!(mock.exchange(google, &client, "bad", "v", "r").is_err());
        assert_eq!(mock.calls().len(), 2);
    }
}
