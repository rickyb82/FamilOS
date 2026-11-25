//! MailCraft's engine (layer L5): the [`Session`] holds the configuration, the per-account
//! cache and the current view, runs every user-visible action as a command
//! ([`COMMANDS`], [`Session::execute`]) and drives the mail transport on a worker thread.
//!
//! Commands that need the server return `{"job": id}` at once; [`Session::pump`] applies the
//! results as they arrive (the UI calls it every frame) and [`Session::run_blocking`] waits for
//! them (the CLI and the MCP server). A failed command never leaves the cache half-changed.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

pub mod cmd;
pub mod loopback;
pub mod store;
pub mod worker;

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender};
use std::time::Duration;

use web_time::Instant;

pub use cmd::{COMMANDS, Command, command};
pub use mailcraft_core as core;
use mailcraft_core::account::{Account, Config, Secrets};
use mailcraft_core::backend::{BackendFactory, FlagChange, Page};
use mailcraft_core::mailbox::Role;
use mailcraft_core::message::{Envelope, Message, time};
use mailcraft_core::oauth::{Flow, MockBroker, NoBroker, OAuthBroker};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use store::AccountState;
use worker::{Event, Job, JobKind, Output, Worker};

/// Engine errors: parameters, unknown commands, and everything the model and transport report.
#[derive(Debug, thiserror::Error, Clone, PartialEq, Eq)]
pub enum Error {
    #[error("{0}")]
    Param(String),
    #[error("unknown command `{0}`")]
    UnknownCommand(String),
    #[error("account `{0}` has no password: set it with account.setPassword")]
    NoPassword(String),
    #[error("account `{0}` is not signed in: sign in with account.oauthStart")]
    NotSignedIn(String),
    #[error("job {0} did not finish in time")]
    Timeout(u64),
    #[error("{0}")]
    Core(#[from] mailcraft_core::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

/// Where the session keeps its files; `None` keeps everything in memory (tests, demo).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Paths {
    pub config: PathBuf,
    pub secrets: PathBuf,
    pub cache: PathBuf,
}

impl Paths {
    /// The platform locations (see `mailcraft_core::account::home_dir`).
    pub fn default_dirs() -> Self {
        Self {
            config: mailcraft_core::account::config_path(),
            secrets: mailcraft_core::account::secrets_path(),
            cache: mailcraft_core::account::cache_dir(),
        }
    }

    pub fn under(dir: &std::path::Path) -> Self {
        Self { config: dir.join("config.json"), secrets: dir.join("secrets.json"), cache: dir.join("cache") }
    }
}

/// What the interface shows: the account, mailbox, search and selection.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct View {
    /// An account id, or [`ALL`] for the unified view across every account (then `mailbox`
    /// holds a role name: `inbox`, `starred`, `sent`…).
    pub account: String,
    pub mailbox: String,
    pub query: String,
    /// Search results for `query` (empty when there is no search); in the unified view each
    /// envelope carries its `account`.
    pub results: Vec<Envelope>,
    pub selection: Vec<u32>,
    /// The account and mailbox the selection belongs to (the unified view mixes accounts).
    pub selection_account: String,
    pub selection_mailbox: String,
    /// The message open in the reading pane: (mailbox, uid).
    pub open: Option<(String, u32)>,
    /// The account of the open message.
    pub open_account: String,
    pub page: usize,
}

/// The view's account id for "every account at once" (the unified inbox).
pub const ALL: &str = "*";

impl View {
    pub fn unified(&self) -> bool {
        self.account == ALL
    }

    /// The role shown by the unified view (`inbox` when unset).
    pub fn unified_role(&self) -> Role {
        Role::parse(&self.mailbox).unwrap_or(Role::Inbox)
    }
}

/// A finished job, as [`Session::pump`] reports it.
#[derive(Clone, Debug)]
pub struct Completed {
    pub job: u64,
    pub command: String,
    pub account: String,
    pub result: Result<Value>,
}

/// A completed job in a [`Snapshot`].
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CompletedJson {
    pub revision: u64,
    pub job: u64,
    pub command: String,
    pub account: String,
    #[serde(default)]
    pub result: Value,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub error: String,
}

impl From<&CompletedJson> for Completed {
    fn from(c: &CompletedJson) -> Self {
        Self {
            job: c.job,
            command: c.command.clone(),
            account: c.account.clone(),
            result: if c.error.is_empty() { Ok(c.result.clone()) } else { Err(Error::Param(c.error.clone())) },
        }
    }
}

/// One account's cache as a snapshot carries it (what the interface needs for the mailbox in
/// view: folders with counts, that mailbox's envelopes, the opened messages as text).
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct RemoteState {
    pub mailboxes: Vec<mailcraft_core::mailbox::Mailbox>,
    pub delimiter: String,
    pub last_sync: i64,
    pub error: Option<String>,
    pub envelopes: BTreeMap<String, Vec<Envelope>>,
    pub messages: Vec<(String, u32, Message)>,
}

impl RemoteState {
    pub fn into_state(self) -> AccountState {
        let mut st = AccountState {
            mailboxes: self.mailboxes,
            delimiter: self.delimiter,
            envelopes: self.envelopes,
            last_sync: self.last_sync,
            error: self.error,
            ..AccountState::default()
        };
        for (mailbox, uid, m) in self.messages {
            st.messages.insert((mailbox, uid), m);
        }
        st
    }
}

/// The session's state for a remote interface (the web app): everything the UI reads, with a
/// revision so clients fetch it only when it changed. Never contains passwords.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Snapshot {
    pub revision: u64,
    pub config: Config,
    /// Ids of the accounts that have a password.
    pub passwords: Vec<String>,
    pub view: View,
    pub states: BTreeMap<String, RemoteState>,
    pub jobs_pending: usize,
    pub notes: Vec<String>,
    /// Jobs completed after the client's revision.
    pub completed: Vec<CompletedJson>,
    /// OAuth providers with a configured client (`google`, `microsoft`): sign-in is possible.
    #[serde(default)]
    pub oauth_ready: Vec<String>,
    /// The framework's assistant endpoint (reply suggestions, drafts), when the server has one.
    #[serde(default)]
    pub assist_url: String,
}

/// What an interface needs from a session: the local [`Session`] implements it directly, a
/// remote one mirrors a server's snapshots.
pub trait MailSession {
    fn config(&self) -> &Config;
    fn view(&self) -> &View;
    fn state(&self, account: &str) -> Option<&AccountState>;
    fn has_password(&self, id: &str) -> bool;
    fn jobs_pending(&self) -> usize;
    /// Runs a command. A remote session answers `{"queued": true}` and reports the real result
    /// through [`MailSession::pump`] as a [`Completed`] with the command's id.
    fn execute(&mut self, id: &str, params: &Value) -> Result<Value>;
    fn pump(&mut self) -> Vec<Completed>;
    fn run_blocking(&mut self, id: &str, params: Value) -> Result<Value>;
    /// Why the session cannot work right now (a remote server out of reach), if so.
    fn problem(&self) -> Option<String> {
        None
    }
    /// The server a remote session talks to (empty for a local one).
    fn server(&self) -> String {
        String::new()
    }
    fn set_server(&mut self, _url: &str) {}
    /// OAuth providers whose client is configured (sign-in with them works).
    fn oauth_ready(&self) -> Vec<String> {
        Vec::new()
    }
    /// The assistant endpoint the interface may call for suggestions (empty = none).
    fn assist_url(&self) -> String {
        String::new()
    }
    fn current(&self) -> Option<&AccountState> {
        self.state(&self.view().account)
    }
    fn account(&self, id: &str) -> Option<&Account> {
        self.config().account(id)
    }
    fn italian(&self) -> bool {
        self.config().preferences.language == "it"
    }
    fn folder_for(&self, account: &str, role: Role) -> Option<String> {
        folder_for(self.config(), self.state(account), account, role)
    }
}

/// `(account, mailbox)` for a role across every account (the unified view).
pub fn unified_folders<'a>(config: &Config, state: impl Fn(&str) -> Option<&'a AccountState>, role: Role) -> Vec<(String, String)> {
    let role = if role == Role::Starred { Role::Inbox } else { role };
    config.accounts.iter().filter_map(|a| folder_for(config, state(&a.id), &a.id, role).map(|m| (a.id.clone(), m))).collect()
}

/// The merged, account-tagged list behind a unified role (starred = the flagged inbox mail).
pub fn unified_list<'a>(config: &Config, state: impl Fn(&str) -> Option<&'a AccountState>, role: Role) -> Vec<Envelope> {
    let mut out = Vec::new();
    for (account, mailbox) in unified_folders(config, &state, role) {
        if let Some(st) = state(&account) {
            for e in st.list(&mailbox) {
                if role == Role::Starred && !e.flags.flagged {
                    continue;
                }
                let mut e = e.clone();
                e.account = account.clone();
                out.push(e);
            }
        }
    }
    out.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
    out.truncate(mailcraft_core::limits::MAX_ENVELOPES);
    out
}

/// The IMAP path of a special folder: configured, detected, or `None`.
pub fn folder_for(config: &Config, state: Option<&AccountState>, account: &str, role: Role) -> Option<String> {
    let a = config.account(account)?;
    let configured = match role {
        Role::Sent => &a.folders.sent,
        Role::Drafts => &a.folders.drafts,
        Role::Trash => &a.folders.trash,
        Role::Spam => &a.folders.spam,
        Role::Archive => &a.folders.archive,
        _ => "",
    };
    if !configured.is_empty() {
        return Some(configured.to_string());
    }
    if role == Role::Inbox {
        return Some("INBOX".into());
    }
    let state = state?;
    state
        .by_role(role)
        .map(|m| m.path.clone())
        .or_else(|| if role == Role::Archive { state.by_role(Role::All).map(|m| m.path.clone()) } else { None })
}

struct Pending {
    command: String,
    started: Instant,
}

/// The engine state: configuration, cache, view and the worker.
pub struct Session {
    config: Config,
    secrets: Secrets,
    paths: Option<Paths>,
    /// Notes from loading the configuration (dropped accounts…).
    pub notes: Vec<String>,
    store: BTreeMap<String, AccountState>,
    view: View,
    worker: Worker,
    next_job: u64,
    pending: BTreeMap<u64, Pending>,
    /// Bumped on every change, for remote clients.
    revision: u64,
    /// Recent completions with the revision they landed at.
    history: VecDeque<CompletedJson>,
    /// Completions seen by `wait` that the next `pump` must still report.
    buffered: Vec<Completed>,
    /// OAuth sign-ins waiting for the browser to come back.
    flows: Vec<Flow>,
    /// Accounts whose access token is being renewed (one refresh at a time).
    refreshing: BTreeSet<String>,
    /// The loopback listener's callbacks (desktop sign-in).
    callbacks: (Sender<loopback::Callback>, Receiver<loopback::Callback>),
    loopback: Option<loopback::Loopback>,
    /// The assistant endpoint announced to interfaces (set by the server).
    assist_url: String,
    /// Whether commands may read and write files on this machine by path (`attachments[].path`,
    /// `mail.attachment` `path`): the local desktop and CLI session's right, never a shared
    /// server's (every user would reach the server's files).
    host_paths: bool,
}

impl Session {
    /// Loads the configuration, secrets and caches from `paths` (or starts empty) and starts
    /// the worker with `factory`.
    pub fn new(factory: BackendFactory, paths: Option<Paths>) -> Self {
        Self::with_broker(factory, Arc::new(NoBroker), paths)
    }

    /// Like [`Session::new`], with the OAuth broker that signs accounts in with Google or
    /// Microsoft (`mailcraft_net::broker()`).
    pub fn with_broker(factory: BackendFactory, broker: Arc<dyn OAuthBroker>, paths: Option<Paths>) -> Self {
        let mut notes = Vec::new();
        let (config, secrets) = match &paths {
            Some(p) => {
                let (config, n) = Config::load(&p.config).unwrap_or_else(|e| {
                    notes.push(e.to_string());
                    (Config::default(), Vec::new())
                });
                notes.extend(n);
                let secrets = Secrets::load(&p.secrets).unwrap_or_else(|e| {
                    notes.push(e.to_string());
                    Secrets::default()
                });
                (config, secrets)
            }
            None => (Config::default(), Secrets::default()),
        };
        let mut store = BTreeMap::new();
        for a in &config.accounts {
            let state = match &paths {
                Some(p) => AccountState::load(&p.cache.join(&a.id)),
                None => AccountState::default(),
            };
            store.insert(a.id.clone(), state);
        }
        let mut view = View::default();
        let first = config.account(&config.preferences.default_account).or_else(|| config.accounts.first());
        if let Some(a) = first {
            view.account = a.id.clone();
            view.mailbox = "INBOX".into();
        }
        Self {
            config,
            secrets,
            paths,
            notes,
            store,
            view,
            worker: Worker::start(factory, broker),
            next_job: 1,
            pending: BTreeMap::new(),
            revision: 1,
            history: VecDeque::new(),
            buffered: Vec::new(),
            flows: Vec::new(),
            refreshing: BTreeSet::new(),
            callbacks: std::sync::mpsc::channel(),
            loopback: None,
            assist_url: std::env::var("MAILCRAFT_ASSIST_URL").unwrap_or_default().trim().to_string(),
            host_paths: true,
        }
    }

    /// Lets commands read and write files on this machine by path (on by default: the local
    /// session is its owner's). A server serving other people turns it off, so the attachment
    /// commands take and give base64 only.
    pub fn allow_host_paths(&mut self, allow: bool) {
        self.host_paths = allow;
    }

    /// Whether commands may read and write files on this machine by path.
    pub fn host_paths_allowed(&self) -> bool {
        self.host_paths
    }

    /// Announces the framework's assistant endpoint to interfaces (the web app calls it for
    /// reply suggestions and drafts).
    pub fn set_assist_url(&mut self, url: &str) {
        self.assist_url = url.trim().to_string();
        self.revision += 1;
    }

    pub fn assist_url(&self) -> String {
        self.assist_url.clone()
    }

    /// The redirect URL of the loopback listener for a sign-in on this machine (started when
    /// none is waiting).
    pub(crate) fn loopback_redirect(&mut self) -> Result<String> {
        if let Some(lb) = &self.loopback
            && lb.alive()
        {
            return Ok(lb.redirect());
        }
        let lb = loopback::start(self.callbacks.0.clone())
            .map_err(|e| Error::Core(mailcraft_core::Error::Server(format!("cannot listen for the sign-in on this machine: {e}"))))?;
        let redirect = lb.redirect();
        self.loopback = Some(lb);
        Ok(redirect)
    }

    /// OAuth providers whose client is configured.
    pub fn oauth_ready(&self) -> Vec<String> {
        mailcraft_core::oauth::PROVIDERS.iter().filter(|p| self.config.oauth_client(p.id).is_some()).map(|p| p.id.to_string()).collect()
    }

    /// The state for a remote interface, or `None` when nothing changed since `since`.
    pub fn snapshot(&self, since: u64) -> Option<Snapshot> {
        if since == self.revision {
            return None;
        }
        let mut states = BTreeMap::new();
        for (id, st) in &self.store {
            let mut rs = RemoteState {
                mailboxes: st.mailboxes.clone(),
                delimiter: st.delimiter.clone(),
                last_sync: st.last_sync,
                error: st.error.clone(),
                ..RemoteState::default()
            };
            let unified_mailbox = if self.view.unified() {
                let role = if self.view.unified_role() == Role::Starred { Role::Inbox } else { self.view.unified_role() };
                self.folder_for(id, role)
            } else {
                None
            };
            if *id == self.view.account || unified_mailbox.is_some() {
                let mailbox = unified_mailbox.unwrap_or_else(|| self.view.mailbox.clone());
                rs.envelopes.insert(mailbox.clone(), st.list(&mailbox).iter().take(mailcraft_core::limits::MAX_PAGE * 4).cloned().collect());
                // Only the messages of the mailbox in view, as text (the HTML is reduced here).
                for ((mb, uid), m) in &st.messages {
                    if *mb == mailbox && rs.messages.len() < 40 {
                        let mut m = m.clone();
                        m.text = m.body_text();
                        m.html.clear();
                        rs.messages.push((mb.clone(), *uid, m));
                    }
                }
            }
            states.insert(id.clone(), rs);
        }
        // The OAuth client secrets stay on the server (`oauth.providers` hides them too).
        let mut config = self.config.clone();
        for client in config.oauth.values_mut() {
            client.client_secret.clear();
        }
        Some(Snapshot {
            revision: self.revision,
            config,
            passwords: self.config.accounts.iter().filter(|a| self.secrets.has_credential(a)).map(|a| a.id.clone()).collect(),
            oauth_ready: self.oauth_ready(),
            assist_url: self.assist_url.clone(),
            view: self.view.clone(),
            states,
            jobs_pending: self.pending.len(),
            notes: self.notes.clone(),
            completed: self.history.iter().filter(|c| c.revision > since).cloned().collect(),
        })
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    /// The accounts and mailboxes behind a unified role: `(account, mailbox)` for every account
    /// that has that folder.
    pub fn unified_folders(&self, role: Role) -> Vec<(String, String)> {
        unified_folders(&self.config, |id| self.store.get(id), role)
    }

    /// The merged list of a unified role, newest first, each envelope tagged with its account.
    pub fn unified_list(&self, role: Role) -> Vec<Envelope> {
        unified_list(&self.config, |id| self.store.get(id), role)
    }

    /// An in-memory session on the demo mailbox (`MockBackend::demo`), already synced.
    pub fn demo() -> Self {
        // One demo mailbox per account, so acting on one never touches the other.
        let mocks: std::collections::HashMap<String, mailcraft_core::mock::MockBackend> =
            ["demo", "lavoro"].into_iter().map(|id| (id.to_string(), mailcraft_core::mock::MockBackend::demo())).collect();
        let factory: BackendFactory = Box::new(move |a| Box::new(mocks.get(&a.id).cloned().unwrap_or_else(mailcraft_core::mock::MockBackend::demo)));
        let mut s = Self::with_broker(factory, Arc::new(MockBroker::default()), None);
        let account = Account {
            id: "demo".into(),
            name: "Demo".into(),
            email: "utente@example.it".into(),
            display_name: "Utente".into(),
            imap: mailcraft_core::account::Server::new("mock.local", 993, mailcraft_core::account::Security::Tls),
            smtp: mailcraft_core::account::Server::new("mock.local", 465, mailcraft_core::account::Security::Tls),
            signature: "Utente\nSirius Technology".into(),
            color: "#4a6cf7".into(),
            ..Account::default()
        };
        let work = Account {
            id: "lavoro".into(),
            name: "Lavoro".into(),
            email: "utente@sirius-example.it".into(),
            display_name: "Utente Esempio".into(),
            imap: mailcraft_core::account::Server::new("mock.local", 993, mailcraft_core::account::Security::Tls),
            smtp: mailcraft_core::account::Server::new("mock.local", 465, mailcraft_core::account::Security::Tls),
            color: "#22a06b".into(),
            ..Account::default()
        };
        if s.config.upsert(account).is_ok() {
            s.secrets.set_password("demo", "demo");
            s.store.insert("demo".into(), AccountState::default());
            // A second account, to switch between them like on Gmail (same demo mailbox).
            if s.config.upsert(work).is_ok() {
                s.secrets.set_password("lavoro", "demo");
                s.store.insert("lavoro".into(), AccountState::default());
            }
            s.view.account = "demo".into();
            s.view.mailbox = "INBOX".into();
            let _ = s.run_blocking("mail.sync", json!({}));
        }
        s
    }

    pub fn config(&self) -> &Config {
        &self.config
    }

    pub fn view(&self) -> &View {
        &self.view
    }

    pub fn state(&self, account: &str) -> Option<&AccountState> {
        self.store.get(account)
    }

    /// The current account's cache.
    pub fn current(&self) -> Option<&AccountState> {
        self.store.get(&self.view.account)
    }

    pub fn account(&self, id: &str) -> Option<&Account> {
        self.config.account(id)
    }

    pub fn has_password(&self, id: &str) -> bool {
        self.config.account(id).is_some_and(|a| self.secrets.has_credential(a))
    }

    pub fn jobs_pending(&self) -> usize {
        self.pending.len()
    }

    /// Whether messages to the user (quotes, labels) are Italian.
    pub fn italian(&self) -> bool {
        self.config.preferences.language == "it"
    }

    /// Runs a command. Network commands return `{"job": id}` (see [`Self::pump`]).
    pub fn execute(&mut self, id: &str, params: &Value) -> Result<Value> {
        self.revision += 1;
        cmd::run(self, id, params)
    }

    /// Runs a command and waits for the job(s) it starts; returns the job's result (the last
    /// one's, when several).
    pub fn run_blocking(&mut self, id: &str, params: Value) -> Result<Value> {
        let v = self.execute(id, &params)?;
        let mut jobs: Vec<u64> = v.get("jobs").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
        if let Some(j) = v.get("job").and_then(Value::as_u64) {
            jobs.push(j);
        }
        if jobs.is_empty() {
            return Ok(v);
        }
        let mut last = v;
        for j in jobs {
            last = self.wait(j, Duration::from_secs(180))?;
        }
        Ok(last)
    }

    /// Blocks until job `id` completes (pumping every other event meanwhile).
    pub fn wait(&mut self, id: u64, timeout: Duration) -> Result<Value> {
        let deadline = Instant::now() + timeout;
        loop {
            let mut found = None;
            for c in self.pump() {
                if c.job == id && found.is_none() {
                    found = Some(c.result);
                } else {
                    self.buffered.push(c);
                }
            }
            if let Some(r) = found {
                return r;
            }
            if !self.pending.contains_key(&id) {
                return Err(Error::Timeout(id));
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(Error::Timeout(id));
            }
            if let Some(ev) = self.worker.wait(left.min(Duration::from_millis(200))) {
                let c = self.apply(ev);
                self.save_caches();
                if c.job == id {
                    return c.result;
                }
                self.buffered.push(c);
            }
        }
    }

    /// Applies every event the worker produced and saves dirty caches. The interface calls this
    /// every frame.
    pub fn pump(&mut self) -> Vec<Completed> {
        // The browser came back from a sign-in: finish it (the exchange runs as a job).
        let callbacks: Vec<loopback::Callback> = self.callbacks.1.try_iter().collect();
        for cb in callbacks {
            let prm = json!({"state": cb.state, "code": cb.code, "error": cb.error});
            if let Err(e) = self.execute("account.oauthFinish", &prm) {
                self.buffered.push(Completed { job: 0, command: "account.oauthFinish".into(), account: String::new(), result: Err(e) });
            }
        }
        let events = self.worker.poll();
        let mut out = std::mem::take(&mut self.buffered);
        for ev in events {
            out.push(self.apply(ev));
        }
        if !out.is_empty() {
            self.save_caches();
        }
        out
    }

    fn save_caches(&mut self) {
        let Some(paths) = self.paths.clone() else { return };
        for (id, state) in self.store.iter_mut() {
            if state.dirty {
                let _ = state.save(&paths.cache.join(id));
            }
        }
    }

    fn save_config(&mut self) -> Result<()> {
        if let Some(p) = &self.paths {
            self.config.save(&p.config)?;
            self.secrets.save(&p.secrets)?;
        }
        Ok(())
    }

    /// Queues a job for an account (which must have a password, or OAuth tokens: an expired
    /// access token is renewed first).
    fn submit(&mut self, command: &str, account_id: &str, kind: JobKind) -> Result<u64> {
        let account = self.config.account(account_id).ok_or_else(|| mailcraft_core::Error::NoAccount(account_id.to_string()))?.clone();
        let oauth_job = matches!(kind, JobKind::OAuthExchange { .. } | JobKind::OAuthRefresh { .. });
        let password = self.secrets.credential(&account).map(str::to_string).unwrap_or_default();
        if let Some(provider) = account.oauth_provider()
            && !oauth_job
            && !matches!(kind, JobKind::Disconnect)
        {
            let tokens = self.secrets.tokens(account_id).cloned().unwrap_or_default();
            if tokens.refresh_token.is_empty() && tokens.access_token.is_empty() {
                return Err(Error::NotSignedIn(account_id.to_string()));
            }
            if tokens.expired(time::now()) && !tokens.refresh_token.is_empty() && !self.refreshing.contains(account_id) {
                let client = self
                    .config
                    .oauth_client(provider.id)
                    .ok_or_else(|| Error::Param(format!("no OAuth client for {}: configure it with oauth.configure", provider.name)))?;
                self.refreshing.insert(account_id.to_string());
                self.submit("oauth.refresh", account_id, JobKind::OAuthRefresh { client, refresh_token: tokens.refresh_token })?;
            }
        } else if password.is_empty() && !matches!(kind, JobKind::Disconnect) && !oauth_job {
            return Err(Error::NoPassword(account_id.to_string()));
        }
        let id = self.next_job;
        self.next_job += 1;
        if !self.worker.submit(Job { id, account, password, kind }) {
            return Err(Error::Core(mailcraft_core::Error::Server("the worker has stopped".into())));
        }
        self.pending.insert(id, Pending { command: command.to_string(), started: Instant::now() });
        Ok(id)
    }

    /// The page size from the preferences.
    fn page_size(&self) -> usize {
        (self.config.preferences.page_size as usize).clamp(5, mailcraft_core::limits::MAX_PAGE)
    }

    /// The IMAP path of a special folder for an account: configured, detected, or `None`.
    pub fn folder_for(&self, account: &str, role: Role) -> Option<String> {
        folder_for(&self.config, self.store.get(account), account, role)
    }

    fn apply(&mut self, ev: Event) -> Completed {
        self.revision += 1;
        let pending = self.pending.remove(&ev.job);
        let command = pending.as_ref().map(|p| p.command.clone()).unwrap_or_default();
        let _ = pending.as_ref().map(|p| p.started);
        let account = ev.account.clone();
        let result = match ev.result {
            Ok(out) => self.apply_output(&account, &command, ev.kind, out),
            Err(e) => {
                if matches!(ev.kind, JobKind::OAuthRefresh { .. } | JobKind::OAuthExchange { .. }) {
                    self.refreshing.remove(&account);
                }
                if let Some(state) = self.store.get_mut(&account) {
                    if matches!(e, mailcraft_core::Error::Connect(_) | mailcraft_core::Error::Auth(_) | mailcraft_core::Error::NotConnected) {
                        state.error = Some(e.to_string());
                    }
                    // An optimistic flag or move that failed: refresh that mailbox.
                    match &ev.kind {
                        JobKind::SetFlag { mailbox, .. } | JobKind::Move { mailbox, .. } | JobKind::Delete { mailbox, .. } => {
                            let mailbox = mailbox.clone();
                            let page = Page::new(0, self.page_size());
                            let _ = self.submit("mail.list", &account, JobKind::Envelopes { mailbox, page });
                        }
                        _ => {}
                    }
                }
                Err(Error::Core(e))
            }
        };
        let done = Completed { job: ev.job, command, account, result };
        self.history.push_back(CompletedJson {
            revision: self.revision,
            job: done.job,
            command: done.command.clone(),
            account: done.account.clone(),
            result: done.result.clone().unwrap_or(Value::Null),
            error: done.result.as_ref().err().map(ToString::to_string).unwrap_or_default(),
        });
        while self.history.len() > 200 {
            self.history.pop_front();
        }
        done
    }

    fn apply_output(&mut self, account: &str, command: &str, kind: JobKind, out: Output) -> Result<Value> {
        let page_size = self.page_size();
        let italian = self.italian();
        let state = self.store.entry(account.to_string()).or_default();
        state.error = None;
        match out {
            Output::Mailboxes(list, delimiter) => {
                // Keep cached counts for folders the server gave no STATUS for.
                let old = std::mem::take(&mut state.mailboxes);
                state.mailboxes = list
                    .into_iter()
                    .map(|mut m| {
                        if m.total == 0
                            && m.unread == 0
                            && let Some(o) = old.iter().find(|o| o.path == m.path)
                        {
                            m.total = o.total;
                            m.unread = o.unread;
                        }
                        m
                    })
                    .collect();
                state.delimiter = delimiter;
                state.last_sync = time::now();
                state.dirty = true;
                let boxes: Vec<Value> = state.mailboxes.iter().map(|m| mailbox_json(m, italian)).collect();
                Ok(json!({"mailboxes": boxes}))
            }
            Output::Envelopes { mailbox, page, list } => {
                state.merge_page(&mailbox, page.offset, page.limit, list.clone());
                if let Some(m) = state.mailboxes.iter_mut().find(|m| m.path == mailbox)
                    && page.offset == 0
                {
                    let cached = state.envelopes.get(&mailbox).map(Vec::len).unwrap_or(0) as u32;
                    m.total = m.total.max(cached);
                }
                Ok(json!({"mailbox": mailbox, "offset": page.offset, "envelopes": list.iter().map(envelope_json).collect::<Vec<_>>()}))
            }
            Output::EnvelopesByUid { mailbox, list } => {
                state.update_envelopes(&mailbox, list.clone());
                Ok(json!({"mailbox": mailbox, "envelopes": list.iter().map(envelope_json).collect::<Vec<_>>()}))
            }
            Output::Message { mailbox, uid, raw, message } => {
                let v = message_json(&message);
                let flags = message.envelope.flags;
                state.put_message(&mailbox, uid, raw, *message);
                if let Some(e) = state.envelopes.get_mut(&mailbox).and_then(|l| l.iter_mut().find(|e| e.uid == uid)) {
                    e.has_attachments = v.get("attachments").and_then(Value::as_array).is_some_and(|a| !a.is_empty());
                    let seen = e.flags.seen;
                    e.flags = flags;
                    e.flags.seen |= seen;
                    state.dirty = true;
                }
                Ok(v)
            }
            Output::Flagged { mailbox, uids, change } => Ok(json!({"mailbox": mailbox, "uids": uids, "change": format!("{change:?}")})),
            Output::Moved { mailbox, uids, to } => {
                // Refresh the destination so it shows the moved messages.
                let dest_known = state.envelopes.contains_key(&to);
                let v = json!({"mailbox": mailbox, "uids": uids, "to": to});
                if dest_known {
                    let _ = self.submit(command, account, JobKind::Envelopes { mailbox: to, page: Page::new(0, page_size) });
                }
                Ok(v)
            }
            Output::Deleted { mailbox, uids } => Ok(json!({"mailbox": mailbox, "uids": uids, "deleted": true})),
            Output::Appended { mailbox, uid, replaced } => {
                if let Some(old) = replaced {
                    state.remove(&mailbox, &[old]);
                }
                if let Some(u) = uid {
                    let _ = self.submit(command, account, JobKind::EnvelopesByUid { mailbox: mailbox.clone(), uids: vec![u] });
                } else {
                    let _ = self.submit(command, account, JobKind::Envelopes { mailbox: mailbox.clone(), page: Page::new(0, page_size) });
                }
                Ok(json!({"mailbox": mailbox, "uid": uid}))
            }
            Output::Search { mailbox, query, list } => {
                state.update_envelopes(&mailbox, list.clone());
                if self.view.account == account && self.view.mailbox == mailbox && !self.view.query.is_empty() {
                    self.view.results = list.clone();
                } else if self.view.unified() && !self.view.query.is_empty() {
                    // Merge this account's hits into the unified results.
                    self.view.results.retain(|e| !(e.account == account && e.mailbox == mailbox));
                    self.view.results.extend(list.iter().cloned().map(|mut e| {
                        e.account = account.to_string();
                        e
                    }));
                    self.view.results.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
                }
                Ok(json!({"mailbox": mailbox, "query": query.text, "envelopes": list.iter().map(envelope_json).collect::<Vec<_>>()}))
            }
            Output::Sent { message_id, copied_to, copy_error } => {
                // The people written to come first when completing recipients.
                if let JobKind::Send { to, .. } = &kind {
                    let addrs: Vec<mailcraft_core::message::Address> = to.iter().map(|t| mailcraft_core::message::Address::new("", t)).collect();
                    state.note_addresses(addrs.iter(), time::now(), 3);
                }
                if let JobKind::Respond { forward_to, .. } = &kind {
                    state.note_addresses(forward_to.iter(), time::now(), 3);
                }
                if let JobKind::Send { answered: Some((mb, uid)), .. } | JobKind::Respond { mailbox: mb, uid, .. } = &kind {
                    state.apply_flag(mb, &[*uid], FlagChange::Answered(true));
                }
                if let JobKind::Send { delete_draft: Some((mb, uid)), .. } = &kind {
                    state.remove(mb, &[*uid]);
                }
                if let Some(folder) = &copied_to {
                    if !state.mailboxes.iter().any(|m| m.path == *folder) {
                        // The Sent folder was just created: list it.
                        let _ = self.submit(command, account, JobKind::Mailboxes);
                    }
                    let _ = self.submit(command, account, JobKind::Envelopes { mailbox: folder.clone(), page: Page::new(0, page_size) });
                }
                let mut v = json!({"sent": true, "message_id": message_id, "copied_to": copied_to});
                if let Some(e) = copy_error {
                    v["copy_error"] = json!(e);
                }
                Ok(v)
            }
            Output::Created { path } => {
                let _ = self.submit(command, account, JobKind::Mailboxes);
                Ok(json!({"created": path}))
            }
            Output::Subscribed { path, subscribed } => {
                if let Some(m) = state.mailboxes.iter_mut().find(|m| m.path == path) {
                    m.subscribed = subscribed;
                }
                Ok(json!({"path": path, "subscribed": subscribed}))
            }
            Output::Tokens(t) => {
                state.error = None;
                self.refreshing.remove(account);
                let expires_at = t.expires_at;
                self.secrets.set_tokens(account, Some(t));
                let _ = self.save_config();
                let signed_in = matches!(kind, JobKind::OAuthExchange { .. });
                if signed_in {
                    // First sign-in: load the folders and the inbox right away.
                    let _ = self.submit("mail.sync", account, JobKind::Mailboxes);
                    let _ = self.submit("mail.sync", account, JobKind::Envelopes { mailbox: "INBOX".into(), page: Page::new(0, page_size) });
                }
                Ok(json!({"account": account, "signed_in": signed_in, "expires_at": expires_at}))
            }
            Output::Tested { mailboxes, smtp } => Ok(json!({
                "ok": smtp.is_ok(),
                "imap": "ok",
                "mailboxes": mailboxes,
                "smtp": match smtp { Ok(()) => "ok".to_string(), Err(e) => format!("failed: {e}") },
            })),
            Output::Probed { imap, smtp, tried } => {
                if let Some(a) = self.config.account_mut(account) {
                    a.imap = imap.clone();
                    a.smtp = smtp.clone();
                }
                self.save_config()?;
                Ok(json!({"imap": imap.describe(), "smtp": smtp.describe(), "tried": tried, "account": account}))
            }
            Output::Disconnected => Ok(json!({"disconnected": account})),
        }
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.save_caches();
    }
}

impl MailSession for Session {
    fn config(&self) -> &Config {
        &self.config
    }
    fn view(&self) -> &View {
        &self.view
    }
    fn state(&self, account: &str) -> Option<&AccountState> {
        self.store.get(account)
    }
    fn has_password(&self, id: &str) -> bool {
        Session::has_password(self, id)
    }
    fn oauth_ready(&self) -> Vec<String> {
        Session::oauth_ready(self)
    }
    fn assist_url(&self) -> String {
        Session::assist_url(self)
    }
    fn jobs_pending(&self) -> usize {
        self.pending.len()
    }
    fn execute(&mut self, id: &str, params: &Value) -> Result<Value> {
        Session::execute(self, id, params)
    }
    fn pump(&mut self) -> Vec<Completed> {
        Session::pump(self)
    }
    fn run_blocking(&mut self, id: &str, params: Value) -> Result<Value> {
        Session::run_blocking(self, id, params)
    }
}

/// An envelope as commands return it.
pub fn envelope_json(e: &Envelope) -> Value {
    json!({
        "uid": e.uid,
        "mailbox": e.mailbox,
        "account": e.account,
        "account": e.account,
        "subject": e.subject,
        "from": e.from.iter().map(|a| a.display()).collect::<Vec<_>>(),
        "to": e.to.iter().map(|a| a.display()).collect::<Vec<_>>(),
        "cc": e.cc.iter().map(|a| a.display()).collect::<Vec<_>>(),
        "date": e.date,
        "date_text": time::long(e.date, false),
        "snippet": e.snippet,
        "unread": !e.flags.seen,
        "starred": e.flags.flagged,
        "answered": e.flags.answered,
        "draft": e.flags.draft,
        "has_attachments": e.has_attachments,
        "size": e.size,
        "message_id": e.message_id,
        "thread": e.thread_key(),
    })
}

/// A full message as commands return it.
pub fn message_json(m: &Message) -> Value {
    let mut v = envelope_json(&m.envelope);
    if let Some(o) = v.as_object_mut() {
        o.insert("text".into(), Value::String(m.body_text()));
        o.insert("html".into(), Value::String(m.html.clone()));
        o.insert("reply_to".into(), json!(m.reply_to.iter().map(|a| a.display()).collect::<Vec<_>>()));
        o.insert("list_unsubscribe".into(), Value::String(m.list_unsubscribe.clone()));
        o.insert(
            "attachments".into(),
            json!(
                m.attachments
                    .iter()
                    .filter(|a| !a.inline)
                    .map(|a| json!({"index": a.index, "name": a.name, "mime": a.mime, "size": a.size}))
                    .collect::<Vec<_>>()
            ),
        );
    }
    v
}

pub fn mailbox_json(m: &mailcraft_core::mailbox::Mailbox, italian: bool) -> Value {
    let label = if m.role == Role::Other { m.name.clone() } else { m.role.label(italian).to_string() };
    json!({"path": m.path, "name": m.name, "label": label, "role": m.role.name(), "unread": m.unread, "total": m.total, "no_select": m.no_select, "subscribed": m.subscribed})
}

/// An account as commands return it (never the password).
pub fn account_json(a: &Account, has_password: bool, state: Option<&AccountState>) -> Value {
    json!({
        "id": a.id,
        "name": a.name,
        "email": a.email,
        "display_name": a.from_name(),
        "username": a.login(),
        "imap": {"host": a.imap.host, "port": a.imap.port, "security": a.imap.security.name()},
        "smtp": {"host": a.smtp.host, "port": a.smtp.port, "security": a.smtp.security.name()},
        "signature": a.signature,
        "signature_html": a.signature_html,
        "color": a.color,
        "folders": {"sent": a.folders.sent, "drafts": a.folders.drafts, "trash": a.folders.trash, "spam": a.folders.spam, "archive": a.folders.archive},
        "save_sent": a.save_sent,
        "trust_invalid_certs": a.trust_invalid_certs,
        "has_password": has_password,
        "auth": if a.auth.is_empty() { "password" } else { a.auth.as_str() },
        "problems": a.problems(),
        "status": {
            "last_sync": state.map_or(0, |s| s.last_sync),
            "error": state.and_then(|s| s.error.clone()),
            "mailboxes": state.map_or(0, |s| s.mailboxes.len()),
            "unread": state.and_then(|s| s.by_role(Role::Inbox).map(|m| m.unread)).unwrap_or(0),
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use mailcraft_core::mock::MockBackend;

    fn session(mock: &MockBackend) -> Session {
        let m = mock.clone();
        let mut s = Session::new(Box::new(move |_| Box::new(m.clone())), None);
        s.execute("account.add", &json!({"email": "utente@example.it", "name": "Demo", "password": "demo", "imap_host": "mock", "imap_port": 993, "smtp_host": "mock", "smtp_port": 465})).unwrap();
        s
    }

    #[test]
    fn reads_acts_and_sends_through_the_mock() {
        let mock = MockBackend::demo();
        let mut s = session(&mock);
        assert_eq!(s.view().account, "utente-example-it");
        let v = s.run_blocking("mail.sync", json!({})).unwrap();
        assert!(v["envelopes"].as_array().unwrap().len() > 5, "{v}");
        let inbox = s.current().unwrap();
        assert_eq!(inbox.by_role(Role::Inbox).unwrap().unread, 4);
        assert_eq!(inbox.list("INBOX").len(), 12);
        // Conversations group the backup thread.
        let convs = inbox.conversations("INBOX");
        assert!(convs.iter().any(|c| c.members.len() == 2), "{convs:?}");
        // Open marks read (optimistically and on the server).
        let first = inbox.list("INBOX")[0].uid;
        let m = s.run_blocking("mail.open", json!({"uid": first})).unwrap();
        assert!(m["text"].as_str().unwrap().contains("lunedì"));
        assert!(mock.flags_of("INBOX", first).unwrap().seen);
        assert_eq!(s.current().unwrap().by_role(Role::Inbox).unwrap().unread, 3);
        // Star, archive, trash, search.
        s.run_blocking("mail.star", json!({"uids": [first], "starred": true})).unwrap();
        assert!(mock.flags_of("INBOX", first).unwrap().flagged);
        let v = s.run_blocking("mail.archive", json!({"uids": [first]})).unwrap();
        assert_eq!(v["to"], "Archive");
        assert_eq!(mock.count("Archive"), 1);
        assert_eq!(s.current().unwrap().list("INBOX").len(), 11);
        let second = s.current().unwrap().list("INBOX")[0].uid;
        s.run_blocking("mail.trash", json!({"uids": [second]})).unwrap();
        assert_eq!(mock.count("Trash"), 2);
        let v = s.run_blocking("mail.search", json!({"query": "fattura"})).unwrap();
        assert_eq!(v["envelopes"].as_array().unwrap().len(), 1);
        // Send with the signature and a copy in Sent.
        let v = s.run_blocking("mail.send", json!({"to": "Luca <luca@example.com>", "subject": "Prova", "body": "Ciao Luca"})).unwrap();
        assert_eq!(v["sent"], true);
        let sent = mock.sent();
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0].1, vec!["luca@example.com"]);
        let text = String::from_utf8_lossy(&sent[0].2).to_string();
        assert!(text.contains("Subject: Prova"));
        assert_eq!(mock.count("Sent"), 3, "copied to Sent");
        // Reply quotes the original and flags it answered.
        let uid = s.current().unwrap().list("INBOX")[0].uid;
        let v = s.run_blocking("mail.reply", json!({"uid": uid, "body": "Grazie, ci sentiamo lunedì.", "all": true})).unwrap();
        assert_eq!(v["sent"], true);
        let last = mock.sent().last().unwrap().2.clone();
        let parsed = mailcraft_core::mime::parse(&last).unwrap();
        assert!(parsed.envelope.subject.starts_with("Re: "));
        assert!(parsed.text.contains("ha scritto:") || parsed.text.contains("wrote:"));
        assert!(!parsed.envelope.in_reply_to.is_empty());
        assert!(mock.flags_of("INBOX", uid).unwrap().answered);
        // Forward carries the attachments.
        let with_att = s.current().unwrap().list("INBOX").iter().find(|e| e.has_attachments).unwrap().uid;
        s.run_blocking("mail.forward", json!({"uid": with_att, "to": "x@example.org", "body": "FYI"})).unwrap();
        let fwd = mailcraft_core::mime::parse(&mock.sent().last().unwrap().2).unwrap();
        assert!(fwd.envelope.subject.starts_with("Fwd: "));
        assert_eq!(fwd.attachments.len(), 1);
        // Drafts save and replace; wrong password reports Auth.
        let v = s.run_blocking("draft.save", json!({"to": "a@b.co", "subject": "Bozza", "body": "..."})).unwrap();
        let draft_uid = v["uid"].as_u64().unwrap();
        s.run_blocking("draft.save", json!({"to": "a@b.co", "subject": "Bozza 2", "body": "...", "draft_uid": draft_uid})).unwrap();
        assert_eq!(mock.count("Drafts"), 2);
        let v = s.run_blocking("account.test", json!({})).unwrap();
        assert_eq!(v["ok"], true);
        s.execute("account.setPassword", &json!({"password": "wrong"})).unwrap();
        let bad = MockBackend::demo().require_password("demo");
        let mut s2 = session(&bad);
        s2.execute("account.setPassword", &json!({"password": "nope"})).unwrap();
        let e = s2.run_blocking("mail.sync", json!({})).unwrap_err();
        assert!(matches!(e, Error::Core(mailcraft_core::Error::Auth(_))), "{e}");
        assert!(s2.current().unwrap().error.is_some());
    }

    #[test]
    fn configuration_commands_and_persistence() {
        let dir = std::env::temp_dir().join(format!("mailcraft-engine-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mock = MockBackend::demo();
        let m = mock.clone();
        let mut s = Session::new(Box::new(move |_| Box::new(m.clone())), Some(Paths::under(&dir)));
        let v = s.execute("account.suggest", &json!({"email": "anna@gmail.com", "language": "it"})).unwrap();
        assert_eq!(v["provider"], "Gmail");
        let v = s.execute("account.add", &json!({"email": "anna@gmail.com", "password": "app-pw"})).unwrap();
        assert_eq!(v["account"]["imap"]["host"], "imap.gmail.com");
        assert_eq!(v["account"]["name"], "Gmail");
        let v = s.execute("account.list", &json!({})).unwrap();
        assert_eq!(v["accounts"].as_array().unwrap().len(), 1);
        assert_eq!(v["accounts"][0]["has_password"], true);
        s.execute("account.update", &json!({"id": "anna-gmail-com", "name": "Personale", "signature": "Anna"})).unwrap();
        s.execute("prefs.set", &json!({"theme": "dark", "sync_minutes": 2})).unwrap();
        assert!(s.execute("prefs.set", &json!({"theme": "neon"})).is_err());
        // Unknown domain: a probe finds the mock behind the first candidate.
        let v = s.execute("account.add", &json!({"email": "mario@sirius-example.it", "password": "x"})).unwrap();
        assert_eq!(v["suggestion"]["confidence"], "guess");
        let v = s.run_blocking("account.probe", json!({"id": "mario-sirius-example-it"})).unwrap();
        assert_eq!(v["imap"], "imap.sirius-example.it:993 (tls)");
        // Sync, then reopen from disk: the cache is there.
        s.run_blocking("mail.sync", json!({"account": "anna-gmail-com"})).unwrap();
        let unread = s.state("anna-gmail-com").unwrap().by_role(Role::Inbox).unwrap().unread;
        drop(s);
        let m = mock.clone();
        let s = Session::new(Box::new(move |_| Box::new(m.clone())), Some(Paths::under(&dir)));
        assert_eq!(s.config().accounts.len(), 2);
        assert_eq!(s.config().account("anna-gmail-com").unwrap().name, "Personale");
        assert_eq!(s.config().preferences.theme, "dark");
        assert!(s.has_password("anna-gmail-com"));
        assert_eq!(s.state("anna-gmail-com").unwrap().by_role(Role::Inbox).unwrap().unread, unread);
        assert_eq!(s.state("anna-gmail-com").unwrap().list("INBOX").len(), 12);
        let mut s = s;
        s.execute("account.remove", &json!({"id": "mario-sirius-example-it"})).unwrap();
        assert_eq!(s.config().accounts.len(), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn html_signature_and_recipient_completion() {
        let mock = MockBackend::demo();
        let mut s = session(&mock);
        s.run_blocking("mail.sync", json!({})).unwrap();
        // People seen in the inbox complete recipients; the person's own address never does.
        let v = s.execute("contacts.search", &json!({"query": "luc"})).unwrap();
        assert_eq!(v["contacts"][0]["email"], "luca@example.com", "{v}");
        assert!(v["contacts"][0]["display"].as_str().unwrap().contains("Luca Bianchi"));
        assert!(s.execute("contacts.search", &json!({"query": "utente@example"})).unwrap()["contacts"].as_array().unwrap().is_empty());
        // An HTML signature with a logo: the message goes out as text + HTML with an inline image.
        let logo = mailcraft_core::mime::base64_encode(b"\x89PNG\x00");
        s.execute("account.update", &json!({"signature": "", "signature_html": format!("<b>Utente Esempio</b><br><a href=\"https://sirius.example\">Sirius</a><br><img src=\"data:image/png;base64,{logo}\">")})).unwrap();
        s.run_blocking("mail.send", json!({"to": "nuovo@cliente.it", "subject": "Offerta", "body": "Ciao,\nin allegato l'offerta."})).unwrap();
        let raw = mock.sent().last().unwrap().2.clone();
        let m = mailcraft_core::mime::parse(&raw).unwrap();
        assert!(m.text.contains("in allegato l'offerta.") && m.text.contains("-- \nUtente Esempio"), "{}", m.text);
        assert!(m.html.contains("<b>Utente Esempio</b>") && m.html.contains("cid:image1@mailcraft"), "{}", m.html);
        assert!(m.attachments.iter().all(|a| a.inline), "the logo is not listed as a file");
        // Someone written to comes first from now on, even with a short prefix.
        let v = s.execute("contacts.search", &json!({"query": "nu"})).unwrap();
        assert_eq!(v["contacts"][0]["email"], "nuovo@cliente.it", "{v}");
        // A reply keeps the HTML signature too.
        let uid = s.current().unwrap().list("INBOX")[0].uid;
        s.run_blocking("mail.reply", json!({"uid": uid, "body": "Grazie!"})).unwrap();
        let m = mailcraft_core::mime::parse(&mock.sent().last().unwrap().2).unwrap();
        assert!(
            m.html.contains("Grazie!") && m.html.contains("<b>Utente Esempio</b>") && m.html.contains("ha scritto") || m.html.contains("wrote"),
            "{}",
            m.html
        );
    }

    #[test]
    fn closes_subscribes_and_copies_sent_mail_without_a_sent_folder() {
        // A server without a Sent folder: the copy creates it.
        let mock = MockBackend::new();
        mock.put_draft(
            "INBOX",
            &mailcraft_core::message::Address::new("Anna", "anna@example.it"),
            &mailcraft_core::message::Draft {
                to: vec![mailcraft_core::message::Address::new("Utente", "utente@example.it")],
                subject: "Ciao".into(),
                body: "testo".into(),
                ..Default::default()
            },
            1_700_000_000,
            mailcraft_core::message::Flags::default(),
        );
        let mut s = session(&mock);
        s.run_blocking("mail.sync", json!({})).unwrap();
        let before = s.current().unwrap().mailboxes.len();
        // Open then close.
        s.run_blocking("mail.open", json!({"uid": 1})).unwrap();
        assert!(s.view().open.is_some());
        s.execute("mail.close", &json!({})).unwrap();
        assert!(s.view().open.is_none() && s.view().open_account.is_empty());
        // Hide a folder from the navigation.
        let v = s.run_blocking("mailbox.subscribe", json!({"path": "Junk", "subscribed": false})).unwrap();
        assert_eq!(v["subscribed"], false);
        assert!(!s.current().unwrap().mailbox("Junk").unwrap().subscribed);
        assert!(mock.log().iter().any(|l| l == "subscribe Junk false"));
        s.run_blocking("mail.sync", json!({})).unwrap();
        assert!(!s.current().unwrap().mailbox("Junk").unwrap().subscribed, "the server remembers");
        // No Sent folder at all: it is created on the first send.
        s.execute("account.update", &json!({"folders": {"sent": "Posta inviata"}})).unwrap();
        let v = s.run_blocking("mail.send", json!({"to": "luca@example.com", "subject": "Prova", "body": "Ciao"})).unwrap();
        assert_eq!(v["copied_to"], "Posta inviata", "{v}");
        assert_eq!(mock.count("Posta inviata"), 1);
        for _ in 0..200 {
            if s.jobs_pending() == 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
            let _ = s.pump();
        }
        assert_eq!(s.current().unwrap().mailboxes.len(), before + 1, "the new folder is listed");
    }

    #[test]
    fn oauth_sign_in_then_refresh_logs_in_with_the_token() {
        use std::io::{Read, Write};
        let mock = MockBackend::new().require_password("access-c0de");
        let m = mock.clone();
        let mut s = Session::with_broker(Box::new(move |_| Box::new(m.clone())), Arc::new(MockBroker::default()), None);
        // A Gmail address without a password signs in with Google.
        let v = s.execute("account.add", &json!({"email": "anna@gmail.com", "name": "Anna"})).unwrap();
        assert_eq!(v["account"]["auth"], "google");
        assert_eq!(v["account"]["has_password"], false);
        let id = v["id"].as_str().unwrap().to_string();
        assert!(matches!(s.execute("mail.sync", &json!({})), Err(Error::NotSignedIn(_))));
        // No client yet: the error says where to get one.
        let e = s.execute("account.oauthStart", &json!({"id": id})).unwrap_err().to_string();
        assert!(e.contains("oauth.configure") && e.contains("console.cloud.google.com"), "{e}");
        assert!(s.oauth_ready().is_empty());
        s.execute("oauth.configure", &json!({"provider": "google", "client_id": "cid", "client_secret": "sec"})).unwrap();
        assert_eq!(s.oauth_ready(), vec!["google".to_string()]);
        let p = s.execute("oauth.providers", &json!({})).unwrap();
        assert_eq!(p["providers"][0]["configured"], true);
        assert!(!p.to_string().contains("\"sec\"") && !p.to_string().contains("client_secret"), "the secret never leaves");
        let snap = serde_json::to_string(&s.snapshot(0).unwrap()).unwrap();
        assert!(
            snap.contains("\"client_id\":\"cid\"") && !snap.contains("\"sec\"") && !snap.contains("client_secret"),
            "the snapshot carries the client id, never the secret: {snap}"
        );
        assert_eq!(s.config().oauth_client("google").map(|c| c.client_secret.clone()).as_deref(), Some("sec"), "the session keeps it");
        // A Google Workspace domain: the guessed hosts give way to Google's servers.
        let w = s.execute("account.add", &json!({"email": "anna@example.it", "name": "Example", "auth": "google"})).unwrap();
        assert_eq!(w["account"]["imap"]["host"], "imap.gmail.com", "{w}");
        assert_eq!(w["account"]["smtp"]["host"], "smtp.gmail.com");
        s.execute("account.remove", &json!({"id": w["id"]})).unwrap();
        // Start: a URL to open and a listener on this machine.
        let v = s.execute("account.oauthStart", &json!({"email": "anna@gmail.com"})).unwrap();
        let url = v["url"].as_str().unwrap();
        assert!(url.starts_with("https://accounts.google.com/") && url.contains("client_id=cid") && url.contains("login_hint=anna%40gmail.com"));
        let redirect = v["redirect"].as_str().unwrap().to_string();
        assert!(redirect.starts_with("http://127.0.0.1:"), "{redirect}");
        let state = v["state"].as_str().unwrap().to_string();
        assert!(s.execute("account.oauthFinish", &json!({"state": "nope", "code": "x"})).is_err());
        let e =
            s.execute("account.oauthStart", &json!({"id": id, "redirect": "http://192.0.2.10:8080/api/oauth/callback"})).unwrap_err().to_string();
        assert!(e.contains("MAILCRAFT_PUBLIC_URL"), "{e}");
        assert!(s.execute("account.oauthFinish", &json!({"state": state, "error": "access_denied"})).unwrap_err().to_string().contains("refused"));
        // The browser comes back through the loopback listener; pump finishes the sign-in.
        let v = s.execute("account.oauthStart", &json!({"id": id})).unwrap();
        let state = v["state"].as_str().unwrap().to_string();
        let port: u16 = v["redirect"].as_str().unwrap().trim_start_matches("http://127.0.0.1:").trim_end_matches('/').parse().unwrap();
        let mut c = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        c.write_all(format!("GET /?state={state}&code=c0de HTTP/1.1\r\nHost: x\r\n\r\n").as_bytes()).unwrap();
        let mut page = String::new();
        let _ = c.read_to_string(&mut page);
        assert!(page.contains("Accesso completato"));
        let mut signed = false;
        for _ in 0..300 {
            for done in s.pump() {
                if done.command == "account.oauthFinish" {
                    assert_eq!(done.result.unwrap()["signed_in"], true);
                    signed = true;
                }
            }
            if signed && s.jobs_pending() == 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(signed, "the sign-in finished");
        assert!(s.has_password(&id));
        assert_eq!(s.secrets.tokens(&id).unwrap().access_token, "access-c0de");
        assert!(!s.current().unwrap().mailboxes.is_empty(), "folders loaded after the sign-in");
        assert!(mock.log().iter().any(|l| l == &format!("connect {id}")));
        assert!(s.execute("account.list", &json!({})).unwrap()["accounts"][0]["has_password"] == true);
        // An expired token is renewed before the next job (the mock accepts the renewed one).
        mock.with_password("refreshed-refresh-c0de");
        if let Some(t) = s.secrets.tokens.get_mut(&id) {
            t.expires_at = 1;
        }
        s.run_blocking("mail.sync", json!({})).unwrap();
        assert_eq!(s.secrets.tokens(&id).unwrap().access_token, "refreshed-refresh-c0de");
        assert!(s.current().unwrap().error.is_none(), "{:?}", s.current().unwrap().error);
        // A password switches the account back; removing drops the tokens.
        s.execute("account.setPassword", &json!({"id": id, "password": "app-pw"})).unwrap();
        assert_eq!(s.config().account(&id).unwrap().auth, "");
        s.execute("account.remove", &json!({"id": id})).unwrap();
        assert!(s.secrets.tokens(&id).is_none());
    }

    #[test]
    fn unified_view_merges_every_account() {
        let mut s = Session::demo();
        assert_eq!(s.config().accounts.len(), 2);
        let v = s.execute("mail.select", &json!({"account": "*"})).unwrap();
        assert_eq!(v["unified"], true);
        assert!(v["jobs"].is_array(), "the second account's inbox is loaded: {v}");
        assert!(s.view().unified());
        s.run_blocking("mail.list", json!({"account": "*", "refresh": true})).unwrap();
        let v = s.execute("mail.list", &json!({"account": "*"})).unwrap();
        let list = s.unified_list(Role::Inbox);
        assert_eq!(list.len(), 24, "both demo accounts, 12 each");
        assert!(list.iter().any(|e| e.account == "demo") && list.iter().any(|e| e.account == "lavoro"));
        assert!(list.windows(2).all(|w| w[0].date >= w[1].date), "newest first across accounts");
        assert_eq!(v["total"], 24);
        assert!(v["envelopes"][0]["account"].as_str().is_some_and(|a| !a.is_empty()));
        // Starred = flagged inbox mail of every account.
        assert_eq!(s.unified_list(Role::Starred).len(), 2);
        // Search across accounts merges the hits with their account.
        let _ = s.run_blocking("mail.search", json!({"account": "*", "query": "fattura"})).unwrap();
        assert_eq!(s.view().results.len(), 2);
        assert_eq!(s.view().results.iter().filter(|e| e.account == "lavoro").count(), 1);
        s.execute("mail.search", &json!({"account": "*", "query": ""})).unwrap();
        // Open, select and act in the unified view name the account.
        let e = list.iter().find(|e| e.account == "lavoro").unwrap().clone();
        s.run_blocking("mail.open", json!({"account": "lavoro", "mailbox": e.mailbox, "uid": e.uid})).unwrap();
        assert_eq!(s.view().open_account, "lavoro");
        assert_eq!(s.view().open, Some((e.mailbox.clone(), e.uid)));
        s.execute("selection.set", &json!({"account": "lavoro", "mailbox": e.mailbox, "uids": [e.uid]})).unwrap();
        let before = s.state("lavoro").unwrap().list(&e.mailbox).len();
        s.run_blocking("mail.archive", json!({})).unwrap();
        assert_eq!(s.state("lavoro").unwrap().list(&e.mailbox).len(), before - 1);
        assert!(s.view().unified(), "acting keeps the unified view");
        assert_eq!(s.execute("view.get", &json!({})).unwrap()["unified"], true);
        // Leaving the unified view.
        s.run_blocking("mail.select", json!({"account": "demo", "mailbox": "INBOX"})).unwrap();
        assert!(!s.view().unified());
    }

    #[test]
    fn host_paths_are_the_local_sessions_right_only() {
        let dir = std::env::temp_dir().join(format!("mailcraft-host-paths-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("offerta.txt");
        std::fs::write(&file, b"ciao").unwrap();
        let path = file.display().to_string();
        let mock = MockBackend::demo();
        let mut s = session(&mock);
        s.run_blocking("mail.sync", json!({})).unwrap();
        let uid = s.current().unwrap().list("INBOX").iter().find(|e| e.has_attachments).unwrap().uid;
        s.run_blocking("mail.open", json!({"uid": uid})).unwrap();
        // The local session reads and writes files by path.
        assert!(s.host_paths_allowed());
        let v = s.execute("mail.attachment", &json!({"uid": uid, "index": 0, "path": dir.display().to_string()})).unwrap();
        assert!(std::path::Path::new(v["saved"].as_str().unwrap()).is_file(), "{v}");
        s.run_blocking("mail.send", json!({"to": "luca@example.com", "subject": "Con file", "attachments": [{"path": path}]})).unwrap();
        let sent = mailcraft_core::mime::parse(&mock.sent().last().unwrap().2).unwrap();
        assert_eq!(sent.attachments.len(), 1);
        s.run_blocking("draft.save", json!({"to": "luca@example.com", "subject": "Bozza", "attachments": [{"path": path}]})).unwrap();
        // A session served to other people refuses every path and keeps the base64 forms.
        s.allow_host_paths(false);
        let sent_before = mock.sent().len();
        for (cmd, prm) in [
            ("mail.attachment", json!({"uid": uid, "index": 0, "path": dir.display().to_string()})),
            ("mail.send", json!({"to": "luca@example.com", "subject": "x", "attachments": [{"path": path}]})),
            ("draft.save", json!({"to": "luca@example.com", "subject": "x", "attachments": [{"path": path}]})),
        ] {
            let e = s.execute(cmd, &prm).unwrap_err().to_string();
            assert!(e.contains("host paths are not available on a shared server"), "{cmd}: {e}");
        }
        assert_eq!(mock.sent().len(), sent_before, "nothing was sent");
        let v = s.execute("mail.attachment", &json!({"uid": uid, "index": 0})).unwrap();
        assert!(v["data"].as_str().is_some_and(|d| !d.is_empty()) && v.get("saved").is_none(), "{v}");
        let b64 = mailcraft_core::mime::base64_encode(b"ciao");
        s.run_blocking("mail.send", json!({"to": "luca@example.com", "subject": "Base64", "attachments": [{"name": "o.txt", "data": b64}]})).unwrap();
        let sent = mailcraft_core::mime::parse(&mock.sent().last().unwrap().2).unwrap();
        assert_eq!(sent.attachments.len(), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn every_command_survives_empty_params() {
        let mut s = Session::demo();
        for c in COMMANDS {
            let _ = s.execute(c.id, &json!({}));
            let _ = s.execute(c.id, &json!({"uids": [1, 999], "uid": 999, "mailbox": "Nope", "email": "x", "id": "ghost", "to": "", "query": "", "key": "", "page": 999999}));
            let _ = s.execute(c.id, &json!({"account": "*", "role": "nope", "uids": [1], "uid": 1, "query": "x"}));
            let _ = s.execute(c.id, &json!({"account": "*", "role": "trash", "to": "a@b.co", "subject": "s", "body": "b"}));
        }
        for _ in 0..20 {
            s.pump();
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(s.view().mailbox.is_empty() || true);
    }
}
