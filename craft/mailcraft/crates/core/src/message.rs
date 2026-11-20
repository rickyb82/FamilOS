//! Addresses, envelopes (the list rows), full messages and drafts.

use serde::{Deserialize, Serialize};

use crate::{clean, limits};

/// A mailbox address with an optional display name.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Address {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub name: String,
    pub email: String,
}

impl Address {
    pub fn new(name: &str, email: &str) -> Self {
        Self { name: clean(name, 200).trim().trim_matches('"').to_string(), email: clean(email, 254).trim().to_string() }
    }

    /// Parses `Anna Rossi <anna@example.com>, luca@example.com, "Bianchi, M." <m@x.it>`.
    pub fn parse_list(text: &str) -> Vec<Self> {
        let mut out = Vec::new();
        let mut current = String::new();
        let mut quoted = false;
        let mut depth = 0i32;
        for c in text.chars() {
            match c {
                '"' => {
                    quoted = !quoted;
                    current.push(c);
                }
                '<' if !quoted => {
                    depth += 1;
                    current.push(c);
                }
                '>' if !quoted => {
                    depth -= 1;
                    current.push(c);
                }
                ',' | ';' | '\n' if !quoted && depth <= 0 => {
                    if let Some(a) = Self::parse_one(&current) {
                        out.push(a);
                    }
                    current.clear();
                }
                _ => current.push(c),
            }
            if out.len() >= limits::MAX_RECIPIENTS {
                break;
            }
        }
        if let Some(a) = Self::parse_one(&current) {
            out.push(a);
        }
        out
    }

    /// One address: `Name <addr>`, `<addr>`, `addr` or `addr (Name)`.
    pub fn parse_one(text: &str) -> Option<Self> {
        let t = text.trim();
        if t.is_empty() {
            return None;
        }
        if let Some((name, rest)) = t.split_once('<') {
            let email = rest.split('>').next().unwrap_or_default().trim();
            if email.contains('@') {
                return Some(Self::new(name.trim(), email));
            }
        }
        if let Some((email, rest)) = t.split_once('(') {
            let name = rest.trim_end_matches(')');
            if email.trim().contains('@') {
                return Some(Self::new(name, email.trim()));
            }
        }
        if t.contains('@') && !t.contains(' ') {
            return Some(Self::new("", t));
        }
        None
    }

    /// `Name <email>` or the bare address.
    pub fn display(&self) -> String {
        if self.name.is_empty() {
            self.email.clone()
        } else if self.name.chars().any(|c| matches!(c, ',' | '<' | '>' | '@' | '"' | ';')) {
            format!("\"{}\" <{}>", self.name.replace('"', ""), self.email)
        } else {
            format!("{} <{}>", self.name, self.email)
        }
    }

    /// The name, or the part before `@`.
    pub fn short(&self) -> &str {
        if !self.name.is_empty() {
            return &self.name;
        }
        self.email.split('@').next().unwrap_or(&self.email)
    }
}

/// Lists several addresses with commas.
pub fn join_addresses(list: &[Address]) -> String {
    list.iter().map(Address::display).collect::<Vec<_>>().join(", ")
}

/// IMAP system flags.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Flags {
    pub seen: bool,
    pub flagged: bool,
    pub answered: bool,
    pub draft: bool,
    pub deleted: bool,
}

/// What the list shows about a message: headers, flags, size, snippet.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Envelope {
    pub uid: u32,
    pub mailbox: String,
    /// The account id, filled in when envelopes of several accounts are listed together.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub account: String,
    pub subject: String,
    pub from: Vec<Address>,
    pub to: Vec<Address>,
    pub cc: Vec<Address>,
    /// Unix seconds (0 = unknown).
    pub date: i64,
    pub snippet: String,
    pub flags: Flags,
    pub size: u32,
    pub has_attachments: bool,
    pub message_id: String,
    pub in_reply_to: String,
    pub references: Vec<String>,
}

impl Envelope {
    /// The subject without reply/forward prefixes, lower-case, for grouping.
    pub fn normalized_subject(&self) -> String {
        normalize_subject(&self.subject)
    }

    /// Messages with the same key form one conversation: the root of the references chain,
    /// else the message itself, else the subject.
    pub fn thread_key(&self) -> String {
        if let Some(root) = self.references.first().filter(|r| !r.is_empty()) {
            return format!("id:{root}");
        }
        if !self.in_reply_to.is_empty() {
            return format!("id:{}", self.in_reply_to);
        }
        if !self.message_id.is_empty() {
            return format!("id:{}", self.message_id);
        }
        format!("subject:{}", self.normalized_subject())
    }

    /// The first sender's short name.
    pub fn sender(&self) -> &str {
        self.from.first().map_or("", Address::short)
    }
}

/// Strips `Re:`, `Fwd:`, `R:`, `I:`, `FW:`, `AW:`, `TR:`… repeatedly and lower-cases.
pub fn normalize_subject(subject: &str) -> String {
    let mut s = subject.trim();
    loop {
        let lower = s.to_ascii_lowercase();
        let mut stripped = false;
        for p in ["re:", "fwd:", "fw:", "r:", "i:", "aw:", "tr:", "rif:", "ris:", "wg:", "sv:", "vs:"] {
            if lower.starts_with(p) {
                s = s.get(p.len()..).unwrap_or_default().trim_start();
                stripped = true;
                break;
            }
        }
        if s.starts_with('[') && lower.starts_with("[re") {
            s = s.trim_start_matches(|c| c != ']').trim_start_matches(']').trim_start();
            stripped = true;
        }
        if !stripped {
            break;
        }
    }
    s.trim().to_lowercase()
}

/// A listed attachment (the bytes come with the full message).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attachment {
    pub index: usize,
    pub name: String,
    pub mime: String,
    pub size: usize,
    /// Shown inline by the message (an image in the HTML) rather than listed.
    #[serde(default)]
    pub inline: bool,
}

/// A fully fetched message.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Message {
    pub envelope: Envelope,
    pub text: String,
    pub html: String,
    pub attachments: Vec<Attachment>,
    pub reply_to: Vec<Address>,
    pub list_unsubscribe: String,
}

impl Message {
    /// The text body, or the HTML reduced to text.
    pub fn body_text(&self) -> String {
        if !self.text.trim().is_empty() {
            return self.text.clone();
        }
        crate::mime::html_to_text(&self.html)
    }
}

/// An attachment added to a draft.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DraftAttachment {
    pub name: String,
    pub mime: String,
    #[serde(with = "base64_bytes")]
    pub data: Vec<u8>,
    /// Set for an image the HTML body shows inline (`<img src="cid:…">`), not a file to list.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub cid: String,
}

/// A message being written.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Draft {
    pub account: String,
    pub to: Vec<Address>,
    pub cc: Vec<Address>,
    pub bcc: Vec<Address>,
    pub subject: String,
    pub body: String,
    /// The HTML version of the body (sent as multipart/alternative when set).
    pub html: String,
    pub in_reply_to: String,
    pub references: Vec<String>,
    pub attachments: Vec<DraftAttachment>,
    /// Set when the draft was saved to the Drafts folder (to replace it).
    pub draft_uid: Option<u32>,
    /// The message being replied to or forwarded (mailbox, uid), to flag it when sent.
    pub replying_to: Option<(String, u32)>,
}

impl Draft {
    pub fn recipients(&self) -> Vec<String> {
        self.to.iter().chain(&self.cc).chain(&self.bcc).map(|a| a.email.clone()).filter(|e| !e.is_empty()).take(limits::MAX_RECIPIENTS).collect()
    }

    pub fn is_empty(&self) -> bool {
        self.to.is_empty()
            && self.cc.is_empty()
            && self.bcc.is_empty()
            && self.subject.trim().is_empty()
            && self.body.trim().is_empty()
            && self.attachments.is_empty()
    }
}

/// The first ~160 characters of a body, whitespace collapsed.
pub fn snippet(text: &str) -> String {
    let mut out = String::new();
    let mut space = true;
    for c in text.chars() {
        if c.is_whitespace() {
            if !space {
                out.push(' ');
                space = true;
            }
        } else if !c.is_control() {
            out.push(c);
            space = false;
        }
        if out.len() >= 180 {
            break;
        }
    }
    crate::clip(out.trim_end(), 160).to_string()
}

mod base64_bytes {
    use serde::{Deserialize, Deserializer, Serializer};

    pub fn serialize<S: Serializer>(bytes: &[u8], s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&crate::mime::base64_encode(bytes))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Vec<u8>, D::Error> {
        let text = String::deserialize(d)?;
        crate::mime::base64_decode(&text).ok_or_else(|| serde::de::Error::custom("invalid base64"))
    }
}

/// Calendar helpers (UTC) without a date-time dependency.
pub mod time {
    /// Seconds since the Unix epoch, now.
    pub fn now() -> i64 {
        web_time::SystemTime::now().duration_since(web_time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
    }

    /// (year, month, day, hour, minute, second, weekday 0=Sunday) of a Unix time, UTC.
    pub fn civil(ts: i64) -> (i64, u32, u32, u32, u32, u32, u32) {
        let days = ts.div_euclid(86_400);
        let secs = ts.rem_euclid(86_400) as u32;
        // Howard Hinnant's days-from-civil inverse.
        let z = days + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
        let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
        let y = if m <= 2 { y + 1 } else { y };
        let weekday = (days + 4).rem_euclid(7) as u32;
        (y, m, d, secs / 3_600, (secs / 60) % 60, secs % 60, weekday)
    }

    /// Unix time of a UTC civil date.
    pub fn from_civil(y: i64, m: u32, d: u32, h: u32, mi: u32, s: u32) -> i64 {
        let (y, m) = if m <= 2 { (y - 1, m as i64 + 9) } else { (y, m as i64 - 3) };
        let era = y.div_euclid(400);
        let yoe = y.rem_euclid(400);
        let doy = (153 * m + 2) / 5 + d as i64 - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        let days = era * 146_097 + doe - 719_468;
        days * 86_400 + h as i64 * 3_600 + mi as i64 * 60 + s as i64
    }

    const DAYS: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const MONTHS_IT: [&str; 12] = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];

    /// `Mon, 12 Oct 2026 20:01:08 +0000` for a `Date:` header.
    pub fn rfc2822(ts: i64) -> String {
        let (y, m, d, h, mi, s, wd) = civil(ts);
        format!("{}, {d} {} {y} {h:02}:{mi:02}:{s:02} +0000", DAYS[wd as usize % 7], MONTHS[(m as usize).clamp(1, 12) - 1])
    }

    /// A short date for a list row: the time today, `12 Oct` this year, else `12/10/2025`.
    pub fn short(ts: i64, now: i64, italian: bool) -> String {
        if ts <= 0 {
            return String::new();
        }
        let (y, m, d, h, mi, _, _) = civil(ts);
        let (ny, nm, nd, ..) = civil(now);
        if (y, m, d) == (ny, nm, nd) {
            return format!("{h:02}:{mi:02}");
        }
        let months = if italian { MONTHS_IT } else { MONTHS };
        if y == ny {
            return format!("{d} {}", months[(m as usize).clamp(1, 12) - 1]);
        }
        if italian { format!("{d:02}/{m:02}/{y}") } else { format!("{y}-{m:02}-{d:02}") }
    }

    /// The machine's offset from UTC in minutes (the browser's on the web).
    pub fn host_offset_minutes() -> i32 {
        chrono::Local::now().offset().local_minus_utc() / 60
    }

    /// Reads a time zone preference: `auto` (none), `UTC`, `+02:00`, `-5`, `-05:30`, or minutes.
    pub fn parse_offset(text: &str) -> Option<i32> {
        let t = text.trim();
        if t.is_empty() || t.eq_ignore_ascii_case("auto") || t.eq_ignore_ascii_case("system") || t.eq_ignore_ascii_case("local") {
            return None;
        }
        if t.eq_ignore_ascii_case("utc") || t.eq_ignore_ascii_case("z") {
            return Some(0);
        }
        let (sign, rest) = match t.chars().next() {
            Some('+') => (1, &t[1..]),
            Some('-') => (-1, &t[1..]),
            _ => (1, t),
        };
        let minutes = match rest.split_once(':') {
            Some((h, m)) => h.trim().parse::<i32>().ok()? * 60 + m.trim().parse::<i32>().ok()?,
            None => {
                let n = rest.trim().parse::<i32>().ok()?;
                if n.abs() <= 14 { n * 60 } else { n }
            }
        };
        let v = sign * minutes;
        (-14 * 60..=14 * 60).contains(&v).then_some(v)
    }

    /// `+02:00` for an offset in minutes.
    pub fn offset_text(minutes: i32) -> String {
        format!("{}{:02}:{:02}", if minutes < 0 { '-' } else { '+' }, minutes.abs() / 60, minutes.abs() % 60)
    }

    /// Moves a UTC timestamp into a zone so [`civil`] gives wall-clock values.
    pub fn shifted(ts: i64, offset_minutes: i32) -> i64 {
        ts.saturating_add(i64::from(offset_minutes) * 60)
    }

    /// A full date and time: `12 Oct 2026, 20:01`.
    pub fn long(ts: i64, italian: bool) -> String {
        if ts <= 0 {
            return String::new();
        }
        let (y, m, d, h, mi, _, _) = civil(ts);
        let months = if italian { MONTHS_IT } else { MONTHS };
        format!("{d} {} {y}, {h:02}:{mi:02}", months[(m as usize).clamp(1, 12) - 1])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn time_zones_parse_and_shift() {
        assert_eq!(time::parse_offset("auto"), None);
        assert_eq!(time::parse_offset("+02:00"), Some(120));
        assert_eq!(time::parse_offset("-05:30"), Some(-330));
        assert_eq!(time::parse_offset("2"), Some(120));
        assert_eq!(time::parse_offset("120"), Some(120));
        assert_eq!(time::parse_offset("UTC"), Some(0));
        assert_eq!(time::parse_offset("+15:00"), None);
        assert_eq!(time::offset_text(120), "+02:00");
        assert_eq!(time::offset_text(-330), "-05:30");
        let ts = time::from_civil(2026, 10, 13, 22, 30, 0);
        assert_eq!(time::long(time::shifted(ts, 120), true), "14 ott 2026, 00:30");
        assert!(time::host_offset_minutes().abs() <= 14 * 60);
    }

    #[test]
    fn addresses_parse_and_display() {
        let list = Address::parse_list("Anna Rossi <anna@example.com>, luca@example.com; \"Bianchi, M.\" <m@x.it>, broken, <solo@x.it>");
        assert_eq!(list.len(), 4);
        assert_eq!(list[0], Address::new("Anna Rossi", "anna@example.com"));
        assert_eq!(list[1].display(), "luca@example.com");
        assert_eq!(list[2].name, "Bianchi, M.");
        assert_eq!(list[2].display(), "\"Bianchi, M.\" <m@x.it>");
        assert_eq!(list[3].email, "solo@x.it");
        assert_eq!(list[1].short(), "luca");
        assert_eq!(Address::parse_one("x@y.z (Name)").unwrap().name, "Name");
    }

    #[test]
    fn subjects_and_threads() {
        assert_eq!(normalize_subject("Re: RE: Fwd: R: I: Preventivo"), "preventivo");
        assert_eq!(normalize_subject("[Re] x"), "x");
        let root = Envelope { message_id: "<a@x>".into(), subject: "Hello".into(), ..Default::default() };
        let reply = Envelope {
            message_id: "<b@x>".into(),
            in_reply_to: "<a@x>".into(),
            references: vec!["<a@x>".into()],
            subject: "Re: Hello".into(),
            ..Default::default()
        };
        assert_eq!(root.thread_key(), reply.thread_key());
        let bare = Envelope { subject: "Re: Hello".into(), ..Default::default() };
        assert_eq!(bare.thread_key(), "subject:hello");
        assert_eq!(snippet("  Ciao\n\n  a   tutti \u{7} "), "Ciao a tutti");
    }

    #[test]
    fn calendar_maths() {
        assert_eq!(time::civil(0), (1970, 1, 1, 0, 0, 0, 4));
        let ts = time::from_civil(2026, 10, 12, 20, 1, 8);
        assert_eq!(time::civil(ts), (2026, 10, 12, 20, 1, 8, 1));
        assert_eq!(time::rfc2822(ts), "Mon, 12 Oct 2026 20:01:08 +0000");
        assert_eq!(time::short(ts, ts + 60, true), "20:01");
        assert_eq!(time::short(ts, ts + 86_400 * 3, true), "12 ott");
        assert_eq!(time::short(ts, ts + 86_400 * 400, false), "2026-10-12");
        assert_eq!(time::long(ts, false), "12 Oct 2026, 20:01");
        assert_eq!(time::civil(time::from_civil(2000, 2, 29, 0, 0, 0)).2, 29);
    }
}
