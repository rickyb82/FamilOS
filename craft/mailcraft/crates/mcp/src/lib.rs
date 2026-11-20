//! MailCraft's MCP server (layer L6): the Model Context Protocol over stdio (newline-delimited
//! JSON-RPC 2.0), so AI agents can configure accounts in plain language and read, triage and
//! write mail.
//!
//! Every tool is an engine command ([`Session::run_blocking`]): what the interface does, an
//! agent can do, and nothing else. Passwords are stored by the engine (a user-only file) and
//! never returned by any tool.
#![recursion_limit = "512"]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use std::io::{BufRead, Write};

use mailcraft_engine::core::message::time;
use mailcraft_engine::core::{limits, mime};
use mailcraft_engine::{COMMANDS, MailSession, Paths, Session};
use serde_json::{Value, json};

/// MCP protocol revision this server prefers.
pub const PROTOCOL_VERSION: &str = "2025-06-18";
/// Revisions it also speaks; a client asking for another gets [`PROTOCOL_VERSION`].
pub const SUPPORTED_VERSIONS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];
/// The accounts and their status (`resources/read`).
pub const ACCOUNTS_URI: &str = "mailcraft://accounts";
/// The current inbox as a text list (`resources/read`).
pub const INBOX_URI: &str = "mailcraft://inbox";
/// Longest request line read (bytes).
const MAX_LINE: usize = 32 * 1024 * 1024;
/// Longest message body returned by read_message.
const MAX_TEXT: usize = 200_000;

const INSTRUCTIONS: &str = "MailCraft is an IMAP/SMTP mail client. Configure it in plain language: suggest_settings guesses the servers of \
an address (Gmail, Outlook, Aruba, Libero, PEC…), add_account stores an account (the password goes to a user-only secrets file) and \
tests it, find_servers probes the usual host names when the provider is unknown, set_preferences changes language/theme/sync. \
Prompts: setup_account (configure a mailbox from a description), triage_inbox (summarise and act on unread mail), write_email, configure. \
Mail: sync, list_mailboxes, list_messages (uids), search (from: subject: is:unread has:attachment newer_than:7d), read_message, reply, \
forward, send_mail, mark, archive, trash, spam, move, save_draft, save_attachment. Attachments travel as content: send_mail takes \
attachments [{name, data (base64)}], save_attachment without a path returns the file as an embedded resource (base64 blob); files up to 25 MB. \
Never invent a password: ask the person for it. \
Gmail and Microsoft 365 sign in with OAuth like Thunderbird: configure_oauth once with the client id and secret the person created in the provider's console (oauth_providers explains where), \
then add_account without a password and sign_in: give the person the URL to open; the sign-in completes by itself, then test_account. An app password works too. \
Before sending or deleting on their behalf, show what will be sent or removed and wait for a yes.";

type ToolResult = Result<Value, String>;

/// The server: one engine session on the user's configuration.
pub struct Server {
    session: Box<dyn MailSession>,
}

fn tools() -> Value {
    let account = json!({"type": "string", "description": "Account id, address or name (default: the current account); \"*\" = every account at once (list_messages, search, sync)"});
    let mailbox = json!({"type": "string", "description": "IMAP folder path (default: the inbox or the folder in view)"});
    let role = json!({"type": "string", "enum": ["inbox", "sent", "drafts", "trash", "spam", "archive"], "description": "A special folder instead of `mailbox`"});
    let uids = json!({"type": "array", "items": {"type": "integer", "minimum": 1}, "description": "Message uids from list_messages/search"});
    let uid = json!({"type": "integer", "minimum": 1, "description": "A message uid from list_messages/search"});
    let language = json!({"type": "string", "enum": ["en", "it"], "description": "Language of notes and quotes (default: the preference)"});
    let security = json!({"type": "string", "enum": ["tls", "starttls", "none"]});
    let port = json!({"type": "integer", "minimum": 1, "maximum": 65535});
    let addresses = json!({"type": "string", "description": "Recipients, comma separated: `Name <addr>` or `addr`"});
    let server_fields = json!({
        "name": {"type": "string", "description": "Display name of the account (default: the provider or the domain)"},
        "display_name": {"type": "string", "description": "The name put in From:"},
        "username": {"type": "string", "description": "Login when it is not the address"},
        "imap_host": {"type": "string"}, "imap_port": port, "imap_security": security,
        "smtp_host": {"type": "string"}, "smtp_port": port, "smtp_security": security,
        "signature": {"type": "string", "description": "Plain-text signature"},
        "signature_html": {"type": "string", "description": "HTML signature (logo, bold, links); the text one is derived when empty"},
        "color": {"type": "string", "description": "#rrggbb"},
        "trust_invalid_certs": {"type": "boolean", "description": "Accept a server certificate that does not validate (local bridges, self-hosted servers)"},
        "save_sent": {"type": "boolean", "description": "Copy sent mail to the Sent folder (off for Gmail, which does it itself)"},
    });
    let mut add_props = server_fields.clone();
    if let Some(o) = add_props.as_object_mut() {
        o.insert("email".into(), json!({"type": "string"}));
        o.insert("password".into(), json!({"type": "string", "description": "The mailbox or app password (stored locally, never returned)"}));
        o.insert(
            "test".into(),
            json!({"type": "boolean", "description": "Log in to IMAP and SMTP after saving (default true); unknown providers are probed first"}),
        );
        o.insert("language".into(), language.clone());
    }
    let mut update_props = server_fields;
    if let Some(o) = update_props.as_object_mut() {
        o.insert("id".into(), account.clone());
        o.insert("email".into(), json!({"type": "string"}));
        o.insert("password".into(), json!({"type": "string"}));
        o.insert("folders".into(), json!({"type": "object", "description": "Special folders by IMAP path", "properties": {"sent": {"type": "string"}, "drafts": {"type": "string"}, "trash": {"type": "string"}, "spam": {"type": "string"}, "archive": {"type": "string"}}}));
    }
    json!([
        {"name": "suggest_settings", "description": "IMAP/SMTP settings for an e-mail address: the provider's documented servers (Gmail, Outlook, Yahoo, iCloud, Aruba, PEC, Libero, Virgilio, Tiscali, TIM, Fastweb, GMX, Zoho, Fastmail, Proton Bridge…) or conventional guesses for an unknown domain, with notes (app passwords, OAuth, bridges).",
         "inputSchema": {"type": "object", "properties": {"email": {"type": "string"}, "language": language}, "required": ["email"]}},
        {"name": "add_account", "description": "Add (or replace) an account from an address and password; servers not given come from suggest_settings. With test=true (default) it logs in to IMAP and SMTP; for unknown domains it probes the usual host names first and keeps the working one. Returns the saved account, what was tested and the provider's notes.",
         "inputSchema": {"type": "object", "properties": add_props, "required": ["email"]}},
        {"name": "oauth_providers", "description": "The OAuth providers (Google, Microsoft 365): whether a client is configured, where to create one and what to pick there.",
         "inputSchema": {"type": "object", "properties": {"language": language}}},
        {"name": "configure_oauth", "description": "Save the OAuth client (id and secret) created in the provider's console; an empty client_id removes it. Needed once before sign_in.",
         "inputSchema": {"type": "object", "properties": {"provider": {"type": "string", "enum": ["google", "microsoft"]}, "client_id": {"type": "string"}, "client_secret": {"type": "string"}}, "required": ["provider", "client_id"]}},
        {"name": "sign_in", "description": "Start the OAuth sign-in of an account (Google or Microsoft 365): returns the URL the person must open in a browser. The sign-in completes on its own when the browser comes back; then call test_account.",
         "inputSchema": {"type": "object", "properties": {"account": account, "provider": {"type": "string", "enum": ["google", "microsoft"]}, "redirect": {"type": "string", "description": "Redirect URL (default: a listener on this machine)"}}}},
        {"name": "list_accounts", "description": "The configured accounts (never passwords or tokens): servers, sign-in method, signature, status, unread count.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "update_account", "description": "Change an account's name, servers, signature, folders, password… Only the fields given change.",
         "inputSchema": {"type": "object", "properties": update_props, "required": ["id"]}},
        {"name": "remove_account", "description": "Remove an account, its password and its cache.",
         "inputSchema": {"type": "object", "properties": {"id": account}, "required": ["id"]}},
        {"name": "test_account", "description": "Log in to the account's IMAP and SMTP servers and count its folders.",
         "inputSchema": {"type": "object", "properties": {"id": account}}},
        {"name": "find_servers", "description": "Try the suggested IMAP/SMTP servers for an account until one accepts the login, and save the working pair (for domains whose provider is unknown).",
         "inputSchema": {"type": "object", "properties": {"id": account}}},
        {"name": "set_password", "description": "Store or replace an account's password.",
         "inputSchema": {"type": "object", "properties": {"id": account, "password": {"type": "string"}}, "required": ["password"]}},
        {"name": "get_preferences", "description": "Interface preferences: language, theme, sync interval, page size, reading pane, conversations, mark read on open, undo send seconds, default account.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "set_preferences", "description": "Change preferences (only the fields given).",
         "inputSchema": {"type": "object", "properties": {
            "language": {"type": "string", "enum": ["en", "it", "system"]}, "theme": {"type": "string", "enum": ["light", "dark", "system"]},
            "sync_minutes": {"type": "integer", "minimum": 0, "maximum": 1440}, "page_size": {"type": "integer", "minimum": 5, "maximum": 500},
            "reading_pane": {"type": "boolean"}, "conversations": {"type": "boolean"}, "mark_read_on_open": {"type": "boolean"},
            "assist_suggestions": {"type": "boolean", "description": "Reply suggestions from the framework's assistant under each message"}, "assist_cloud": {"type": "boolean", "description": "Let the assistant use cloud models (default false: local only)"},
            "time_offset": {"type": "string", "description": "Time zone dates are shown in: auto (this device's), UTC, or an offset like +02:00"},
            "undo_send_seconds": {"type": "integer", "minimum": 0, "maximum": 30}, "default_account": account}}},
        {"name": "sync", "description": "Refresh the folders and the first page of a mailbox from the server.",
         "inputSchema": {"type": "object", "properties": {"account": account, "mailbox": mailbox, "all": {"type": "boolean", "description": "Every account"}}}},
        {"name": "list_mailboxes", "description": "The account's folders with roles (inbox, sent, drafts, trash, spam, archive) and unread/total counts.",
         "inputSchema": {"type": "object", "properties": {"account": account}}},
        {"name": "list_messages", "description": "A page of messages, newest first: uid, date, sender, subject, snippet, flags (● unread, ★ starred, 📎 attachment).",
         "inputSchema": {"type": "object", "properties": {"account": account, "mailbox": mailbox, "role": role, "page": {"type": "integer", "minimum": 0}, "unread_only": {"type": "boolean"}, "refresh": {"type": "boolean", "description": "Fetch from the server (default true)"}}}},
        {"name": "search", "description": "Search a folder: words plus from:, to:, subject:, is:unread, is:starred, has:attachment, newer_than:7d.",
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "account": account, "mailbox": mailbox, "role": role, "page": {"type": "integer", "minimum": 0}}, "required": ["query"]}},
        {"name": "read_message", "description": "The full message: headers, text body (HTML reduced to text), attachment list. Marks it read unless mark_read=false.",
         "inputSchema": {"type": "object", "properties": {"uid": uid, "account": account, "mailbox": mailbox, "mark_read": {"type": "boolean"}}, "required": ["uid"]}},
        {"name": "save_attachment", "description": "An attachment of a message (read_message lists them with their index). Without `path` the file comes back as an embedded resource (base64 blob, up to 25 MB) with its name, type and size; with `path` it is saved there on the server's machine (a directory keeps the file's name), which a shared server refuses.",
         "inputSchema": {"type": "object", "properties": {"uid": uid, "index": {"type": "integer", "minimum": 0, "description": "The attachment's index from read_message"}, "path": {"type": "string", "description": "Optional: a file or directory on the server's machine (not on a shared server)"}, "account": account, "mailbox": mailbox}, "required": ["uid", "index"]}},
        {"name": "send_mail", "description": "Send a new message (the account's signature is added). Show the person the text first and send only when they agree.",
         "inputSchema": {"type": "object", "properties": {"to": addresses, "cc": addresses, "bcc": addresses, "subject": {"type": "string"}, "body": {"type": "string"}, "html": {"type": "string", "description": "Optional HTML version of the body"}, "account": account,
            "attachments": {"type": "array", "maxItems": limits::MAX_ATTACHMENTS, "description": "Files to attach: {name, data} with the content in base64 (up to 25 MB each), or {path} of a file on the server's machine where the server allows it (not on a shared server)",
                "items": {"type": "object", "properties": {
                    "name": {"type": "string", "maxLength": limits::MAX_FILE_NAME, "description": "The file name the recipient sees"},
                    "data": {"type": "string", "contentEncoding": "base64", "description": "The file's content, base64"},
                    "mime": {"type": "string", "description": "Content type (default: from the name's extension)"},
                    "path": {"type": "string", "description": "Instead of name and data: a file on the server's machine"}}}}}, "required": ["to", "subject", "body"]}},
        {"name": "search_contacts", "description": "People seen in mail or written to, best match first: use it to resolve a name into an address before sending.",
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "A name or address prefix (empty = the most used)"}, "limit": {"type": "integer", "minimum": 1}, "account": account}}},
        {"name": "reply", "description": "Reply to a message (quoting it) with the given text; all=true answers everyone.",
         "inputSchema": {"type": "object", "properties": {"uid": uid, "body": {"type": "string"}, "all": {"type": "boolean"}, "quote": {"type": "boolean"}, "account": account, "mailbox": mailbox}, "required": ["uid", "body"]}},
        {"name": "forward", "description": "Forward a message (attachments included) to recipients, with an optional note.",
         "inputSchema": {"type": "object", "properties": {"uid": uid, "to": addresses, "body": {"type": "string"}, "account": account, "mailbox": mailbox}, "required": ["uid", "to"]}},
        {"name": "mark", "description": "Mark messages read/unread and/or starred/unstarred.",
         "inputSchema": {"type": "object", "properties": {"uids": uids, "read": {"type": "boolean"}, "starred": {"type": "boolean"}, "account": account, "mailbox": mailbox}, "required": ["uids"]}},
        {"name": "archive", "description": "Archive messages (move them out of the inbox).",
         "inputSchema": {"type": "object", "properties": {"uids": uids, "account": account, "mailbox": mailbox}, "required": ["uids"]}},
        {"name": "trash", "description": "Move messages to the trash (from the trash: delete for good).",
         "inputSchema": {"type": "object", "properties": {"uids": uids, "account": account, "mailbox": mailbox}, "required": ["uids"]}},
        {"name": "spam", "description": "Move messages to the spam folder.",
         "inputSchema": {"type": "object", "properties": {"uids": uids, "account": account, "mailbox": mailbox}, "required": ["uids"]}},
        {"name": "move", "description": "Move messages to a folder (IMAP path from list_mailboxes).",
         "inputSchema": {"type": "object", "properties": {"uids": uids, "to": {"type": "string"}, "account": account, "mailbox": mailbox}, "required": ["uids", "to"]}},
        {"name": "save_draft", "description": "Save a draft in the Drafts folder (draft_uid replaces an earlier one).",
         "inputSchema": {"type": "object", "properties": {"to": addresses, "cc": addresses, "bcc": addresses, "subject": {"type": "string"}, "body": {"type": "string"}, "draft_uid": uid, "account": account}}},
        {"name": "subscribe_folder", "description": "Show or hide a folder in the navigation (IMAP SUBSCRIBE/UNSUBSCRIBE); hidden folders stay on the server.",
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "IMAP path from list_mailboxes"}, "subscribed": {"type": "boolean", "description": "true = shown (default)"}, "account": account}, "required": ["path"]}},
        {"name": "create_folder", "description": "Create an IMAP folder.",
         "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "account": account}, "required": ["name"]}},
        {"name": "list_commands", "description": "Every engine command with its parameters.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "run_command", "description": "Run any engine command by id (see list_commands).",
         "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "params": {"type": "object"}}, "required": ["command"]}},
    ])
}

fn prompts() -> Value {
    json!([
        {"name": "setup_account", "title": "Set up a mailbox", "description": "Configure an e-mail account from a plain-language description (address, provider, password, preferences).",
         "arguments": [
            {"name": "description", "description": "E.g. 'la mia mail Aruba info@sirius.it, password …, firma Riccardo'", "required": true},
            {"name": "language", "description": "en or it", "required": false}]},
        {"name": "triage_inbox", "title": "Triage the inbox", "description": "Summarise unread mail, group it by urgency and propose replies, archiving and deletions.",
         "arguments": [
            {"name": "account", "description": "Account id, address or name", "required": false},
            {"name": "focus", "description": "What matters today (a client, a project, invoices…)", "required": false},
            {"name": "language", "description": "en or it", "required": false}]},
        {"name": "write_email", "title": "Write an e-mail", "description": "Draft a message from a brief, show it, then send it or save it as a draft.",
         "arguments": [
            {"name": "to", "description": "Recipients", "required": true},
            {"name": "about", "description": "What the message should say", "required": true},
            {"name": "tone", "description": "formal, friendly, short…", "required": false},
            {"name": "language", "description": "en or it", "required": false}]},
        {"name": "configure", "title": "Change settings", "description": "Apply a settings request in plain language: theme, language, sync interval, signature, folders, servers.",
         "arguments": [{"name": "request", "description": "E.g. 'tema scuro, sincronizza ogni 2 minuti, firma: Riccardo Burrai'", "required": true}]},
    ])
}

fn prompt_text(name: &str, args: &Value) -> Option<(&'static str, String)> {
    let a = |k: &str| args.get(k).and_then(Value::as_str).unwrap_or_default().trim().to_string();
    let lang = a("language");
    let answer_in = if lang.is_empty() { "the person's language".to_string() } else { lang.clone() };
    match name {
        "setup_account" => {
            let d = a("description");
            if d.is_empty() {
                return None;
            }
            Some((
                "Configure an e-mail account from a description",
                format!(
                    "Set up an e-mail account in MailCraft from this description (answer in {answer_in}):\n\n{d}\n\n\
Work like this:\n\
1. Find the address in the description. If it is missing, ask for it and stop.\n\
2. Call suggest_settings with the address (language {l}). Read the provider, the servers and the notes.\n\
3. If the description names servers, ports or security, they win over the suggestion. Keep the person's words for name, display name and signature.\n\
4. If the password is in the description, use it. If not, ask for it (an app password where the notes say so) and stop until it arrives. Never guess a password.\n\
5. Call add_account with email, password and anything the person specified (test=true). For an unknown domain the tool probes the usual servers; if it fails, show the tried servers and ask for the IMAP and SMTP hosts.\n\
6. If the test fails with an authentication error, repeat the notes (app password, IMAP enabled, OAuth-only providers) and ask whether to retry with another password.\n\
7. Report in {answer_in}: the account saved, the servers, what was tested, the unread count, and the preferences you changed (set_preferences) if the description asked for a language, theme or sync interval.",
                    l = if lang.is_empty() { "it if the description is Italian, else en" } else { &lang }
                ),
            ))
        }
        "triage_inbox" => {
            let account = a("account");
            let focus = a("focus");
            Some((
                "Summarise and act on unread mail",
                format!(
                    "Triage the inbox{} in MailCraft (answer in {answer_in}){}.\n\n\
1. Call sync, then list_messages with unread_only=true (and search is:unread newer_than:7d if the page is short).\n\
2. Call read_message on each unread message that looks important (skip obvious newsletters and notifications after reading their snippet).\n\
3. Group the messages: needs a reply, needs an action, informational, noise. For each important one give sender, subject, a one-line summary and the uid.\n\
4. Propose what to do: replies (draft them in full), archive (uids), trash (uids), star. Do not send, archive or delete anything until the person says so; then use reply, archive, trash, mark.\n\
5. End with the list of uids left unread.",
                    if account.is_empty() { String::new() } else { format!(" of account `{account}`") },
                    if focus.is_empty() { String::new() } else { format!(". Today's focus: {focus}") },
                ),
            ))
        }
        "write_email" => {
            let to = a("to");
            let about = a("about");
            if to.is_empty() || about.is_empty() {
                return None;
            }
            let tone = a("tone");
            Some((
                "Draft, show and send an e-mail",
                format!(
                    "Write an e-mail in MailCraft to: {to}\nAbout: {about}\n{}\n\
Write it in {answer_in}: a clear subject, a short greeting, the message, a closing line (the account's signature is added automatically; do not write one). \
Show the person the recipients, subject and full text and ask whether to send, change or save as a draft. Only then call send_mail (or save_draft).",
                    if tone.is_empty() { String::new() } else { format!("Tone: {tone}") },
                ),
            ))
        }
        "configure" => {
            let r = a("request");
            if r.is_empty() {
                return None;
            }
            Some((
                "Apply a settings request",
                format!(
                    "Apply this MailCraft settings request:\n\n{r}\n\n\
Map it to tools: language/theme/sync interval/page size/reading pane/conversations → set_preferences; signature, display name, servers, ports, security, special folders, password → update_account \
(call list_accounts first to find the account id when several exist; ask which one if it is ambiguous); a new mailbox → the setup_account flow (suggest_settings, add_account). \
Confirm what changed, in the person's language."
                ),
            ))
        }
        _ => None,
    }
}

/// The key of a tool result moved out of `structuredContent` into an embedded resource.
const EMBEDDED: &str = "_embedded_resource";

/// A file name in a URI path segment.
fn percent_encode(text: &str) -> String {
    text.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { char::from(b).to_string() } else { format!("%{b:02X}") }).collect()
}

/// `send_mail`'s attachments checked before they reach the engine: a file given by content
/// needs a plain file name, base64 data and stays within [`limits::MAX_TOOL_FILE`].
fn attachments_param(list: &Value) -> Result<Value, String> {
    let items = list.as_array().ok_or("`attachments` must be a list")?;
    if items.len() > limits::MAX_ATTACHMENTS {
        return Err(format!("at most {} attachments", limits::MAX_ATTACHMENTS));
    }
    let mut out = Vec::with_capacity(items.len());
    for (i, a) in items.iter().enumerate() {
        if let Some(path) = str_arg(a, "path") {
            out.push(json!({"path": path}));
            continue;
        }
        let data = a.get("data").and_then(Value::as_str).ok_or_else(|| format!("attachment {i}: give `name` and `data` (base64)"))?;
        let name = str_arg(a, "name").ok_or_else(|| format!("attachment {i}: `name` is required with `data`"))?;
        let name = name.rsplit(['/', '\\']).next().unwrap_or(name).trim();
        if name.is_empty() || name.len() > limits::MAX_FILE_NAME || name.chars().any(char::is_control) {
            return Err(format!("attachment {i}: the name must be a file name of at most {} bytes", limits::MAX_FILE_NAME));
        }
        let size = mime::base64_decode(data).ok_or_else(|| format!("attachment {i} ({name}): `data` must be base64"))?.len();
        if size > limits::MAX_TOOL_FILE {
            return Err(format!("attachment {i} ({name}) is {size} bytes: files up to {} MB", limits::MAX_TOOL_FILE / (1024 * 1024)));
        }
        let mut item = json!({"name": name, "data": data});
        if let Some(m) = str_arg(a, "mime") {
            item["mime"] = json!(m);
        }
        out.push(item);
    }
    Ok(Value::Array(out))
}

fn str_arg<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty())
}

fn flag(e: &Value) -> String {
    let mut f = String::new();
    if e["unread"].as_bool().unwrap_or(false) {
        f.push('●');
    }
    if e["starred"].as_bool().unwrap_or(false) {
        f.push('★');
    }
    if e["has_attachments"].as_bool().unwrap_or(false) {
        f.push('📎');
    }
    if e["answered"].as_bool().unwrap_or(false) {
        f.push('↩');
    }
    f
}

/// One line per message, for lists.
fn list_text(envelopes: &[Value], italian: bool, offset_minutes: i32) -> String {
    if envelopes.is_empty() {
        return if italian { "Nessun messaggio.".into() } else { "No messages.".into() };
    }
    let now = time::shifted(time::now(), offset_minutes);
    envelopes
        .iter()
        .map(|e| {
            let from = e["from"].as_array().and_then(|a| a.first()).and_then(Value::as_str).unwrap_or("");
            let from = from.split(" <").next().unwrap_or(from);
            let date = time::short(time::shifted(e["date"].as_i64().unwrap_or(0), offset_minutes), now, italian);
            let snippet = e["snippet"].as_str().unwrap_or("");
            let snippet =
                if snippet.chars().count() > 90 { format!("{}…", snippet.chars().take(90).collect::<String>()) } else { snippet.to_string() };
            // The unified list names each message's account: the uid alone is not enough there.
            let account = e["account"].as_str().filter(|a| !a.is_empty()).map(|a| format!(" @{a}")).unwrap_or_default();
            format!(
                "[{}{}] {:<3} {:>9}  {} — {} — {}",
                e["uid"],
                account,
                flag(e),
                date,
                from,
                e["subject"].as_str().unwrap_or("(no subject)"),
                snippet
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

impl Default for Server {
    fn default() -> Self {
        Self::new(Session::new(Box::new(|_| Box::new(mailcraft_engine::core::mock::MockBackend::new())), None))
    }
}

impl Server {
    pub fn new(session: impl MailSession + 'static) -> Self {
        Self { session: Box::new(session) }
    }

    /// A server on the user's configuration with the given transport factory.
    pub fn with_factory(factory: mailcraft_engine::core::backend::BackendFactory, paths: Option<Paths>) -> Self {
        Self::new(Session::new(factory, paths))
    }

    pub fn session(&self) -> &dyn MailSession {
        self.session.as_ref()
    }

    fn run(&mut self, command: &str, params: Value) -> ToolResult {
        self.session.run_blocking(command, params).map_err(|e| e.to_string())
    }

    fn italian(&self, args: &Value) -> bool {
        str_arg(args, "language").map_or(self.session.italian(), |l| l.eq_ignore_ascii_case("it"))
    }

    fn pass_through(&self, args: &Value, keys: &[&str]) -> Value {
        let mut v = json!({});
        for k in keys {
            if let Some(x) = args.get(k) {
                v[k] = x.clone();
            }
        }
        v
    }

    /// The attachment saved where `path` says (a host path, where the server allows it), or,
    /// without a path, the file itself as an embedded resource, within [`limits::MAX_TOOL_FILE`].
    fn save_attachment(&mut self, args: &Value) -> ToolResult {
        let to_path = str_arg(args, "path").is_some();
        let v = self.run("mail.attachment", self.pass_through(args, &["uid", "index", "path", "account", "mailbox"]))?;
        if to_path {
            return Ok(v);
        }
        let size = v["size"].as_u64().unwrap_or(0);
        if size > limits::MAX_TOOL_FILE as u64 {
            return Err(format!("the attachment is {size} bytes: a tool returns files up to {} MB", limits::MAX_TOOL_FILE / (1024 * 1024)));
        }
        let name = v["name"].as_str().filter(|n| !n.is_empty()).unwrap_or("attachment");
        let mime = v["mime"].as_str().filter(|m| !m.is_empty()).unwrap_or("application/octet-stream");
        let uri = format!(
            "mailcraft://attachment/{}/{}/{}",
            args.get("uid").and_then(Value::as_u64).unwrap_or(0),
            args.get("index").and_then(Value::as_u64).unwrap_or(0),
            percent_encode(name)
        );
        Ok(json!({
            "text": format!("{name} ({mime}, {size} bytes): the file is the embedded resource {uri} (base64)."),
            "name": name,
            "mime": mime,
            "size": size,
            "uri": uri,
            EMBEDDED: {"uri": uri, "mimeType": mime, "blob": v["data"]},
        }))
    }

    fn call(&mut self, name: &str, args: &Value) -> ToolResult {
        let italian = self.italian(args);
        let account_keys = ["account", "mailbox", "role"];
        match name {
            "suggest_settings" => self.run("account.suggest", self.pass_through(args, &["email", "language"])),
            "add_account" => {
                let keys = [
                    "email",
                    "password",
                    "name",
                    "display_name",
                    "username",
                    "imap_host",
                    "imap_port",
                    "imap_security",
                    "smtp_host",
                    "smtp_port",
                    "smtp_security",
                    "signature",
                    "signature_html",
                    "color",
                    "trust_invalid_certs",
                    "save_sent",
                    "language",
                ];
                let added = self.run("account.add", self.pass_through(args, &keys))?;
                let id = added["id"].as_str().unwrap_or_default().to_string();
                let mut out =
                    json!({"account": added["account"], "notes": added["suggestion"]["notes"], "provider": added["suggestion"]["provider"]});
                let test = args.get("test").and_then(Value::as_bool).unwrap_or(true);
                if added["account"]["auth"].as_str().is_some_and(|a| a != "password") && added["account"]["has_password"] != true {
                    out["next"] = json!("sign_in");
                    out["text"] = json!(if italian {
                        format!("Account salvato. Accede con {}: chiama sign_in e fai aprire l'indirizzo alla persona.", added["account"]["auth"])
                    } else {
                        format!("Account saved. It signs in with {}: call sign_in and have the person open the address.", added["account"]["auth"])
                    });
                    return Ok(out);
                }
                if !test {
                    return Ok(out);
                }
                if str_arg(args, "password").is_none() {
                    out["test"] = json!("skipped: no password given (set it with set_password, then test_account)");
                    return Ok(out);
                }
                let explicit = str_arg(args, "imap_host").is_some() || str_arg(args, "smtp_host").is_some();
                if added["suggestion"]["confidence"] == "guess" && !explicit {
                    match self.run("account.probe", json!({"id": id})) {
                        Ok(p) => out["probe"] = p,
                        Err(e) => {
                            out["probe"] = json!(format!("failed: {e}"));
                            out["test"] = json!(
                                "not run: no server accepted the login. Ask for the IMAP and SMTP hosts and call update_account, then test_account."
                            );
                            return Ok(out);
                        }
                    }
                }
                match self.run("account.test", json!({"id": id})) {
                    Ok(t) => out["test"] = t,
                    Err(e) => out["test"] = json!(format!("failed: {e}")),
                }
                if let Ok(a) = self.run("account.list", json!({})) {
                    out["account"] =
                        a["accounts"].as_array().and_then(|l| l.iter().find(|x| x["id"] == id).cloned()).unwrap_or(out["account"].clone());
                }
                Ok(out)
            }
            "list_accounts" => self.run("account.list", json!({})),
            "oauth_providers" => self.run("oauth.providers", self.pass_through(args, &["language"])),
            "configure_oauth" => self.run("oauth.configure", self.pass_through(args, &["provider", "client_id", "client_secret"])),
            "sign_in" => {
                let mut prm = self.pass_through(args, &["provider", "redirect"]);
                if let Some(a) = args.get("account") {
                    prm["id"] = a.clone();
                }
                let v = self.run("account.oauthStart", prm)?;
                let url = v["url"].as_str().unwrap_or_default();
                let text = if italian {
                    format!(
                        "Apri questo indirizzo nel browser e accedi con {}:\n{url}\nL'accesso si completa da solo; poi test_account.",
                        v["provider"]
                    )
                } else {
                    format!(
                        "Open this address in a browser and sign in with {}:\n{url}\nThe sign-in completes on its own; then test_account.",
                        v["provider"]
                    )
                };
                Ok(json!({"text": text, "url": url, "provider": v["provider"], "account": v["account"], "redirect": v["redirect"]}))
            }
            "update_account" => {
                let keys = [
                    "id",
                    "email",
                    "password",
                    "name",
                    "display_name",
                    "username",
                    "imap_host",
                    "imap_port",
                    "imap_security",
                    "smtp_host",
                    "smtp_port",
                    "smtp_security",
                    "signature",
                    "signature_html",
                    "color",
                    "trust_invalid_certs",
                    "save_sent",
                    "folders",
                ];
                self.run("account.update", self.pass_through(args, &keys))
            }
            "remove_account" => self.run("account.remove", self.pass_through(args, &["id"])),
            "test_account" => self.run("account.test", self.pass_through(args, &["id"])),
            "find_servers" => self.run("account.probe", self.pass_through(args, &["id"])),
            "set_password" => self.run("account.setPassword", self.pass_through(args, &["id", "password"])),
            "get_preferences" => self.run("prefs.get", json!({})),
            "set_preferences" => self.run("prefs.set", args.clone()),
            "sync" => {
                let v = self.run("mail.sync", self.pass_through(args, &["account", "mailbox", "all"]))?;
                let boxes = self.run("mail.mailboxes", self.pass_through(args, &["account"]))?;
                Ok(
                    json!({"synced": true, "mailboxes": boxes["mailboxes"], "first_page": list_text(v["envelopes"].as_array().map(Vec::as_slice).unwrap_or_default(), italian, self.session.config().preferences.offset_minutes())}),
                )
            }
            "list_mailboxes" => {
                if self.session.current().is_some_and(|s| s.mailboxes.is_empty()) {
                    let _ = self.run("mail.sync", self.pass_through(args, &["account"]));
                }
                let v = self.run("mail.mailboxes", self.pass_through(args, &["account", "language"]))?;
                let lines: Vec<String> = v["mailboxes"]
                    .as_array()
                    .map(|l| {
                        l.iter()
                            .map(|m| {
                                format!(
                                    "{} [{}] unread {} / {}{}",
                                    m["path"].as_str().unwrap_or(""),
                                    m["role"].as_str().unwrap_or(""),
                                    m["unread"],
                                    m["total"],
                                    if m["subscribed"] == false { " (hidden)" } else { "" }
                                )
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                Ok(json!({"text": lines.join("\n"), "mailboxes": v["mailboxes"]}))
            }
            "list_messages" => {
                let mut prm = self.pass_through(args, &["account", "mailbox", "role", "page"]);
                prm["refresh"] = json!(args.get("refresh").and_then(Value::as_bool).unwrap_or(true));
                let v = self.run("mail.list", prm)?;
                let mut list: Vec<Value> = v["envelopes"].as_array().cloned().unwrap_or_default();
                if args.get("unread_only").and_then(Value::as_bool).unwrap_or(false) {
                    list.retain(|e| e["unread"].as_bool().unwrap_or(false));
                }
                Ok(
                    json!({"text": list_text(&list, italian, self.session.config().preferences.offset_minutes()), "mailbox": v["mailbox"], "count": list.len(), "messages": list}),
                )
            }
            "search" => {
                let v = self.run("mail.search", self.pass_through(args, &["query", "account", "mailbox", "role", "page"]))?;
                let list: Vec<Value> = v["envelopes"].as_array().cloned().unwrap_or_default();
                Ok(
                    json!({"text": list_text(&list, italian, self.session.config().preferences.offset_minutes()), "count": list.len(), "messages": list}),
                )
            }
            "read_message" => {
                let v = self.run("mail.open", self.pass_through(args, &["uid", "account", "mailbox", "mark_read"]))?;
                let text = v["text"].as_str().unwrap_or_default();
                let text = mailcraft_engine::core::clip(text, MAX_TEXT);
                let header = format!(
                    "From: {}\nTo: {}\n{}Date: {}\nSubject: {}\n{}",
                    v["from"].as_array().map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")).unwrap_or_default(),
                    v["to"].as_array().map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")).unwrap_or_default(),
                    v["cc"]
                        .as_array()
                        .filter(|a| !a.is_empty())
                        .map(|a| format!("Cc: {}\n", a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")))
                        .unwrap_or_default(),
                    time::long(time::shifted(v["date"].as_i64().unwrap_or(0), self.session.config().preferences.offset_minutes()), italian),
                    v["subject"].as_str().unwrap_or(""),
                    v["attachments"]
                        .as_array()
                        .filter(|a| !a.is_empty())
                        .map(|a| format!(
                            "Attachments: {}\n",
                            a.iter()
                                .map(|x| format!(
                                    "[{}] {} ({}, {} bytes)",
                                    x["index"],
                                    x["name"].as_str().unwrap_or(""),
                                    x["mime"].as_str().unwrap_or(""),
                                    x["size"]
                                ))
                                .collect::<Vec<_>>()
                                .join(", ")
                        ))
                        .unwrap_or_default(),
                );
                let mut out = v.clone();
                out["text"] = json!(format!("{header}\n{text}"));
                if let Some(o) = out.as_object_mut() {
                    o.remove("html");
                }
                Ok(out)
            }
            "save_attachment" => self.save_attachment(args),
            "send_mail" => {
                let mut prm = self.pass_through(args, &["to", "cc", "bcc", "subject", "body", "html", "account"]);
                if let Some(list) = args.get("attachments").filter(|l| !l.is_null()) {
                    prm["attachments"] = attachments_param(list)?;
                }
                self.run("mail.send", prm)
            }
            "search_contacts" => {
                let v = self.run("contacts.search", self.pass_through(args, &["query", "limit", "account"]))?;
                let lines: Vec<String> = v["contacts"]
                    .as_array()
                    .map(|l| l.iter().filter_map(|c| c["display"].as_str().map(str::to_string)).collect())
                    .unwrap_or_default();
                Ok(json!({"text": if lines.is_empty() { "No contact matches.".to_string() } else { lines.join("\n") }, "contacts": v["contacts"]}))
            }
            "reply" => self.run("mail.reply", self.pass_through(args, &["uid", "body", "all", "quote", "account", "mailbox", "language"])),
            "forward" => self.run("mail.forward", self.pass_through(args, &["uid", "to", "body", "account", "mailbox", "language"])),
            "mark" => {
                let mut out = json!({});
                if let Some(read) = args.get("read").and_then(Value::as_bool) {
                    let mut prm = self.pass_through(args, &account_keys);
                    prm["uids"] = args["uids"].clone();
                    prm["read"] = json!(read);
                    out["read"] = self.run("mail.markRead", prm)?;
                }
                if let Some(starred) = args.get("starred").and_then(Value::as_bool) {
                    let mut prm = self.pass_through(args, &account_keys);
                    prm["uids"] = args["uids"].clone();
                    prm["starred"] = json!(starred);
                    out["starred"] = self.run("mail.star", prm)?;
                }
                if out.as_object().is_some_and(|o| o.is_empty()) {
                    return Err("pass `read` and/or `starred`".into());
                }
                Ok(out)
            }
            "archive" | "trash" | "spam" => {
                let mut prm = self.pass_through(args, &account_keys);
                prm["uids"] = args["uids"].clone();
                self.run(&format!("mail.{name}"), prm)
            }
            "move" => {
                let mut prm = self.pass_through(args, &account_keys);
                prm["uids"] = args["uids"].clone();
                prm["to"] = args["to"].clone();
                self.run("mail.move", prm)
            }
            "save_draft" => self.run("draft.save", self.pass_through(args, &["to", "cc", "bcc", "subject", "body", "draft_uid", "account"])),
            "create_folder" => self.run("mailbox.create", self.pass_through(args, &["name", "account"])),
            "subscribe_folder" => self.run("mailbox.subscribe", self.pass_through(args, &["path", "subscribed", "account"])),
            "list_commands" => Ok(
                json!({"commands": COMMANDS.iter().map(|c| json!({"id": c.id, "label": c.label, "menu": c.menu, "shortcut": c.shortcut, "params": c.params, "network": c.network})).collect::<Vec<_>>()}),
            ),
            "run_command" => {
                let command = str_arg(args, "command").ok_or("`command` is required")?.to_string();
                let params = args.get("params").cloned().unwrap_or_else(|| json!({}));
                self.run(&command, params)
            }
            _ => Err(format!("unknown tool {:?}", name.chars().take(40).collect::<String>())),
        }
    }

    fn accounts_text(&self) -> String {
        self.session
            .config()
            .accounts
            .iter()
            .map(|a| {
                let st = self.session.state(&a.id);
                format!(
                    "{} — {} <{}> — IMAP {} — SMTP {} — unread {}{}",
                    a.id,
                    a.name,
                    a.email,
                    a.imap.describe(),
                    a.smtp.describe(),
                    st.and_then(|s| s.by_role(mailcraft_engine::core::mailbox::Role::Inbox).map(|m| m.unread)).unwrap_or(0),
                    st.and_then(|s| s.error.as_ref()).map(|e| format!(" — error: {e}")).unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Handles one JSON-RPC message; `None` for notifications.
    pub fn handle(&mut self, msg: &Value) -> Option<Value> {
        let id = msg.get("id").cloned()?;
        let method = msg.get("method").and_then(Value::as_str).unwrap_or_default();
        let params = msg.get("params").cloned().unwrap_or(Value::Null);
        let result = match method {
            "initialize" => Ok(json!({
                "protocolVersion": params.get("protocolVersion").and_then(Value::as_str).filter(|v| SUPPORTED_VERSIONS.contains(v)).unwrap_or(PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": false}, "resources": {"listChanged": false, "subscribe": false}, "prompts": {"listChanged": false}},
                "serverInfo": {"name": "mailcraft", "title": "MailCraft", "version": env!("CARGO_PKG_VERSION")},
                "instructions": INSTRUCTIONS,
            })),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({"tools": tools()})),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or_default();
                let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                Ok(match self.call(name, &args) {
                    Ok(mut v) => {
                        // A file (save_attachment) travels once, as an embedded resource.
                        let embedded = v.as_object_mut().and_then(|o| o.remove(EMBEDDED));
                        let text = match v.get("text").and_then(Value::as_str) {
                            Some(t) => t.to_string(),
                            None => serde_json::to_string_pretty(&v).unwrap_or_default(),
                        };
                        let structured = if v.is_object() { v } else { json!({"result": v}) };
                        let mut content = vec![json!({"type": "text", "text": text})];
                        content.extend(embedded.map(|r| json!({"type": "resource", "resource": r})));
                        json!({"content": content, "structuredContent": structured, "isError": false})
                    }
                    Err(e) => json!({"content": [{"type": "text", "text": e}], "isError": true}),
                })
            }
            "prompts/list" => Ok(json!({"prompts": prompts()})),
            "prompts/get" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or_default();
                let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                match prompt_text(name, &args) {
                    Some((description, text)) => {
                        Ok(json!({"description": description, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}))
                    }
                    None => Err((-32602, format!("unknown prompt or missing arguments: {:?}", name.chars().take(40).collect::<String>()))),
                }
            }
            "resources/list" => Ok(json!({"resources": [
                {"uri": ACCOUNTS_URI, "name": "accounts", "title": "Accounts", "description": "Configured accounts, servers and status", "mimeType": "text/plain"},
                {"uri": INBOX_URI, "name": "inbox", "title": "Inbox", "description": "The current inbox, one line per message", "mimeType": "text/plain"},
            ]})),
            "resources/templates/list" => Ok(json!({"resourceTemplates": []})),
            "resources/read" => match params.get("uri").and_then(Value::as_str) {
                Some(ACCOUNTS_URI) => Ok(json!({"contents": [{"uri": ACCOUNTS_URI, "mimeType": "text/plain", "text": self.accounts_text()}]})),
                Some(INBOX_URI) => {
                    let italian = self.session.italian();
                    let list: Vec<Value> = self
                        .session
                        .current()
                        .map(|s| s.list("INBOX").iter().take(50).map(mailcraft_engine::envelope_json).collect())
                        .unwrap_or_default();
                    Ok(
                        json!({"contents": [{"uri": INBOX_URI, "mimeType": "text/plain", "text": list_text(&list, italian, self.session.config().preferences.offset_minutes())}]}),
                    )
                }
                _ => Err((-32002, "resource not found".to_string())),
            },
            _ => Err((-32601, format!("method not found: {}", method.chars().take(64).collect::<String>()))),
        };
        Some(match result {
            Ok(r) => json!({"jsonrpc": "2.0", "id": id, "result": r}),
            Err((code, message)) => json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),
        })
    }

    /// Serves until `input` ends: one JSON-RPC message per line in, one response per line out.
    pub fn serve(&mut self, input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
        for line in input.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let reply = if line.len() > MAX_LINE {
                Some(json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32600, "message": "request too large"}}))
            } else {
                match serde_json::from_str::<Value>(&line) {
                    Ok(Value::Array(batch)) => {
                        let replies: Vec<Value> = batch.iter().filter_map(|m| self.handle(m)).collect();
                        (!replies.is_empty()).then_some(Value::Array(replies))
                    }
                    Ok(msg) => self.handle(&msg),
                    Err(e) => Some(json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": format!("parse error: {e}")}})),
                }
            };
            if let Some(r) = reply {
                writeln!(output, "{r}")?;
                output.flush()?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mailcraft_engine::core::mock::MockBackend;

    fn server(mock: &MockBackend) -> Server {
        let m = mock.clone();
        Server::new(Session::new(Box::new(move |_| Box::new(m.clone())), None))
    }

    fn call(s: &mut Server, name: &str, args: Value) -> Value {
        let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
        assert_eq!(r["result"]["isError"], false, "{name}: {r}");
        r["result"]["structuredContent"].clone()
    }

    fn fails(s: &mut Server, name: &str, args: Value) -> String {
        let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
        assert_eq!(r["result"]["isError"], true, "{name}: {r}");
        r["result"]["content"][0]["text"].as_str().unwrap().to_string()
    }

    #[test]
    fn handshake_tools_prompts_and_resources() {
        let mut s = Server::default();
        let init = s.handle(&json!({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})).unwrap();
        assert_eq!(init["result"]["serverInfo"]["name"], "mailcraft");
        assert_eq!(init["result"]["protocolVersion"], "2024-11-05");
        assert!(s.handle(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).is_none());
        let tools = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).unwrap();
        let names: Vec<&str> = tools["result"]["tools"].as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert!(names.len() >= 28, "{names:?}");
        for t in tools["result"]["tools"].as_array().unwrap() {
            assert!(t["inputSchema"]["type"] == "object", "{}", t["name"]);
        }
        let prompts = s.handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})).unwrap();
        assert_eq!(prompts["result"]["prompts"].as_array().unwrap().len(), 4);
        let p = s.handle(&json!({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "setup_account", "arguments": {"description": "la mia mail aruba info@sirius-example.it", "language": "it"}}})).unwrap();
        assert!(p["result"]["messages"][0]["content"]["text"].as_str().unwrap().contains("suggest_settings"));
        let bad = s.handle(&json!({"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "write_email", "arguments": {}}})).unwrap();
        assert_eq!(bad["error"]["code"], -32602);
        let res = s.handle(&json!({"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": ACCOUNTS_URI}})).unwrap();
        assert!(res["result"]["contents"][0]["text"].is_string());
        assert!(s.handle(&json!({"jsonrpc": "2.0", "id": 6, "method": "nope"})).unwrap()["error"]["code"] == -32601);
    }

    #[test]
    fn an_agent_configures_an_account_and_works_the_inbox() {
        let mock = MockBackend::demo().require_password("app-pw");
        let mut s = server(&mock);
        let sg = call(&mut s, "suggest_settings", json!({"email": "riccardo@gmail.com", "language": "it"}));
        assert_eq!(sg["provider"], "Gmail");
        assert!(sg["notes"][0].as_str().unwrap().contains("password per le app"));
        // Wrong password: the account is saved, the test reports the failure.
        let v = call(&mut s, "add_account", json!({"email": "riccardo@gmail.com", "password": "nope", "name": "Personale"}));
        assert!(v["test"].as_str().unwrap().starts_with("failed"), "{v}");
        assert!(v["account"]["has_password"] == true);
        call(&mut s, "set_password", json!({"id": "riccardo-gmail-com", "password": "app-pw"}));
        let t = call(&mut s, "test_account", json!({"id": "riccardo-gmail-com"}));
        assert_eq!(t["ok"], true);
        // Unknown domain: probed, then tested.
        let v = call(&mut s, "add_account", json!({"email": "mario@sirius-example.it", "password": "app-pw", "signature": "Mario"}));
        assert_eq!(v["probe"]["imap"], "imap.sirius-example.it:993 (tls)");
        assert_eq!(v["test"]["ok"], true);
        let accounts = call(&mut s, "list_accounts", json!({}));
        assert_eq!(accounts["accounts"].as_array().unwrap().len(), 2);
        assert!(!accounts.to_string().contains("app-pw"), "passwords never leave");
        // OAuth: a Gmail address without a password waits for sign_in; the client comes first.
        let v = call(&mut s, "add_account", json!({"email": "anna@gmail.com", "name": "Anna"}));
        assert_eq!(v["next"], "sign_in");
        assert!(fails(&mut s, "sign_in", json!({"account": "anna@gmail.com"})).contains("configure"));
        let p = call(&mut s, "oauth_providers", json!({}));
        assert_eq!(p["providers"][0]["id"], "google");
        call(&mut s, "configure_oauth", json!({"provider": "google", "client_id": "cid", "client_secret": "s3"}));
        let v = call(&mut s, "sign_in", json!({"account": "anna@gmail.com"}));
        assert!(v["url"].as_str().unwrap().contains("client_id=cid"));
        assert!(v["text"].as_str().unwrap().contains("accounts.google.com"));
        assert!(!call(&mut s, "list_accounts", json!({})).to_string().contains("s3\""), "secrets never leave");
        call(&mut s, "set_preferences", json!({"theme": "dark", "language": "it", "sync_minutes": 3}));
        assert_eq!(call(&mut s, "get_preferences", json!({}))["preferences"]["theme"], "dark");
        assert!(fails(&mut s, "set_preferences", json!({"theme": "neon"})).contains("theme"));
        // Mail.
        let v = call(&mut s, "sync", json!({"account": "riccardo-gmail-com"}));
        assert!(v["first_page"].as_str().unwrap().contains("Riunione"));
        let boxes = call(&mut s, "list_mailboxes", json!({"account": "riccardo-gmail-com"}));
        assert!(boxes["text"].as_str().unwrap().contains("INBOX [inbox]"));
        let list = call(&mut s, "list_messages", json!({"account": "riccardo-gmail-com", "unread_only": true}));
        assert_eq!(list["count"], 4);
        let uid = list["messages"][0]["uid"].as_u64().unwrap();
        let m = call(&mut s, "read_message", json!({"uid": uid, "account": "riccardo-gmail-com"}));
        let text = m["text"].as_str().unwrap();
        assert!(text.starts_with("From: ") && text.contains("Subject: ") && text.contains("lunedì"), "{text}");
        assert!(m.get("html").is_none());
        let found = call(&mut s, "search", json!({"query": "fattura has:attachment", "account": "riccardo-gmail-com"}));
        assert_eq!(found["count"], 1);
        let r = call(&mut s, "reply", json!({"uid": uid, "body": "Va bene per lunedì.", "account": "riccardo-gmail-com"}));
        assert_eq!(r["sent"], true);
        assert_eq!(mock.sent().len(), 1);
        call(&mut s, "mark", json!({"uids": [uid], "starred": true, "account": "riccardo-gmail-com"}));
        assert!(mock.flags_of("INBOX", uid as u32).unwrap().flagged);
        call(&mut s, "archive", json!({"uids": [uid], "account": "riccardo-gmail-com"}));
        assert_eq!(mock.count("Archive"), 1);
        let sent = call(&mut s, "send_mail", json!({"to": "luca@example.com", "subject": "Ciao", "body": "Test", "account": "riccardo-gmail-com"}));
        assert_eq!(sent["sent"], true);
        assert!(fails(&mut s, "send_mail", json!({"to": "", "subject": "x", "body": "y"})).contains("to"));
        assert!(fails(&mut s, "mark", json!({"uids": [1]})).contains("read"));
        call(&mut s, "remove_account", json!({"id": "mario-sirius-example-it"}));
        assert_eq!(call(&mut s, "list_accounts", json!({}))["accounts"].as_array().unwrap().len(), 2, "gmail + anna");
    }

    /// On a shared server (Emily's chat) host paths are refused, so attachments travel as
    /// content: base64 into send_mail, an embedded resource out of save_attachment.
    #[test]
    fn attachments_travel_as_content_on_a_shared_server() {
        let mock = MockBackend::demo();
        let m = mock.clone();
        let mut session = Session::new(Box::new(move |_| Box::new(m.clone())), None);
        session.allow_host_paths(false);
        let mut s = Server::new(session);
        call(
            &mut s,
            "add_account",
            json!({"email": "anna@example.com", "password": "pw", "imap_host": "imap.example.com", "smtp_host": "smtp.example.com", "test": false}),
        );
        let found = call(&mut s, "search", json!({"query": "fattura has:attachment"}));
        let uid = found["messages"][0]["uid"].as_u64().unwrap();
        call(&mut s, "read_message", json!({"uid": uid}));
        let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "save_attachment", "arguments": {"uid": uid, "index": 0}}})).unwrap();
        let result = &r["result"];
        assert_eq!(result["isError"], false, "{r}");
        assert_eq!(result["content"][1]["type"], "resource", "{r}");
        let resource = &result["content"][1]["resource"];
        let blob = resource["blob"].as_str().unwrap();
        let bytes = mime::base64_decode(blob).unwrap();
        assert!(!bytes.is_empty());
        assert_eq!(Some(bytes.len() as u64), result["structuredContent"]["size"].as_u64());
        assert_eq!(resource["mimeType"], result["structuredContent"]["mime"]);
        assert!(resource["uri"].as_str().unwrap().starts_with(&format!("mailcraft://attachment/{uid}/0/")), "{resource}");
        assert!(
            result["structuredContent"].get(EMBEDDED).is_none() && !result["content"][0]["text"].as_str().unwrap().contains(blob),
            "the file travels once"
        );
        assert!(fails(&mut s, "save_attachment", json!({"uid": uid, "index": 0, "path": "/tmp"})).contains("host paths"));
        // Into the mail: a name and the content in base64; the type comes from the name.
        let pdf = mime::base64_encode(b"%PDF-1.4 offerta");
        let sent = call(
            &mut s,
            "send_mail",
            json!({"to": "luca@example.com", "subject": "Offerta", "body": "In allegato.", "attachments": [{"name": "../offerta.pdf", "data": pdf}]}),
        );
        assert_eq!(sent["sent"], true, "{sent}");
        let raw = String::from_utf8_lossy(&mock.sent().last().unwrap().2).into_owned();
        assert!(raw.contains("Content-Type: application/pdf; name=\"offerta.pdf\""), "{raw}");
        // Refused before anything is sent.
        for (bad, why) in [
            (json!([{"data": pdf}]), "name"),
            (json!([{"name": "a.txt", "data": "@@"}]), "base64"),
            (json!([{"name": "x".repeat(300), "data": pdf}]), "file name"),
            (json!([{"name": "big.bin", "data": mime::base64_encode(&vec![0u8; limits::MAX_TOOL_FILE + 1])}]), "MB"),
            (json!([{"path": "/etc/hostname"}]), "host paths"),
            (json!({"name": "a.txt"}), "list"),
        ] {
            let e = fails(&mut s, "send_mail", json!({"to": "luca@example.com", "subject": "x", "body": "y", "attachments": bad}));
            assert!(e.contains(why), "{why}: {e}");
        }
        assert_eq!(mock.sent().len(), 1);
    }

    #[test]
    fn serve_speaks_line_delimited_json_rpc() {
        let mut s = Server::default();
        let input = b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\nnot json\n\n[{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}]\n";
        let mut out = Vec::new();
        s.serve(&input[..], &mut out).unwrap();
        let lines: Vec<&str> = std::str::from_utf8(&out).unwrap().lines().collect();
        assert_eq!(lines.len(), 3);
        assert!(lines[0].contains("\"id\":1"));
        assert!(lines[1].contains("parse error"));
        assert!(lines[2].starts_with('['));
    }
}
