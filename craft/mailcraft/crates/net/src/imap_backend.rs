//! The IMAP side of the backend (SMTP is in `smtp.rs`).

use imap::types::{Fetch, Flag};
use imap::{Client, Connection, Session};
use mailcraft_core::account::{Account, Security};
use mailcraft_core::backend::{FlagChange, MailBackend, Page, SearchQuery};
use mailcraft_core::mailbox::Mailbox;
use mailcraft_core::message::{Envelope, Flags, snippet, time};
use mailcraft_core::{Error, Result, limits, mime};

use crate::tls;

type Imap = Session<imap::Connection>;

/// IMAP + SMTP for one account. Reconnects on the next call after an error.
#[derive(Default)]
pub struct ImapSmtpBackend {
    account: Option<Account>,
    password: String,
    session: Option<Imap>,
    selected: Option<String>,
    delimiter: String,
    has_move: bool,
    has_uidplus: bool,
}

const LIST_ITEMS: &str = "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.700>)";

fn imap_err(e: imap::Error) -> Error {
    match e {
        imap::Error::No(r) => Error::Server(r.information),
        imap::Error::Bad(r) => Error::Server(r.information),
        imap::Error::Io(e) => Error::Connect(e.to_string()),
        imap::Error::ConnectionLost => Error::NotConnected,
        other => Error::Server(other.to_string()),
    }
}

/// The XOAUTH2 SASL answer (the crate base64-encodes it).
struct XOAuth2(String);

impl imap::Authenticator for XOAuth2 {
    type Response = String;

    fn process(&self, _challenge: &[u8]) -> Self::Response {
        self.0.clone()
    }
}

/// Quotes an IMAP string (mailbox names, search text).
fn quoted(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' | '\\' => {
                out.push('\\');
                out.push(c);
            }
            '\r' | '\n' => {}
            _ => out.push(c),
        }
    }
    out.push('"');
    out
}

fn attribute_text<A: std::fmt::Debug>(a: &A) -> String {
    // `Sent` or `Extension("\\Sent")` → `\sent`; roles and \NoSelect are matched on that.
    let dbg = format!("{a:?}");
    let word: String = dbg.trim_end_matches(['"', ')']).rsplit(|c: char| !c.is_ascii_alphabetic()).next().unwrap_or_default().to_string();
    format!("\\{word}")
}

fn flags_of(flags: &[Flag<'_>]) -> Flags {
    let mut f = Flags::default();
    for flag in flags {
        match flag {
            Flag::Seen => f.seen = true,
            Flag::Flagged => f.flagged = true,
            Flag::Answered => f.answered = true,
            Flag::Draft => f.draft = true,
            Flag::Deleted => f.deleted = true,
            _ => {}
        }
    }
    f
}

/// Something readable from the first bytes of a message's text (possibly a multipart body
/// with part headers and encoded parts in it). `headers` gives the top-level encoding.
fn snippet_from_partial(headers: &[u8], bytes: &[u8]) -> String {
    let head = String::from_utf8_lossy(headers).to_ascii_lowercase();
    let encoding_of = |line: &str| -> Option<(bool, bool)> {
        let l = line.trim_start_matches("content-transfer-encoding:").trim();
        Some((l.contains("quoted-printable"), l.contains("base64")))
    };
    let (mut qp, mut b64) = head.lines().find(|l| l.starts_with("content-transfer-encoding:")).and_then(encoding_of).unwrap_or((false, false));
    let multipart = head.lines().any(|l| l.starts_with("content-type:") && l.contains("multipart/"));
    let text = String::from_utf8_lossy(bytes);
    let mut out = String::new();
    let mut in_part_headers = multipart;
    for line in text.lines() {
        let t = line.trim_end();
        if multipart && t.starts_with("--") && t.len() > 2 {
            if !out.trim().is_empty() {
                break;
            }
            in_part_headers = true;
            qp = false;
            b64 = false;
            continue;
        }
        if in_part_headers {
            let lower = t.to_ascii_lowercase();
            if lower.starts_with("content-transfer-encoding:") {
                (qp, b64) = encoding_of(&lower).unwrap_or((false, false));
            }
            if t.is_empty() {
                in_part_headers = false;
            }
            continue;
        }
        out.push_str(t);
        out.push('\n');
        if out.len() > 600 {
            break;
        }
    }
    let decoded = if b64 {
        let compact: String = out.chars().filter(|c| !c.is_whitespace()).collect();
        let whole = compact.len() / 4 * 4;
        mailcraft_core::mime::base64_decode(compact.get(..whole).unwrap_or_default())
            .map(|b| String::from_utf8_lossy(&b).into_owned())
            .unwrap_or_default()
    } else if qp {
        decode_qp(&out)
    } else {
        out
    };
    snippet(&decoded)
}

fn decode_qp(text: &str) -> String {
    let mut bytes = Vec::with_capacity(text.len());
    let src = text.as_bytes();
    let mut i = 0;
    while i < src.len() {
        let c = src[i];
        if c == b'=' {
            if src.get(i + 1) == Some(&b'\n') {
                i += 2;
                continue;
            }
            if let (Some(h), Some(l)) = (src.get(i + 1), src.get(i + 2))
                && let (Some(hv), Some(lv)) = ((*h as char).to_digit(16), (*l as char).to_digit(16))
            {
                bytes.push((hv * 16 + lv) as u8);
                i += 3;
                continue;
            }
        }
        bytes.push(c);
        i += 1;
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn envelope_of(mailbox: &str, f: &Fetch<'_>) -> Option<Envelope> {
    let uid = f.uid?;
    let mut e = f.header().and_then(|h| mime::parse_headers(h).ok()).unwrap_or_default();
    e.uid = uid;
    e.mailbox = mailbox.to_string();
    e.flags = flags_of(f.flags());
    e.size = f.size.unwrap_or(0);
    if let (Some(text), Some(header)) = (f.text(), f.header()) {
        e.snippet = snippet_from_partial(header, text);
    }
    Some(e)
}

/// `dd-Mon-yyyy` for SEARCH SINCE.
fn imap_date(ts: i64) -> String {
    let (y, m, d, ..) = time::civil(ts);
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    format!("{d}-{}-{y}", MONTHS[(m as usize).clamp(1, 12) - 1])
}

fn uid_set(uids: &[u32]) -> String {
    uids.iter().take(10_000).map(u32::to_string).collect::<Vec<_>>().join(",")
}

/// A client that has read the server's greeting, over TLS unless the account says otherwise.
fn connect(account: &Account) -> std::result::Result<Client<Connection>, String> {
    let host = account.imap.host.as_str();
    let mut tcp = std::net::TcpStream::connect((host, account.imap.port)).map_err(|e| e.to_string())?;
    let stream: Connection = match account.imap.security {
        Security::Tls => Box::new(tls::wrap(host, tcp, account.trust_invalid_certs)?),
        Security::StartTls => {
            tls::starttls(&mut tcp)?;
            Box::new(tls::wrap(host, tcp, account.trust_invalid_certs)?)
        }
        Security::None => Box::new(tcp),
    };
    let mut client = Client::new(stream);
    if account.imap.security == Security::StartTls {
        // Read before the upgrade, in `tls::starttls`.
        client.greeting_read = true;
    } else {
        client.read_greeting().map_err(|e| e.to_string())?;
    }
    Ok(client)
}

impl ImapSmtpBackend {
    fn session(&mut self) -> Result<&mut Imap> {
        if self.session.is_none() {
            let (account, password) = match &self.account {
                Some(a) => (a.clone(), self.password.clone()),
                None => return Err(Error::NotConnected),
            };
            self.open(&account, &password)?;
        }
        self.session.as_mut().ok_or(Error::NotConnected)
    }

    fn open(&mut self, account: &Account, password: &str) -> Result<()> {
        let client = connect(account).map_err(|e| Error::Connect(format!("{}: {e}", account.imap.describe())))?;
        let auth_err = |e: imap::Error| match e {
            imap::Error::No(r) => Error::Auth(r.information),
            imap::Error::Bad(r) => Error::Auth(r.information),
            other => Error::Auth(other.to_string()),
        };
        // OAuth accounts present their access token with SASL XOAUTH2; the rest log in.
        let mut session = if account.oauth_provider().is_some() {
            let sasl = XOAuth2(mailcraft_core::oauth::xoauth2(account.login(), password));
            client.authenticate("XOAUTH2", &sasl).map_err(|(e, _)| auth_err(e))?
        } else {
            client.login(account.login(), password).map_err(|(e, _)| auth_err(e))?
        };
        let caps = session.capabilities().map_err(imap_err)?;
        self.has_move = caps.has_str("MOVE");
        self.has_uidplus = caps.has_str("UIDPLUS");
        self.session = Some(session);
        self.selected = None;
        Ok(())
    }

    /// Runs a call; on a lost connection, drops the session so the next call reconnects.
    fn call<T>(&mut self, f: impl FnOnce(&mut Imap) -> imap::error::Result<T>) -> Result<T> {
        let session = self.session()?;
        match f(session) {
            Ok(v) => Ok(v),
            Err(e) => {
                let err = imap_err(e);
                if matches!(err, Error::NotConnected | Error::Connect(_)) {
                    self.session = None;
                    self.selected = None;
                }
                Err(err)
            }
        }
    }

    fn select(&mut self, mailbox: &str) -> Result<u32> {
        let exists = self.call(|s| s.select(mailbox).map(|m| m.exists))?;
        self.selected = Some(mailbox.to_string());
        Ok(exists)
    }

    fn status(&mut self, path: &str) -> Option<(u32, u32)> {
        let lines = self.call(|s| s.run_command_and_read_response(format!("STATUS {} (MESSAGES UNSEEN)", quoted(path)))).ok()?;
        let text = String::from_utf8_lossy(&lines);
        let after = |key: &str| {
            text.find(key)
                .and_then(|i| text.get(i + key.len()..))
                .and_then(|r| r.trim_start().split(|c: char| !c.is_ascii_digit()).next()?.parse::<u32>().ok())
        };
        Some((after("MESSAGES").unwrap_or(0), after("UNSEEN").unwrap_or(0)))
    }

    fn mark_deleted_and_expunge(&mut self, uids: &[u32]) -> Result<()> {
        let set = uid_set(uids);
        self.call(|s| s.uid_store(&set, "+FLAGS.SILENT (\\Deleted)").map(|_| ()))?;
        if self.has_uidplus { self.call(|s| s.uid_expunge(&set).map(|_| ())) } else { self.call(|s| s.expunge().map(|_| ())) }
    }
}

impl MailBackend for ImapSmtpBackend {
    fn connect(&mut self, account: &Account, password: &str) -> Result<()> {
        self.disconnect();
        self.open(account, password)?;
        self.account = Some(account.clone());
        self.password = password.to_string();
        Ok(())
    }

    fn disconnect(&mut self) {
        if let Some(mut s) = self.session.take() {
            let _ = s.logout();
        }
        self.selected = None;
    }

    fn mailboxes(&mut self) -> Result<(Vec<Mailbox>, String)> {
        let names: Vec<(String, Vec<String>, Option<String>)> = self.call(|s| {
            s.list(Some(""), Some("*")).map(|names| {
                names
                    .iter()
                    .take(limits::MAX_MAILBOXES)
                    .map(|n| (n.name().to_string(), n.attributes().iter().map(attribute_text).collect(), n.delimiter().map(str::to_string)))
                    .collect()
            })
        })?;
        let delimiter = names.iter().find_map(|(_, _, d)| d.clone()).unwrap_or_else(|| "/".to_string());
        self.delimiter = delimiter.clone();
        // Subscriptions (LSUB): a server that answers nothing, or does not support it, shows
        // every folder; otherwise only the subscribed ones are shown in the navigation.
        let subscribed: Vec<String> = self
            .call(|s| s.lsub(Some(""), Some("*")).map(|l| l.iter().take(limits::MAX_MAILBOXES).map(|n| n.name().to_string()).collect()))
            .unwrap_or_default();
        let mut out = Vec::with_capacity(names.len());
        for (i, (path, attrs, delim)) in names.into_iter().enumerate() {
            let mut m = Mailbox::new(&path, &attrs, delim.as_deref());
            if !subscribed.is_empty() {
                m.subscribed = path.eq_ignore_ascii_case("INBOX") || subscribed.contains(&path);
            }
            if !m.no_select
                && i < 60
                && let Some((total, unread)) = self.status(&path)
            {
                m.total = total;
                m.unread = unread;
            }
            out.push(m);
        }
        if !out.iter().any(|m| m.path.eq_ignore_ascii_case("INBOX")) {
            out.insert(0, Mailbox::new("INBOX", &[], None));
        }
        Ok((out, delimiter))
    }

    fn envelopes(&mut self, mailbox: &str, page: Page) -> Result<Vec<Envelope>> {
        let exists = self.select(mailbox)?;
        let end = exists as i64 - page.offset as i64;
        if end < 1 {
            return Ok(Vec::new());
        }
        let start = (end - page.limit as i64 + 1).max(1);
        let mut list: Vec<Envelope> =
            self.call(|s| s.fetch(format!("{start}:{end}"), LIST_ITEMS).map(|f| f.iter().filter_map(|x| envelope_of(mailbox, x)).collect()))?;
        list.sort_by_key(|e| std::cmp::Reverse(e.uid));
        Ok(list)
    }

    fn envelopes_by_uid(&mut self, mailbox: &str, uids: &[u32]) -> Result<Vec<Envelope>> {
        if uids.is_empty() {
            return Ok(Vec::new());
        }
        self.select(mailbox)?;
        let set = uid_set(uids);
        let mut list: Vec<Envelope> =
            self.call(|s| s.uid_fetch(&set, LIST_ITEMS).map(|f| f.iter().filter_map(|x| envelope_of(mailbox, x)).collect()))?;
        list.sort_by_key(|e| std::cmp::Reverse(e.uid));
        Ok(list)
    }

    fn fetch(&mut self, mailbox: &str, uid: u32) -> Result<Vec<u8>> {
        self.select(mailbox)?;
        let raw = self.call(|s| {
            s.uid_fetch(uid.to_string(), "(UID BODY.PEEK[])")
                .map(|f| f.iter().find(|x| x.uid == Some(uid)).and_then(|x| x.body().map(<[u8]>::to_vec)))
        })?;
        let raw = raw.ok_or(Error::NoMessage(uid))?;
        if raw.len() > limits::MAX_RAW_MESSAGE {
            return Err(Error::Message(format!("message {uid} is larger than {} MB", limits::MAX_RAW_MESSAGE / 1024 / 1024)));
        }
        Ok(raw)
    }

    fn set_flag(&mut self, mailbox: &str, uids: &[u32], change: FlagChange) -> Result<()> {
        if uids.is_empty() {
            return Ok(());
        }
        self.select(mailbox)?;
        let (flag, on) = match change {
            FlagChange::Seen(v) => ("\\Seen", v),
            FlagChange::Flagged(v) => ("\\Flagged", v),
            FlagChange::Answered(v) => ("\\Answered", v),
            FlagChange::Draft(v) => ("\\Draft", v),
        };
        let query = format!("{}FLAGS.SILENT ({flag})", if on { "+" } else { "-" });
        let set = uid_set(uids);
        self.call(|s| s.uid_store(&set, &query).map(|_| ()))
    }

    fn move_messages(&mut self, mailbox: &str, uids: &[u32], to: &str) -> Result<()> {
        if uids.is_empty() {
            return Ok(());
        }
        self.select(mailbox)?;
        let set = uid_set(uids);
        if self.has_move {
            return self.call(|s| s.uid_mv(&set, to));
        }
        self.call(|s| s.uid_copy(&set, to))?;
        self.mark_deleted_and_expunge(uids)
    }

    fn delete(&mut self, mailbox: &str, uids: &[u32]) -> Result<()> {
        if uids.is_empty() {
            return Ok(());
        }
        self.select(mailbox)?;
        self.mark_deleted_and_expunge(uids)
    }

    fn append(&mut self, mailbox: &str, raw: &[u8], flags: Flags) -> Result<Option<u32>> {
        let mut list: Vec<Flag<'static>> = Vec::new();
        if flags.seen {
            list.push(Flag::Seen);
        }
        if flags.flagged {
            list.push(Flag::Flagged);
        }
        if flags.draft {
            list.push(Flag::Draft);
        }
        if flags.answered {
            list.push(Flag::Answered);
        }
        self.call(|s| {
            s.append(mailbox, raw).flags(list).finish().map(|a| {
                a.uids.and_then(|u| {
                    u.into_iter().next().map(|r| match r {
                        imap_proto::UidSetMember::Uid(v) => v,
                        imap_proto::UidSetMember::UidRange(range) => *range.start(),
                    })
                })
            })
        })
    }

    fn search(&mut self, mailbox: &str, query: &SearchQuery, page: Page) -> Result<Vec<Envelope>> {
        self.select(mailbox)?;
        let ascii = [&query.text, &query.from, &query.to, &query.subject].iter().all(|s| s.is_ascii());
        let mut criteria: Vec<String> = Vec::new();
        if query.unread_only {
            criteria.push("UNSEEN".into());
        }
        if query.flagged_only {
            criteria.push("FLAGGED".into());
        }
        if let Some(d) = query.since_days {
            criteria.push(format!("SINCE {}", imap_date(time::now() - i64::from(d) * 86_400)));
        }
        if ascii {
            for (key, value) in [("FROM", &query.from), ("TO", &query.to), ("SUBJECT", &query.subject)] {
                if !value.is_empty() {
                    criteria.push(format!("{key} {}", quoted(value)));
                }
            }
            for word in query.text.split_whitespace().take(20) {
                criteria.push(format!("TEXT {}", quoted(word)));
            }
        }
        let crit = if criteria.is_empty() { "ALL".to_string() } else { criteria.join(" ") };
        let mut uids: Vec<u32> = self.call(|s| s.uid_search(&crit).map(|set| set.into_iter().collect()))?;
        uids.sort_unstable_by(|a, b| b.cmp(a));
        let wanted: Vec<u32> =
            uids.into_iter().skip(if ascii { page.offset } else { 0 }).take(if ascii { page.limit } else { limits::MAX_PAGE }).collect();
        let mut list = self.envelopes_by_uid(mailbox, &wanted)?;
        if !ascii {
            list.retain(|e| query.matches(e));
            list = list.into_iter().skip(page.offset).take(page.limit).collect();
        } else if query.with_attachments {
            list.retain(|e| e.has_attachments);
        }
        Ok(list)
    }

    fn create_mailbox(&mut self, path: &str) -> Result<()> {
        self.call(|s| s.create(path))
    }

    fn subscribe(&mut self, path: &str, subscribed: bool) -> Result<()> {
        self.call(|s| if subscribed { s.subscribe(path) } else { s.unsubscribe(path) })
    }

    fn send(&mut self, from: &str, to: &[String], raw: &[u8]) -> Result<()> {
        let account = self.account.clone().ok_or(Error::NotConnected)?;
        crate::smtp::send_raw(&account, &self.password, from, to, raw)
    }

    fn test_smtp(&mut self) -> Result<()> {
        let account = self.account.clone().ok_or(Error::NotConnected)?;
        crate::smtp::test_connection(&account, &self.password)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers() {
        assert_eq!(quoted("a\"b\\c"), "\"a\\\"b\\\\c\"");
        assert_eq!(imap_date(time::from_civil(2026, 10, 12, 0, 0, 0)), "12-Oct-2026");
        let partial = b"--b1\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\nCiao =C3=A8 tutto=\r\n ok\r\n--b1\r\nContent-Type: text/html\r\n\r\n<p>x</p>";
        assert_eq!(snippet_from_partial(b"Content-Type: multipart/mixed; boundary=b1\r\n", partial), "Ciao è tutto ok");
        assert_eq!(snippet_from_partial(b"Content-Type: text/plain\r\n", b"Plain body\r\nsecond line"), "Plain body second line");
        let b64 = mailcraft_core::mime::base64_encode("Ciao è tutto ok, anche in base64".as_bytes());
        assert_eq!(
            snippet_from_partial(b"Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n", b64.as_bytes()),
            "Ciao è tutto ok, anche in base64"
        );
        #[derive(Debug)]
        #[allow(dead_code)]
        enum A {
            NoSelect,
            Extension(&'static str),
        }
        assert_eq!(attribute_text(&A::NoSelect), "\\NoSelect");
        assert_eq!(attribute_text(&A::Extension("\\Sent")), "\\Sent");
        assert!(flags_of(&[Flag::Seen, Flag::Flagged]).flagged);
    }
}
