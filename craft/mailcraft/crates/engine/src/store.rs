//! The local cache: mailboxes and envelopes per account, opened messages, and its files on
//! disk so the client opens instantly and works offline.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};

use mailcraft_core::account::slug;
use mailcraft_core::backend::FlagChange;
use mailcraft_core::mailbox::{Mailbox, Role};
use mailcraft_core::message::{Address, Envelope, Message};
use mailcraft_core::{Result, limits};
use serde::{Deserialize, Serialize};

/// What is cached for one account.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AccountState {
    pub mailboxes: Vec<Mailbox>,
    pub delimiter: String,
    /// Everyone seen in headers and written to, by lower-case address: the address book that
    /// completes recipients.
    pub contacts: BTreeMap<String, Contact>,
    /// Envelopes per mailbox path, newest first.
    pub envelopes: BTreeMap<String, Vec<Envelope>>,
    /// Unix time of the last successful mailbox list.
    pub last_sync: i64,
    #[serde(skip)]
    pub error: Option<String>,
    #[serde(skip)]
    pub messages: HashMap<(String, u32), Message>,
    #[serde(skip)]
    pub raw: HashMap<(String, u32), Vec<u8>>,
    #[serde(skip)]
    pub dirty: bool,
}

impl AccountState {
    pub fn mailbox(&self, path: &str) -> Option<&Mailbox> {
        self.mailboxes.iter().find(|m| m.path == path)
    }

    /// The mailbox with a role, by SPECIAL-USE/name detection.
    pub fn by_role(&self, role: Role) -> Option<&Mailbox> {
        self.mailboxes.iter().find(|m| m.role == role && !m.no_select)
    }

    pub fn list(&self, path: &str) -> &[Envelope] {
        self.envelopes.get(path).map(Vec::as_slice).unwrap_or_default()
    }

    pub fn envelope(&self, path: &str, uid: u32) -> Option<&Envelope> {
        self.envelopes.get(path).and_then(|l| l.iter().find(|e| e.uid == uid))
    }

    /// Merges a fetched page: replaces the window, keeps the rest, sorts newest first.
    /// Remembers the people in these envelopes (sender, recipients) for recipient completion.
    pub fn note_envelopes(&mut self, list: &[Envelope]) {
        for e in list {
            let when = e.date;
            self.note_addresses(e.from.iter().chain(&e.to).chain(&e.cc), when, 1);
        }
    }

    /// Remembers addresses written to or seen; `weight` ranks what the person wrote to higher.
    pub fn note_addresses<'a>(&mut self, addrs: impl Iterator<Item = &'a Address>, when: i64, weight: u32) {
        for a in addrs {
            let key = a.email.trim().to_ascii_lowercase();
            if !mailcraft_core::account::is_email(&key) || key.contains("noreply") || key.contains("no-reply") {
                continue;
            }
            let c = self.contacts.entry(key.clone()).or_insert_with(|| Contact { email: key.clone(), ..Contact::default() });
            if c.name.is_empty() && !a.name.trim().is_empty() {
                c.name = a.name.trim().to_string();
            }
            c.count = c.count.saturating_add(weight);
            c.last = c.last.max(when);
        }
        if self.contacts.len() > MAX_CONTACTS {
            let mut by_use: Vec<(u32, i64, String)> = self.contacts.values().map(|c| (c.count, c.last, c.email.clone())).collect();
            by_use.sort();
            for (_, _, email) in by_use.into_iter().take(self.contacts.len() - MAX_CONTACTS) {
                self.contacts.remove(&email);
            }
        }
        self.dirty = true;
    }

    pub fn merge_page(&mut self, path: &str, offset: usize, limit: usize, page: Vec<Envelope>) {
        self.note_envelopes(&page);
        let list = self.envelopes.entry(path.to_string()).or_default();
        let uids: Vec<u32> = page.iter().map(|e| e.uid).collect();
        list.retain(|e| !uids.contains(&e.uid));
        // The server page is authoritative for its window: drop what was there and is gone.
        if offset == 0 && !page.is_empty() {
            let newest_in_page = page.iter().map(|e| e.uid).max().unwrap_or(0);
            let oldest_in_page = page.iter().map(|e| e.uid).min().unwrap_or(0);
            list.retain(|e| e.uid < oldest_in_page || e.uid > newest_in_page);
            if page.len() < limit {
                // The mailbox has fewer messages than one page: everything else is stale.
                list.clear();
            }
        } else if page.is_empty() && offset == 0 {
            list.clear();
        }
        list.extend(page);
        list.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
        list.truncate(limits::MAX_ENVELOPES);
        self.dirty = true;
    }

    pub fn update_envelopes(&mut self, path: &str, fresh: Vec<Envelope>) {
        self.note_envelopes(&fresh);
        let list = self.envelopes.entry(path.to_string()).or_default();
        for f in fresh {
            match list.iter_mut().find(|e| e.uid == f.uid) {
                Some(slot) => *slot = f,
                None => list.push(f),
            }
        }
        list.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
        self.dirty = true;
    }

    pub fn apply_flag(&mut self, path: &str, uids: &[u32], change: FlagChange) {
        let mut delta_unread: i64 = 0;
        if let Some(list) = self.envelopes.get_mut(path) {
            for e in list.iter_mut().filter(|e| uids.contains(&e.uid)) {
                let was_unread = !e.flags.seen;
                change.apply(&mut e.flags);
                if was_unread != !e.flags.seen {
                    delta_unread += if e.flags.seen { -1 } else { 1 };
                }
            }
        }
        for m in self.messages.iter_mut().filter(|((mb, uid), _)| mb == path && uids.contains(uid)) {
            change.apply(&mut m.1.envelope.flags);
        }
        if let Some(m) = self.mailboxes.iter_mut().find(|m| m.path == path) {
            m.unread = (i64::from(m.unread) + delta_unread).clamp(0, i64::from(u32::MAX)) as u32;
        }
        self.dirty = true;
    }

    /// Removes messages from a mailbox's cache (moved or deleted) and fixes the counts.
    pub fn remove(&mut self, path: &str, uids: &[u32]) -> Vec<Envelope> {
        let mut removed = Vec::new();
        if let Some(list) = self.envelopes.get_mut(path) {
            let (gone, kept): (Vec<Envelope>, Vec<Envelope>) = list.drain(..).partition(|e| uids.contains(&e.uid));
            *list = kept;
            removed = gone;
        }
        let unread = removed.iter().filter(|e| !e.flags.seen).count() as u32;
        if let Some(m) = self.mailboxes.iter_mut().find(|m| m.path == path) {
            m.total = m.total.saturating_sub(removed.len() as u32);
            m.unread = m.unread.saturating_sub(unread);
        }
        self.messages.retain(|(mb, uid), _| !(mb == path && uids.contains(uid)));
        self.raw.retain(|(mb, uid), _| !(mb == path && uids.contains(uid)));
        self.dirty = true;
        removed
    }

    pub fn put_message(&mut self, path: &str, uid: u32, raw: Vec<u8>, message: Message) {
        if self.messages.len() >= 200 {
            self.messages.clear();
            self.raw.clear();
        }
        self.messages.insert((path.to_string(), uid), message);
        self.raw.insert((path.to_string(), uid), raw);
    }

    pub fn message(&self, path: &str, uid: u32) -> Option<&Message> {
        self.messages.get(&(path.to_string(), uid))
    }

    /// Conversations: envelopes grouped by thread key, newest first, each with its members
    /// (newest first) and unread count.
    pub fn conversations(&self, path: &str) -> Vec<Conversation> {
        let mut by_key: Vec<Conversation> = Vec::new();
        let mut index: HashMap<String, usize> = HashMap::new();
        for e in self.list(path) {
            let key = e.thread_key();
            match index.get(&key) {
                Some(&i) => {
                    if let Some(c) = by_key.get_mut(i) {
                        c.members.push(e.uid);
                        c.unread += u32::from(!e.flags.seen);
                        c.flagged |= e.flags.flagged;
                        c.has_attachments |= e.has_attachments;
                        if !c.senders.contains(&e.sender().to_string()) {
                            c.senders.push(e.sender().to_string());
                        }
                    }
                }
                None => {
                    index.insert(key, by_key.len());
                    by_key.push(Conversation {
                        latest: e.uid,
                        members: vec![e.uid],
                        unread: u32::from(!e.flags.seen),
                        flagged: e.flags.flagged,
                        has_attachments: e.has_attachments,
                        senders: vec![e.sender().to_string()],
                    });
                }
            }
        }
        by_key
    }

    fn file(dir: &Path, path: &str) -> PathBuf {
        dir.join(format!("{}.json", slug(path)))
    }

    pub fn save(&mut self, dir: &Path) -> Result<()> {
        std::fs::create_dir_all(dir).map_err(|e| mailcraft_core::Error::Io(format!("{}: {e}", dir.display())))?;
        let meta =
            serde_json::json!({"mailboxes": self.mailboxes, "delimiter": self.delimiter, "last_sync": self.last_sync, "contacts": self.contacts});
        std::fs::write(dir.join("mailboxes.json"), meta.to_string()).map_err(|e| mailcraft_core::Error::Io(e.to_string()))?;
        for (path, list) in &self.envelopes {
            let keep: Vec<&Envelope> = list.iter().take(limits::MAX_PAGE * 4).collect();
            let text = serde_json::to_string(&keep).unwrap_or_else(|_| "[]".into());
            std::fs::write(Self::file(dir, path), text).map_err(|e| mailcraft_core::Error::Io(e.to_string()))?;
        }
        self.dirty = false;
        Ok(())
    }

    pub fn load(dir: &Path) -> Self {
        let mut state = Self::default();
        if let Ok(text) = std::fs::read_to_string(dir.join("mailboxes.json"))
            && let Ok(v) = serde_json::from_str::<serde_json::Value>(&text)
        {
            state.mailboxes = serde_json::from_value(v.get("mailboxes").cloned().unwrap_or_default()).unwrap_or_default();
            state.delimiter = v.get("delimiter").and_then(|d| d.as_str()).unwrap_or("/").to_string();
            state.last_sync = v.get("last_sync").and_then(serde_json::Value::as_i64).unwrap_or(0);
            state.contacts = serde_json::from_value(v.get("contacts").cloned().unwrap_or_default()).unwrap_or_default();
            if state.contacts.len() > MAX_CONTACTS {
                state.contacts = BTreeMap::new();
            }
        }
        let paths: Vec<String> = state.mailboxes.iter().map(|m| m.path.clone()).collect();
        for path in paths {
            if let Ok(text) = std::fs::read_to_string(Self::file(dir, &path))
                && let Ok(mut list) = serde_json::from_str::<Vec<Envelope>>(&text)
            {
                list.truncate(limits::MAX_ENVELOPES);
                list.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
                state.envelopes.insert(path, list);
            }
        }
        state
    }
}

/// Contacts kept per account.
pub const MAX_CONTACTS: usize = 5_000;

/// Someone seen in a message or written to.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Contact {
    pub name: String,
    pub email: String,
    /// How often seen; writing to someone counts more than seeing them.
    pub count: u32,
    /// Unix time of the latest message with them.
    pub last: i64,
}

impl Contact {
    /// Matches a typed prefix against the address, the name, or any word of the name.
    pub fn matches(&self, query: &str) -> bool {
        let q = query.trim().to_ascii_lowercase();
        if q.is_empty() {
            return true;
        }
        let name = self.name.to_ascii_lowercase();
        self.email.starts_with(&q)
            || self.email.contains(&format!("@{q}"))
            || name.starts_with(&q)
            || name.split([' ', '.', '-']).any(|w| w.starts_with(&q))
    }
}

/// A conversation in the list.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Conversation {
    /// The newest member.
    pub latest: u32,
    pub members: Vec<u32>,
    pub unread: u32,
    pub flagged: bool,
    pub has_attachments: bool,
    pub senders: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use mailcraft_core::message::{Address, Flags};

    fn env(uid: u32, subject: &str, seen: bool) -> Envelope {
        Envelope {
            uid,
            subject: subject.into(),
            message_id: format!("<{uid}@x>"),
            from: vec![Address::new("A", "a@x.it")],
            flags: Flags { seen, ..Flags::default() },
            ..Envelope::default()
        }
    }

    #[test]
    fn pages_merge_flags_apply_and_threads_group() {
        let mut s =
            AccountState { mailboxes: vec![Mailbox { path: "INBOX".into(), unread: 2, total: 3, ..Mailbox::default() }], ..AccountState::default() };
        s.merge_page("INBOX", 0, 50, vec![env(3, "c", false), env(2, "b", false), env(1, "a", true)]);
        assert_eq!(s.list("INBOX").iter().map(|e| e.uid).collect::<Vec<_>>(), vec![3, 2, 1]);
        s.merge_page("INBOX", 0, 2, vec![env(5, "e", false), env(4, "d", true)]);
        assert_eq!(s.list("INBOX").len(), 5);
        s.apply_flag("INBOX", &[3, 2], FlagChange::Seen(true));
        assert_eq!(s.mailboxes[0].unread, 0);
        let removed = s.remove("INBOX", &[5]);
        assert_eq!(removed.len(), 1);
        assert_eq!((s.mailboxes[0].total, s.mailboxes[0].unread), (2, 0));
        let mut reply = env(6, "Re: e", false);
        reply.references = vec!["<4@x>".into()];
        s.update_envelopes("INBOX", vec![reply]);
        let convs = s.conversations("INBOX");
        assert_eq!(convs[0].members, vec![6, 4]);
        assert_eq!(convs[0].unread, 1);
        let dir = std::env::temp_dir().join(format!("mailcraft-store-{}", std::process::id()));
        s.save(&dir).unwrap();
        let back = AccountState::load(&dir);
        assert_eq!(back.list("INBOX").len(), s.list("INBOX").len());
        assert_eq!(back.mailboxes.len(), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
