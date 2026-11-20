//! Accounts, preferences and secrets: what the user configures (by hand, or in plain language
//! through the MCP server) and how it is validated and stored.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::{Error, Result, clean, limits};

/// A stable account identifier (a slug made from the address, unique in the configuration).
pub type AccountId = String;

/// How a connection is encrypted.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Security {
    /// TLS from the first byte (IMAPS 993, SMTPS 465).
    #[default]
    Tls,
    /// Plain connection upgraded with STARTTLS (IMAP 143, SMTP 587).
    #[serde(alias = "starttls", alias = "start-tls")]
    StartTls,
    /// No encryption (local bridges and tests only).
    None,
}

impl Security {
    pub const ALL: [Self; 3] = [Self::Tls, Self::StartTls, Self::None];

    pub const fn name(self) -> &'static str {
        match self {
            Self::Tls => "tls",
            Self::StartTls => "starttls",
            Self::None => "none",
        }
    }

    /// Reads `tls`, `ssl`, `ssl/tls`, `starttls`, `none`, `plain`… (case-insensitive).
    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_ascii_lowercase().replace([' ', '-', '_', '/'], "").as_str() {
            "tls" | "ssl" | "ssltls" | "implicit" | "imaps" | "smtps" | "ssl3" => Some(Self::Tls),
            "starttls" | "explicit" | "upgrade" => Some(Self::StartTls),
            "none" | "plain" | "plaintext" | "no" | "off" | "clear" => Some(Self::None),
            _ => None,
        }
    }
}

/// An IMAP or SMTP endpoint.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Server {
    pub host: String,
    pub port: u16,
    #[serde(default)]
    pub security: Security,
}

impl Server {
    pub fn new(host: &str, port: u16, security: Security) -> Self {
        Self { host: clean(host, 253).trim().to_ascii_lowercase(), port, security }
    }

    /// `imap.example.com:993 (tls)`.
    pub fn describe(&self) -> String {
        format!("{}:{} ({})", self.host, self.port, self.security.name())
    }

    fn validate(&self, what: &str, problems: &mut Vec<String>) {
        if self.host.trim().is_empty() {
            problems.push(format!("{what}: the host is missing"));
        } else if !self.host.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == ':') {
            problems.push(format!("{what}: `{}` is not a valid host name", self.host));
        }
        if self.port == 0 {
            problems.push(format!("{what}: the port is missing"));
        }
    }
}

/// Where sent, draft, trashed, spam and archived messages go (IMAP names; empty = detect).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SpecialFolders {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub sent: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub drafts: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub trash: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub spam: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub archive: String,
}

/// One mail account. The password lives in [`Secrets`], never here.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Account {
    pub id: AccountId,
    /// Shown in the interface ("Work", "Aruba").
    pub name: String,
    pub email: String,
    /// The name put in `From:`; empty means the account name.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub display_name: String,
    /// Login name; empty means the e-mail address.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub username: String,
    pub imap: Server,
    pub smtp: Server,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub signature: String,
    /// Accent colour (hex) for the account chip.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub color: String,
    #[serde(default)]
    pub folders: SpecialFolders,
    /// Copy sent mail to the Sent folder (some providers, like Gmail, do it themselves).
    #[serde(default = "yes")]
    pub save_sent: bool,
    /// Accept a server certificate that does not validate (self-hosted servers only).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub trust_invalid_certs: bool,
    /// How the account logs in: `password` (empty = the same), or an OAuth provider id
    /// (`google`, `microsoft`) whose tokens live in [`Secrets`].
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub auth: String,
    /// The signature as HTML (logo, bold, links); `signature` is its plain-text version.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub signature_html: String,
}

const fn yes() -> bool {
    true
}

impl Default for Account {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: String::new(),
            email: String::new(),
            display_name: String::new(),
            username: String::new(),
            imap: Server::default(),
            smtp: Server::default(),
            signature: String::new(),
            color: String::new(),
            folders: SpecialFolders::default(),
            save_sent: true,
            trust_invalid_certs: false,
            auth: String::new(),
            signature_html: String::new(),
        }
    }
}

impl Account {
    /// The login name: the explicit user name or the address.
    pub fn login(&self) -> &str {
        if self.username.trim().is_empty() { &self.email } else { &self.username }
    }

    /// The plain-text signature: the one typed, or the HTML one reduced to text.
    pub fn signature_text(&self) -> String {
        if !self.signature.trim().is_empty() {
            return self.signature.trim().to_string();
        }
        crate::mime::html_to_text(&self.signature_html).trim().to_string()
    }

    /// The OAuth provider the account signs in with, if it does not use a password.
    pub fn oauth_provider(&self) -> Option<&'static crate::oauth::Provider> {
        if self.auth.is_empty() || self.auth == "password" { None } else { crate::oauth::provider(&self.auth) }
    }

    /// The `From:` display name.
    pub fn from_name(&self) -> &str {
        if self.display_name.trim().is_empty() { &self.name } else { &self.display_name }
    }

    /// What is wrong with the account, or nothing.
    pub fn problems(&self) -> Vec<String> {
        let mut p = Vec::new();
        if self.name.trim().is_empty() {
            p.push("the account needs a name".to_string());
        }
        if !is_email(&self.email) {
            p.push(format!("`{}` is not an e-mail address", self.email));
        }
        self.imap.validate("IMAP", &mut p);
        self.smtp.validate("SMTP", &mut p);
        match (self.imap.port, self.imap.security) {
            (993, Security::StartTls | Security::None) => p.push("IMAP port 993 normally uses TLS, not STARTTLS".to_string()),
            (143, Security::Tls) => p.push("IMAP port 143 normally uses STARTTLS, not TLS".to_string()),
            _ => {}
        }
        match (self.smtp.port, self.smtp.security) {
            (465, Security::StartTls | Security::None) => p.push("SMTP port 465 normally uses TLS, not STARTTLS".to_string()),
            (587 | 25, Security::Tls) => p.push(format!("SMTP port {} normally uses STARTTLS, not TLS", self.smtp.port)),
            _ => {}
        }
        p
    }

    /// Cleans every text field and clips it.
    pub fn sanitised(mut self) -> Self {
        self.id = slug(&self.id);
        self.name = clean(&self.name, 100).trim().to_string();
        self.email = clean(&self.email, 254).trim().to_string();
        self.display_name = clean(&self.display_name, 100).trim().to_string();
        self.username = clean(&self.username, 254).trim().to_string();
        self.signature = clean(&self.signature, 4_000);
        self.color = clean(&self.color, 9).trim().to_string();
        self.imap = Server::new(&self.imap.host, self.imap.port, self.imap.security);
        self.smtp = Server::new(&self.smtp.host, self.smtp.port, self.smtp.security);
        for f in [&mut self.folders.sent, &mut self.folders.drafts, &mut self.folders.trash, &mut self.folders.spam, &mut self.folders.archive] {
            *f = clean(f, 500).trim().to_string();
        }
        self
    }
}

/// A loose e-mail address check: one `@`, something on both sides, a dot in the domain.
pub fn is_email(s: &str) -> bool {
    let s = s.trim();
    let Some((local, domain)) = s.split_once('@') else { return false };
    !local.is_empty()
        && !domain.is_empty()
        && domain.contains('.')
        && !domain.starts_with('.')
        && !domain.ends_with('.')
        && s.len() <= 254
        && !s.chars().any(|c| c.is_whitespace() || c.is_control() || c == '<' || c == '>' || c == ',')
}

/// The domain part of an address, lower-case.
pub fn domain_of(email: &str) -> Option<String> {
    email.trim().rsplit_once('@').map(|(_, d)| d.trim().to_ascii_lowercase()).filter(|d| !d.is_empty())
}

/// An identifier from free text: lower-case ASCII letters, digits and dashes.
pub fn slug(text: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in text.trim().chars().take(80) {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            dash = false;
        } else if !dash && !out.is_empty() {
            out.push('-');
            dash = true;
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    out
}

/// Interface preferences.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Preferences {
    /// `en`, `it`, or empty for the system language.
    pub language: String,
    /// `light`, `dark` or `system`.
    pub theme: String,
    /// Minutes between automatic syncs (0 = manual).
    pub sync_minutes: u32,
    /// Messages per page in the list.
    pub page_size: u32,
    /// Show the reading pane beside the list (otherwise it replaces it, like Gmail).
    pub reading_pane: bool,
    /// Group messages into conversations.
    pub conversations: bool,
    /// Mark a message read when it is opened.
    pub mark_read_on_open: bool,
    /// Seconds to undo a send (0 = send at once).
    pub undo_send_seconds: u32,
    /// The account opened at start; empty = the first.
    pub default_account: String,
    /// Ask the assistant (the framework's `assist` API) for reply suggestions when a message
    /// is opened.
    pub assist_suggestions: bool,
    /// Let the assistant use cloud models too (otherwise local models only: the mail never
    /// leaves the network).
    pub assist_cloud: bool,
    /// The time zone dates are shown in: `auto` (the machine's or the browser's), or an
    /// offset like `+02:00`.
    pub time_offset: String,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            language: String::new(),
            theme: "system".into(),
            sync_minutes: 5,
            page_size: 50,
            reading_pane: true,
            conversations: true,
            mark_read_on_open: true,
            assist_suggestions: true,
            assist_cloud: false,
            time_offset: "auto".into(),
            undo_send_seconds: 5,
            default_account: String::new(),
        }
    }
}

impl Preferences {
    /// Sets one preference from text (what the MCP `set_preferences` tool and the CLI use).
    pub fn set(&mut self, key: &str, value: &str) -> Result<()> {
        let v = value.trim();
        let bool_of = |v: &str| match v.to_ascii_lowercase().as_str() {
            "true" | "yes" | "on" | "1" | "si" | "sì" => Ok(true),
            "false" | "no" | "off" | "0" => Ok(false),
            _ => Err(Error::Config(format!("`{key}` must be true or false, not `{v}`"))),
        };
        let num = |v: &str, max: u32| {
            v.parse::<u32>().ok().filter(|n| *n <= max).ok_or_else(|| Error::Config(format!("`{key}` must be a number up to {max}")))
        };
        match key.trim().to_ascii_lowercase().replace('-', "_").as_str() {
            "language" | "lang" | "lingua" => {
                let code = v.to_ascii_lowercase();
                if !matches!(code.as_str(), "" | "en" | "it" | "system") {
                    return Err(Error::Config("`language` must be en, it or system".into()));
                }
                self.language = if code == "system" { String::new() } else { code };
            }
            "theme" | "tema" => {
                let t = v.to_ascii_lowercase();
                self.theme = match t.as_str() {
                    "light" | "chiaro" => "light".into(),
                    "dark" | "scuro" => "dark".into(),
                    "system" | "auto" | "sistema" | "" => "system".into(),
                    _ => return Err(Error::Config("`theme` must be light, dark or system".into())),
                };
            }
            "sync_minutes" | "sync" | "sync_interval" => self.sync_minutes = num(v, 1440)?,
            "page_size" | "page" => self.page_size = num(v, limits::MAX_PAGE as u32)?.max(5),
            "reading_pane" | "preview" => self.reading_pane = bool_of(v)?,
            "conversations" | "threads" | "threading" => self.conversations = bool_of(v)?,
            "mark_read_on_open" | "mark_read" => self.mark_read_on_open = bool_of(v)?,
            "undo_send_seconds" | "undo_send" => self.undo_send_seconds = num(v, 30)?,
            "assist_suggestions" | "suggestions" | "suggerimenti" => self.assist_suggestions = bool_of(v)?,
            "assist_cloud" | "allow_cloud" | "cloud" => self.assist_cloud = bool_of(v)?,
            "time_offset" | "timezone" | "time_zone" | "fuso" | "fuso_orario" => {
                self.time_offset = match crate::message::time::parse_offset(v) {
                    Some(m) => crate::message::time::offset_text(m),
                    None if v.is_empty() || v.eq_ignore_ascii_case("auto") || v.eq_ignore_ascii_case("system") || v.eq_ignore_ascii_case("local") => {
                        "auto".into()
                    }
                    None => return Err(Error::Config(format!("`time_offset` must be auto, UTC or an offset like +02:00, not `{v}`"))),
                };
            }
            "default_account" => self.default_account = slug(v),
            other => return Err(Error::Config(format!("unknown preference `{other}`"))),
        }
        Ok(())
    }

    /// Every preference as `(key, value)` text.
    pub fn entries(&self) -> Vec<(&'static str, String)> {
        vec![
            ("language", if self.language.is_empty() { "system".into() } else { self.language.clone() }),
            ("theme", self.theme.clone()),
            ("sync_minutes", self.sync_minutes.to_string()),
            ("page_size", self.page_size.to_string()),
            ("reading_pane", self.reading_pane.to_string()),
            ("conversations", self.conversations.to_string()),
            ("mark_read_on_open", self.mark_read_on_open.to_string()),
            ("undo_send_seconds", self.undo_send_seconds.to_string()),
            ("default_account", self.default_account.clone()),
            ("assist_suggestions", self.assist_suggestions.to_string()),
            ("assist_cloud", self.assist_cloud.to_string()),
            ("time_offset", self.time_offset.clone()),
        ]
    }

    /// The offset from UTC in minutes that dates are shown in: the preference, else the host's.
    pub fn offset_minutes(&self) -> i32 {
        crate::message::time::parse_offset(&self.time_offset).unwrap_or_else(crate::message::time::host_offset_minutes)
    }
}

/// The configuration file: accounts and preferences (no secrets).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub version: u32,
    pub accounts: Vec<Account>,
    pub preferences: Preferences,
    /// OAuth clients by provider id (`google`, `microsoft`), created by the person in the
    /// provider's console. The environment (`MAILCRAFT_GOOGLE_CLIENT_ID`…) is the fallback.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub oauth: crate::oauth::Clients,
}

impl Default for Config {
    fn default() -> Self {
        Self { version: Self::VERSION, accounts: Vec::new(), preferences: Preferences::default(), oauth: BTreeMap::new() }
    }
}

impl Config {
    pub const VERSION: u32 = 1;

    /// The OAuth client for a provider: configured here, else from the environment.
    pub fn oauth_client(&self, provider: &str) -> Option<crate::oauth::ClientCredentials> {
        self.oauth.get(provider).filter(|c| !c.client_id.trim().is_empty()).cloned().or_else(|| crate::oauth::ClientCredentials::from_env(provider))
    }

    pub fn account(&self, id: &str) -> Option<&Account> {
        self.accounts.iter().find(|a| a.id == id)
    }

    pub fn account_mut(&mut self, id: &str) -> Option<&mut Account> {
        self.accounts.iter_mut().find(|a| a.id == id)
    }

    /// Finds an account by id, address or name (case-insensitive).
    pub fn find(&self, key: &str) -> Option<&Account> {
        let k = key.trim().to_ascii_lowercase();
        self.account(&k).or_else(|| self.accounts.iter().find(|a| a.email.eq_ignore_ascii_case(&k) || a.name.eq_ignore_ascii_case(&k)))
    }

    /// Adds or replaces an account after validation; a missing id is made from the address.
    pub fn upsert(&mut self, mut account: Account) -> Result<AccountId> {
        if account.id.trim().is_empty() {
            account.id = slug(&account.email);
        }
        let account = account.sanitised();
        if account.id.is_empty() {
            return Err(Error::Config("the account needs an id or an e-mail address".into()));
        }
        let problems = account.problems();
        if !problems.is_empty() {
            return Err(Error::Config(problems.join("; ")));
        }
        let id = account.id.clone();
        match self.accounts.iter_mut().find(|a| a.id == id) {
            Some(slot) => *slot = account,
            None => {
                if self.accounts.len() >= limits::MAX_ACCOUNTS {
                    return Err(Error::Config(format!("at most {} accounts", limits::MAX_ACCOUNTS)));
                }
                self.accounts.push(account);
            }
        }
        Ok(id)
    }

    pub fn remove(&mut self, id: &str) -> Result<Account> {
        let i = self.accounts.iter().position(|a| a.id == id).ok_or_else(|| Error::NoAccount(id.to_string()))?;
        if self.preferences.default_account == id {
            self.preferences.default_account.clear();
        }
        Ok(self.accounts.remove(i))
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_else(|_| "{}".to_string())
    }

    /// Reads a configuration, repairing what it can (bad accounts are dropped with a note).
    pub fn from_json(text: &str) -> Result<(Self, Vec<String>)> {
        let mut cfg: Self = serde_json::from_str(text).map_err(|e| Error::Config(format!("invalid configuration: {e}")))?;
        if cfg.version > Self::VERSION {
            return Err(Error::Config(format!("configuration version {} is newer than this MailCraft ({})", cfg.version, Self::VERSION)));
        }
        cfg.version = Self::VERSION;
        let mut notes = Vec::new();
        let mut seen = std::collections::BTreeSet::new();
        let accounts = std::mem::take(&mut cfg.accounts);
        for a in accounts.into_iter().take(limits::MAX_ACCOUNTS) {
            let label = if a.id.is_empty() { a.email.clone() } else { a.id.clone() };
            match cfg.upsert(a) {
                Ok(id) if seen.insert(id.clone()) => {}
                Ok(id) => notes.push(format!("duplicate account `{id}` replaced")),
                Err(e) => notes.push(format!("account `{label}` dropped: {e}")),
            }
        }
        Ok((cfg, notes))
    }

    pub fn load(path: &Path) -> Result<(Self, Vec<String>)> {
        match std::fs::read_to_string(path) {
            Ok(text) => Self::from_json(&text),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok((Self::default(), Vec::new())),
            Err(e) => Err(Error::Io(format!("{}: {e}", path.display()))),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        write_private(path, self.to_json().as_bytes())
    }
}

/// Passwords by account id, kept apart from the configuration in a file readable only by the
/// user (0600 on Unix).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Secrets {
    pub passwords: BTreeMap<AccountId, String>,
    /// OAuth tokens of the accounts that sign in with a provider.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub tokens: BTreeMap<AccountId, crate::oauth::Tokens>,
}

impl Secrets {
    pub fn password(&self, id: &str) -> Option<&str> {
        self.passwords.get(id).map(String::as_str).filter(|p| !p.is_empty())
    }

    pub fn tokens(&self, id: &str) -> Option<&crate::oauth::Tokens> {
        self.tokens.get(id).filter(|t| !t.access_token.is_empty() || !t.refresh_token.is_empty())
    }

    pub fn set_tokens(&mut self, id: &str, tokens: Option<crate::oauth::Tokens>) {
        match tokens {
            Some(t) => {
                self.tokens.insert(id.to_string(), t);
            }
            None => {
                self.tokens.remove(id);
            }
        }
    }

    /// What logs the account in: its password, or its OAuth access token.
    pub fn credential(&self, account: &Account) -> Option<&str> {
        if account.oauth_provider().is_some() {
            self.tokens(&account.id).map(|t| t.access_token.as_str()).filter(|t| !t.is_empty())
        } else {
            self.password(&account.id)
        }
    }

    /// The account can log in (a password, or OAuth tokens).
    pub fn has_credential(&self, account: &Account) -> bool {
        if account.oauth_provider().is_some() { self.tokens(&account.id).is_some() } else { self.password(&account.id).is_some() }
    }

    pub fn set_password(&mut self, id: &str, password: &str) {
        if password.is_empty() {
            self.passwords.remove(id);
        } else {
            self.passwords.insert(id.to_string(), clean(password, 1_000));
        }
    }

    pub fn load(path: &Path) -> Result<Self> {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).map_err(|e| Error::Config(format!("invalid secrets file: {e}"))),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(Error::Io(format!("{}: {e}", path.display()))),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        let text = serde_json::to_string_pretty(self).map_err(|e| Error::Io(e.to_string()))?;
        write_private(path, text.as_bytes())
    }
}

/// Writes a file readable only by the user (0600 on Unix), creating the directory.
pub fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| Error::Io(format!("{}: {e}", dir.display())))?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes).map_err(|e| Error::Io(format!("{}: {e}", tmp.display())))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, path).map_err(|e| Error::Io(format!("{}: {e}", path.display())))
}

/// Where MailCraft keeps its files: `$MAILCRAFT_HOME`, else the platform configuration
/// directory (`~/.config/mailcraft` on Linux, `~/Library/Application Support/mailcraft` on macOS,
/// `%APPDATA%\mailcraft` on Windows).
pub fn home_dir() -> PathBuf {
    if let Some(h) = std::env::var_os("MAILCRAFT_HOME").filter(|h| !h.is_empty()) {
        return PathBuf::from(h);
    }
    #[cfg(target_os = "macos")]
    let base = std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library").join("Application Support"));
    #[cfg(target_os = "windows")]
    let base = std::env::var_os("APPDATA").map(PathBuf::from);
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")));
    base.unwrap_or_else(|| PathBuf::from(".")).join("mailcraft")
}

pub fn config_path() -> PathBuf {
    home_dir().join("config.json")
}

pub fn secrets_path() -> PathBuf {
    home_dir().join("secrets.json")
}

pub fn cache_dir() -> PathBuf {
    home_dir().join("cache")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn account() -> Account {
        Account {
            name: "Work".into(),
            email: "Anna.Rossi@Example.com".into(),
            imap: Server::new("imap.example.com", 993, Security::Tls),
            smtp: Server::new("smtp.example.com", 587, Security::StartTls),
            ..Account::default()
        }
    }

    #[test]
    fn accounts_are_validated_and_slugged() {
        let mut cfg = Config::default();
        let id = cfg.upsert(account()).unwrap();
        assert_eq!(id, "anna-rossi-example-com");
        assert_eq!(cfg.account(&id).unwrap().login(), "Anna.Rossi@Example.com");
        assert!(cfg.find("anna.rossi@example.com").is_some());
        assert!(cfg.find("work").is_some());
        let bad = Account { email: "nope".into(), imap: Server::new("", 0, Security::Tls), ..account() };
        let e = cfg.upsert(bad).unwrap_err().to_string();
        assert!(e.contains("not an e-mail address") && e.contains("IMAP: the host is missing") && e.contains("port is missing"), "{e}");
        let mismatch = Account { imap: Server::new("imap.example.com", 993, Security::StartTls), ..account() };
        assert!(mismatch.problems().iter().any(|p| p.contains("993")));
        assert_eq!(Security::parse("SSL/TLS"), Some(Security::Tls));
        assert_eq!(Security::parse("StartTLS"), Some(Security::StartTls));
        assert_eq!(Security::parse("plain"), Some(Security::None));
        assert_eq!(Security::parse("quantum"), None);
    }

    #[test]
    fn config_round_trips_and_repairs() {
        let mut cfg = Config::default();
        cfg.upsert(account()).unwrap();
        cfg.preferences.set("theme", "scuro").unwrap();
        cfg.preferences.set("sync", "10").unwrap();
        assert!(cfg.preferences.set("theme", "neon").is_err());
        assert!(cfg.preferences.set("sync", "99999").is_err());
        assert!(cfg.preferences.set("whatever", "1").is_err());
        let (back, notes) = Config::from_json(&cfg.to_json()).unwrap();
        assert_eq!(back, cfg);
        assert!(notes.is_empty());
        let hostile = r#"{"version":1,"accounts":[{"id":"x","name":"","email":"bad","imap":{"host":"h","port":1},"smtp":{"host":"h","port":1}},
            {"id":"ok","name":"Ok","email":"a@b.co","imap":{"host":"h","port":993},"smtp":{"host":"h","port":465}},
            {"id":"ok","name":"Dup","email":"a@b.co","imap":{"host":"h","port":993},"smtp":{"host":"h","port":465}}]}"#;
        let (cfg, notes) = Config::from_json(hostile).unwrap();
        assert_eq!(cfg.accounts.len(), 1);
        assert_eq!(notes.len(), 2, "{notes:?}");
        assert!(Config::from_json(r#"{"version":99}"#).is_err());
        assert!(Config::from_json("{").is_err());
    }

    #[test]
    fn files_are_private() {
        let dir = std::env::temp_dir().join(format!("mailcraft-test-{}", std::process::id()));
        let mut secrets = Secrets::default();
        secrets.set_password("work", "s3cret");
        let path = dir.join("secrets.json");
        secrets.save(&path).unwrap();
        assert_eq!(Secrets::load(&path).unwrap().password("work"), Some("s3cret"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        }
        let mut cfg = Config::default();
        cfg.upsert(account()).unwrap();
        cfg.save(&dir.join("config.json")).unwrap();
        assert_eq!(Config::load(&dir.join("config.json")).unwrap().0, cfg);
        assert_eq!(Config::load(&dir.join("missing.json")).unwrap().0, Config::default());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
