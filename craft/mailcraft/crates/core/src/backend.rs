//! The transport every mail backend implements (IMAP+SMTP in `mailcraft-net`, the mock here).
//! Calls block; the engine runs them on a worker thread.

use crate::account::Account;
use crate::mailbox::Mailbox;
use crate::message::{Envelope, Flags};
use crate::{Result, limits};

/// A window into a mailbox, newest first.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Page {
    pub offset: usize,
    pub limit: usize,
}

impl Page {
    pub fn new(offset: usize, limit: usize) -> Self {
        Self { offset, limit: limit.clamp(1, limits::MAX_PAGE) }
    }
}

impl Default for Page {
    fn default() -> Self {
        Self::new(0, 50)
    }
}

/// A search over one mailbox.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SearchQuery {
    /// Words looked for in subject, sender and body.
    pub text: String,
    pub from: String,
    pub to: String,
    pub subject: String,
    pub unread_only: bool,
    pub flagged_only: bool,
    pub with_attachments: bool,
    /// Only messages newer than this many days.
    pub since_days: Option<u32>,
}

impl SearchQuery {
    /// Gmail-style operators: `from:anna subject:fattura is:unread is:starred has:attachment
    /// newer_than:7d` plus free words.
    pub fn parse(text: &str) -> Self {
        let mut q = Self::default();
        let mut words = Vec::new();
        for tok in text.split_whitespace().take(100) {
            let lower = tok.to_ascii_lowercase();
            match lower.split_once(':') {
                Some(("from" | "da", v)) => q.from = v.to_string(),
                Some(("to" | "a", v)) => q.to = v.to_string(),
                Some(("subject" | "oggetto", v)) => q.subject = v.to_string(),
                Some(("is", "unread" | "nonletto" | "nonletti")) => q.unread_only = true,
                Some(("is", "starred" | "speciale" | "flagged")) => q.flagged_only = true,
                Some(("has", "attachment" | "allegato" | "allegati")) => q.with_attachments = true,
                Some(("newer_than" | "newer", v)) => q.since_days = parse_days(v),
                _ => words.push(tok.to_string()),
            }
        }
        q.text = words.join(" ");
        q
    }

    pub fn is_empty(&self) -> bool {
        self == &Self::default()
    }

    /// Whether an envelope matches the parts a client can check locally (text matches subject,
    /// sender and snippet).
    pub fn matches(&self, e: &Envelope) -> bool {
        let has = |hay: &str, needle: &str| needle.is_empty() || hay.to_lowercase().contains(&needle.to_lowercase());
        let people = |list: &[crate::message::Address]| list.iter().map(|a| format!("{} {}", a.name, a.email)).collect::<Vec<_>>().join(" ");
        (!self.unread_only || !e.flags.seen)
            && (!self.flagged_only || e.flags.flagged)
            && (!self.with_attachments || e.has_attachments)
            && has(&people(&e.from), &self.from)
            && has(&people(&e.to), &self.to)
            && has(&e.subject, &self.subject)
            && self.text.split_whitespace().all(|w| has(&format!("{} {} {}", e.subject, people(&e.from), e.snippet), w))
            && self.since_days.is_none_or(|d| e.date == 0 || e.date >= crate::message::time::now() - i64::from(d) * 86_400)
    }
}

fn parse_days(v: &str) -> Option<u32> {
    let (num, unit) = v.split_at(v.find(|c: char| !c.is_ascii_digit()).unwrap_or(v.len()));
    let n: u32 = num.parse().ok()?;
    Some(match unit {
        "" | "d" => n,
        "w" => n.saturating_mul(7),
        "m" => n.saturating_mul(30),
        "y" => n.saturating_mul(365),
        _ => return None,
    })
}

/// A flag to set or clear on messages.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FlagChange {
    Seen(bool),
    Flagged(bool),
    Answered(bool),
    Draft(bool),
}

impl FlagChange {
    pub fn apply(self, f: &mut Flags) {
        match self {
            Self::Seen(v) => f.seen = v,
            Self::Flagged(v) => f.flagged = v,
            Self::Answered(v) => f.answered = v,
            Self::Draft(v) => f.draft = v,
        }
    }
}

/// A mail transport for one account. Implementations keep their own connection state.
pub trait MailBackend: Send {
    /// Connects and logs in. Called again after a failure.
    fn connect(&mut self, account: &Account, password: &str) -> Result<()>;
    fn disconnect(&mut self);
    /// Every folder with roles, counts and the hierarchy delimiter.
    fn mailboxes(&mut self) -> Result<(Vec<Mailbox>, String)>;
    /// A page of envelopes, newest first.
    fn envelopes(&mut self, mailbox: &str, page: Page) -> Result<Vec<Envelope>>;
    /// Envelopes of the given uids (for refreshing flags or cache misses).
    fn envelopes_by_uid(&mut self, mailbox: &str, uids: &[u32]) -> Result<Vec<Envelope>>;
    /// The raw RFC 822 message.
    fn fetch(&mut self, mailbox: &str, uid: u32) -> Result<Vec<u8>>;
    fn set_flag(&mut self, mailbox: &str, uids: &[u32], change: FlagChange) -> Result<()>;
    fn move_messages(&mut self, mailbox: &str, uids: &[u32], to: &str) -> Result<()>;
    /// Deletes for good (flag + expunge).
    fn delete(&mut self, mailbox: &str, uids: &[u32]) -> Result<()>;
    /// Stores a message (a draft, a copy of sent mail); returns the new uid when known.
    fn append(&mut self, mailbox: &str, raw: &[u8], flags: Flags) -> Result<Option<u32>>;
    fn search(&mut self, mailbox: &str, query: &SearchQuery, page: Page) -> Result<Vec<Envelope>>;
    fn create_mailbox(&mut self, path: &str) -> Result<()>;
    /// Subscribes to (or unsubscribes from) a folder: what the navigation shows.
    fn subscribe(&mut self, path: &str, subscribed: bool) -> Result<()>;
    /// Sends a raw message through SMTP.
    fn send(&mut self, from: &str, to: &[String], raw: &[u8]) -> Result<()>;
    /// Checks that the SMTP server accepts the account (connection and login), without sending.
    fn test_smtp(&mut self) -> Result<()> {
        Ok(())
    }
}

/// Makes a backend for an account (the engine calls it once per account, lazily).
pub type BackendFactory = Box<dyn Fn(&Account) -> Box<dyn MailBackend> + Send + Sync>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queries_parse_operators() {
        let q = SearchQuery::parse("fattura from:aruba is:unread has:attachment newer_than:2w ottobre");
        assert_eq!(q.text, "fattura ottobre");
        assert_eq!(q.from, "aruba");
        assert!(q.unread_only && q.with_attachments && !q.flagged_only);
        assert_eq!(q.since_days, Some(14));
        let e = Envelope {
            subject: "Fattura ottobre".into(),
            from: vec![crate::message::Address::new("Aruba", "fatture@aruba.it")],
            has_attachments: true,
            date: crate::message::time::now(),
            ..Default::default()
        };
        assert!(q.matches(&e));
        assert!(!SearchQuery::parse("is:starred").matches(&e));
        assert_eq!(Page::new(0, 10_000).limit, limits::MAX_PAGE);
    }
}
