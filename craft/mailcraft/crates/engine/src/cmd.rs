//! The command table: every user-visible action, with its id, label, menu path, shortcut and
//! parameters, and the dispatcher that runs it.

use mailcraft_core::account::{Account, Security, slug};
use mailcraft_core::autoconfig;
use mailcraft_core::backend::{FlagChange, Page, SearchQuery};
use mailcraft_core::mailbox::Role;
use mailcraft_core::message::{Address, Draft, DraftAttachment, Flags, time};
use mailcraft_core::{limits, mime, oauth};
use serde_json::{Value, json};

use crate::worker::JobKind;
use crate::{ALL, Error, Result, Session, account_json, envelope_json, mailbox_json, message_json};

/// A command's description (the catalogue the CLI, the MCP server and the interface show).
#[derive(Clone, Copy, Debug)]
pub struct Command {
    pub id: &'static str,
    pub label: &'static str,
    pub menu: &'static str,
    pub shortcut: &'static str,
    pub params: &'static str,
    /// Talks to the server: returns `{"job": id}` or `{"jobs": [...]}`.
    pub network: bool,
}

const fn c(id: &'static str, label: &'static str, menu: &'static str, shortcut: &'static str, params: &'static str, network: bool) -> Command {
    Command { id, label, menu, shortcut, params, network }
}

pub const COMMANDS: &[Command] = &[
    c(
        "account.suggest",
        "Suggest Settings",
        "Settings › Accounts",
        "",
        "{email, language?: en|it} → provider, imap, smtp, alternatives, notes",
        false,
    ),
    c(
        "account.add",
        "Add Account",
        "Settings › Accounts › Add",
        "",
        "{email, password?, name?, display_name?, username?, imap_host?, imap_port?, imap_security?: tls|starttls|none, smtp_host?, smtp_port?, smtp_security?, signature?, signature_html?, color?, trust_invalid_certs?, save_sent?, auth?} (missing servers come from the provider suggestion)",
        false,
    ),
    c(
        "account.update",
        "Edit Account",
        "Settings › Accounts › Edit",
        "",
        "{id?, ...same fields as account.add, folders?: {sent, drafts, trash, spam, archive}}",
        false,
    ),
    c("account.remove", "Remove Account", "Settings › Accounts › Remove", "", "{id}", false),
    c("account.list", "Accounts", "Settings › Accounts", "", "{} → accounts (without passwords) and their status", false),
    c(
        "account.setPassword",
        "Set Password",
        "Settings › Accounts › Password",
        "",
        "{id?, password} (an OAuth account switches back to a password)",
        false,
    ),
    c(
        "account.oauthStart",
        "Sign In",
        "Settings › Accounts › Sign in",
        "",
        "{id? | email?, provider?: google|microsoft (default: the account's or the address's), redirect?: URL (default: a listener on this machine)} → {url to open in the browser, state}; the browser's return finishes the sign-in (account.oauthFinish)",
        false,
    ),
    c(
        "account.oauthFinish",
        "Finish Sign In",
        "Settings › Accounts › Sign in",
        "",
        "{state, code | error} → job: exchanges the code for tokens, then loads the folders",
        true,
    ),
    c(
        "oauth.providers",
        "OAuth Providers",
        "Settings › Sign-in",
        "",
        "{} → google, microsoft: configured?, where to create the client, notes",
        false,
    ),
    c(
        "oauth.configure",
        "Configure OAuth Client",
        "Settings › Sign-in",
        "",
        "{provider: google|microsoft, client_id (empty = remove), client_secret?}",
        false,
    ),
    c("account.test", "Test Account", "Settings › Accounts › Test", "", "{id?} → job: IMAP login, mailbox count, SMTP login", true),
    c(
        "account.probe",
        "Find Servers",
        "Settings › Accounts › Find Servers",
        "",
        "{id?} → job: tries the suggested IMAP/SMTP servers until one accepts the login, and saves it",
        true,
    ),
    c("account.select", "Switch Account", "View › Account", "", "{id}", false),
    c("prefs.get", "Preferences", "Settings › Preferences", "", "{} → every preference", false),
    c(
        "prefs.set",
        "Set Preference",
        "Settings › Preferences",
        "",
        "{key, value} or {language?, theme?, sync_minutes?, page_size?, reading_pane?, conversations?, mark_read_on_open?, undo_send_seconds?, default_account?}",
        false,
    ),
    c("mail.sync", "Sync", "Mail › Sync", "Cmd+R", "{account?, mailbox?, all?: bool} → jobs: mailboxes and the first page", true),
    c("mail.mailboxes", "Mailboxes", "View › Mailboxes", "", "{account?} → cached folders with roles and counts", false),
    c(
        "mail.select",
        "Open Mailbox",
        "View › Mailbox",
        "",
        "{account? (\"*\" = every account, unified), mailbox? | role?: inbox|sent|drafts|trash|spam|archive|starred} → the view; loads the mailbox when not cached",
        true,
    ),
    c(
        "mail.list",
        "Messages",
        "View › Messages",
        "",
        "{account? (\"*\" merges every account by role), mailbox? | role?, page?, refresh?: bool} → cached envelopes (and a job when refreshing or not cached)",
        true,
    ),
    c(
        "mail.search",
        "Search",
        "Mail › Search",
        "/",
        "{query (from: to: subject: is:unread is:starred has:attachment newer_than:7d + words), account? (\"*\" = every account), mailbox? | role?, page?} → job(s)",
        true,
    ),
    c("mail.open", "Open Message", "Mail › Open", "Enter", "{uid, mailbox?, account?} → the message (text, html, attachments); marks it read", true),
    c(
        "mail.attachment",
        "Attachment",
        "Mail › Attachment",
        "",
        "{uid, index, mailbox?, path?} → the bytes (base64) or saves to path; the message must be open",
        false,
    ),
    c("mail.markRead", "Mark Read/Unread", "Mail › Mark", "Shift+U", "{uids?, read: bool, mailbox?}", true),
    c("mail.star", "Star", "Mail › Star", "S", "{uids?, starred: bool, mailbox?}", true),
    c("mail.archive", "Archive", "Mail › Archive", "E", "{uids?, mailbox?}", true),
    c("mail.trash", "Delete", "Mail › Delete", "#", "{uids?, mailbox?} (to the trash; from the trash: for good)", true),
    c("mail.spam", "Report Spam", "Mail › Spam", "!", "{uids?, mailbox?}", true),
    c("mail.move", "Move To", "Mail › Move", "V", "{uids?, to, mailbox?}", true),
    c("mail.delete", "Delete Forever", "Mail › Delete Forever", "", "{uids?, mailbox?}", true),
    c(
        "mail.send",
        "Send",
        "Compose › Send",
        "Cmd+Enter",
        "{account?, to, cc?, bcc?, subject, body, html?, in_reply_to?, references?, attachments?: [{name, mime, data (base64)} | {path}], reply_to_uid?, reply_mailbox?, draft_uid?, signature?: bool}",
        true,
    ),
    c("mail.reply", "Reply", "Mail › Reply", "R", "{uid, body, all?: bool, mailbox?, quote?: bool, account?}", true),
    c("mail.forward", "Forward", "Mail › Forward", "F", "{uid, to, body?, mailbox?, account?}", true),
    c(
        "draft.save",
        "Save Draft",
        "Compose › Save Draft",
        "Cmd+S",
        "{account?, to?, cc?, bcc?, subject?, body?, in_reply_to?, references?, draft_uid?} → job: the draft's uid",
        true,
    ),
    c("draft.discard", "Discard Draft", "Compose › Discard", "", "{uid, mailbox?}", true),
    c("mailbox.create", "New Folder", "Mail › New Folder", "", "{name, account?}", true),
    c(
        "mailbox.subscribe",
        "Show Folder",
        "Mail › Show Folder",
        "",
        "{path, subscribed?: bool (default true), account?} → IMAP SUBSCRIBE/UNSUBSCRIBE; unsubscribed folders leave the navigation",
        true,
    ),
    c("mail.close", "Close Message", "Mail › Close", "Escape", "{} → closes the open message", false),
    c(
        "contacts.search",
        "Contacts",
        "Mail › Contacts",
        "",
        "{query?, limit? (8), account?} → people seen in mail or written to, best match first",
        false,
    ),
    c("selection.set", "Select", "Edit › Select", "", "{uids: [...]}", false),
    c("selection.all", "Select All", "Edit › Select All", "Cmd+A", "{}", false),
    c("selection.clear", "Clear Selection", "Edit › Clear Selection", "Escape", "{}", false),
    c("view.get", "View", "View", "", "{} → account, mailbox, query, selection, open message, jobs pending", false),
    c("app.status", "Status", "Help › Status", "", "{} → accounts, errors, jobs pending", false),
];

pub fn command(id: &str) -> Option<&'static Command> {
    COMMANDS.iter().find(|c| c.id == id)
}

mod p {
    use super::*;

    pub fn str<'a>(prm: &'a Value, key: &str) -> Result<&'a str> {
        prm.get(key).and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty()).ok_or_else(|| Error::Param(format!("`{key}` is required")))
    }

    pub fn opt_str<'a>(prm: &'a Value, key: &str) -> Option<&'a str> {
        prm.get(key).and_then(Value::as_str).map(str::trim)
    }

    pub fn opt_bool(prm: &Value, key: &str) -> Option<bool> {
        match prm.get(key) {
            Some(Value::Bool(b)) => Some(*b),
            Some(Value::String(s)) => match s.to_ascii_lowercase().as_str() {
                "true" | "yes" | "on" | "1" => Some(true),
                "false" | "no" | "off" | "0" => Some(false),
                _ => None,
            },
            _ => None,
        }
    }

    pub fn opt_u32(prm: &Value, key: &str) -> Option<u32> {
        match prm.get(key) {
            Some(Value::Number(n)) => n.as_u64().and_then(|v| u32::try_from(v).ok()),
            Some(Value::String(s)) => s.trim().parse().ok(),
            _ => None,
        }
    }

    pub fn u32(prm: &Value, key: &str) -> Result<u32> {
        opt_u32(prm, key).ok_or_else(|| Error::Param(format!("`{key}` must be a positive number")))
    }

    /// `uids` (a list or one number), `uid`, or the selection.
    pub fn uids(prm: &Value, selection: &[u32]) -> Result<Vec<u32>> {
        let mut out: Vec<u32> = match prm.get("uids") {
            Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_u64().and_then(|n| u32::try_from(n).ok())).collect(),
            Some(Value::Number(n)) => n.as_u64().and_then(|n| u32::try_from(n).ok()).into_iter().collect(),
            Some(Value::String(s)) => s.split([',', ' ']).filter_map(|t| t.trim().parse().ok()).collect(),
            _ => Vec::new(),
        };
        if let Some(u) = opt_u32(prm, "uid") {
            out.push(u);
        }
        if out.is_empty() {
            out = selection.to_vec();
        }
        out.sort_unstable();
        out.dedup();
        out.truncate(10_000);
        if out.is_empty() {
            return Err(Error::Param("no message selected: pass `uids`".into()));
        }
        Ok(out)
    }

    pub fn addresses(prm: &Value, key: &str) -> Vec<Address> {
        match prm.get(key) {
            Some(Value::String(s)) => Address::parse_list(s),
            Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str()).flat_map(Address::parse_list).take(limits::MAX_RECIPIENTS).collect(),
            _ => Vec::new(),
        }
    }

    pub fn security(prm: &Value, key: &str) -> Result<Option<Security>> {
        match opt_str(prm, key).filter(|s| !s.is_empty()) {
            None => Ok(None),
            Some(s) => Security::parse(s).map(Some).ok_or_else(|| Error::Param(format!("`{key}` must be tls, starttls or none"))),
        }
    }
}

impl Session {
    fn account_id(&self, prm: &Value) -> Result<String> {
        let key = p::opt_str(prm, "account").or_else(|| p::opt_str(prm, "id")).filter(|s| !s.is_empty());
        match key {
            Some(ALL) => Ok(ALL.to_string()),
            Some(k) => self.config.find(k).map(|a| a.id.clone()).ok_or_else(|| Error::Core(mailcraft_core::Error::NoAccount(k.to_string()))),
            None if self.view.unified() => {
                // The unified view mixes accounts: act on the selection's or the open message's.
                if !self.view.selection.is_empty() && !self.view.selection_account.is_empty() {
                    Ok(self.view.selection_account.clone())
                } else if !self.view.open_account.is_empty() {
                    Ok(self.view.open_account.clone())
                } else {
                    self.config
                        .accounts
                        .first()
                        .map(|a| a.id.clone())
                        .ok_or_else(|| Error::Param("no account configured: add one with account.add".into()))
                }
            }
            None if !self.view.account.is_empty() => Ok(self.view.account.clone()),
            None => Err(Error::Param("no account configured: add one with account.add".into())),
        }
    }

    /// The mailbox named by `mailbox`, `role`, or the view's.
    fn mailbox_of(&self, prm: &Value, account: &str) -> Result<String> {
        if let Some(m) = p::opt_str(prm, "mailbox").filter(|m| !m.is_empty()) {
            return Ok(m.to_string());
        }
        if let Some(r) = p::opt_str(prm, "role").filter(|r| !r.is_empty()) {
            let role = Role::parse(r).ok_or_else(|| Error::Param(format!("unknown role `{r}`")))?;
            return self.folder_for(account, role).ok_or_else(|| Error::Core(mailcraft_core::Error::NoMailbox(r.to_string())));
        }
        if self.view.account == account && !self.view.mailbox.is_empty() {
            return Ok(self.view.mailbox.clone());
        }
        if self.view.unified() {
            if !self.view.selection.is_empty() && self.view.selection_account == account && !self.view.selection_mailbox.is_empty() {
                return Ok(self.view.selection_mailbox.clone());
            }
            if self.view.open_account == account
                && let Some((m, _)) = &self.view.open
            {
                return Ok(m.clone());
            }
            if let Some(m) = self.folder_for(account, if self.view.unified_role() == Role::Starred { Role::Inbox } else { self.view.unified_role() })
            {
                return Ok(m);
            }
        }
        Ok("INBOX".into())
    }

    /// Where a copy of sent mail goes: the configured or detected Sent folder, else a new
    /// `Sent` (under `INBOX` when the server keeps every folder there); `None` when the account
    /// does not keep copies.
    fn sent_folder(&self, acc: &str, account: &Account) -> Option<String> {
        if !account.save_sent {
            return None;
        }
        if let Some(f) = self.folder_for(acc, Role::Sent) {
            return Some(f);
        }
        let st = self.store.get(acc)?;
        let delimiter = if st.delimiter.is_empty() { "/".to_string() } else { st.delimiter.clone() };
        let prefix = format!("INBOX{delimiter}");
        let under_inbox = st.mailboxes.iter().any(|m| m.path.starts_with(&prefix))
            && st.mailboxes.iter().all(|m| m.path.eq_ignore_ascii_case("INBOX") || m.path.starts_with(&prefix));
        Some(if under_inbox { format!("{prefix}Sent") } else { "Sent".to_string() })
    }

    /// The role asked for (`role` param) or the unified view's.
    fn unified_role_of(&self, prm: &Value) -> Result<Role> {
        match p::opt_str(prm, "role").filter(|r| !r.is_empty()) {
            Some(r) => Role::parse(r).ok_or_else(|| Error::Param(format!("unknown role `{r}`"))),
            None => Ok(if self.view.unified() { self.view.unified_role() } else { Role::Inbox }),
        }
    }

    fn special(&self, account: &str, role: Role) -> Result<String> {
        self.folder_for(account, role)
            .ok_or_else(|| Error::Core(mailcraft_core::Error::NoMailbox(format!("no {} folder: set it in the account's folders", role.name()))))
    }

    fn account_from_params(&self, prm: &Value, base: Account) -> Result<Account> {
        let mut a = base;
        if let Some(v) = p::opt_str(prm, "email").filter(|s| !s.is_empty()) {
            a.email = v.to_string();
        }
        if let Some(v) = p::opt_str(prm, "name").filter(|s| !s.is_empty()) {
            a.name = v.to_string();
        }
        if let Some(v) = p::opt_str(prm, "display_name") {
            a.display_name = v.to_string();
        }
        if let Some(v) = p::opt_str(prm, "username") {
            a.username = v.to_string();
        }
        if let Some(v) = p::opt_str(prm, "signature") {
            a.signature = v.to_string();
        }
        if let Some(v) = p::opt_str(prm, "signature_html") {
            a.signature_html = mailcraft_core::clip(v, 200_000).to_string();
        }
        if let Some(v) = p::opt_str(prm, "color") {
            a.color = v.to_string();
        }
        if let Some(v) = p::opt_bool(prm, "trust_invalid_certs") {
            a.trust_invalid_certs = v;
        }
        if let Some(v) = p::opt_bool(prm, "save_sent") {
            a.save_sent = v;
        }
        if let Some(v) = p::opt_str(prm, "auth") {
            let v = v.trim().to_ascii_lowercase();
            a.auth = match v.as_str() {
                "" | "password" => String::new(),
                other => oauth::provider(other)
                    .map(|p| p.id.to_string())
                    .ok_or_else(|| Error::Param(format!("unknown auth `{other}`: password, google or microsoft")))?,
            };
        }
        for (prefix, server) in [("imap", &mut a.imap), ("smtp", &mut a.smtp)] {
            if let Some(h) = p::opt_str(prm, &format!("{prefix}_host")).filter(|s| !s.is_empty()) {
                server.host = h.to_string();
            }
            if let Some(port) = p::opt_u32(prm, &format!("{prefix}_port")) {
                server.port = u16::try_from(port).map_err(|_| Error::Param(format!("`{prefix}_port` must be below 65536")))?;
                // A port without a security hint implies the conventional one.
                server.security = match server.port {
                    993 | 465 => Security::Tls,
                    143 | 587 | 25 => Security::StartTls,
                    _ => server.security,
                };
            }
            if let Some(sec) = p::security(prm, &format!("{prefix}_security"))? {
                server.security = sec;
            }
        }
        if let Some(f) = prm.get("folders").and_then(Value::as_object) {
            let get = |k: &str| f.get(k).and_then(Value::as_str).map(str::to_string);
            if let Some(v) = get("sent") {
                a.folders.sent = v;
            }
            if let Some(v) = get("drafts") {
                a.folders.drafts = v;
            }
            if let Some(v) = get("trash") {
                a.folders.trash = v;
            }
            if let Some(v) = get("spam") {
                a.folders.spam = v;
            }
            if let Some(v) = get("archive") {
                a.folders.archive = v;
            }
        }
        Ok(a)
    }

    /// Builds the raw message of a draft (signature added) and the SMTP envelope.
    fn build_outgoing(&self, account: &Account, draft: &Draft, with_signature: bool) -> Result<(Vec<u8>, Vec<String>)> {
        let mut d = draft.clone();
        let note = draft.body.clone();
        let text_sig = account.signature_text();
        if with_signature && !text_sig.is_empty() && !d.body.contains(&text_sig) {
            d.body = format!("{}\n\n-- \n{text_sig}", d.body.trim_end());
        }
        // An HTML signature makes the message HTML (with a text alternative): logo, bold, links.
        // A body written with the compose markup is HTML already and gets the signature too.
        let html_sig = if !account.signature_html.trim().is_empty() {
            account.signature_html.trim().to_string()
        } else if !text_sig.is_empty() {
            mime::text_to_html(&text_sig)
        } else {
            String::new()
        };
        if with_signature && !html_sig.is_empty() {
            if !d.html.trim().is_empty() {
                if !d.html.contains(&html_sig) {
                    d.html = format!("{}<br><br>-- <br>{html_sig}", d.html.trim_end());
                }
            } else if !account.signature_html.trim().is_empty() {
                d.html = format!("{}<br><br>-- <br>{html_sig}", mime::text_to_html(note.trim_end()));
            }
        }
        mime::inline_data_images(&mut d);
        let from = Address::new(account.from_name(), &account.email);
        let domain = account.email.split('@').nth(1).unwrap_or("mailcraft");
        let id = format!("<{}.{}@{}>", time::now(), self.next_job, domain);
        let raw = mime::build(&d, &from, time::now(), &id)?;
        Ok((raw, d.recipients()))
    }

    /// Refuses a file path given to a command when this session may not touch the host's files.
    fn host_path_allowed(&self) -> Result<()> {
        if self.host_paths_allowed() {
            Ok(())
        } else {
            Err(Error::Param("host paths are not available on a shared server: send the attachment as base64".into()))
        }
    }

    fn draft_from_params(&self, prm: &Value) -> Result<Draft> {
        let mut d = Draft {
            to: p::addresses(prm, "to"),
            cc: p::addresses(prm, "cc"),
            bcc: p::addresses(prm, "bcc"),
            subject: p::opt_str(prm, "subject").unwrap_or_default().to_string(),
            body: prm.get("body").and_then(Value::as_str).unwrap_or_default().to_string(),
            html: prm.get("html").and_then(Value::as_str).unwrap_or_default().to_string(),
            in_reply_to: p::opt_str(prm, "in_reply_to").unwrap_or_default().to_string(),
            references: prm
                .get("references")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default(),
            draft_uid: p::opt_u32(prm, "draft_uid"),
            ..Draft::default()
        };
        if let Some(list) = prm.get("attachments").and_then(Value::as_array) {
            for a in list.iter().take(limits::MAX_ATTACHMENTS) {
                if let Some(path) = a.get("path").and_then(Value::as_str) {
                    self.host_path_allowed()?;
                    let data = std::fs::read(path).map_err(|e| Error::Param(format!("attachment `{path}`: {e}")))?;
                    let name =
                        std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "attachment".into());
                    d.attachments.push(DraftAttachment { name, mime: guess_mime(path).to_string(), data, cid: String::new() });
                } else {
                    let data = a
                        .get("data")
                        .and_then(Value::as_str)
                        .and_then(mime::base64_decode)
                        .ok_or_else(|| Error::Param("attachment `data` must be base64".into()))?;
                    let name = mailcraft_core::clip(a.get("name").and_then(Value::as_str).unwrap_or("attachment"), limits::MAX_FILE_NAME);
                    d.attachments.push(DraftAttachment {
                        name: name.to_string(),
                        mime: a.get("mime").and_then(Value::as_str).filter(|m| !m.trim().is_empty()).unwrap_or_else(|| guess_mime(name)).to_string(),
                        data,
                        cid: a.get("cid").and_then(Value::as_str).unwrap_or_default().replace(['<', '>', '\r', '\n', ' '], ""),
                    });
                }
            }
        }
        Ok(d)
    }
}

fn prefs_json(p: &mailcraft_core::account::Preferences) -> Value {
    Value::Object(p.entries().into_iter().map(|(k, v)| (k.to_string(), Value::String(v))).collect())
}

fn guess_mime(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or_default().to_ascii_lowercase().as_str() {
        "pdf" => "application/pdf",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "txt" | "md" => "text/plain",
        "html" | "htm" => "text/html",
        "csv" => "text/csv",
        "json" => "application/json",
        "zip" => "application/zip",
        "xlsx" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx" => "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        _ => "application/octet-stream",
    }
}

/// Runs command `id` with `prm` on the session.
pub(crate) fn run(s: &mut Session, id: &str, prm: &Value) -> Result<Value> {
    let italian = s.italian() || p::opt_str(prm, "language").is_some_and(|l| l.eq_ignore_ascii_case("it"));
    match id {
        "account.suggest" => {
            let email = p::str(prm, "email")?;
            let sg = autoconfig::suggest(email, italian)?;
            Ok(json!({
                "email": email,
                "provider": sg.provider,
                "confidence": match sg.confidence { autoconfig::Confidence::Known => "known", autoconfig::Confidence::Guess => "guess" },
                "username": sg.username,
                "imap": {"host": sg.imap.host, "port": sg.imap.port, "security": sg.imap.security.name()},
                "smtp": {"host": sg.smtp.host, "port": sg.smtp.port, "security": sg.smtp.security.name()},
                "alternatives": sg.alternatives.iter().map(|(i, m)| json!({"imap": i.describe(), "smtp": m.describe()})).collect::<Vec<_>>(),
                "notes": sg.notes,
                "oauth": sg.oauth,
            }))
        }
        "account.add" => {
            let email = p::str(prm, "email")?.to_string();
            let sg = autoconfig::suggest(&email, italian)?;
            let base = Account {
                id: p::opt_str(prm, "id").map(slug).unwrap_or_else(|| slug(&email)),
                name: sg.provider.map(str::to_string).unwrap_or_else(|| mailcraft_core::account::domain_of(&email).unwrap_or_default()),
                email: email.clone(),
                imap: sg.imap.clone(),
                smtp: sg.smtp.clone(),
                ..Account::default()
            };
            let mut account = s.account_from_params(prm, base)?;
            // A Google or Microsoft address without a password signs in with the provider.
            if account.auth.is_empty() && p::opt_str(prm, "auth").is_none() && p::opt_str(prm, "password").is_none_or(str::is_empty) {
                account.auth = sg.oauth.unwrap_or_default().to_string();
            }
            // An account that signs in with a provider uses the provider's servers (custom domains).
            if let Some(provider) = account.oauth_provider() {
                provider.apply_servers(&mut account);
            }
            let id = s.config.upsert(account)?;
            if let Some(pw) = p::opt_str(prm, "password").filter(|p| !p.is_empty()) {
                s.secrets.set_password(&id, pw);
            }
            s.store.entry(id.clone()).or_default();
            if s.view.account.is_empty() {
                s.view.account = id.clone();
                s.view.mailbox = "INBOX".into();
            }
            s.save_config()?;
            let a = s.config.account(&id).cloned().unwrap_or_default();
            Ok(json!({
                "id": id,
                "account": account_json(&a, s.has_password(&id), s.store.get(&id)),
                "suggestion": {"provider": sg.provider, "confidence": match sg.confidence { autoconfig::Confidence::Known => "known", autoconfig::Confidence::Guess => "guess" }, "notes": sg.notes},
            }))
        }
        "account.update" => {
            let id = s.account_id(prm)?;
            let base = s.config.account(&id).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(id.clone()))?;
            let updated = s.account_from_params(prm, base)?;
            let mut updated = Account { id: id.clone(), ..updated };
            if let Some(pw) = p::opt_str(prm, "password").filter(|p| !p.is_empty()) {
                s.secrets.set_password(&id, pw);
                if p::opt_str(prm, "auth").is_none() {
                    updated.auth.clear();
                }
            }
            if let Some(provider) = updated.oauth_provider() {
                provider.apply_servers(&mut updated);
            }
            s.config.upsert(updated)?;
            s.save_config()?;
            let _ = s.submit("account.update", &id, JobKind::Disconnect);
            let a = s.config.account(&id).cloned().unwrap_or_default();
            Ok(json!({"id": id, "account": account_json(&a, s.has_password(&id), s.store.get(&id))}))
        }
        "account.remove" => {
            let id = s.account_id(prm)?;
            s.config.remove(&id)?;
            s.secrets.set_password(&id, "");
            s.secrets.set_tokens(&id, None);
            s.store.remove(&id);
            if let Some(paths) = &s.paths {
                let _ = std::fs::remove_dir_all(paths.cache.join(&id));
            }
            if s.view.account == id {
                s.view = crate::View::default();
                if let Some(a) = s.config.accounts.first() {
                    s.view.account = a.id.clone();
                    s.view.mailbox = "INBOX".into();
                }
            }
            s.save_config()?;
            Ok(json!({"removed": id}))
        }
        "account.list" => {
            let list: Vec<Value> = s.config.accounts.iter().map(|a| account_json(a, s.has_password(&a.id), s.store.get(&a.id))).collect();
            Ok(json!({"accounts": list, "current": s.view.account}))
        }
        "account.setPassword" => {
            let id = s.account_id(prm)?;
            let pw = prm.get("password").and_then(Value::as_str).ok_or_else(|| Error::Param("`password` is required".into()))?;
            s.secrets.set_password(&id, pw);
            if !pw.is_empty()
                && let Some(mut a) = s.config.account(&id).cloned()
                && !a.auth.is_empty()
            {
                a.auth.clear();
                s.config.upsert(a)?;
            }
            s.save_config()?;
            let _ = s.submit("account.setPassword", &id, JobKind::Disconnect);
            Ok(json!({"id": id, "has_password": !pw.is_empty()}))
        }
        "account.test" => {
            let id = s.account_id(prm)?;
            let job = s.submit("account.test", &id, JobKind::Test)?;
            Ok(json!({"job": job}))
        }
        "account.oauthStart" => {
            let id = s.account_id(prm)?;
            let mut account = s.config.account(&id).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(id.clone()))?;
            let provider = match p::opt_str(prm, "provider").filter(|v| !v.is_empty()) {
                Some(v) => oauth::provider(v).ok_or_else(|| Error::Param(format!("unknown provider `{v}`: google or microsoft")))?,
                None => account
                    .oauth_provider()
                    .or_else(|| oauth::provider_for_email(&account.email))
                    .ok_or_else(|| Error::Param("name the provider to sign in with: google or microsoft".into()))?,
            };
            let client = s.config.oauth_client(provider.id).ok_or_else(|| {
                Error::Param(format!(
                    "no OAuth client for {}: create one at {} and save it with oauth.configure (or MAILCRAFT_{}_CLIENT_ID)",
                    provider.name,
                    provider.console,
                    provider.id.to_ascii_uppercase()
                ))
            })?;
            let changed = account.auth != provider.id;
            account.auth = provider.id.to_string();
            if provider.apply_servers(&mut account) || changed {
                s.config.upsert(account.clone())?;
                s.save_config()?;
            }
            let redirect = match p::opt_str(prm, "redirect").filter(|r| !r.is_empty()) {
                Some(r) => {
                    if let Some(why) = oauth::redirect_problem(r) {
                        return Err(Error::Param(format!("sign-in cannot return to {r}: {why}")));
                    }
                    r.to_string()
                }
                None => s.loopback_redirect()?,
            };
            let (flow, url) = oauth::start(provider, &client, &id, &account.email, &redirect);
            let now = time::now();
            s.flows.retain(|f| !f.expired(now) && f.account != id);
            let state = flow.state.clone();
            s.flows.push(flow);
            Ok(json!({"url": url, "state": state, "redirect": redirect, "provider": provider.id, "account": id}))
        }
        "account.oauthFinish" => {
            let state = p::str(prm, "state")?.to_string();
            let now = time::now();
            s.flows.retain(|f| !f.expired(now));
            let i = s.flows.iter().position(|f| f.state == state).ok_or_else(|| Error::Param("unknown or expired sign-in: start it again".into()))?;
            if let Some(e) = p::opt_str(prm, "error").filter(|e| !e.is_empty()) {
                s.flows.remove(i);
                return Err(Error::Core(mailcraft_core::Error::Auth(format!("sign-in refused: {e}"))));
            }
            let code = p::str(prm, "code")?.to_string();
            let flow = s.flows.remove(i);
            let client =
                s.config.oauth_client(flow.provider.id).ok_or_else(|| Error::Param(format!("no OAuth client for {}", flow.provider.name)))?;
            let job = s.submit(
                "account.oauthFinish",
                &flow.account,
                JobKind::OAuthExchange { client, code, verifier: flow.verifier, redirect: flow.redirect },
            )?;
            Ok(json!({"job": job, "account": flow.account}))
        }
        "oauth.providers" => {
            let list: Vec<Value> = oauth::PROVIDERS
                .iter()
                .map(|p| {
                    let source = if s.config.oauth.get(p.id).is_some_and(|c| !c.client_id.trim().is_empty()) {
                        "config"
                    } else if std::env::var(format!("MAILCRAFT_{}_CLIENT_ID", p.id.to_ascii_uppercase())).is_ok_and(|v| !v.trim().is_empty()) {
                        "env"
                    } else if oauth::ClientCredentials::builtin(p.id).is_some() {
                        "builtin"
                    } else {
                        ""
                    };
                    json!({"id": p.id, "name": p.name, "configured": !source.is_empty(), "source": source, "console": p.console, "scope": p.scope, "note": if italian { p.note_it } else { p.note }, "client_id": s.config.oauth_client(p.id).map(|c| c.client_id).unwrap_or_default()})
                })
                .collect();
            Ok(json!({"providers": list}))
        }
        "oauth.configure" => {
            let provider = p::str(prm, "provider")?;
            let provider = oauth::provider(provider).ok_or_else(|| Error::Param(format!("unknown provider `{provider}`: google or microsoft")))?;
            let client_id = p::opt_str(prm, "client_id").unwrap_or_default().trim().to_string();
            if client_id.is_empty() {
                s.config.oauth.remove(provider.id);
            } else {
                let client_secret = p::opt_str(prm, "client_secret").unwrap_or_default().trim().to_string();
                s.config.oauth.insert(
                    provider.id.to_string(),
                    oauth::ClientCredentials {
                        client_id: mailcraft_core::clip(&client_id, 500).to_string(),
                        client_secret: mailcraft_core::clip(&client_secret, 500).to_string(),
                    },
                );
            }
            s.save_config()?;
            Ok(json!({"provider": provider.id, "configured": s.config.oauth_client(provider.id).is_some()}))
        }
        "account.probe" => {
            let id = s.account_id(prm)?;
            let a = s.config.account(&id).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(id.clone()))?;
            let sg = autoconfig::suggest(&a.email, italian)?;
            let mut candidates = vec![(a.imap.clone(), a.smtp.clone()), (sg.imap, sg.smtp)];
            candidates.extend(sg.alternatives);
            candidates.dedup();
            let job = s.submit("account.probe", &id, JobKind::Probe { candidates })?;
            Ok(json!({"job": job}))
        }
        "account.select" => {
            let id = s.account_id(prm)?;
            if s.view.account != id {
                s.view = crate::View { account: id.clone(), mailbox: "INBOX".into(), ..crate::View::default() };
            }
            Ok(json!({"account": id}))
        }
        "prefs.get" => Ok(json!({"preferences": prefs_json(&s.config.preferences)})),
        "prefs.set" => {
            let mut prefs = s.config.preferences.clone();
            let mut changed = 0;
            if let (Some(k), Some(v)) = (p::opt_str(prm, "key"), prm.get("value")) {
                let text = match v {
                    Value::String(t) => t.clone(),
                    other => other.to_string(),
                };
                prefs.set(k, &text)?;
                changed += 1;
            }
            for (k, v) in prm.as_object().into_iter().flatten() {
                if matches!(k.as_str(), "key" | "value" | "language_hint") {
                    continue;
                }
                let text = match v {
                    Value::String(t) => t.clone(),
                    other => other.to_string(),
                };
                if k == "language" && text.is_empty() {
                    continue;
                }
                prefs.set(k, &text)?;
                changed += 1;
            }
            if changed == 0 {
                return Err(Error::Param("pass `key` and `value`, or preference fields".into()));
            }
            s.config.preferences = prefs;
            s.save_config()?;
            Ok(json!({"preferences": prefs_json(&s.config.preferences)}))
        }
        "mail.sync" => {
            let all = p::opt_bool(prm, "all").unwrap_or(false)
                || p::opt_str(prm, "account") == Some(ALL)
                || (p::opt_str(prm, "account").is_none() && (s.view.account.is_empty() || s.view.unified()));
            let ids: Vec<String> = if all { s.config.accounts.iter().map(|a| a.id.clone()).collect() } else { vec![s.account_id(prm)?] };
            if ids.is_empty() {
                return Err(Error::Param("no account configured: add one with account.add".into()));
            }
            let page = Page::new(0, s.page_size());
            let mut jobs = Vec::new();
            for id in ids {
                let mailbox = s.mailbox_of(prm, &id)?;
                jobs.push(s.submit("mail.sync", &id, JobKind::Mailboxes)?);
                jobs.push(s.submit("mail.sync", &id, JobKind::Envelopes { mailbox, page })?);
            }
            Ok(json!({"jobs": jobs}))
        }
        "mail.mailboxes" => {
            let id = s.account_id(prm)?;
            let list: Vec<Value> = s.store.get(&id).map(|st| st.mailboxes.iter().map(|m| mailbox_json(m, italian)).collect()).unwrap_or_default();
            Ok(json!({"account": id, "mailboxes": list}))
        }
        "mail.select" if s.account_id(prm)? == ALL || p::opt_bool(prm, "unified") == Some(true) => {
            let role = s.unified_role_of(prm)?;
            if !s.view.unified() || s.view.mailbox != role.name() {
                s.view = crate::View { account: ALL.into(), mailbox: role.name().into(), ..crate::View::default() };
            }
            let folders = s.unified_folders(role);
            let mut jobs = Vec::new();
            for (account, mailbox) in &folders {
                if s.store.get(account).is_some_and(|st| st.mailboxes.is_empty())
                    && let Ok(job) = s.submit("mail.sync", account, JobKind::Mailboxes)
                {
                    jobs.push(job);
                }
                if s.store.get(account).is_some_and(|st| st.list(mailbox).is_empty()) {
                    let page = Page::new(0, s.page_size());
                    if let Ok(job) = s.submit("mail.list", account, JobKind::Envelopes { mailbox: mailbox.clone(), page }) {
                        jobs.push(job);
                    }
                }
            }
            let mut v = json!({"account": ALL, "unified": true, "role": role.name(), "folders": folders.iter().map(|(a, m)| json!({"account": a, "mailbox": m})).collect::<Vec<_>>()});
            if !jobs.is_empty() {
                v["jobs"] = json!(jobs);
            }
            Ok(v)
        }
        "mail.select" => {
            let id = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &id)?;
            if s.view.account != id || s.view.mailbox != mailbox {
                s.view = crate::View { account: id.clone(), mailbox: mailbox.clone(), ..crate::View::default() };
            }
            let cached = s.store.get(&id).map_or(0, |st| st.list(&mailbox).len());
            let mut v = json!({"account": id, "mailbox": mailbox, "cached": cached});
            if cached == 0 {
                let page = Page::new(0, s.page_size());
                let job = s.submit("mail.list", &id, JobKind::Envelopes { mailbox, page })?;
                v["job"] = json!(job);
            }
            Ok(v)
        }
        "mail.list" if s.account_id(prm)? == ALL => {
            let role = s.unified_role_of(prm)?;
            let size = s.page_size();
            let page_no = p::opt_u32(prm, "page").unwrap_or(0) as usize;
            let offset = page_no.saturating_mul(size).min(limits::MAX_ENVELOPES);
            let list = s.unified_list(role);
            let page: Vec<Value> = list.iter().skip(offset).take(size).map(envelope_json).collect();
            let mut v = json!({"account": ALL, "unified": true, "role": role.name(), "page": page_no, "total": list.len(), "envelopes": page, "cached": true});
            if p::opt_bool(prm, "refresh").unwrap_or(false) || list.is_empty() {
                let mut jobs = Vec::new();
                for (account, mailbox) in s.unified_folders(role) {
                    if s.store.get(&account).is_some_and(|st| st.mailboxes.is_empty())
                        && let Ok(job) = s.submit("mail.sync", &account, JobKind::Mailboxes)
                    {
                        jobs.push(job);
                    }
                    if let Ok(job) = s.submit("mail.list", &account, JobKind::Envelopes { mailbox, page: Page::new(0, size) }) {
                        jobs.push(job);
                    }
                }
                if !jobs.is_empty() {
                    v["jobs"] = json!(jobs);
                }
            }
            Ok(v)
        }
        "mail.list" => {
            let id = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &id)?;
            let size = s.page_size();
            let page_no = p::opt_u32(prm, "page").unwrap_or(0) as usize;
            let offset = page_no.saturating_mul(size).min(limits::MAX_ENVELOPES);
            let refresh = p::opt_bool(prm, "refresh").unwrap_or(false);
            let cached: Vec<Value> =
                s.store.get(&id).map(|st| st.list(&mailbox).iter().skip(offset).take(size).map(envelope_json).collect()).unwrap_or_default();
            let mut v = json!({"account": id, "mailbox": mailbox, "page": page_no, "envelopes": cached, "cached": true});
            if refresh || v["envelopes"].as_array().is_some_and(Vec::is_empty) {
                let job = s.submit("mail.list", &id, JobKind::Envelopes { mailbox, page: Page::new(offset, size) })?;
                v["job"] = json!(job);
            }
            Ok(v)
        }
        "mail.search" if s.account_id(prm)? == ALL => {
            let role = s.unified_role_of(prm)?;
            let text = prm.get("query").and_then(Value::as_str).unwrap_or_default().trim().to_string();
            let query = SearchQuery::parse(&text);
            if s.view.unified() {
                s.view.query = text.clone();
                s.view.results.clear();
            }
            if query.is_empty() {
                return Ok(json!({"account": ALL, "query": "", "envelopes": []}));
            }
            let size = s.page_size();
            let mut jobs = Vec::new();
            for (account, mailbox) in s.unified_folders(role) {
                if let Ok(job) = s.submit("mail.search", &account, JobKind::Search { mailbox, query: query.clone(), page: Page::new(0, size) }) {
                    jobs.push(job);
                }
            }
            if jobs.is_empty() {
                return Err(Error::Param("no account to search".into()));
            }
            Ok(json!({"jobs": jobs}))
        }
        "mail.search" => {
            let id = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &id)?;
            let text = prm.get("query").and_then(Value::as_str).unwrap_or_default().trim().to_string();
            let query = SearchQuery::parse(&text);
            if s.view.account == id && s.view.mailbox == mailbox {
                s.view.query = text.clone();
                if text.is_empty() {
                    s.view.results.clear();
                }
            }
            if query.is_empty() {
                return Ok(json!({"account": id, "mailbox": mailbox, "query": "", "envelopes": []}));
            }
            let size = s.page_size();
            let offset = (p::opt_u32(prm, "page").unwrap_or(0) as usize).saturating_mul(size);
            let job = s.submit("mail.search", &id, JobKind::Search { mailbox, query, page: Page::new(offset, size) })?;
            Ok(json!({"job": job}))
        }
        "mail.open" => {
            let id = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &id)?;
            let uid = p::u32(prm, "uid")?;
            if s.view.account == id || s.view.unified() {
                s.view.open = Some((mailbox.clone(), uid));
                s.view.open_account = id.clone();
            }
            let mark = s.config.preferences.mark_read_on_open && p::opt_bool(prm, "mark_read").unwrap_or(true);
            let unread = s.store.get(&id).and_then(|st| st.envelope(&mailbox, uid)).is_some_and(|e| !e.flags.seen);
            if mark && unread {
                if let Some(st) = s.store.get_mut(&id) {
                    st.apply_flag(&mailbox, &[uid], FlagChange::Seen(true));
                }
                let _ =
                    s.submit("mail.markRead", &id, JobKind::SetFlag { mailbox: mailbox.clone(), uids: vec![uid], change: FlagChange::Seen(true) });
            }
            if let Some(m) = s.store.get(&id).and_then(|st| st.message(&mailbox, uid)) {
                return Ok(message_json(m));
            }
            let job = s.submit("mail.open", &id, JobKind::Fetch { mailbox, uid })?;
            Ok(json!({"job": job}))
        }
        "mail.attachment" => {
            let id = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &id)?;
            let uid = p::u32(prm, "uid")?;
            let index = p::u32(prm, "index")? as usize;
            let raw = s
                .store
                .get(&id)
                .and_then(|st| st.raw.get(&(mailbox.clone(), uid)))
                .ok_or_else(|| Error::Param("open the message first (mail.open)".into()))?;
            let all = mime::attachments(raw);
            let a = all.get(index).ok_or_else(|| Error::Param(format!("no attachment {index}")))?;
            if let Some(path) = p::opt_str(prm, "path").filter(|p| !p.is_empty()) {
                s.host_path_allowed()?;
                let target = std::path::Path::new(path);
                let file = if target.is_dir() { target.join(&a.name) } else { target.to_path_buf() };
                std::fs::write(&file, &a.data).map_err(|e| Error::Param(format!("{}: {e}", file.display())))?;
                return Ok(json!({"saved": file.display().to_string(), "name": a.name, "mime": a.mime, "size": a.data.len()}));
            }
            Ok(json!({"name": a.name, "mime": a.mime, "size": a.data.len(), "data": mime::base64_encode(&a.data)}))
        }
        "mail.markRead" | "mail.star" => {
            let acc = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &acc)?;
            let uids = p::uids(prm, &s.view.selection)?;
            let change = if id == "mail.markRead" {
                FlagChange::Seen(p::opt_bool(prm, "read").unwrap_or(true))
            } else {
                FlagChange::Flagged(p::opt_bool(prm, "starred").unwrap_or(true))
            };
            if let Some(st) = s.store.get_mut(&acc) {
                st.apply_flag(&mailbox, &uids, change);
            }
            let job = s.submit(id, &acc, JobKind::SetFlag { mailbox, uids, change })?;
            Ok(json!({"job": job}))
        }
        "mail.archive" | "mail.trash" | "mail.spam" | "mail.move" | "mail.delete" => {
            let acc = s.account_id(prm)?;
            let mailbox = s.mailbox_of(prm, &acc)?;
            let uids = p::uids(prm, &s.view.selection)?;
            let in_trash = s.folder_for(&acc, Role::Trash).is_some_and(|t| t == mailbox);
            let target: Option<String> = match id {
                "mail.archive" => Some(s.special(&acc, Role::Archive)?),
                "mail.trash" if !in_trash => Some(s.special(&acc, Role::Trash)?),
                "mail.spam" => Some(s.special(&acc, Role::Spam)?),
                "mail.move" => Some(p::str(prm, "to")?.to_string()),
                _ => None,
            };
            if target.as_deref() == Some(mailbox.as_str()) {
                return Err(Error::Param(format!("the messages are already in {mailbox}")));
            }
            if let Some(st) = s.store.get_mut(&acc) {
                st.remove(&mailbox, &uids);
            }
            s.view.selection.retain(|u| !uids.contains(u));
            if s.view.open.as_ref().is_some_and(|(mb, u)| *mb == mailbox && uids.contains(u)) {
                s.view.open = None;
            }
            let answer_to = target.clone();
            let kind = match target {
                Some(to) => JobKind::Move { mailbox, uids, to },
                None => JobKind::Delete { mailbox, uids },
            };
            let job = s.submit(id, &acc, kind)?;
            Ok(json!({"job": job, "to": answer_to}))
        }
        "mail.send" => {
            let acc = s.account_id(prm)?;
            let account = s.config.account(&acc).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(acc.clone()))?;
            let draft = s.draft_from_params(prm)?;
            if draft.to.is_empty() && draft.cc.is_empty() && draft.bcc.is_empty() {
                return Err(Error::Param("`to` is required".into()));
            }
            let (raw, to) = s.build_outgoing(&account, &draft, p::opt_bool(prm, "signature").unwrap_or(true))?;
            let copy_to = s.sent_folder(&acc, &account);
            let answered = p::opt_u32(prm, "reply_to_uid").map(|u| (p::opt_str(prm, "reply_mailbox").unwrap_or("INBOX").to_string(), u));
            let delete_draft = s.folder_for(&acc, Role::Drafts).zip(draft.draft_uid);
            let job = s.submit(id, &acc, JobKind::Send { from: account.email.clone(), to, raw, copy_to, answered, delete_draft })?;
            Ok(json!({"job": job}))
        }
        "mail.reply" | "mail.forward" => {
            let acc = s.account_id(prm)?;
            let account = s.config.account(&acc).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(acc.clone()))?;
            let mailbox = s.mailbox_of(prm, &acc)?;
            let uid = p::u32(prm, "uid")?;
            let forward_to = if id == "mail.forward" {
                let to = p::addresses(prm, "to");
                if to.is_empty() {
                    return Err(Error::Param("`to` is required".into()));
                }
                to
            } else {
                Vec::new()
            };
            let body = prm.get("body").and_then(Value::as_str).unwrap_or_default().to_string();
            if id == "mail.reply" && body.trim().is_empty() {
                return Err(Error::Param("`body` is required".into()));
            }
            let copy_to = s.sent_folder(&acc, &account);
            let kind = JobKind::Respond {
                mailbox,
                uid,
                body,
                all: p::opt_bool(prm, "all").unwrap_or(false),
                forward_to,
                from: Address::new(account.from_name(), &account.email),
                signature: account.signature_text(),
                signature_html: account.signature_html.clone(),
                italian,
                copy_to,
                quote: p::opt_bool(prm, "quote").unwrap_or(true),
            };
            let job = s.submit(id, &acc, kind)?;
            Ok(json!({"job": job}))
        }
        "draft.save" => {
            let acc = s.account_id(prm)?;
            let account = s.config.account(&acc).cloned().ok_or_else(|| mailcraft_core::Error::NoAccount(acc.clone()))?;
            let draft = s.draft_from_params(prm)?;
            if draft.is_empty() {
                return Err(Error::Param("the draft is empty".into()));
            }
            // Drafts may lack recipients: build with a placeholder when needed.
            let mut d = draft.clone();
            if d.to.is_empty() && d.cc.is_empty() && d.bcc.is_empty() {
                d.to = vec![Address::new("", &account.email)];
            }
            let (raw, _) = s.build_outgoing(&account, &d, false)?;
            let folder = s.special(&acc, Role::Drafts)?;
            let job = s.submit(
                id,
                &acc,
                JobKind::Append { mailbox: folder, raw, flags: Flags { seen: true, draft: true, ..Flags::default() }, replace_uid: draft.draft_uid },
            )?;
            Ok(json!({"job": job}))
        }
        "draft.discard" => {
            let acc = s.account_id(prm)?;
            let uid = p::u32(prm, "uid")?;
            let folder = p::opt_str(prm, "mailbox").filter(|m| !m.is_empty()).map(str::to_string).unwrap_or(s.special(&acc, Role::Drafts)?);
            if let Some(st) = s.store.get_mut(&acc) {
                st.remove(&folder, &[uid]);
            }
            let job = s.submit(id, &acc, JobKind::Delete { mailbox: folder, uids: vec![uid] })?;
            Ok(json!({"job": job}))
        }
        "mailbox.subscribe" => {
            let acc = s.account_id(prm)?;
            let path = p::str(prm, "path").or_else(|_| p::str(prm, "mailbox"))?.to_string();
            if path.len() > 500 || path.contains(['\r', '\n']) {
                return Err(Error::Param("invalid folder name".into()));
            }
            let subscribed = p::opt_bool(prm, "subscribed").unwrap_or(true);
            let job = s.submit(id, &acc, JobKind::Subscribe { path, subscribed })?;
            Ok(json!({"job": job}))
        }
        "contacts.search" => {
            let query = p::opt_str(prm, "query").unwrap_or_default().trim().to_string();
            let limit = p::opt_u32(prm, "limit").unwrap_or(8).clamp(1, 200) as usize;
            let only: Option<String> = p::opt_str(prm, "account")
                .filter(|a| !a.is_empty() && *a != ALL)
                .map(|a| s.config.find(a).map(|x| x.id.clone()).unwrap_or(a.to_string()));
            let own: Vec<String> = s.config.accounts.iter().map(|a| a.email.to_ascii_lowercase()).collect();
            let mut hits: Vec<crate::store::Contact> = Vec::new();
            for (id, st) in &s.store {
                if only.as_ref().is_some_and(|o| o != id) {
                    continue;
                }
                for c in st.contacts.values().filter(|c| c.matches(&query) && !own.contains(&c.email)) {
                    match hits.iter_mut().find(|h| h.email == c.email) {
                        Some(h) => {
                            h.count = h.count.saturating_add(c.count);
                            h.last = h.last.max(c.last);
                            if h.name.is_empty() {
                                h.name = c.name.clone();
                            }
                        }
                        None => hits.push(c.clone()),
                    }
                }
            }
            let q = query.to_ascii_lowercase();
            hits.sort_by(|a, b| {
                let exact = |c: &crate::store::Contact| c.email.starts_with(&q) || c.name.to_ascii_lowercase().starts_with(&q);
                exact(b).cmp(&exact(a)).then(b.count.cmp(&a.count)).then(b.last.cmp(&a.last)).then(a.email.cmp(&b.email))
            });
            hits.truncate(limit);
            Ok(
                json!({"query": query, "contacts": hits.iter().map(|c| json!({"name": c.name, "email": c.email, "count": c.count, "display": Address::new(&c.name, &c.email).display()})).collect::<Vec<_>>()}),
            )
        }
        "mail.close" => {
            s.view.open = None;
            s.view.open_account.clear();
            Ok(json!({"open": Value::Null}))
        }
        "mailbox.create" => {
            let acc = s.account_id(prm)?;
            let name = p::str(prm, "name")?.to_string();
            if name.len() > 500 || name.contains(['\r', '\n']) {
                return Err(Error::Param("invalid folder name".into()));
            }
            let job = s.submit(id, &acc, JobKind::CreateMailbox { path: name })?;
            Ok(json!({"job": job}))
        }
        "selection.set" => {
            let uids = p::uids(prm, &[]).unwrap_or_default();
            let account = match p::opt_str(prm, "account").filter(|a| !a.is_empty()) {
                Some(a) => s.config.find(a).map(|x| x.id.clone()).unwrap_or_default(),
                None if s.view.unified() => s.view.open_account.clone(),
                None => s.view.account.clone(),
            };
            let mailbox = p::opt_str(prm, "mailbox").filter(|m| !m.is_empty()).map(str::to_string).unwrap_or_else(|| {
                if s.view.unified() {
                    s.view.open.as_ref().map(|(m, _)| m.clone()).or_else(|| s.folder_for(&account, s.view.unified_role())).unwrap_or_default()
                } else {
                    s.view.mailbox.clone()
                }
            });
            s.view.selection = uids;
            s.view.selection_account = account;
            s.view.selection_mailbox = mailbox;
            Ok(json!({"selection": s.view.selection, "account": s.view.selection_account, "mailbox": s.view.selection_mailbox}))
        }
        "selection.all" => {
            if s.view.unified() {
                // One account at a time: the open message's (or the first) account's list.
                let account = if s.view.open_account.is_empty() {
                    s.config.accounts.first().map(|a| a.id.clone()).unwrap_or_default()
                } else {
                    s.view.open_account.clone()
                };
                let mailbox = s.mailbox_of(&json!({}), &account)?;
                let list: Vec<u32> = if s.view.query.is_empty() {
                    s.store.get(&account).map(|st| st.list(&mailbox).iter().map(|e| e.uid).collect()).unwrap_or_default()
                } else {
                    s.view.results.iter().filter(|e| e.account == account).map(|e| e.uid).collect()
                };
                s.view.selection = list;
                s.view.selection_account = account;
                s.view.selection_mailbox = mailbox;
                return Ok(json!({"selection": s.view.selection, "account": s.view.selection_account}));
            }
            let list: Vec<u32> = if s.view.query.is_empty() {
                s.current().map(|st| st.list(&s.view.mailbox).iter().map(|e| e.uid).collect()).unwrap_or_default()
            } else {
                s.view.results.iter().map(|e| e.uid).collect()
            };
            s.view.selection = list;
            s.view.selection_account = s.view.account.clone();
            s.view.selection_mailbox = s.view.mailbox.clone();
            Ok(json!({"selection": s.view.selection}))
        }
        "selection.clear" => {
            s.view.selection.clear();
            Ok(json!({"selection": []}))
        }
        "view.get" => Ok(json!({
            "account": s.view.account,
            "unified": s.view.unified(),
            "mailbox": s.view.mailbox,
            "selection_account": s.view.selection_account,
            "open_account": s.view.open_account,
            "query": s.view.query,
            "selection": s.view.selection,
            "open": s.view.open.as_ref().map(|(m, u)| json!({"mailbox": m, "uid": u})),
            "jobs_pending": s.pending.len(),
        })),
        "app.status" => {
            let accounts: Vec<Value> = s.config.accounts.iter().map(|a| account_json(a, s.has_password(&a.id), s.store.get(&a.id))).collect();
            Ok(
                json!({"accounts": accounts, "jobs_pending": s.pending.len(), "notes": s.notes, "home": s.paths.as_ref().map(|p| p.config.parent().map(|d| d.display().to_string()))}),
            )
        }
        _ => Err(Error::UnknownCommand(id.to_string())),
    }
}
