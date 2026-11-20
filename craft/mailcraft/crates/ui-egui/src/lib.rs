//! MailCraft's egui front end (layer L6): a Gmail-style client — folders on the left, the
//! message list, a reading pane, a floating compose window — on top of the engine. It is
//! thin: it draws `Session` state and acts only through [`MailApp::run`], so every action is
//! also a command for the CLI and the MCP server.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod chrome;
mod compose;
pub mod format;
pub mod i18n;
pub mod icons;
mod list;
mod nav;
mod reader;
pub mod richtext;
mod settings;
pub mod theme;
mod widgets;

use std::time::Duration;

use web_time::Instant;

pub use i18n::Language;
pub use mailcraft_engine::MailSession;
use mailcraft_engine::core::mailbox::Role;
use mailcraft_engine::core::message::DraftAttachment;
use mailcraft_engine::store::AccountState;
pub use mailcraft_engine::{self as engine, core};
use mailcraft_engine::{ALL, Completed, Session, unified_list};
use serde_json::{Value, json};

/// Which screen fills the window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    Mail,
    Settings,
}

/// Something only the host app can do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    /// Pick files to attach to the compose window.
    AttachFiles,
    /// Save an attachment's bytes somewhere.
    SaveAttachment { name: String, data: Vec<u8> },
    /// Open a link in the browser.
    OpenUrl(String),
    /// Print a page (a self-contained HTML document that prints itself when opened).
    Print { title: String, html: String },
    /// Ask the framework's assistant (`POST` the JSON body to the assist URL, same origin,
    /// header `X-Requested-With: FamilAI`); the host answers with [`MailApp::assist_answer`].
    Assist { token: u64, url: String, body: Value },
}

/// What an assistant answer is for.
#[derive(Clone, Debug, PartialEq, Eq)]
enum AssistJob {
    /// Reply suggestions for a message (account, mailbox, uid).
    Suggest(String, String, u32),
    /// A draft for the compose window.
    Draft,
}

/// Reply suggestions and drafts from the framework's assistant.
#[derive(Default)]
pub struct AssistState {
    /// Suggestions by (account, mailbox, uid); an empty list while loading or when none came.
    pub suggestions: std::collections::HashMap<(String, String, u32), Vec<String>>,
    pending: std::collections::HashMap<u64, AssistJob>,
    next_token: u64,
    /// What the person wants the draft to say.
    pub instruction: String,
    pub busy: bool,
    pub error: String,
}

/// The message being written.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Compose {
    pub to: String,
    pub cc: String,
    pub bcc: String,
    pub subject: String,
    pub body: String,
    pub show_cc: bool,
    pub attachments: Vec<DraftAttachment>,
    pub draft_uid: Option<u32>,
    /// Replying to (mailbox, uid, everyone).
    pub reply_to: Option<(String, u32, bool)>,
    /// Forwarding (mailbox, uid); the original's attachments travel with `mail.forward`.
    pub forward_of: Option<(String, u32)>,
    /// Names of the original's attachments, shown while forwarding.
    pub forward_attachments: Vec<String>,
    pub minimized: bool,
    pub focus_body: bool,
    /// The account the message is sent from (its id); empty = the view's account.
    pub from_account: String,
    /// Where the person dragged the window (from its bottom-right home).
    pub offset: egui::Vec2,
    /// The quoted original of a reply (kept apart so a drafted text can replace the note).
    pub quote: String,
    /// The next picked files are images to show inline; attachments before this index are files.
    pub inline_from: Option<usize>,
    /// The formatting row ("Aa") is shown above the text unless hidden.
    pub hide_format: bool,
    /// The body's selection (character indices) as of the last frame it had the focus: egui
    /// collapses the selection when the text box loses the focus to a toolbar button, so the
    /// toolbar formats this one.
    pub selection: Option<(usize, usize)>,
    /// Give the body the focus back with the caret here, on the next frame without a click
    /// (egui takes the focus away again from a text box that is not under a click).
    pub refocus: Option<usize>,
}

/// The whole app state the UI keeps on top of the engine session.
pub struct MailApp {
    /// The local engine session, or a remote one (the web app).
    pub session: Box<dyn MailSession>,
    pub language: Language,
    pub dark: bool,
    applied_dark: Option<bool>,
    pub screen: Screen,
    pub search: String,
    search_focus: bool,
    pub compose: Option<Compose>,
    pub status: String,
    toast: Option<(String, Instant)>,
    /// Host actions the app should serve (file dialogs, links).
    pub requests: Vec<Request>,
    settings: settings::SettingsState,
    last_sync: Option<Instant>,
    /// Keep the reader on screen when the reading pane is off (Gmail-style full view).
    reading_full: bool,
    /// Messages of the open conversation that were expanded in the reader.
    expanded: Vec<u32>,
    /// Settings opened only because no account existed yet: leave it once one appears.
    auto_settings: bool,
    /// Recipient completion: the prefix asked for and the matches (display, address).
    pub contact_query: String,
    pub contact_hits: Vec<(String, String)>,
    pub assist: AssistState,
}

impl MailApp {
    pub fn new(session: Box<dyn MailSession>) -> Self {
        let language = match session.config().preferences.language.as_str() {
            "it" => Language::It,
            "en" => Language::En,
            _ => Language::system(),
        };
        let dark = session.config().preferences.theme == "dark";
        let screen = if session.config().accounts.is_empty() { Screen::Settings } else { Screen::Mail };
        Self {
            session,
            language,
            dark,
            applied_dark: None,
            screen,
            search: String::new(),
            search_focus: false,
            compose: None,
            status: String::new(),
            toast: None,
            requests: Vec::new(),
            settings: settings::SettingsState::default(),
            last_sync: None,
            reading_full: false,
            expanded: Vec::new(),
            auto_settings: screen == Screen::Settings,
            contact_query: String::new(),
            contact_hits: Vec::new(),
            assist: AssistState::default(),
        }
    }

    /// The demo app: the mock mailbox, Italian or English, no files.
    pub fn demo() -> Self {
        Self::new(Box::new(Session::demo()))
    }

    /// Runs a command; failures go to the status bar. A local session answers at once, a remote
    /// one through [`MailSession::pump`]; both answers reach [`Self::handle_result`].
    pub fn run(&mut self, id: &str, params: Value) -> Option<Value> {
        match self.session.execute(id, &params) {
            Ok(v) => {
                if v.get("queued").is_none() && v.get("job").is_none() && v.get("jobs").is_none() {
                    self.handle_result(id, &v);
                }
                Some(v)
            }
            Err(e) => {
                self.status = self.language.tr_message(&e.to_string());
                None
            }
        }
    }

    /// Follow-ups that need a command's answer (the settings form, attachment downloads).
    fn handle_result(&mut self, command: &str, v: &Value) {
        match command {
            "account.suggest" => {
                if let Some(f) = &mut self.settings.editing {
                    f.apply_suggestion(v);
                }
            }
            "account.add" | "account.update" => {
                let id = v["id"].as_str().unwrap_or_default().to_string();
                if !id.is_empty() {
                    if let Some(f) = &mut self.settings.editing {
                        f.id = Some(id.clone());
                        f.password.clear();
                    }
                    self.settings.result = self.tr("Saved").to_string();
                    self.settings.reload = true;
                    if self.settings.oauth_after_save {
                        self.settings.oauth_after_save = false;
                        self.run("account.oauthStart", json!({"id": id}));
                    } else if self.session.has_password(&id) || v["account"]["has_password"] == true {
                        self.run("account.test", json!({"id": id}));
                        // Load the folders right away: the form lists them.
                        self.run("mail.sync", json!({"account": id}));
                    }
                }
            }
            "account.oauthStart" => {
                if let Some(url) = v["url"].as_str() {
                    self.requests.push(Request::OpenUrl(url.to_string()));
                    // The return address is what the provider's client must list, exactly.
                    let redirect = v["redirect"].as_str().unwrap_or_default();
                    let text = self.language.fill(
                        "Complete the sign-in in the browser, then come back here (return address registered with the provider: {redirect})",
                        &[("redirect", redirect)],
                    );
                    self.settings.result = text.clone();
                    self.status = text;
                }
            }
            "contacts.search" => {
                // Only the answer to the prefix still typed matters.
                if v["query"].as_str() == Some(self.contact_query.as_str()) {
                    self.contact_hits = v["contacts"]
                        .as_array()
                        .map(|l| l.iter().filter_map(|c| Some((c["display"].as_str()?.to_string(), c["email"].as_str()?.to_string()))).collect())
                        .unwrap_or_default();
                }
            }
            "mail.attachment" => {
                if let (Some(name), Some(data)) = (v["name"].as_str(), v["data"].as_str().and_then(core::mime::base64_decode)) {
                    self.requests.push(Request::SaveAttachment { name: name.to_string(), data });
                }
            }
            _ => {}
        }
    }

    /// Opens the settings form for a new account with this address.
    pub fn settings_add(&mut self, email: &str) {
        self.screen = Screen::Settings;
        self.settings.editing = Some(settings::AccountForm::from_email(email));
    }

    /// Opens the settings form of an account.
    pub fn settings_edit(&mut self, id: &str) {
        if let Some(a) = self.session.account(id).cloned() {
            self.screen = Screen::Settings;
            self.settings.editing = Some(settings::AccountForm::from_account(&a));
        }
    }

    pub fn tr<'a>(&self, text: &'a str) -> &'a str {
        self.language.tr(text)
    }

    pub fn italian(&self) -> bool {
        self.language == Language::It
    }

    /// The offset from UTC (minutes) dates are shown in: the preference, else the host's.
    pub fn tz(&self) -> i32 {
        self.session.config().preferences.offset_minutes()
    }

    /// A UTC timestamp moved into the shown time zone, for `time::short`/`time::long`.
    pub fn local(&self, ts: i64) -> i64 {
        core::message::time::shifted(ts, self.tz())
    }

    /// A command's translated label and shortcut, for tooltips.
    pub fn tip(&self, command: &str) -> String {
        match mailcraft_engine::command(command) {
            Some(c) if !c.shortcut.is_empty() => format!("{} ({})", self.tr(c.label), c.shortcut),
            Some(c) => self.tr(c.label).to_string(),
            None => command.to_string(),
        }
    }

    /// The unified view (every account at once) is on.
    pub fn unified(&self) -> bool {
        self.session.view().unified()
    }

    /// The account of the open message: outside the unified view, the view's account.
    pub fn open_account(&self) -> String {
        let v = self.session.view();
        if !v.open_account.is_empty() {
            v.open_account.clone()
        } else if v.unified() {
            String::new()
        } else {
            v.account.clone()
        }
    }

    /// The cached state of the open message's account.
    pub fn open_state(&self) -> Option<&AccountState> {
        self.session.state(&self.open_account())
    }

    /// The account an action without an explicit one goes to: the view's, or in the unified
    /// view the selection's, the open message's, else the first account (as the engine does).
    pub fn acting_account(&self) -> String {
        let v = self.session.view();
        if !v.unified() {
            return v.account.clone();
        }
        if !v.selection.is_empty() && !v.selection_account.is_empty() {
            return v.selection_account.clone();
        }
        if !v.open_account.is_empty() {
            return v.open_account.clone();
        }
        self.session.config().accounts.first().map(|a| a.id.clone()).unwrap_or_default()
    }

    /// The merged list behind the unified view's role, each envelope tagged with its account.
    pub fn unified_rows(&self) -> Vec<core::message::Envelope> {
        unified_list(self.session.config(), |id| self.session.state(id), self.session.view().unified_role())
    }

    /// Unread inbox mail across every account.
    pub fn total_unread(&self) -> u32 {
        self.session
            .config()
            .accounts
            .iter()
            .map(|a| self.session.state(&a.id).and_then(|s| s.by_role(Role::Inbox)).map_or(0, |m| m.unread))
            .fold(0, u32::saturating_add)
    }

    fn toast(&mut self, text: String) {
        self.toast = Some((text, Instant::now()));
    }

    /// Applies finished jobs: status messages, toasts.
    fn pump(&mut self) {
        let done: Vec<Completed> = self.session.pump();
        if self.auto_settings && !self.session.config().accounts.is_empty() && self.settings.editing.is_none() {
            self.auto_settings = false;
            self.screen = Screen::Mail;
        }
        for c in done {
            match c.result {
                Ok(v) => match c.command.as_str() {
                    "account.suggest" | "account.add" | "account.update" | "account.oauthStart" | "mail.attachment" | "contacts.search" => {
                        self.handle_result(&c.command, &v)
                    }
                    "account.oauthFinish" => {
                        let text = self.tr("Signed in").to_string();
                        self.settings.result = text.clone();
                        self.settings.reload = true;
                        self.toast(text);
                        if let Some(id) = v["account"].as_str() {
                            self.run("account.test", json!({"id": id}));
                        }
                    }
                    "mail.send" | "mail.reply" | "mail.forward" => {
                        if let Some(e) = v["copy_error"].as_str() {
                            self.status = self.language.fill("Message sent, but the copy to the Sent folder failed: {error}", &[("error", e)]);
                        }
                        self.toast(self.tr("Message sent").to_string());
                    }
                    "draft.save" => self.toast(self.tr("Draft saved").to_string()),
                    "account.test" => {
                        let text = if v["ok"] == true {
                            self.tr("Connection OK: IMAP and SMTP accept the login").to_string()
                        } else {
                            format!("IMAP OK · SMTP {}", v["smtp"].as_str().unwrap_or("?"))
                        };
                        self.settings.result = text.clone();
                        self.toast(text);
                    }
                    "account.probe" => {
                        let text =
                            format!("{}: {} · {}", self.tr("Servers found"), v["imap"].as_str().unwrap_or(""), v["smtp"].as_str().unwrap_or(""));
                        self.settings.result = text.clone();
                        self.settings.reload = true;
                        self.toast(text);
                    }
                    "mail.sync" => self.status = self.language.fill("Synced {time}", &[("time", &chrome::clock(self.tz()))]),
                    _ => {}
                },
                Err(e) => {
                    let text = self.language.tr_message(&e.to_string());
                    if c.command.starts_with("account.") {
                        self.settings.result = text.clone();
                    }
                    self.status = text;
                }
            }
        }
    }

    /// Asks for suggestions once the open message is loaded (called every frame, cheap).
    fn assist_tick(&mut self) {
        if self.session.view().open.is_some() {
            self.assist_suggest_open();
        }
    }

    /// Starts a sync of the current account when it is time (or `force`).
    pub fn sync(&mut self, force: bool) {
        let minutes = self.session.config().preferences.sync_minutes;
        let due = match self.last_sync {
            None => true,
            Some(t) => minutes > 0 && t.elapsed() >= Duration::from_secs(u64::from(minutes) * 60),
        };
        if (force || due) && !self.session.config().accounts.is_empty() {
            self.last_sync = Some(Instant::now());
            if self.run("mail.sync", json!({"all": true})).is_some() {
                self.status = self.tr("Syncing…").to_string();
            }
        }
    }

    /// Shows a mailbox of the current account.
    pub fn select_mailbox(&mut self, path: &str) {
        self.search.clear();
        self.reading_full = false;
        self.expanded.clear();
        self.run("mail.select", json!({"mailbox": path}));
        let _ = self.run("mail.search", json!({"query": ""}));
    }

    pub fn select_role(&mut self, role: Role) {
        if self.unified() {
            self.search.clear();
            self.reading_full = false;
            self.expanded.clear();
            self.run("mail.select", json!({"account": ALL, "role": role.name()}));
            let _ = self.run("mail.search", json!({"account": ALL, "query": ""}));
            return;
        }
        let account = self.session.view().account.clone();
        match self.session.folder_for(&account, role) {
            Some(path) => self.select_mailbox(&path),
            None => self.status = self.language.fill("No {role} folder on this account", &[("role", role.label(self.italian()))]),
        }
    }

    /// Switches to an account, or to every account at once (`"*"`, the unified inbox).
    pub fn select_account(&mut self, id: &str) {
        if id == ALL {
            self.search.clear();
            self.reading_full = false;
            self.expanded.clear();
            self.run("mail.select", json!({"account": ALL, "role": Role::Inbox.name()}));
            return;
        }
        self.run("account.select", json!({"id": id}));
        self.search.clear();
        self.reading_full = false;
        if self.session.current().is_some_and(|s| s.mailboxes.is_empty()) {
            self.run("mail.sync", json!({"account": id}));
        }
    }

    /// Opens a message of the mailbox in view (or, in the unified view, of the selection).
    pub fn open(&mut self, uid: u32) {
        let v = self.session.view();
        let (account, mailbox) =
            if v.unified() { (v.selection_account.clone(), v.selection_mailbox.clone()) } else { (v.account.clone(), v.mailbox.clone()) };
        self.open_at(&account, &mailbox, uid);
    }

    /// Opens a message in the reader (fetching it when needed).
    pub fn open_at(&mut self, account: &str, mailbox: &str, uid: u32) {
        let mut prm = json!({"uid": uid, "mailbox": mailbox});
        if !account.is_empty() {
            prm["account"] = json!(account);
        }
        self.run("mail.open", prm);
        self.expanded = vec![uid];
        if !self.session.config().preferences.reading_pane {
            self.reading_full = true;
        }
    }

    pub fn close_reader(&mut self) {
        self.reading_full = false;
        self.expanded.clear();
        self.run("selection.clear", json!({}));
        self.run("mail.close", json!({}));
    }

    /// Runs a move-style command on the selection (or the open message), with an undo toast.
    pub fn act(&mut self, command: &str, params: Value) {
        let mut prm = params;
        let view = self.session.view().clone();
        if prm.get("uids").is_none()
            && view.selection.is_empty()
            && let Some((mailbox, uid)) = view.open.clone()
        {
            prm["uids"] = json!([uid]);
            if view.unified() && !view.open_account.is_empty() {
                prm["account"] = json!(view.open_account);
                prm["mailbox"] = json!(mailbox);
            }
        } else if prm.get("uids").is_some()
            && prm.get("account").is_none()
            && view.unified()
            && !view.open_account.is_empty()
            && let Some((mailbox, _)) = view.open.clone()
        {
            // The reader's actions name the open message: they go to its account.
            prm["account"] = json!(view.open_account);
            prm["mailbox"] = json!(mailbox);
        }
        if self.run(command, prm).is_some() {
            let label = match command {
                "mail.archive" => self.tr("Archived"),
                "mail.trash" => self.tr("Moved to the trash"),
                "mail.spam" => self.tr("Reported as spam"),
                "mail.move" => self.tr("Moved"),
                "mail.delete" => self.tr("Deleted forever"),
                "mail.markRead" => self.tr("Marked"),
                "mail.star" => self.tr("Star changed"),
                _ => "",
            };
            if !label.is_empty() {
                self.toast(label.to_string());
            }
            if matches!(command, "mail.archive" | "mail.trash" | "mail.spam" | "mail.move") {
                self.reading_full = false;
            }
        }
    }

    /// Prints the open message: a self-contained page handed to the host.
    pub fn print_open_message(&mut self) {
        let Some((mailbox, uid)) = self.session.view().open.clone() else { return };
        let Some(m) = self.open_state().and_then(|st| st.message(&mailbox, uid)).cloned() else {
            self.status = self.tr("Wait for the message to load").to_string();
            return;
        };
        let italian = self.italian();
        let headers = [
            (self.tr("From"), core::message::join_addresses(&m.envelope.from)),
            (self.tr("To"), core::message::join_addresses(&m.envelope.to)),
            (self.tr("Cc"), core::message::join_addresses(&m.envelope.cc)),
            (self.tr("Date"), core::message::time::long(self.local(m.envelope.date), italian)),
        ];
        let title = if m.envelope.subject.trim().is_empty() { "(no subject)".to_string() } else { m.envelope.subject.clone() };
        let html = core::mime::printable(&title, &headers, &m.html, &m.text);
        self.requests.push(Request::Print { title, html });
    }

    /// Prints the message being written.
    pub fn print_compose(&mut self) {
        let Some(c) = self.compose.clone() else { return };
        let from = self
            .session
            .account(&c.from_account)
            .or_else(|| self.session.account(&self.session.view().account))
            .map(|a| a.email.clone())
            .unwrap_or_default();
        let headers = [(self.tr("From"), from), (self.tr("To"), c.to.clone()), (self.tr("Cc"), c.cc.clone())];
        let title = if c.subject.trim().is_empty() { self.tr("New message").to_string() } else { c.subject.clone() };
        let html = core::mime::printable(&title, &headers, &core::mime::markup_to_html(&c.body), "");
        self.requests.push(Request::Print { title, html });
    }

    /// Replaces the recipient being typed (the text after the last comma) with `display`.
    pub fn complete_recipient(value: &mut String, display: &str) {
        let keep = value.rfind(',').map(|i| value[..=i].to_string()).unwrap_or_default();
        *value = if keep.is_empty() { format!("{display}, ") } else { format!("{} {display}, ", keep.trim_end()) };
    }

    pub fn compose_new(&mut self) {
        if let Some(c) = &mut self.compose {
            // A message is already being written: bring it back instead of replacing it.
            c.minimized = false;
            c.focus_body = true;
            return;
        }
        self.compose = Some(Compose { focus_body: false, from_account: self.acting_account(), ..Compose::default() });
    }

    /// Opens a reply (or reply-all) to the open message, with the quote in the body.
    pub fn compose_reply(&mut self, all: bool) {
        let Some((mailbox, uid)) = self.session.view().open.clone() else { return };
        let account = self.open_account();
        let Some(st) = self.session.state(&account) else { return };
        let Some(m) = st.message(&mailbox, uid) else {
            self.status = self.tr("Wait for the message to load").to_string();
            return;
        };
        let me = self.session.account(&account).map(|a| a.email.to_ascii_lowercase()).unwrap_or_default();
        let reply_to: Vec<_> = if m.reply_to.is_empty() { m.envelope.from.clone() } else { m.reply_to.clone() };
        let mut to: Vec<String> = reply_to.iter().filter(|a| a.email.to_ascii_lowercase() != me).map(|a| a.display()).collect();
        if to.is_empty() {
            to = m.envelope.to.iter().map(|a| a.display()).collect();
        }
        let cc: Vec<String> = if all {
            m.envelope
                .to
                .iter()
                .chain(&m.envelope.cc)
                .filter(|a| a.email.to_ascii_lowercase() != me && !to.iter().any(|t| t.contains(&a.email)))
                .map(|a| a.display())
                .collect()
        } else {
            Vec::new()
        };
        let subject = if m.envelope.subject.to_ascii_lowercase().starts_with("re:") {
            m.envelope.subject.clone()
        } else {
            format!("Re: {}", m.envelope.subject)
        };
        let quote = core::mime::quote(m, self.italian());
        self.compose = Some(Compose {
            to: to.join(", "),
            cc: cc.join(", "),
            show_cc: !cc.is_empty(),
            subject,
            body: format!("\n{quote}"),
            quote,
            reply_to: Some((mailbox, uid, all)),
            focus_body: true,
            from_account: account,
            ..Compose::default()
        });
    }

    /// Opens a reply that starts with a suggested sentence.
    pub fn reply_with(&mut self, text: &str) {
        self.compose_reply(false);
        if let Some(c) = &mut self.compose {
            c.body = format!("{text}\n{}", c.quote);
            c.focus_body = false;
        }
    }

    /// The assistant is available: the server announced its endpoint.
    pub fn assist_available(&self) -> bool {
        !self.session.assist_url().is_empty()
    }

    /// Asks the assistant something; the host performs the request.
    fn assist_request(&mut self, job: AssistJob, mut body: Value) {
        let url = self.session.assist_url();
        if url.is_empty() {
            return;
        }
        body["local_only"] = json!(!self.session.config().preferences.assist_cloud);
        body["language"] = json!(self.language.code());
        self.assist.next_token += 1;
        let token = self.assist.next_token;
        self.assist.pending.insert(token, job);
        self.assist.busy = true;
        self.requests.push(Request::Assist { token, url, body });
    }

    /// Asks for reply suggestions of the open message, once per message.
    fn assist_suggest_open(&mut self) {
        if !self.assist_available() || !self.session.config().preferences.assist_suggestions {
            return;
        }
        let Some((mailbox, uid)) = self.session.view().open.clone() else { return };
        let account = self.open_account();
        let key = (account.clone(), mailbox.clone(), uid);
        if self.assist.suggestions.contains_key(&key) {
            return;
        }
        let Some(m) = self.open_state().and_then(|st| st.message(&mailbox, uid)) else { return };
        let body = json!({
            "kind": "suggest",
            "subject": m.envelope.subject,
            "sender": core::message::join_addresses(&m.envelope.from),
            "text": core::clip(&m.body_text(), 12_000),
        });
        self.assist.suggestions.insert(key.clone(), Vec::new());
        self.assist_request(AssistJob::Suggest(account, mailbox, uid), body);
    }

    /// Asks the assistant to write the reply being composed from the instruction typed.
    pub fn assist_draft(&mut self) {
        let Some(c) = self.compose.clone() else { return };
        let instruction = self.assist.instruction.trim().to_string();
        let original =
            c.reply_to.clone().and_then(|(mailbox, uid, _)| self.session.state(&c.from_account).and_then(|st| st.message(&mailbox, uid)).cloned());
        let body = json!({
            "kind": "draft",
            "subject": c.subject,
            "sender": original.as_ref().map(|m| core::message::join_addresses(&m.envelope.from)).unwrap_or_default(),
            "text": original.as_ref().map(|m| core::clip(&m.body_text(), 12_000).to_string()).unwrap_or_default(),
            "to": c.to,
            "instruction": instruction,
            "tone": "professionale",
        });
        self.assist_request(AssistJob::Draft, body);
    }

    /// The host's answer to a [`Request::Assist`]: suggestions go under the message, a draft
    /// into the compose window.
    pub fn assist_answer(&mut self, token: u64, result: Result<Value, String>) {
        let Some(job) = self.assist.pending.remove(&token) else { return };
        self.assist.busy = !self.assist.pending.is_empty();
        match (job, result) {
            (AssistJob::Suggest(a, m, uid), Ok(v)) => {
                let list: Vec<String> = v["suggestions"]
                    .as_array()
                    .map(|l| l.iter().filter_map(Value::as_str).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).take(5).collect())
                    .unwrap_or_default();
                self.assist.suggestions.insert((a, m, uid), list);
                self.assist.error.clear();
            }
            (AssistJob::Draft, Ok(v)) => {
                if let (Some(draft), Some(c)) = (v["draft"].as_str(), &mut self.compose) {
                    c.body = if c.quote.is_empty() { draft.trim_end().to_string() } else { format!("{}\n{}", draft.trim_end(), c.quote) };
                }
                self.assist.error.clear();
            }
            (AssistJob::Suggest(a, m, uid), Err(e)) => {
                self.assist.suggestions.remove(&(a, m, uid));
                self.assist.error = e.clone();
                self.status = e;
            }
            (AssistJob::Draft, Err(e)) => {
                self.assist.error = e.clone();
                self.status = e;
            }
        }
    }

    pub fn compose_forward(&mut self) {
        let Some((mailbox, uid)) = self.session.view().open.clone() else { return };
        let account = self.open_account();
        let Some(m) = self.session.state(&account).and_then(|st| st.message(&mailbox, uid)) else {
            self.status = self.tr("Wait for the message to load").to_string();
            return;
        };
        let subject = if m.envelope.subject.to_ascii_lowercase().starts_with("fwd:") {
            m.envelope.subject.clone()
        } else {
            format!("Fwd: {}", m.envelope.subject)
        };
        let header = self.tr("---------- Forwarded message ----------");
        let body = format!(
            "\n\n{header}\n{}: {}\n{}: {}\n{}: {}\n{}: {}\n\n{}",
            self.tr("From"),
            core::message::join_addresses(&m.envelope.from),
            self.tr("Date"),
            core::message::time::long(self.local(m.envelope.date), self.italian()),
            self.tr("Subject"),
            m.envelope.subject,
            self.tr("To"),
            core::message::join_addresses(&m.envelope.to),
            m.body_text()
        );
        let forward_attachments = m.attachments.iter().filter(|a| !a.inline).map(|a| a.name.clone()).collect();
        self.compose =
            Some(Compose { subject, body, forward_attachments, forward_of: Some((mailbox, uid)), from_account: account, ..Compose::default() });
    }

    fn compose_params(&self, c: &Compose) -> Value {
        let mut v = json!({"to": c.to, "cc": c.cc, "bcc": c.bcc, "subject": c.subject, "body": c.body});
        // Formatting or inline images: the body also goes out as HTML.
        if core::mime::has_markup(&c.body) || c.attachments.iter().any(|a| !a.cid.is_empty()) {
            v["html"] = json!(core::mime::markup_to_html(&c.body));
        }
        if !c.from_account.is_empty() {
            v["account"] = json!(c.from_account);
        }
        if let Some(uid) = c.draft_uid {
            v["draft_uid"] = json!(uid);
        }
        if let Some((mailbox, uid, _)) = &c.reply_to {
            v["reply_to_uid"] = json!(uid);
            v["reply_mailbox"] = json!(mailbox);
            let account = if c.from_account.is_empty() { self.session.view().account.clone() } else { c.from_account.clone() };
            if let Some(m) = self.session.state(&account).and_then(|st| st.message(mailbox, *uid)) {
                v["in_reply_to"] = json!(m.envelope.message_id);
                let mut refs = m.envelope.references.clone();
                refs.push(m.envelope.message_id.clone());
                v["references"] = json!(refs);
            }
        }
        if !c.attachments.is_empty() {
            v["attachments"] = json!(
                c.attachments
                    .iter()
                    .map(|a| json!({"name": a.name, "mime": a.mime, "data": core::mime::base64_encode(&a.data), "cid": a.cid}))
                    .collect::<Vec<_>>()
            );
        }
        v
    }

    /// Sends the compose window's message.
    pub fn send_compose(&mut self) {
        let Some(c) = self.compose.clone() else { return };
        if c.to.trim().is_empty() && c.cc.trim().is_empty() && c.bcc.trim().is_empty() {
            self.status = self.tr("Add at least one recipient").to_string();
            return;
        }
        // A forward goes through mail.forward so the original's attachments travel along: the
        // engine rebuilds the forwarded block, so only the person's note is passed.
        let ok = if let Some((mailbox, uid)) = c.forward_of.clone() {
            let header = self.tr("---------- Forwarded message ----------");
            let note = c.body.split(header).next().unwrap_or_default().trim().to_string();
            let mut prm = json!({"uid": uid, "mailbox": mailbox, "to": format!("{}, {}", c.to, c.cc), "body": note});
            if !c.from_account.is_empty() {
                prm["account"] = json!(c.from_account);
            }
            self.run("mail.forward", prm).is_some()
        } else {
            let params = self.compose_params(&c);
            self.run("mail.send", params).is_some()
        };
        if ok {
            self.compose = None;
            self.status = self.tr("Sending…").to_string();
        }
    }

    pub fn save_draft(&mut self) {
        let Some(c) = self.compose.clone() else { return };
        let params = self.compose_params(&c);
        if self.run("draft.save", params).is_some() {
            self.status = self.tr("Saving draft…").to_string();
        }
    }

    pub fn discard_compose(&mut self) {
        if let Some(c) = self.compose.take()
            && let Some(uid) = c.draft_uid
        {
            self.run("draft.discard", json!({"uid": uid}));
        }
    }

    /// Opens a draft from a Drafts folder in the compose window.
    pub fn edit_draft(&mut self, account: &str, mailbox: &str, uid: u32) {
        match self.session.state(account).and_then(|st| st.message(mailbox, uid)) {
            Some(m) => {
                self.compose = Some(Compose {
                    to: core::message::join_addresses(&m.envelope.to),
                    cc: core::message::join_addresses(&m.envelope.cc),
                    show_cc: !m.envelope.cc.is_empty(),
                    subject: m.envelope.subject.clone(),
                    body: m.body_text(),
                    draft_uid: Some(uid),
                    from_account: account.to_string(),
                    ..Compose::default()
                });
            }
            None => {
                self.run("mail.open", json!({"uid": uid, "mailbox": mailbox, "account": account, "mark_read": false}));
                self.expanded = vec![uid];
            }
        }
    }

    pub fn window_title(&self) -> String {
        let account = self.session.view().account.clone();
        let (unread, name) = if self.unified() {
            (self.total_unread(), self.tr("All accounts").to_string())
        } else {
            (
                self.session.current().and_then(|s| s.by_role(Role::Inbox)).map_or(0, |m| m.unread),
                self.session.account(&account).map(|a| a.email.clone()).unwrap_or_default(),
            )
        };
        match (unread, name.is_empty()) {
            (0, true) => "MailCraft".to_string(),
            (0, false) => format!("{name} – MailCraft"),
            (n, _) => format!("({n}) {name} – MailCraft"),
        }
    }

    /// Keyboard shortcuts (outside text fields).
    fn keyboard(&mut self, ui: &egui::Ui) {
        use egui::{Key, Modifiers};
        if ui.ctx().memory(|m| m.focused().is_some()) {
            // Cmd+Enter sends from the compose body; Escape leaves fields.
            if self.compose.is_some() && ui.ctx().input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::Enter)) {
                self.send_compose();
            }
            return;
        }
        let pressed = |m: Modifiers, k: Key| ui.ctx().input_mut(|i| i.consume_key(m, k));
        if pressed(Modifiers::COMMAND, Key::R) {
            self.sync(true);
        } else if pressed(Modifiers::NONE, Key::C) && self.screen == Screen::Mail {
            self.compose_new();
        } else if pressed(Modifiers::NONE, Key::Slash) {
            self.search_focus = true;
        } else if pressed(Modifiers::NONE, Key::Escape) {
            if self.compose.is_some() {
                if let Some(c) = &mut self.compose {
                    c.minimized = !c.minimized;
                }
            } else if self.screen == Screen::Settings && !self.session.config().accounts.is_empty() {
                self.screen = Screen::Mail;
            } else {
                self.close_reader();
            }
        } else if pressed(Modifiers::NONE, Key::E) {
            self.act("mail.archive", json!({}));
        } else if pressed(Modifiers::SHIFT, Key::Num3) || pressed(Modifiers::NONE, Key::Delete) {
            self.act("mail.trash", json!({}));
        } else if pressed(Modifiers::NONE, Key::S) {
            let starred = self.open_envelope().is_some_and(|e| !e.flags.flagged);
            self.act("mail.star", json!({"starred": starred}));
        } else if pressed(Modifiers::SHIFT, Key::U) || pressed(Modifiers::NONE, Key::U) {
            self.act("mail.markRead", json!({"read": false}));
            self.close_reader();
        } else if pressed(Modifiers::NONE, Key::R) {
            self.compose_reply(false);
        } else if pressed(Modifiers::NONE, Key::A) {
            self.compose_reply(true);
        } else if pressed(Modifiers::NONE, Key::F) {
            self.compose_forward();
        } else if pressed(Modifiers::NONE, Key::J) || pressed(Modifiers::NONE, Key::ArrowDown) {
            self.step(1);
        } else if pressed(Modifiers::NONE, Key::K) || pressed(Modifiers::NONE, Key::ArrowUp) {
            self.step(-1);
        } else if pressed(Modifiers::NONE, Key::Enter) {
            if let Some(uid) = self.session.view().selection.first().copied() {
                self.open(uid);
            }
        } else if pressed(Modifiers::COMMAND, Key::A) {
            self.run("selection.all", json!({}));
        } else if pressed(Modifiers::COMMAND, Key::Comma) {
            self.screen = Screen::Settings;
        }
    }

    fn open_envelope(&self) -> Option<core::message::Envelope> {
        let (mailbox, uid) = self.session.view().open.clone()?;
        self.open_state()?.envelope(&mailbox, uid).cloned()
    }

    /// Moves the selection (and the open message) up or down the list.
    fn step(&mut self, delta: i32) {
        let keys = list::visible_keys(self);
        if keys.is_empty() {
            return;
        }
        let v = self.session.view();
        let current = v
            .open
            .as_ref()
            .map(|(m, u)| (self.open_account(), m.clone(), *u))
            .or_else(|| v.selection.first().map(|u| (v.selection_account.clone(), v.selection_mailbox.clone(), *u)));
        let unified = v.unified();
        let position = |k: &(String, String, u32)| keys.iter().position(|x| x.2 == k.2 && (!unified || (x.0 == k.0 && x.1 == k.1)));
        let i = current.as_ref().and_then(position).map_or(0, |i| (i as i32 + delta).clamp(0, keys.len() as i32 - 1) as usize);
        if let Some((account, mailbox, uid)) = keys.get(i).cloned() {
            self.run("selection.set", json!({"uids": [uid], "account": account, "mailbox": mailbox}));
            if self.session.view().open.is_some() {
                self.open_at(&account, &mailbox, uid);
            }
        }
    }

    /// Draws the app.
    pub fn ui(&mut self, ui: &mut egui::Ui) {
        let tokens = theme::Tokens::for_dark_mode(self.dark);
        if self.applied_dark != Some(self.dark) {
            theme::apply(ui.ctx(), &tokens);
            self.applied_dark = Some(self.dark);
        }
        self.pump();
        self.sync(false);
        self.assist_tick();
        if self.session.jobs_pending() > 0 {
            ui.ctx().request_repaint_after(Duration::from_millis(100));
        }
        self.keyboard(ui);
        chrome::top_bar(self, ui, &tokens);
        chrome::status_bar(self, ui, &tokens);
        match self.screen {
            Screen::Settings => settings::show(self, ui, &tokens),
            Screen::Mail => {
                nav::show(self, ui, &tokens);
                let reading_pane = self.session.config().preferences.reading_pane;
                let open = self.session.view().open.is_some();
                if reading_pane && open {
                    egui::Panel::right("reader")
                        .default_size(560.0)
                        .min_size(360.0)
                        .resizable(true)
                        .frame(egui::Frame::NONE.fill(tokens.card))
                        .show(ui, |ui| reader::show(self, ui, &tokens));
                }
                egui::CentralPanel::default().frame(egui::Frame::NONE.fill(tokens.panel)).show(ui, |ui| {
                    if !reading_pane && self.reading_full && open {
                        reader::show(self, ui, &tokens);
                    } else {
                        list::show(self, ui, &tokens);
                    }
                });
            }
        }
        compose::show(self, ui, &tokens);
        chrome::toast(self, ui, &tokens);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(app: &mut MailApp, ctx: &egui::Context, events: Vec<egui::Event>) {
        let input = egui::RawInput {
            screen_rect: Some(egui::Rect::from_min_size(egui::Pos2::ZERO, egui::vec2(1400.0, 900.0))),
            events,
            ..Default::default()
        };
        let mut out = ctx.run_ui(input, |ui| app.ui(ui));
        out.textures_delta.clear();
    }

    fn key(key: egui::Key, modifiers: egui::Modifiers) -> egui::Event {
        egui::Event::Key { key, physical_key: None, pressed: true, repeat: false, modifiers }
    }

    fn settle(app: &mut MailApp, ctx: &egui::Context) {
        for _ in 0..30 {
            frame(app, ctx, vec![]);
            if app.session.jobs_pending() == 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn demo_renders_reads_replies_and_settles() {
        let ctx = egui::Context::default();
        let mut app = MailApp::demo();
        app.language = Language::It;
        settle(&mut app, &ctx);
        assert_eq!(app.screen, Screen::Mail);
        let first = list::visible_uids(&app)[0];
        app.open(first);
        settle(&mut app, &ctx);
        assert!(app.session.current().unwrap().message("INBOX", first).is_some());
        assert!(app.window_title().contains("riccardo@example.it"));
        frame(&mut app, &ctx, vec![key(egui::Key::R, egui::Modifiers::NONE)]);
        assert!(app.compose.as_ref().is_some_and(|c| c.subject.starts_with("Re: ") && c.body.contains("ha scritto:")));
        app.send_compose();
        settle(&mut app, &ctx);
        assert!(app.compose.is_none());
        assert!(app.status.contains("Sincronizz") || app.toast.is_some() || app.status.contains("Invio"), "{}", app.status);
        frame(&mut app, &ctx, vec![key(egui::Key::E, egui::Modifiers::NONE)]);
        settle(&mut app, &ctx);
        assert!(app.session.current().unwrap().envelope("INBOX", first).is_none(), "archived");
        // Open, then the X (Escape) closes the reader.
        let second = list::visible_uids(&app)[0];
        app.open(second);
        settle(&mut app, &ctx);
        assert!(app.session.view().open.is_some());
        frame(&mut app, &ctx, vec![key(egui::Key::Escape, egui::Modifiers::NONE)]);
        assert!(app.session.view().open.is_none(), "the reader closed");
        // j/k move the selection, c opens compose, Escape minimises it, settings render.
        frame(&mut app, &ctx, vec![key(egui::Key::J, egui::Modifiers::NONE)]);
        frame(&mut app, &ctx, vec![key(egui::Key::J, egui::Modifiers::NONE)]);
        frame(&mut app, &ctx, vec![key(egui::Key::K, egui::Modifiers::NONE)]);
        assert_eq!(app.session.view().selection.len(), 1);
        frame(&mut app, &ctx, vec![key(egui::Key::C, egui::Modifiers::NONE)]);
        assert!(app.compose.is_some());
        // Another `c` (typed with nothing focused) keeps the draft being written.
        if let Some(c) = &mut app.compose {
            c.body = "testo".into();
        }
        frame(&mut app, &ctx, vec![key(egui::Key::C, egui::Modifiers::NONE)]);
        assert_eq!(app.compose.as_ref().map(|c| c.body.as_str()), Some("testo"));
        // Recipient completion from the people seen in mail.
        app.contact_query = "luc".into();
        app.run("contacts.search", json!({"query": "luc"}));
        assert_eq!(app.contact_hits.first().map(|h| h.1.as_str()), Some("luca@example.com"));
        let mut to = "anna@example.it, luc".to_string();
        MailApp::complete_recipient(&mut to, &app.contact_hits[0].0);
        assert_eq!(to, "anna@example.it, Luca Bianchi <luca@example.com>, ");
        let mut to = "luc".to_string();
        MailApp::complete_recipient(&mut to, "Luca <luca@example.com>");
        assert_eq!(to, "Luca <luca@example.com>, ");
        frame(&mut app, &ctx, vec![key(egui::Key::Escape, egui::Modifiers::NONE)]);
        assert!(app.compose.as_ref().is_some_and(|c| c.minimized));
        app.compose = None;
        // The assistant: suggestions under the open message become a reply; a draft fills the body.
        assert!(!app.assist_available());
        app.open(list::visible_uids(&app)[0]);
        settle(&mut app, &ctx);
        let (mailbox, uid) = app.session.view().open.clone().unwrap();
        let hit = (app.open_account(), mailbox.clone(), uid);
        app.assist.suggestions.insert(hit.clone(), vec!["Confermo, a giovedì".into()]);
        app.reply_with("Confermo, a giovedì");
        assert!(app.compose.as_ref().is_some_and(|c| c.body.starts_with("Confermo, a giovedì\n") && c.body.contains("ha scritto:")));
        app.assist.instruction = "conferma e chiedi le planimetrie".into();
        app.assist.pending.insert(7, AssistJob::Draft);
        app.assist_answer(7, Ok(json!({"draft": "Ciao Luca,\nconfermo giovedì. Mi mandi le planimetrie?"})));
        assert!(app.compose.as_ref().is_some_and(|c| c.body.starts_with("Ciao Luca,\nconfermo giovedì.") && c.body.contains("ha scritto:")));
        app.assist.pending.insert(8, AssistJob::Suggest(hit.0.clone(), hit.1.clone(), hit.2));
        app.assist_answer(8, Err("503: no local model".into()));
        assert!(!app.assist.suggestions.contains_key(&hit) && app.status.contains("503"));
        app.compose = None;
        app.close_reader();
        app.search = "fattura".into();
        app.run("mail.search", json!({"query": "fattura"}));
        settle(&mut app, &ctx);
        assert_eq!(list::visible_uids(&app).len(), 1);
        app.select_role(Role::Sent);
        settle(&mut app, &ctx);
        assert!(!list::visible_uids(&app).is_empty());
        // The unified inbox: both accounts in one list, actions go to the right account.
        app.select_account("*");
        settle(&mut app, &ctx);
        assert!(app.unified());
        assert!(app.window_title().starts_with('(') && app.window_title().contains("Tutti gli account"), "{}", app.window_title());
        let rows = list::rows(&app);
        assert!(rows.len() > 12, "{} rows", rows.len());
        assert!(rows.iter().any(|r| r.account == "demo") && rows.iter().any(|r| r.account == "lavoro"));
        let other = rows.iter().find(|r| r.account == "lavoro").cloned().unwrap();
        app.run("selection.set", json!({"uids": [other.uid], "account": other.account, "mailbox": other.mailbox}));
        app.open_at(&other.account, &other.mailbox, other.uid);
        settle(&mut app, &ctx);
        assert_eq!(app.open_account(), "lavoro");
        assert!(app.session.state("lavoro").unwrap().message("INBOX", other.uid).is_some());
        frame(&mut app, &ctx, vec![key(egui::Key::R, egui::Modifiers::NONE)]);
        assert_eq!(app.compose.as_ref().map(|c| c.from_account.as_str()), Some("lavoro"));
        app.compose = None;
        frame(&mut app, &ctx, vec![]); // the body's focus goes with the window
        let before = app.session.state("lavoro").unwrap().list("INBOX").len();
        frame(&mut app, &ctx, vec![key(egui::Key::E, egui::Modifiers::NONE)]);
        settle(&mut app, &ctx);
        assert_eq!(app.session.state("lavoro").unwrap().list("INBOX").len(), before - 1, "archived on lavoro");
        assert!(app.unified(), "still unified after acting");
        frame(&mut app, &ctx, vec![key(egui::Key::J, egui::Modifiers::NONE)]);
        assert_eq!(app.session.view().selection.len(), 1);
        app.select_role(Role::Starred);
        settle(&mut app, &ctx);
        assert_eq!(list::rows(&app).len(), 2, "starred mail of both accounts");
        app.select_account("demo");
        assert!(!app.unified());
        app.screen = Screen::Settings;
        app.dark = true;
        frame(&mut app, &ctx, vec![]);
        app.settings.editing = Some(settings::AccountForm::from_email("anna@gmail.com"));
        frame(&mut app, &ctx, vec![]);
        frame(&mut app, &ctx, vec![]);
        // A Gmail address gets its servers and "sign in with Google" without typing anything.
        let f = app.settings.editing.clone().unwrap();
        assert_eq!((f.imap_host.as_str(), f.auth.as_str()), ("imap.gmail.com", "google"));
        frame(&mut app, &ctx, vec![]);
        frame(&mut app, &ctx, vec![key(egui::Key::Escape, egui::Modifiers::NONE)]);
        assert_eq!(app.screen, Screen::Mail);
    }

    #[test]
    fn empty_configuration_opens_settings() {
        let ctx = egui::Context::default();
        let mut app = MailApp::new(Box::new(Session::new(Box::new(|_| Box::new(core::mock::MockBackend::new())), None)));
        assert_eq!(app.screen, Screen::Settings);
        frame(&mut app, &ctx, vec![]);
        frame(&mut app, &ctx, vec![key(egui::Key::C, egui::Modifiers::NONE), key(egui::Key::E, egui::Modifiers::NONE)]);
        assert!(app.compose.is_none());
    }
}
