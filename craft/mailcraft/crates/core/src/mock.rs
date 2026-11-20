//! An in-memory backend: the demo mailbox of the interface, and what every test runs against.
//! Instances made from the same [`MockBackend`] share state (clone it), so a test can inspect
//! what the engine sent.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use crate::account::Account;
use crate::backend::{FlagChange, MailBackend, Page, SearchQuery};
use crate::mailbox::Mailbox;
use crate::message::{Address, Draft, DraftAttachment, Envelope, Flags, time};
use crate::{Error, Result, limits, mime};

#[derive(Clone, Debug)]
struct Stored {
    uid: u32,
    flags: Flags,
    raw: Vec<u8>,
}

#[derive(Clone, Debug, Default)]
struct Folder {
    attributes: Vec<String>,
    next_uid: u32,
    messages: Vec<Stored>,
    unsubscribed: bool,
}

#[derive(Debug, Default)]
struct State {
    folders: BTreeMap<String, Folder>,
    password: Option<String>,
    fail_connect: Option<String>,
    connected: bool,
    sent: Vec<(String, Vec<String>, Vec<u8>)>,
    /// Every backend call, for tests (`"move INBOX 3 -> Archive"`).
    log: Vec<String>,
}

/// The mock transport.
#[derive(Clone, Debug, Default)]
pub struct MockBackend {
    state: Arc<Mutex<State>>,
}

impl MockBackend {
    /// An empty server with the standard folders that accepts any password.
    pub fn new() -> Self {
        let b = Self::default();
        b.with(|s| {
            for (name, attr) in
                [("INBOX", ""), ("Sent", "\\Sent"), ("Drafts", "\\Drafts"), ("Trash", "\\Trash"), ("Junk", "\\Junk"), ("Archive", "\\Archive")]
            {
                s.folders.insert(
                    name.to_string(),
                    Folder {
                        attributes: if attr.is_empty() { vec![] } else { vec![attr.to_string()] },
                        next_uid: 1,
                        messages: vec![],
                        unsubscribed: false,
                    },
                );
            }
        });
        b
    }

    /// Accept only this password.
    pub fn require_password(self, password: &str) -> Self {
        self.with(|s| s.password = Some(password.to_string()));
        self
    }

    /// Changes the password the server accepts (a rotated token).
    pub fn with_password(&self, password: &str) {
        self.with(|s| s.password = Some(password.to_string()));
    }

    /// Fail every connection with this message.
    pub fn failing(self, message: &str) -> Self {
        self.with(|s| s.fail_connect = Some(message.to_string()));
        self
    }

    fn with<T>(&self, f: impl FnOnce(&mut State) -> T) -> T {
        let mut guard = match self.state.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
        f(&mut guard)
    }

    /// Adds a raw message to a folder; returns its uid.
    pub fn put(&self, mailbox: &str, raw: &[u8], flags: Flags) -> u32 {
        self.with(|s| {
            let f = s.folders.entry(mailbox.to_string()).or_insert_with(|| Folder { next_uid: 1, ..Folder::default() });
            let uid = f.next_uid.max(1);
            f.next_uid = uid.saturating_add(1);
            if f.messages.len() < limits::MAX_ENVELOPES {
                f.messages.push(Stored { uid, flags, raw: raw.to_vec() });
            }
            uid
        })
    }

    /// Adds a message built from a draft (what the demo and tests use).
    pub fn put_draft(&self, mailbox: &str, from: &Address, draft: &Draft, date: i64, flags: Flags) -> u32 {
        let id = format!("<{date}.{}@mock.mailcraft>", self.with(|s| s.log.len()));
        let raw = mime::build(draft, from, date, &id).unwrap_or_default();
        self.put(mailbox, &raw, flags)
    }

    /// What was sent through SMTP: (from, recipients, raw).
    pub fn sent(&self) -> Vec<(String, Vec<String>, Vec<u8>)> {
        self.with(|s| s.sent.clone())
    }

    pub fn log(&self) -> Vec<String> {
        self.with(|s| s.log.clone())
    }

    pub fn count(&self, mailbox: &str) -> usize {
        self.with(|s| s.folders.get(mailbox).map_or(0, |f| f.messages.len()))
    }

    pub fn flags_of(&self, mailbox: &str, uid: u32) -> Option<Flags> {
        self.with(|s| s.folders.get(mailbox).and_then(|f| f.messages.iter().find(|m| m.uid == uid)).map(|m| m.flags))
    }

    fn envelope(mailbox: &str, m: &Stored) -> Envelope {
        let mut e = mime::parse(&m.raw).map(|p| p.envelope).unwrap_or_default();
        e.uid = m.uid;
        e.mailbox = mailbox.to_string();
        e.flags = m.flags;
        e.size = m.raw.len() as u32;
        e
    }

    fn require(s: &State) -> Result<()> {
        if s.connected { Ok(()) } else { Err(Error::NotConnected) }
    }

    /// A demo inbox in Italian with a few English messages: unread mail, a starred one, an
    /// attachment, a thread, newsletters, sent mail, a draft and something in the trash.
    pub fn demo() -> Self {
        let b = Self::new();
        let me = Address::new("Riccardo", "riccardo@example.it");
        let now = time::now();
        let h = 3_600;
        let msg = |from: Address, subject: &str, body: &str, ago: i64, flags: Flags, att: Option<(&str, &str)>| {
            let draft = Draft {
                to: vec![me.clone()],
                subject: subject.into(),
                body: body.into(),
                attachments: att
                    .map(|(n, m)| vec![DraftAttachment { name: n.into(), mime: m.into(), data: b"demo".to_vec(), cid: String::new() }])
                    .unwrap_or_default(),
                ..Draft::default()
            };
            b.put_draft("INBOX", &from, &draft, now - ago, flags);
        };
        let unread = Flags::default();
        let read = Flags { seen: true, ..Flags::default() };
        let starred = Flags { seen: true, flagged: true, ..Flags::default() };
        msg(
            Address::new("Anna Rossi", "anna.rossi@example.it"),
            "Riunione di lunedì: ordine del giorno",
            "Ciao Riccardo,\n\nper lunedì alle 10 propongo questi punti:\n1. stato del progetto GridCraft\n2. demo di IdeaCraft al cliente\n3. ferie di novembre\n\nAggiungi pure quello che manca.\n\nAnna",
            h / 2,
            unread,
            None,
        );
        msg(
            Address::new("Fatturazione Aruba", "fatture@example-hosting.it"),
            "Fattura n° 2026-1187 disponibile",
            "Gentile cliente,\nla fattura relativa al rinnovo del dominio è disponibile in allegato e nell'area clienti.\n\nCordiali saluti",
            3 * h,
            unread,
            Some(("fattura-2026-1187.pdf", "application/pdf")),
        );
        msg(
            Address::new("Luca Bianchi", "luca@example.com"),
            "Re: Preventivo rete uffici",
            "Perfetto, confermo l'ordine dei due switch e dell'access point.\n\nQuando riesci a passare per il sopralluogo?\n\nLuca",
            5 * h,
            starred,
            None,
        );
        msg(
            Address::new("GitHub", "noreply@example-git.com"),
            "[rickyb82/ideacraft] CI passed on main",
            "All checks have passed on commit c97e326.\n\nView the run: https://example.com/actions/runs/1",
            7 * h,
            read,
            None,
        );
        msg(
            Address::new("Marta Conti", "marta.conti@example.it"),
            "Foto del sopralluogo",
            "Ti mando le foto del locale tecnico, il rack è quello in fondo a destra.\n\nMarta",
            26 * h,
            read,
            Some(("rack.jpg", "image/jpeg")),
        );
        msg(
            Address::new("Newsletter Rust", "news@example-rust.org"),
            "This week in Rust: egui 0.36, cargo xtask patterns",
            "Highlights of the week:\n- egui 0.36 released\n- a guide to cargo xtask\n- Rust 1.97 is out\n\nRead more at https://example.org/twir",
            30 * h,
            read,
            None,
        );
        msg(
            Address::new("Giulia Ferri", "giulia@example-cliente.it"),
            "Accesso VPN per il nuovo collaboratore",
            "Buongiorno,\npotete creare le credenziali VPN per Paolo Neri (inizia lunedì)? Serve accesso alla VLAN 20.\n\nGrazie,\nGiulia",
            50 * h,
            unread,
            None,
        );
        msg(
            Address::new("Calendario", "calendar@example.it"),
            "Promemoria: scadenza certificato TLS www.example.it",
            "Il certificato scade tra 14 giorni. Rinnovalo dal pannello o lascialo a certbot.",
            70 * h,
            read,
            None,
        );
        msg(
            Address::new("Paolo Neri", "paolo.neri@example.it"),
            "Documenti per l'assunzione",
            "Ciao, in allegato il documento firmato. Fammi sapere se manca qualcosa.\n\nPaolo",
            100 * h,
            read,
            Some(("contratto-firmato.pdf", "application/pdf")),
        );
        msg(
            Address::new("Stefano Galli", "stefano@example.com"),
            "Grazie per la demo di ieri",
            "Hi Riccardo,\n\nthanks for the walkthrough yesterday — the network validator was the highlight. Let's schedule a follow-up next week.\n\nBest,\nStefano",
            120 * h,
            read,
            None,
        );
        // A thread: a question and its answer.
        let root_id = format!("<thread-root.{now}@mock.mailcraft>");
        let q = mime::build(
            &Draft {
                to: vec![me.clone()],
                subject: "Backup notturno fallito".into(),
                body: "Il backup di stanotte si è fermato al 40%. Log in allegato.".into(),
                ..Draft::default()
            },
            &Address::new("Monitoraggio", "monitor@example.it"),
            now - 9 * h,
            &root_id,
        )
        .unwrap_or_default();
        b.put("INBOX", &q, read);
        let a = mime::build(
            &Draft {
                to: vec![Address::new("Monitoraggio", "monitor@example.it")],
                subject: "Re: Backup notturno fallito".into(),
                body: "Visto, era il disco pieno. Ho liberato spazio e rilanciato.".into(),
                in_reply_to: root_id.clone(),
                references: vec![root_id.clone()],
                ..Draft::default()
            },
            &me,
            now - 8 * h,
            &format!("<thread-reply.{now}@mock.mailcraft>"),
        )
        .unwrap_or_default();
        b.put("Sent", &a, read);
        let r = mime::build(
            &Draft {
                to: vec![me.clone()],
                subject: "Re: Backup notturno fallito".into(),
                body: "Grazie! Stanotte è andato a buon fine.".into(),
                in_reply_to: root_id.clone(),
                references: vec![root_id],
                ..Draft::default()
            },
            &Address::new("Monitoraggio", "monitor@example.it"),
            now - 2 * h,
            &format!("<thread-reply2.{now}@mock.mailcraft>"),
        )
        .unwrap_or_default();
        b.put("INBOX", &r, unread);
        b.put_draft(
            "Sent",
            &me,
            &Draft {
                to: vec![Address::new("Luca Bianchi", "luca@example.com")],
                subject: "Preventivo rete uffici".into(),
                body: "Ciao Luca,\n\nin allegato il preventivo per la rete degli uffici: due switch, un access point e il cablaggio.\n\nRiccardo"
                    .into(),
                ..Draft::default()
            },
            now - 20 * h,
            read,
        );
        b.put_draft(
            "Drafts",
            &me,
            &Draft {
                to: vec![Address::new("Anna Rossi", "anna.rossi@example.it")],
                subject: "Ferie di novembre".into(),
                body: "Ciao Anna,\n\npenso di prendere".into(),
                ..Draft::default()
            },
            now - 4 * h,
            Flags { seen: true, draft: true, ..Flags::default() },
        );
        b.put_draft(
            "Trash",
            &Address::new("Offerte", "promo@example-shop.it"),
            &Draft { to: vec![me.clone()], subject: "Ultime ore: -50% su tutto".into(), body: "Solo oggi…".into(), ..Draft::default() },
            now - 200 * h,
            read,
        );
        b.put_draft(
            "Junk",
            &Address::new("Prize Dept", "win@example-spam.biz"),
            &Draft { to: vec![me], subject: "You have won".into(), body: "Click here".into(), ..Draft::default() },
            now - 300 * h,
            read,
        );
        b
    }
}

impl MailBackend for MockBackend {
    fn connect(&mut self, account: &Account, password: &str) -> Result<()> {
        self.with(|s| {
            s.log.push(format!("connect {}", account.id));
            if let Some(m) = &s.fail_connect {
                return Err(Error::Connect(m.clone()));
            }
            if s.password.as_deref().is_some_and(|p| p != password) {
                return Err(Error::Auth("wrong password".into()));
            }
            s.connected = true;
            Ok(())
        })
    }

    fn disconnect(&mut self) {
        self.with(|s| s.connected = false);
    }

    fn mailboxes(&mut self) -> Result<(Vec<Mailbox>, String)> {
        self.with(|s| {
            Self::require(s)?;
            let list = s
                .folders
                .iter()
                .map(|(path, f)| {
                    let mut m = Mailbox::new(path, &f.attributes, Some("/"));
                    m.total = f.messages.len() as u32;
                    m.unread = f.messages.iter().filter(|x| !x.flags.seen).count() as u32;
                    m.subscribed = !f.unsubscribed;
                    m
                })
                .collect();
            Ok((list, "/".to_string()))
        })
    }

    fn envelopes(&mut self, mailbox: &str, page: Page) -> Result<Vec<Envelope>> {
        self.with(|s| {
            Self::require(s)?;
            let f = s.folders.get(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            let mut all: Vec<Envelope> = f.messages.iter().map(|m| Self::envelope(mailbox, m)).collect();
            all.sort_by(|a, b| b.date.cmp(&a.date).then(b.uid.cmp(&a.uid)));
            Ok(all.into_iter().skip(page.offset).take(page.limit).collect())
        })
    }

    fn envelopes_by_uid(&mut self, mailbox: &str, uids: &[u32]) -> Result<Vec<Envelope>> {
        self.with(|s| {
            Self::require(s)?;
            let f = s.folders.get(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            Ok(f.messages.iter().filter(|m| uids.contains(&m.uid)).map(|m| Self::envelope(mailbox, m)).collect())
        })
    }

    fn fetch(&mut self, mailbox: &str, uid: u32) -> Result<Vec<u8>> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("fetch {mailbox} {uid}"));
            let f = s.folders.get(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            f.messages.iter().find(|m| m.uid == uid).map(|m| m.raw.clone()).ok_or(Error::NoMessage(uid))
        })
    }

    fn set_flag(&mut self, mailbox: &str, uids: &[u32], change: FlagChange) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("flag {mailbox} {uids:?} {change:?}"));
            let f = s.folders.get_mut(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            for m in f.messages.iter_mut().filter(|m| uids.contains(&m.uid)) {
                change.apply(&mut m.flags);
            }
            Ok(())
        })
    }

    fn move_messages(&mut self, mailbox: &str, uids: &[u32], to: &str) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("move {mailbox} {uids:?} -> {to}"));
            if !s.folders.contains_key(to) {
                return Err(Error::NoMailbox(to.to_string()));
            }
            let src = s.folders.get_mut(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            let (moved, kept): (Vec<Stored>, Vec<Stored>) = src.messages.drain(..).partition(|m| uids.contains(&m.uid));
            src.messages = kept;
            let dst = s.folders.entry(to.to_string()).or_default();
            for mut m in moved {
                m.uid = dst.next_uid.max(1);
                dst.next_uid = m.uid.saturating_add(1);
                dst.messages.push(m);
            }
            Ok(())
        })
    }

    fn delete(&mut self, mailbox: &str, uids: &[u32]) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("delete {mailbox} {uids:?}"));
            let f = s.folders.get_mut(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            f.messages.retain(|m| !uids.contains(&m.uid));
            Ok(())
        })
    }

    fn append(&mut self, mailbox: &str, raw: &[u8], flags: Flags) -> Result<Option<u32>> {
        self.with(|s| Self::require(s))?;
        if !self.with(|s| s.folders.contains_key(mailbox)) {
            return Err(Error::NoMailbox(mailbox.to_string()));
        }
        Ok(Some(self.put(mailbox, raw, flags)))
    }

    fn search(&mut self, mailbox: &str, query: &SearchQuery, page: Page) -> Result<Vec<Envelope>> {
        let all = self.envelopes(mailbox, Page::new(0, limits::MAX_PAGE))?;
        let body_has = |m: &Stored, words: &str| {
            let text = String::from_utf8_lossy(&m.raw).to_lowercase();
            words.split_whitespace().all(|w| text.contains(&w.to_lowercase()))
        };
        self.with(|s| {
            let f = s.folders.get(mailbox).ok_or_else(|| Error::NoMailbox(mailbox.to_string()))?;
            Ok(all
                .into_iter()
                .filter(|e| {
                    query.matches(e)
                        || (!query.text.is_empty()
                            && f.messages.iter().any(|m| m.uid == e.uid && body_has(m, &query.text))
                            && SearchQuery { text: String::new(), ..query.clone() }.matches(e))
                })
                .skip(page.offset)
                .take(page.limit)
                .collect())
        })
    }

    fn create_mailbox(&mut self, path: &str) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.folders.entry(path.to_string()).or_insert_with(|| Folder { next_uid: 1, ..Folder::default() });
            Ok(())
        })
    }

    fn subscribe(&mut self, path: &str, subscribed: bool) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("subscribe {path} {subscribed}"));
            let f = s.folders.get_mut(path).ok_or_else(|| Error::NoMailbox(path.to_string()))?;
            f.unsubscribed = !subscribed;
            Ok(())
        })
    }

    fn send(&mut self, from: &str, to: &[String], raw: &[u8]) -> Result<()> {
        self.with(|s| {
            Self::require(s)?;
            s.log.push(format!("send {from} -> {to:?}"));
            s.sent.push((from.to_string(), to.to_vec(), raw.to_vec()));
            Ok(())
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mailbox::Role;

    #[test]
    fn demo_behaves_like_a_server() {
        let shared = MockBackend::demo().require_password("pw");
        let mut b = shared.clone();
        let account = Account { id: "demo".into(), ..Account::default() };
        assert_eq!(b.connect(&account, "nope").unwrap_err(), Error::Auth("wrong password".into()));
        assert!(b.mailboxes().is_err());
        b.connect(&account, "pw").unwrap();
        let (boxes, delim) = b.mailboxes().unwrap();
        assert_eq!(delim, "/");
        let inbox = boxes.iter().find(|m| m.role == Role::Inbox).unwrap();
        assert_eq!(inbox.total, 12);
        assert_eq!(inbox.unread, 4);
        let page = b.envelopes("INBOX", Page::new(0, 5)).unwrap();
        assert_eq!(page.len(), 5);
        assert!(page[0].date >= page[1].date, "newest first");
        assert_eq!(page[0].subject, "Riunione di lunedì: ordine del giorno");
        let fatture = b.search("INBOX", &SearchQuery::parse("fattura has:attachment"), Page::default()).unwrap();
        assert_eq!(fatture.len(), 1);
        let uid = fatture[0].uid;
        b.set_flag("INBOX", &[uid], FlagChange::Seen(true)).unwrap();
        assert!(shared.flags_of("INBOX", uid).unwrap().seen);
        b.move_messages("INBOX", &[uid], "Archive").unwrap();
        assert_eq!(shared.count("Archive"), 1);
        assert!(b.move_messages("INBOX", &[1], "Nope").is_err());
        let raw = b.fetch("INBOX", 1).unwrap();
        assert!(mime::parse(&raw).is_ok());
        assert_eq!(b.fetch("INBOX", 999).unwrap_err(), Error::NoMessage(999));
        b.send("riccardo@example.it", &["a@b.co".into()], b"raw").unwrap();
        assert_eq!(shared.sent().len(), 1);
        assert!(shared.log().iter().any(|l| l.starts_with("move INBOX")));
    }
}
