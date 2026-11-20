//! The worker thread: owns one backend per account and runs jobs sent by the session, posting
//! events back. Every job carries the account and password it needs, so the worker has no
//! configuration of its own.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, Sender, channel};

use mailcraft_core::account::{Account, Server};
use mailcraft_core::backend::{BackendFactory, FlagChange, MailBackend, Page, SearchQuery};
use mailcraft_core::mailbox::Mailbox;
use mailcraft_core::message::{Address, Draft, Envelope, Flags, Message, time};
use mailcraft_core::oauth::{ClientCredentials, OAuthBroker, Tokens};
use mailcraft_core::{Error, Result, mime};

/// What a job does.
#[derive(Clone, Debug)]
pub enum JobKind {
    /// Connect (or reconnect) and list mailboxes.
    Mailboxes,
    Envelopes {
        mailbox: String,
        page: Page,
    },
    EnvelopesByUid {
        mailbox: String,
        uids: Vec<u32>,
    },
    Fetch {
        mailbox: String,
        uid: u32,
    },
    SetFlag {
        mailbox: String,
        uids: Vec<u32>,
        change: FlagChange,
    },
    Move {
        mailbox: String,
        uids: Vec<u32>,
        to: String,
    },
    Delete {
        mailbox: String,
        uids: Vec<u32>,
    },
    Append {
        mailbox: String,
        raw: Vec<u8>,
        flags: Flags,
        replace_uid: Option<u32>,
    },
    Search {
        mailbox: String,
        query: SearchQuery,
        page: Page,
    },
    /// Send `raw`; copy it to `copy_to` when set; flag `answered` (mailbox, uid) when set.
    Send {
        from: String,
        to: Vec<String>,
        raw: Vec<u8>,
        copy_to: Option<String>,
        answered: Option<(String, u32)>,
        delete_draft: Option<(String, u32)>,
    },
    /// Fetch a message, build the reply or forward from it, then send like `Send`.
    Respond {
        mailbox: String,
        uid: u32,
        body: String,
        all: bool,
        forward_to: Vec<Address>,
        from: Address,
        signature: String,
        signature_html: String,
        italian: bool,
        copy_to: Option<String>,
        quote: bool,
    },
    CreateMailbox {
        path: String,
    },
    Subscribe {
        path: String,
        subscribed: bool,
    },
    /// Turn an OAuth authorization code into tokens (the provider is the account's).
    OAuthExchange {
        client: ClientCredentials,
        code: String,
        verifier: String,
        redirect: String,
    },
    /// Renew an OAuth access token.
    OAuthRefresh {
        client: ClientCredentials,
        refresh_token: String,
    },
    /// Connect, list mailboxes and test SMTP with a fresh backend (no state kept).
    Test,
    /// Try the candidate (imap, smtp) pairs until one logs in; the first working pair wins.
    Probe {
        candidates: Vec<(Server, Server)>,
    },
    Disconnect,
}

/// A job as the session queues it.
#[derive(Clone, Debug)]
pub struct Job {
    pub id: u64,
    pub account: Account,
    pub password: String,
    pub kind: JobKind,
}

/// What a finished job produced.
#[derive(Clone, Debug)]
pub enum Output {
    Mailboxes(Vec<Mailbox>, String),
    Envelopes {
        mailbox: String,
        page: Page,
        list: Vec<Envelope>,
    },
    EnvelopesByUid {
        mailbox: String,
        list: Vec<Envelope>,
    },
    Message {
        mailbox: String,
        uid: u32,
        raw: Vec<u8>,
        message: Box<Message>,
    },
    Flagged {
        mailbox: String,
        uids: Vec<u32>,
        change: FlagChange,
    },
    Moved {
        mailbox: String,
        uids: Vec<u32>,
        to: String,
    },
    Deleted {
        mailbox: String,
        uids: Vec<u32>,
    },
    Appended {
        mailbox: String,
        uid: Option<u32>,
        replaced: Option<u32>,
    },
    Search {
        mailbox: String,
        query: SearchQuery,
        list: Vec<Envelope>,
    },
    /// `copy_error` says why the copy to the Sent folder failed, when it did.
    Sent {
        message_id: String,
        copied_to: Option<String>,
        copy_error: Option<String>,
    },
    Created {
        path: String,
    },
    Subscribed {
        path: String,
        subscribed: bool,
    },
    /// New OAuth tokens for the account.
    Tokens(Tokens),
    Tested {
        mailboxes: usize,
        smtp: Result<()>,
    },
    Probed {
        imap: Server,
        smtp: Server,
        tried: Vec<String>,
    },
    Disconnected,
}

/// A job's completion.
#[derive(Clone, Debug)]
pub struct Event {
    pub job: u64,
    pub account: String,
    pub kind: JobKind,
    pub result: Result<Output>,
}

pub struct Worker {
    tx: Sender<Job>,
    rx: Receiver<Event>,
}

struct Slot {
    backend: Box<dyn MailBackend>,
    account: Account,
    password: String,
    connected: bool,
}

impl Worker {
    pub fn start(factory: BackendFactory, broker: Arc<dyn OAuthBroker>) -> Self {
        let (job_tx, job_rx) = channel::<Job>();
        let (ev_tx, ev_rx) = channel::<Event>();
        std::thread::Builder::new().name("mailcraft-worker".into()).spawn(move || run(factory, broker, job_rx, ev_tx)).ok();
        Self { tx: job_tx, rx: ev_rx }
    }

    pub fn submit(&self, job: Job) -> bool {
        self.tx.send(job).is_ok()
    }

    pub fn poll(&self) -> Vec<Event> {
        self.rx.try_iter().collect()
    }

    /// Blocks until an event arrives or the timeout passes.
    pub fn wait(&self, timeout: std::time::Duration) -> Option<Event> {
        self.rx.recv_timeout(timeout).ok()
    }
}

fn run(factory: BackendFactory, broker: Arc<dyn OAuthBroker>, jobs: Receiver<Job>, events: Sender<Event>) {
    let mut slots: HashMap<String, Slot> = HashMap::new();
    // Access tokens obtained here, so that jobs queued with an older token use the new one.
    let mut tokens: HashMap<String, String> = HashMap::new();
    while let Ok(mut job) = jobs.recv() {
        let account_id = job.account.id.clone();
        let kind = job.kind.clone();
        if job.account.oauth_provider().is_some()
            && let Some(t) = tokens.get(&account_id)
            && !matches!(job.kind, JobKind::OAuthExchange { .. } | JobKind::OAuthRefresh { .. })
        {
            job.password = t.clone();
        }
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| execute(&factory, broker.as_ref(), &mut slots, &job)))
            .unwrap_or_else(|_| Err(Error::Server("the mail transport failed unexpectedly".into())));
        if let Ok(Output::Tokens(t)) = &result {
            tokens.insert(account_id.clone(), t.access_token.clone());
        }
        if let Err(Error::NotConnected | Error::Connect(_)) = &result
            && let Some(slot) = slots.get_mut(&account_id)
        {
            slot.connected = false;
        }
        if events.send(Event { job: job.id, account: account_id, kind, result }).is_err() {
            return;
        }
    }
}

fn slot<'a>(factory: &BackendFactory, slots: &'a mut HashMap<String, Slot>, account: &Account, password: &str) -> Result<&'a mut Slot> {
    let stale = slots.get(&account.id).is_some_and(|s| s.account != *account || s.password != password);
    if stale && let Some(mut old) = slots.remove(&account.id) {
        old.backend.disconnect();
    }
    let slot = slots.entry(account.id.clone()).or_insert_with(|| Slot {
        backend: factory(account),
        account: account.clone(),
        password: password.to_string(),
        connected: false,
    });
    if !slot.connected {
        slot.backend.connect(account, password)?;
        slot.connected = true;
    }
    Ok(slot)
}

fn execute(factory: &BackendFactory, broker: &dyn OAuthBroker, slots: &mut HashMap<String, Slot>, job: &Job) -> Result<Output> {
    match &job.kind {
        JobKind::OAuthExchange { client, code, verifier, redirect } => {
            let provider = job.account.oauth_provider().ok_or_else(|| Error::Auth("the account does not sign in with OAuth".into()))?;
            return Ok(Output::Tokens(broker.exchange(provider, client, code, verifier, redirect)?));
        }
        JobKind::OAuthRefresh { client, refresh_token } => {
            let provider = job.account.oauth_provider().ok_or_else(|| Error::Auth("the account does not sign in with OAuth".into()))?;
            return Ok(Output::Tokens(broker.refresh(provider, client, refresh_token)?));
        }
        JobKind::Disconnect => {
            if let Some(mut s) = slots.remove(&job.account.id) {
                s.backend.disconnect();
            }
            return Ok(Output::Disconnected);
        }
        JobKind::Test => {
            let mut b = factory(&job.account);
            b.connect(&job.account, &job.password)?;
            let (boxes, _) = b.mailboxes()?;
            let smtp = b.test_smtp();
            b.disconnect();
            return Ok(Output::Tested { mailboxes: boxes.len(), smtp });
        }
        JobKind::Probe { candidates } => {
            let mut tried = Vec::new();
            for (imap, smtp) in candidates.iter().take(40) {
                let candidate = Account { imap: imap.clone(), smtp: smtp.clone(), ..job.account.clone() };
                let mut b = factory(&candidate);
                match b.connect(&candidate, &job.password) {
                    Ok(()) => {
                        b.disconnect();
                        return Ok(Output::Probed { imap: imap.clone(), smtp: smtp.clone(), tried });
                    }
                    Err(e) => tried.push(format!("{}: {e}", imap.describe())),
                }
            }
            return Err(Error::Connect(format!("no IMAP server answered for {}: {}", job.account.email, tried.join(" | "))));
        }
        _ => {}
    }
    let s = slot(factory, slots, &job.account, &job.password)?;
    let b = &mut s.backend;
    match &job.kind {
        JobKind::Mailboxes => {
            let (list, delimiter) = b.mailboxes()?;
            Ok(Output::Mailboxes(list, delimiter))
        }
        JobKind::Envelopes { mailbox, page } => Ok(Output::Envelopes { mailbox: mailbox.clone(), page: *page, list: b.envelopes(mailbox, *page)? }),
        JobKind::EnvelopesByUid { mailbox, uids } => {
            Ok(Output::EnvelopesByUid { mailbox: mailbox.clone(), list: b.envelopes_by_uid(mailbox, uids)? })
        }
        JobKind::Fetch { mailbox, uid } => {
            let raw = b.fetch(mailbox, *uid)?;
            let mut message = mime::parse(&raw)?;
            message.envelope.uid = *uid;
            message.envelope.mailbox = mailbox.clone();
            Ok(Output::Message { mailbox: mailbox.clone(), uid: *uid, raw, message: Box::new(message) })
        }
        JobKind::SetFlag { mailbox, uids, change } => {
            b.set_flag(mailbox, uids, *change)?;
            Ok(Output::Flagged { mailbox: mailbox.clone(), uids: uids.clone(), change: *change })
        }
        JobKind::Move { mailbox, uids, to } => {
            b.move_messages(mailbox, uids, to)?;
            Ok(Output::Moved { mailbox: mailbox.clone(), uids: uids.clone(), to: to.clone() })
        }
        JobKind::Delete { mailbox, uids } => {
            b.delete(mailbox, uids)?;
            Ok(Output::Deleted { mailbox: mailbox.clone(), uids: uids.clone() })
        }
        JobKind::Append { mailbox, raw, flags, replace_uid } => {
            let uid = b.append(mailbox, raw, *flags)?;
            if let Some(old) = replace_uid {
                let _ = b.delete(mailbox, &[*old]);
            }
            Ok(Output::Appended { mailbox: mailbox.clone(), uid, replaced: *replace_uid })
        }
        JobKind::Search { mailbox, query, page } => {
            Ok(Output::Search { mailbox: mailbox.clone(), query: query.clone(), list: b.search(mailbox, query, *page)? })
        }
        JobKind::Send { from, to, raw, copy_to, answered, delete_draft } => {
            b.send(from, to, raw)?;
            let message_id = mime::parse_headers(raw).map(|e| e.message_id).unwrap_or_default();
            let (copied, copy_error) = copy_sent(b.as_mut(), copy_to.as_deref(), raw);
            if let Some((mailbox, uid)) = answered {
                let _ = b.set_flag(mailbox, &[*uid], FlagChange::Answered(true));
            }
            if let Some((mailbox, uid)) = delete_draft {
                let _ = b.delete(mailbox, &[*uid]);
            }
            Ok(Output::Sent { message_id, copied_to: copied, copy_error })
        }
        JobKind::Respond { mailbox, uid, body, all, forward_to, from, signature, signature_html, italian, copy_to, quote } => {
            let raw = b.fetch(mailbox, *uid)?;
            let original = mime::parse(&raw)?;
            let mut draft = compose_response(&original, &raw, body, *all, forward_to, from, signature, signature_html, *italian, *quote);
            mime::inline_data_images(&mut draft);
            let message_id = format!("<{}.{}@{}>", time::now(), uid, from.email.split('@').nth(1).unwrap_or("mailcraft"));
            let out = mime::build(&draft, from, time::now(), &message_id)?;
            b.send(&from.email, &draft.recipients(), &out)?;
            let (copied, copy_error) = copy_sent(b.as_mut(), copy_to.as_deref(), &out);
            if forward_to.is_empty() {
                let _ = b.set_flag(mailbox, &[*uid], FlagChange::Answered(true));
            }
            Ok(Output::Sent { message_id, copied_to: copied, copy_error })
        }
        JobKind::CreateMailbox { path } => {
            b.create_mailbox(path)?;
            Ok(Output::Created { path: path.clone() })
        }
        JobKind::Subscribe { path, subscribed } => {
            b.subscribe(path, *subscribed)?;
            Ok(Output::Subscribed { path: path.clone(), subscribed: *subscribed })
        }
        JobKind::Disconnect | JobKind::Test | JobKind::Probe { .. } | JobKind::OAuthExchange { .. } | JobKind::OAuthRefresh { .. } => {
            Err(Error::Server("handled above".into()))
        }
    }
}

/// Copies a sent message to the Sent folder, creating the folder when the server lacks it.
/// Returns the folder it landed in, or why it did not (the message itself was sent).
fn copy_sent(b: &mut dyn MailBackend, folder: Option<&str>, raw: &[u8]) -> (Option<String>, Option<String>) {
    let Some(folder) = folder else { return (None, None) };
    let flags = Flags { seen: true, ..Flags::default() };
    match b.append(folder, raw, flags) {
        Ok(_) => (Some(folder.to_string()), None),
        Err(first) => {
            if b.create_mailbox(folder).is_ok() && b.append(folder, raw, flags).is_ok() {
                return (Some(folder.to_string()), None);
            }
            (None, Some(first.to_string()))
        }
    }
}

/// Builds a reply (to the sender, or to everyone) or a forward of `original`.
#[allow(clippy::too_many_arguments)]
pub fn compose_response(
    original: &Message,
    raw: &[u8],
    body: &str,
    all: bool,
    forward_to: &[Address],
    from: &Address,
    signature: &str,
    signature_html: &str,
    italian: bool,
    quote: bool,
) -> Draft {
    let e = &original.envelope;
    let me = from.email.to_ascii_lowercase();
    let mut draft = Draft::default();
    let sig = if signature.trim().is_empty() { String::new() } else { format!("\n\n-- \n{}", signature.trim()) };
    // The HTML version: the note, the HTML signature, then the quote or the forwarded text.
    let html_of = |body: &str, tail: &str| -> String {
        if signature_html.trim().is_empty() {
            return String::new();
        }
        format!("{}<br><br>-- <br>{}{}", mime::text_to_html(body.trim_end()), signature_html.trim(), mime::text_to_html(tail))
    };
    if forward_to.is_empty() {
        let reply_to = if original.reply_to.is_empty() { e.from.clone() } else { original.reply_to.clone() };
        draft.to = reply_to.into_iter().filter(|a| a.email.to_ascii_lowercase() != me).collect();
        if draft.to.is_empty() {
            draft.to = e.to.clone();
        }
        if all {
            for a in e.to.iter().chain(&e.cc) {
                let addr = a.email.to_ascii_lowercase();
                if addr != me
                    && !draft.to.iter().any(|t| t.email.eq_ignore_ascii_case(&a.email))
                    && !draft.cc.iter().any(|t| t.email.eq_ignore_ascii_case(&a.email))
                {
                    draft.cc.push(a.clone());
                }
            }
        }
        draft.subject = if e.normalized_subject() == e.subject.trim().to_lowercase() && !e.subject.to_ascii_lowercase().starts_with("re:") {
            format!("Re: {}", e.subject.trim())
        } else {
            e.subject.trim().to_string()
        };
        if !draft.subject.to_ascii_lowercase().starts_with("re:") {
            draft.subject = format!("Re: {}", draft.subject);
        }
        draft.in_reply_to = e.message_id.clone();
        draft.references = e.references.iter().cloned().chain(std::iter::once(e.message_id.clone())).filter(|r| !r.is_empty()).collect();
        let tail = if quote { mime::quote(original, italian) } else { String::new() };
        draft.html = html_of(body, &tail);
        draft.body = format!("{}{}{}", body.trim_end(), sig, tail);
    } else {
        draft.to = forward_to.to_vec();
        let subject = e.subject.trim();
        draft.subject = if subject.to_ascii_lowercase().starts_with("fwd:") || subject.to_ascii_lowercase().starts_with("fw:") {
            subject.to_string()
        } else {
            format!("Fwd: {subject}")
        };
        let header = if italian { "---------- Messaggio inoltrato ----------" } else { "---------- Forwarded message ----------" };
        let (from_l, date_l, subject_l, to_l) = if italian { ("Da", "Data", "Oggetto", "A") } else { ("From", "Date", "Subject", "To") };
        let tail = format!(
            "\n\n{header}\n{from_l}: {}\n{date_l}: {}\n{subject_l}: {}\n{to_l}: {}\n\n{}",
            mailcraft_core::message::join_addresses(&e.from),
            time::long(e.date, italian),
            e.subject,
            mailcraft_core::message::join_addresses(&e.to),
            original.body_text()
        );
        draft.html = html_of(body, &tail);
        draft.body = format!("{}{}{}", body.trim_end(), sig, tail);
        draft.attachments = mime::attachments(raw);
        draft.references = e.references.iter().cloned().chain(std::iter::once(e.message_id.clone())).filter(|r| !r.is_empty()).collect();
    }
    draft
}
