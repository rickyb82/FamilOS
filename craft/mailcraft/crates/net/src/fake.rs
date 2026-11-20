//! Tiny IMAP and SMTP servers on localhost for the tests: enough of the protocols for
//! `ImapSmtpBackend` to log in, list, fetch, flag, move, append, search, expunge and send.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};

#[derive(Clone, Debug)]
pub struct Stored {
    pub uid: u32,
    pub flags: Vec<String>,
    pub raw: Vec<u8>,
}

#[derive(Debug, Default)]
pub struct Store {
    pub folders: BTreeMap<String, (Vec<String>, u32, Vec<Stored>)>,
    /// Folders the client unsubscribed from (everything is subscribed at first).
    pub unsubscribed: Vec<String>,
    pub user: String,
    pub password: String,
    /// An OAuth access token accepted by XOAUTH2 (empty = none).
    pub token: String,
    pub smtp_received: Vec<(String, Vec<String>, Vec<u8>)>,
}

impl Store {
    pub fn put(&mut self, folder: &str, raw: &[u8], flags: &[&str]) -> u32 {
        let f = self.folders.entry(folder.to_string()).or_insert_with(|| (vec![], 1, vec![]));
        let uid = f.1;
        f.1 += 1;
        f.2.push(Stored { uid, flags: flags.iter().map(|s| s.to_string()).collect(), raw: raw.to_vec() });
        uid
    }
}

pub type Shared = Arc<Mutex<Store>>;

pub fn shared(user: &str, password: &str) -> Shared {
    let mut s = Store { user: user.into(), password: password.into(), ..Store::default() };
    for (name, attr) in [("INBOX", ""), ("Sent", "\\Sent"), ("Drafts", "\\Drafts"), ("Trash", "\\Trash"), ("Archive", "\\Archive")] {
        s.folders.insert(name.into(), (if attr.is_empty() { vec![] } else { vec![attr.into()] }, 1, vec![]));
    }
    Arc::new(Mutex::new(s))
}

fn serve(listener: TcpListener, store: Shared, handler: fn(TcpStream, Shared)) {
    std::thread::spawn(move || {
        for conn in listener.incoming().flatten() {
            let st = store.clone();
            std::thread::spawn(move || handler(conn, st));
        }
    });
}

pub fn start_imap(store: Shared) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    serve(listener, store, imap_conn);
    port
}

/// The fake IMAP server behind TLS with a self-signed certificate made for the test:
/// implicit TLS (port 993 style), or STARTTLS after a plain greeting.
pub fn start_imap_tls(store: Shared, starttls: bool) -> u16 {
    let cert = rcgen::generate_simple_self_signed(vec!["localhost".to_string()]).expect("certificate");
    let key = rustls::pki_types::PrivateKeyDer::Pkcs8(cert.signing_key.serialize_der().into());
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = Arc::new(
        rustls::ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .expect("versions")
            .with_no_client_auth()
            .with_single_cert(vec![cert.cert.der().clone()], key)
            .expect("server config"),
    );
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    std::thread::spawn(move || {
        for mut tcp in listener.incoming().flatten() {
            let (st, config) = (store.clone(), config.clone());
            std::thread::spawn(move || {
                if starttls {
                    let _ = tcp.write_all(b"* OK [CAPABILITY IMAP4rev1 STARTTLS] fake ready\r\n");
                    let mut line = String::new();
                    let _ = BufReader::new(&tcp).read_line(&mut line);
                    let tag = line.split_whitespace().next().unwrap_or("*").to_string();
                    if !line.to_ascii_uppercase().contains("STARTTLS") {
                        let _ = tcp.write_all(format!("{tag} BAD expected STARTTLS\r\n").as_bytes());
                        return;
                    }
                    let _ = tcp.write_all(format!("{tag} OK begin TLS\r\n").as_bytes());
                }
                let Ok(conn) = rustls::ServerConnection::new(config) else { return };
                imap_session(BufReader::new(rustls::StreamOwned::new(conn, tcp)), st, !starttls);
            });
        }
    });
    port
}

pub fn start_smtp(store: Shared) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    serve(listener, store, smtp_conn);
    port
}

/// Splits an IMAP command line into atoms, quoted strings, parenthesised lists and literals.
fn tokens(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut depth = 0;
    let mut bracket = 0;
    for c in line.chars() {
        match c {
            '"' if depth == 0 && bracket == 0 => quoted = !quoted,
            '(' if !quoted => {
                depth += 1;
                cur.push(c);
            }
            ')' if !quoted => {
                depth -= 1;
                cur.push(c);
            }
            '[' if !quoted => {
                bracket += 1;
                cur.push(c);
            }
            ']' if !quoted => {
                bracket -= 1;
                cur.push(c);
            }
            ' ' if !quoted && depth == 0 && bracket == 0 => {
                if !cur.is_empty() {
                    out.push(std::mem::take(&mut cur));
                }
            }
            _ => cur.push(c),
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn seq_set(set: &str, max: u32) -> Vec<u32> {
    let mut out = Vec::new();
    for part in set.split(',') {
        let (a, b) = match part.split_once(':') {
            Some((a, b)) => (a, b),
            None => (part, part),
        };
        let a = if a == "*" { max } else { a.parse().unwrap_or(0) };
        let b = if b == "*" { max } else { b.parse().unwrap_or(0) };
        for n in a.min(b)..=a.max(b) {
            out.push(n);
        }
    }
    out
}

fn imap_conn(stream: TcpStream, store: Shared) {
    imap_session(BufReader::new(stream), store, true);
}

/// The IMAP conversation on any stream (plain, or TLS for the TLS tests); `greet` sends the
/// greeting (not after STARTTLS, whose greeting went out in the clear).
fn imap_session<S: Read + Write>(mut reader: BufReader<S>, store: Shared, greet: bool) {
    if greet {
        let _ = reader.get_mut().write_all(b"* OK [CAPABILITY IMAP4rev1 MOVE UIDPLUS] fake ready\r\n");
    }
    let mut selected: Option<String> = None;
    let mut logged = false;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            return;
        }
        let t = tokens(line.trim_end());
        let tag = t.first().cloned().unwrap_or_default();
        let mut cmd = t.get(1).cloned().unwrap_or_default().to_ascii_uppercase();
        let mut args: Vec<String> = t.get(2..).map(|a| a.to_vec()).unwrap_or_default();
        let uid_mode = cmd == "UID";
        if uid_mode {
            cmd = args.first().cloned().unwrap_or_default().to_ascii_uppercase();
            args.remove(0);
        }
        let ok = |w: &mut S, text: &str| {
            let _ = w.write_all(format!("{tag} OK {text}\r\n").as_bytes());
        };
        let no = |w: &mut S, text: &str| {
            let _ = w.write_all(format!("{tag} NO {text}\r\n").as_bytes());
        };
        if !logged && !matches!(cmd.as_str(), "CAPABILITY" | "LOGIN" | "AUTHENTICATE" | "LOGOUT" | "NOOP") {
            no(reader.get_mut(), "not logged in");
            continue;
        }
        match cmd.as_str() {
            "CAPABILITY" => {
                let _ = reader.get_mut().write_all(b"* CAPABILITY IMAP4rev1 MOVE UIDPLUS\r\n");
                ok(reader.get_mut(), "done");
            }
            "NOOP" => ok(reader.get_mut(), "done"),
            "LOGOUT" => {
                let _ = reader.get_mut().write_all(b"* BYE\r\n");
                ok(reader.get_mut(), "bye");
                return;
            }
            "LOGIN" => {
                let s = store.lock().expect("lock");
                if args.first() == Some(&s.user) && args.get(1) == Some(&s.password) {
                    logged = true;
                    ok(reader.get_mut(), "logged in");
                } else {
                    no(reader.get_mut(), "[AUTHENTICATIONFAILED] wrong user or password");
                }
            }
            "AUTHENTICATE" => {
                // SASL XOAUTH2: an empty challenge, then the base64 "user=…\x01auth=Bearer …\x01\x01".
                if args.first().map(|m| m.to_ascii_uppercase()) != Some("XOAUTH2".into()) {
                    no(reader.get_mut(), "unsupported mechanism");
                    continue;
                }
                let _ = reader.get_mut().write_all(b"+ \r\n");
                let mut answer = String::new();
                let _ = reader.read_line(&mut answer);
                let decoded = mailcraft_core::mime::base64_decode(answer.trim()).unwrap_or_default();
                let s = store.lock().expect("lock");
                let expected = mailcraft_core::oauth::xoauth2(&s.user, &s.token);
                if !s.token.is_empty() && decoded == expected.as_bytes() {
                    logged = true;
                    ok(reader.get_mut(), "authenticated");
                } else {
                    no(reader.get_mut(), "[AUTHENTICATIONFAILED] invalid token");
                }
            }
            "LIST" | "LSUB" => {
                let s = store.lock().expect("lock");
                for (name, (attrs, _, _)) in &s.folders {
                    if cmd == "LSUB" && s.unsubscribed.contains(name) {
                        continue;
                    }
                    let a = if attrs.is_empty() { "\\HasNoChildren".to_string() } else { attrs.join(" ") };
                    let _ = reader.get_mut().write_all(format!("* {cmd} ({a}) \"/\" \"{name}\"\r\n").as_bytes());
                }
                ok(reader.get_mut(), "done");
            }
            "SUBSCRIBE" | "UNSUBSCRIBE" => {
                let mut s = store.lock().expect("lock");
                let name = args.first().cloned().unwrap_or_default();
                s.unsubscribed.retain(|n| *n != name);
                if cmd == "UNSUBSCRIBE" {
                    s.unsubscribed.push(name);
                }
                ok(reader.get_mut(), "done");
            }
            "STATUS" => {
                let s = store.lock().expect("lock");
                match args.first().and_then(|n| s.folders.get(n)) {
                    Some((_, _, msgs)) => {
                        let unseen = msgs.iter().filter(|m| !m.flags.iter().any(|f| f == "\\Seen")).count();
                        let _ =
                            reader.get_mut().write_all(format!("* STATUS \"{}\" (MESSAGES {} UNSEEN {unseen})\r\n", args[0], msgs.len()).as_bytes());
                        ok(reader.get_mut(), "done");
                    }
                    None => no(reader.get_mut(), "no such mailbox"),
                }
            }
            "SELECT" | "EXAMINE" => {
                let s = store.lock().expect("lock");
                match args.first().and_then(|n| s.folders.get(n)) {
                    Some((_, next, msgs)) => {
                        let _ = reader.get_mut().write_all(format!("* {} EXISTS\r\n* 0 RECENT\r\n* FLAGS (\\Seen \\Flagged \\Deleted \\Draft \\Answered)\r\n* OK [UIDVALIDITY 1] ok\r\n* OK [UIDNEXT {next}] ok\r\n", msgs.len()).as_bytes());
                        selected = args.first().cloned();
                        ok(reader.get_mut(), "[READ-WRITE] selected");
                    }
                    None => no(reader.get_mut(), "no such mailbox"),
                }
            }
            "CREATE" => {
                let mut s = store.lock().expect("lock");
                s.folders.entry(args.first().cloned().unwrap_or_default()).or_insert_with(|| (vec![], 1, vec![]));
                ok(reader.get_mut(), "created");
            }
            "FETCH" => {
                let s = store.lock().expect("lock");
                let Some((_, _, msgs)) = selected.as_ref().and_then(|n| s.folders.get(n)) else {
                    no(reader.get_mut(), "nothing selected");
                    continue;
                };
                let items = args.get(1).cloned().unwrap_or_default().to_ascii_uppercase();
                let max = if uid_mode { msgs.iter().map(|m| m.uid).max().unwrap_or(0) } else { msgs.len() as u32 };
                let wanted = seq_set(args.first().map(String::as_str).unwrap_or(""), max);
                for (i, m) in msgs.iter().enumerate() {
                    let seq = i as u32 + 1;
                    if !wanted.contains(&if uid_mode { m.uid } else { seq }) {
                        continue;
                    }
                    let mut parts: Vec<Vec<u8>> = vec![format!("UID {}", m.uid).into_bytes()];
                    if items.contains("FLAGS") {
                        parts.push(format!("FLAGS ({})", m.flags.join(" ")).into_bytes());
                    }
                    if items.contains("RFC822.SIZE") {
                        parts.push(format!("RFC822.SIZE {}", m.raw.len()).into_bytes());
                    }
                    let split = m.raw.windows(4).position(|x| x == b"\r\n\r\n").map(|p| p + 4).unwrap_or(m.raw.len());
                    if items.contains("BODY.PEEK[HEADER]") {
                        let mut p = format!("BODY[HEADER] {{{}}}\r\n", split).into_bytes();
                        p.extend_from_slice(&m.raw[..split]);
                        parts.push(p);
                    }
                    if items.contains("BODY.PEEK[TEXT]") {
                        let text = &m.raw[split..];
                        let text = &text[..text.len().min(700)];
                        let mut p = format!("BODY[TEXT]<0> {{{}}}\r\n", text.len()).into_bytes();
                        p.extend_from_slice(text);
                        parts.push(p);
                    }
                    if items.contains("BODY.PEEK[]") {
                        let mut p = format!("BODY[] {{{}}}\r\n", m.raw.len()).into_bytes();
                        p.extend_from_slice(&m.raw);
                        parts.push(p);
                    }
                    let _ = reader.get_mut().write_all(format!("* {seq} FETCH (").as_bytes());
                    for (j, p) in parts.iter().enumerate() {
                        if j > 0 {
                            let _ = reader.get_mut().write_all(b" ");
                        }
                        let _ = reader.get_mut().write_all(p);
                    }
                    let _ = reader.get_mut().write_all(b")\r\n");
                }
                ok(reader.get_mut(), "fetched");
            }
            "STORE" => {
                let mut s = store.lock().expect("lock");
                let Some((_, _, msgs)) = selected.as_ref().and_then(|n| s.folders.get_mut(n)) else {
                    no(reader.get_mut(), "nothing selected");
                    continue;
                };
                let max = msgs.iter().map(|m| m.uid).max().unwrap_or(0);
                let wanted = seq_set(args.first().map(String::as_str).unwrap_or(""), max);
                let op = args.get(1).cloned().unwrap_or_default();
                let flags: Vec<String> =
                    args.get(2).map(|f| f.trim_matches(['(', ')']).split_whitespace().map(str::to_string).collect()).unwrap_or_default();
                for m in msgs.iter_mut().filter(|m| wanted.contains(&m.uid)) {
                    if op.starts_with('+') {
                        for f in &flags {
                            if !m.flags.contains(f) {
                                m.flags.push(f.clone());
                            }
                        }
                    } else {
                        m.flags.retain(|f| !flags.contains(f));
                    }
                }
                ok(reader.get_mut(), "stored");
            }
            "COPY" | "MOVE" => {
                let mut s = store.lock().expect("lock");
                let dest = args.get(1).cloned().unwrap_or_default();
                if !s.folders.contains_key(&dest) {
                    no(reader.get_mut(), "[TRYCREATE] no such mailbox");
                    continue;
                }
                let Some(src) = selected.clone() else {
                    no(reader.get_mut(), "nothing selected");
                    continue;
                };
                let max = s.folders.get(&src).map(|f| f.2.iter().map(|m| m.uid).max().unwrap_or(0)).unwrap_or(0);
                let wanted = seq_set(args.first().map(String::as_str).unwrap_or(""), max);
                let moved: Vec<Stored> =
                    s.folders.get(&src).map(|f| f.2.iter().filter(|m| wanted.contains(&m.uid)).cloned().collect()).unwrap_or_default();
                if cmd == "MOVE"
                    && let Some(f) = s.folders.get_mut(&src)
                {
                    f.2.retain(|m| !wanted.contains(&m.uid));
                }
                for m in moved {
                    let flags: Vec<&str> = m.flags.iter().map(String::as_str).collect();
                    s.put(&dest, &m.raw, &flags);
                }
                ok(reader.get_mut(), "done");
            }
            "EXPUNGE" => {
                let mut s = store.lock().expect("lock");
                if let Some((_, _, msgs)) = selected.as_ref().and_then(|n| s.folders.get_mut(n)) {
                    let only: Option<Vec<u32>> =
                        if uid_mode { args.first().map(|a| seq_set(a, msgs.iter().map(|m| m.uid).max().unwrap_or(0))) } else { None };
                    msgs.retain(|m| !(m.flags.iter().any(|f| f == "\\Deleted") && only.as_ref().is_none_or(|o| o.contains(&m.uid))));
                }
                ok(reader.get_mut(), "expunged");
            }
            "APPEND" => {
                let folder = args.first().cloned().unwrap_or_default();
                let flags: Vec<String> = args
                    .iter()
                    .skip(1)
                    .find(|a| a.starts_with('('))
                    .map(|f| f.trim_matches(['(', ')']).split_whitespace().map(str::to_string).collect())
                    .unwrap_or_default();
                let len: usize = args.last().and_then(|a| a.trim_matches(['{', '}', '+']).parse().ok()).unwrap_or(0);
                let _ = reader.get_mut().write_all(b"+ go ahead\r\n");
                let mut buf = vec![0u8; len];
                let _ = reader.read_exact(&mut buf);
                let mut crlf = String::new();
                let _ = reader.read_line(&mut crlf);
                let mut s = store.lock().expect("lock");
                if !s.folders.contains_key(&folder) {
                    no(reader.get_mut(), "[TRYCREATE] no such mailbox");
                    continue;
                }
                let flags: Vec<&str> = flags.iter().map(String::as_str).collect();
                let uid = s.put(&folder, &buf, &flags);
                ok(reader.get_mut(), &format!("[APPENDUID 1 {uid}] appended"));
            }
            "SEARCH" => {
                let s = store.lock().expect("lock");
                let Some((_, _, msgs)) = selected.as_ref().and_then(|n| s.folders.get(n)) else {
                    no(reader.get_mut(), "nothing selected");
                    continue;
                };
                let mut hits = Vec::new();
                for m in msgs {
                    let text = String::from_utf8_lossy(&m.raw).to_lowercase();
                    let mut matched = true;
                    let mut i = 0;
                    while i < args.len() {
                        match args[i].to_ascii_uppercase().as_str() {
                            "UNSEEN" => matched &= !m.flags.iter().any(|f| f == "\\Seen"),
                            "FLAGGED" => matched &= m.flags.iter().any(|f| f == "\\Flagged"),
                            "FROM" | "SUBJECT" | "TEXT" | "TO" => {
                                matched &= text.contains(&args.get(i + 1).cloned().unwrap_or_default().to_lowercase());
                                i += 1;
                            }
                            "SINCE" => i += 1,
                            _ => {}
                        }
                        i += 1;
                    }
                    if matched {
                        hits.push(m.uid.to_string());
                    }
                }
                let _ = reader.get_mut().write_all(format!("* SEARCH {}\r\n", hits.join(" ")).as_bytes());
                ok(reader.get_mut(), "done");
            }
            _ => {
                let _ = reader.get_mut().write_all(format!("{tag} BAD unknown command {cmd}\r\n").as_bytes());
            }
        }
    }
}

fn smtp_conn(stream: TcpStream, store: Shared) {
    let mut reader = BufReader::new(stream.try_clone().expect("clone"));
    let mut w = stream;
    let _ = w.write_all(b"220 fake SMTP\r\n");
    let (mut from, mut to) = (String::new(), Vec::new());
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap_or(0) == 0 {
            return;
        }
        let upper = line.trim_end().to_ascii_uppercase();
        let reply: &[u8] = if upper.starts_with("EHLO") || upper.starts_with("HELO") {
            b"250-fake\r\n250-AUTH PLAIN LOGIN XOAUTH2\r\n250 8BITMIME\r\n"
        } else if upper.starts_with("AUTH XOAUTH2") {
            let s = store.lock().expect("lock");
            let b64 = line.trim_end().rsplit(' ').next().unwrap_or_default();
            let decoded = mailcraft_core::mime::base64_decode(b64).unwrap_or_default();
            let okay = !s.token.is_empty() && decoded == mailcraft_core::oauth::xoauth2(&s.user, &s.token).as_bytes();
            if okay { b"235 ok\r\n" } else { b"535 5.7.8 authentication failed\r\n" }
        } else if upper.starts_with("AUTH PLAIN") {
            let s = store.lock().expect("lock");
            let b64 = line.trim_end().rsplit(' ').next().unwrap_or_default();
            let decoded = mailcraft_core::mime::base64_decode(b64).unwrap_or_default();
            let parts: Vec<&[u8]> = decoded.split(|b| *b == 0).collect();
            let okay = parts.len() >= 3 && parts[1] == s.user.as_bytes() && parts[2] == s.password.as_bytes();
            if okay { b"235 ok\r\n" } else { b"535 5.7.8 authentication failed\r\n" }
        } else if upper.starts_with("MAIL FROM:") {
            from = line.trim_end().split_once(':').map(|(_, a)| a.trim().trim_matches(['<', '>']).to_string()).unwrap_or_default();
            b"250 ok\r\n"
        } else if upper.starts_with("RCPT TO:") {
            to.push(line.trim_end().split_once(':').map(|(_, a)| a.trim().trim_matches(['<', '>']).to_string()).unwrap_or_default());
            b"250 ok\r\n"
        } else if upper.starts_with("DATA") {
            let _ = w.write_all(b"354 go\r\n");
            let mut data = Vec::new();
            loop {
                let mut l = String::new();
                if reader.read_line(&mut l).unwrap_or(0) == 0 || l == ".\r\n" {
                    break;
                }
                data.extend_from_slice(l.strip_prefix('.').unwrap_or(&l).as_bytes());
            }
            store.lock().expect("lock").smtp_received.push((from.clone(), to.clone(), data));
            b"250 queued\r\n"
        } else if upper.starts_with("QUIT") {
            let _ = w.write_all(b"221 bye\r\n");
            return;
        } else if upper.starts_with("RSET") || upper.starts_with("NOOP") {
            b"250 ok\r\n"
        } else {
            b"500 what\r\n"
        };
        let _ = w.write_all(reply);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ImapSmtpBackend;
    use mailcraft_core::account::{Account, Security, Server};
    use mailcraft_core::backend::{FlagChange, MailBackend, Page, SearchQuery};
    use mailcraft_core::mailbox::Role;
    use mailcraft_core::message::{Address, Draft, Flags};
    use mailcraft_core::{Error, mime};

    fn raw(from: &str, subject: &str, body: &str, i: i64) -> Vec<u8> {
        let d = Draft { to: vec![Address::new("Anna", "anna@example.com")], subject: subject.into(), body: body.into(), ..Draft::default() };
        mime::build(
            &d,
            &Address::new(from, &format!("{}@example.org", from.to_lowercase())),
            1_700_000_000 + i * 3_600,
            &format!("<m{i}@example.org>"),
        )
        .expect("build")
    }

    #[test]
    fn backend_talks_imap_and_smtp() {
        let store = shared("anna@example.com", "pw");
        {
            let mut s = store.lock().expect("lock");
            s.put("INBOX", &raw("Luca", "Preventivo rete", "Ciao, in allegato il preventivo.", 1), &["\\Seen"]);
            s.put("INBOX", &raw("Marta", "Foto sopralluogo", "Le foto del rack.", 2), &[]);
            s.put("INBOX", &raw("Aruba", "Fattura ottobre", "La fattura è disponibile.", 3), &["\\Flagged"]);
        }
        let imap_port = start_imap(store.clone());
        let smtp_port = start_smtp(store.clone());
        let account = Account {
            id: "test".into(),
            name: "Test".into(),
            email: "anna@example.com".into(),
            imap: Server::new("127.0.0.1", imap_port, Security::None),
            smtp: Server::new("127.0.0.1", smtp_port, Security::None),
            ..Account::default()
        };
        let mut b = ImapSmtpBackend::default();
        assert!(matches!(b.connect(&account, "wrong").unwrap_err(), Error::Auth(_)));
        b.connect(&account, "pw").expect("login");
        let (boxes, delim) = b.mailboxes().expect("list");
        assert_eq!(delim, "/");
        let inbox = boxes.iter().find(|m| m.role == Role::Inbox).expect("inbox");
        assert_eq!((inbox.total, inbox.unread), (3, 2));
        assert!(boxes.iter().any(|m| m.role == Role::Sent && m.path == "Sent"));
        // Newest first, snippets from the partial text, flags.
        let page = b.envelopes("INBOX", Page::new(0, 2)).expect("page");
        assert_eq!(page.len(), 2);
        assert_eq!(page[0].subject, "Fattura ottobre");
        assert_eq!(page[0].snippet, "La fattura è disponibile.");
        assert!(page[0].flags.flagged && !page[0].flags.seen);
        assert_eq!(page[0].from[0].name, "Aruba");
        let rest = b.envelopes("INBOX", Page::new(2, 10)).expect("page");
        assert_eq!(rest.len(), 1);
        assert_eq!(rest[0].subject, "Preventivo rete");
        assert!(b.envelopes("INBOX", Page::new(5, 10)).expect("empty").is_empty());
        // Full fetch parses; flags change on the server.
        let m = mime::parse(&b.fetch("INBOX", 2).expect("fetch")).expect("parse");
        assert_eq!(m.envelope.subject, "Foto sopralluogo");
        assert!(matches!(b.fetch("INBOX", 99).unwrap_err(), Error::NoMessage(99)));
        b.set_flag("INBOX", &[2], FlagChange::Seen(true)).expect("flag");
        assert!(b.envelopes_by_uid("INBOX", &[2]).expect("by uid")[0].flags.seen);
        // Search: server-side for ASCII, local for the rest.
        let hits = b.search("INBOX", &SearchQuery::parse("fattura is:starred"), Page::default()).expect("search");
        assert_eq!(hits.len(), 1);
        let hits = b.search("INBOX", &SearchQuery::parse("preventivo è"), Page::default()).expect("search");
        assert_eq!(hits.len(), 0, "non-ASCII words are matched locally on subject/sender/snippet");
        // Move (MOVE capability), append (APPENDUID), delete (expunge).
        b.move_messages("INBOX", &[1], "Archive").expect("move");
        assert_eq!(store.lock().expect("lock").folders["Archive"].2.len(), 1);
        assert!(matches!(b.move_messages("INBOX", &[2], "Nowhere").unwrap_err(), Error::Server(_)));
        let uid = b.append("Drafts", &raw("Anna", "Bozza", "testo", 9), Flags { draft: true, seen: true, ..Flags::default() }).expect("append");
        assert_eq!(uid, Some(1));
        assert_eq!(store.lock().expect("lock").folders["Drafts"].2[0].flags, vec!["\\Seen", "\\Draft"]);
        b.delete("INBOX", &[3]).expect("delete");
        assert_eq!(store.lock().expect("lock").folders["INBOX"].2.len(), 1);
        b.create_mailbox("Progetti").expect("create");
        assert!(store.lock().expect("lock").folders.contains_key("Progetti"));
        // SMTP.
        let out = raw("Anna", "Risposta", "Grazie!", 10);
        b.test_smtp().expect("smtp login");
        b.send("anna@example.com", &["luca@example.org".into(), "marta@example.org".into()], &out).expect("send");
        let s = store.lock().expect("lock");
        assert_eq!(s.smtp_received.len(), 1);
        assert_eq!(s.smtp_received[0].0, "anna@example.com");
        assert_eq!(s.smtp_received[0].1, vec!["luca@example.org", "marta@example.org"]);
        assert!(String::from_utf8_lossy(&s.smtp_received[0].2).contains("Subject: Risposta"));
        drop(s);
        // An OAuth account logs in with XOAUTH2 on both servers (the token is the password).
        store.lock().expect("lock").token = "tok-123".into();
        let google = Account { auth: "google".into(), ..account.clone() };
        let mut o = ImapSmtpBackend::default();
        assert!(matches!(o.connect(&google, "wrong-token").unwrap_err(), Error::Auth(_)));
        o.connect(&google, "tok-123").expect("xoauth2 login");
        assert_eq!(o.mailboxes().expect("list").0.len(), 6);
        o.test_smtp().expect("smtp xoauth2");
        o.send("anna@example.com", &["luca@example.org".into()], &raw("Anna", "Via OAuth", "ok", 11)).expect("send");
        assert_eq!(store.lock().expect("lock").smtp_received.len(), 2);
        o.disconnect();
        b.disconnect();
        // Connection refused is a Connect error, not a panic.
        let dead = Account { imap: Server::new("127.0.0.1", 1, Security::None), ..account.clone() };
        assert!(matches!(ImapSmtpBackend::default().connect(&dead, "pw").unwrap_err(), Error::Connect(_)));
    }

    /// The IMAP connection does its own TLS (rustls with the system's roots): implicit TLS and
    /// STARTTLS work, an untrusted certificate is refused unless the account trusts it, and a
    /// server that speaks no TLS is a connection error.
    #[test]
    fn imap_over_tls_and_starttls() {
        let store = shared("anna@example.com", "pw");
        store.lock().expect("lock").put("INBOX", &raw("Luca", "Via TLS", "cifrato", 1), &[]);
        let account = |port: u16, security: Security, trust: bool| Account {
            id: "tls".into(),
            email: "anna@example.com".into(),
            imap: Server::new("localhost", port, security),
            trust_invalid_certs: trust,
            ..Account::default()
        };
        for (port, security) in [(start_imap_tls(store.clone(), false), Security::Tls), (start_imap_tls(store.clone(), true), Security::StartTls)] {
            let mut b = ImapSmtpBackend::default();
            b.connect(&account(port, security, true), "pw").expect("login over TLS");
            let page = b.envelopes("INBOX", Page::new(0, 10)).expect("page");
            assert_eq!(page.first().map(|e| e.subject.as_str()), Some("Via TLS"), "{security:?}");
            b.disconnect();
            // The self-signed certificate is not among the trusted roots.
            let e = ImapSmtpBackend::default().connect(&account(port, security, false), "pw").unwrap_err();
            assert!(matches!(&e, Error::Connect(m) if m.contains("certificate") || m.contains("Certificate")), "{security:?}: {e:?}");
        }
        // A plain server: no TLS handshake, no STARTTLS.
        let plain = start_imap(store.clone());
        for security in [Security::Tls, Security::StartTls] {
            let e = ImapSmtpBackend::default().connect(&account(plain, security, true), "pw").unwrap_err();
            assert!(matches!(e, Error::Connect(_)), "{security:?}: {e:?}");
        }
    }
}
