//! Mailboxes (IMAP folders) and their roles.

use serde::{Deserialize, Serialize};

/// What a mailbox is for, from SPECIAL-USE attributes or its name.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Inbox,
    Sent,
    Drafts,
    Trash,
    Spam,
    Archive,
    /// Gmail's "All Mail" (every message, archived ones included).
    All,
    Starred,
    Important,
    #[default]
    Other,
}

impl Role {
    pub const ALL: [Self; 10] =
        [Self::Inbox, Self::Starred, Self::Sent, Self::Drafts, Self::Archive, Self::All, Self::Important, Self::Spam, Self::Trash, Self::Other];

    pub const fn name(self) -> &'static str {
        match self {
            Self::Inbox => "inbox",
            Self::Sent => "sent",
            Self::Drafts => "drafts",
            Self::Trash => "trash",
            Self::Spam => "spam",
            Self::Archive => "archive",
            Self::All => "all",
            Self::Starred => "starred",
            Self::Important => "important",
            Self::Other => "other",
        }
    }

    /// The interface label.
    pub const fn label(self, italian: bool) -> &'static str {
        match (self, italian) {
            (Self::Inbox, false) => "Inbox",
            (Self::Inbox, true) => "Posta in arrivo",
            (Self::Sent, false) => "Sent",
            (Self::Sent, true) => "Inviati",
            (Self::Drafts, false) => "Drafts",
            (Self::Drafts, true) => "Bozze",
            (Self::Trash, false) => "Trash",
            (Self::Trash, true) => "Cestino",
            (Self::Spam, false) => "Spam",
            (Self::Spam, true) => "Spam",
            (Self::Archive, false) => "Archive",
            (Self::Archive, true) => "Archivio",
            (Self::All, false) => "All mail",
            (Self::All, true) => "Tutti i messaggi",
            (Self::Starred, false) => "Starred",
            (Self::Starred, true) => "Speciali",
            (Self::Important, false) => "Important",
            (Self::Important, true) => "Importanti",
            (Self::Other, _) => "",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|r| r.name() == text.trim().to_ascii_lowercase())
    }

    /// From an IMAP path and its LIST attributes (`\Sent`, `\Trash`…): attributes win,
    /// then well-known names in English and Italian (Gmail's `[Gmail]/…` included).
    pub fn detect(path: &str, attributes: &[String]) -> Self {
        for a in attributes {
            match a.trim_start_matches('\\').to_ascii_lowercase().as_str() {
                "inbox" => return Self::Inbox,
                "sent" => return Self::Sent,
                "drafts" => return Self::Drafts,
                "trash" => return Self::Trash,
                "junk" => return Self::Spam,
                "archive" => return Self::Archive,
                "all" => return Self::All,
                "flagged" => return Self::Starred,
                "important" => return Self::Important,
                _ => {}
            }
        }
        if path.eq_ignore_ascii_case("inbox") {
            return Self::Inbox;
        }
        let leaf = path.rsplit(['/', '.']).next().unwrap_or(path).trim().to_ascii_lowercase();
        match leaf.as_str() {
            "sent" | "sent items" | "sent messages" | "sent mail" | "posta inviata" | "inviati" | "inviata" | "elementi inviati" => Self::Sent,
            "drafts" | "draft" | "bozze" => Self::Drafts,
            "trash" | "deleted" | "deleted items" | "deleted messages" | "bin" | "cestino" | "eliminati" | "posta eliminata" => Self::Trash,
            "junk" | "spam" | "junk e-mail" | "junk email" | "bulk mail" | "posta indesiderata" => Self::Spam,
            "archive" | "archives" | "archivio" | "archiviati" => Self::Archive,
            "all mail" | "tutti i messaggi" => Self::All,
            "starred" | "speciali" | "flagged" => Self::Starred,
            "important" | "importanti" => Self::Important,
            _ => Self::Other,
        }
    }
}

/// An IMAP folder as the interface shows it.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Mailbox {
    /// The IMAP name (`INBOX`, `[Gmail]/Sent Mail`, `Archive/2025`).
    pub path: String,
    /// The last path segment, for display.
    pub name: String,
    #[serde(default)]
    pub role: Role,
    #[serde(default)]
    pub unread: u32,
    #[serde(default)]
    pub total: u32,
    /// Cannot hold messages (a container of sub-folders).
    #[serde(default)]
    pub no_select: bool,
    /// Subscribed on the server (IMAP LSUB): the navigation shows subscribed folders.
    #[serde(default = "yes")]
    pub subscribed: bool,
}

fn yes() -> bool {
    true
}

impl Mailbox {
    pub fn new(path: &str, attributes: &[String], delimiter: Option<&str>) -> Self {
        let name = delimiter.and_then(|d| path.rsplit(d).next()).unwrap_or(path).to_string();
        let role = Role::detect(path, attributes);
        let no_select = attributes.iter().any(|a| a.eq_ignore_ascii_case("\\noselect") || a.eq_ignore_ascii_case("\\nonexistent"));
        Self { path: path.to_string(), name, role, unread: 0, total: 0, no_select, subscribed: true }
    }

    /// Nesting depth from the delimiter in the path.
    pub fn depth(&self, delimiter: &str) -> usize {
        if delimiter.is_empty() { 0 } else { self.path.matches(delimiter).count() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roles_come_from_attributes_then_names() {
        let s = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(Role::detect("Whatever", &s(&["\\Sent"])), Role::Sent);
        assert_eq!(Role::detect("[Gmail]/Sent Mail", &[]), Role::Sent);
        assert_eq!(Role::detect("INBOX", &[]), Role::Inbox);
        assert_eq!(Role::detect("INBOX.Posta inviata", &[]), Role::Sent);
        assert_eq!(Role::detect("Cestino", &[]), Role::Trash);
        assert_eq!(Role::detect("Junk E-mail", &[]), Role::Spam);
        assert_eq!(Role::detect("[Gmail]/All Mail", &[]), Role::All);
        assert_eq!(Role::detect("Progetti/2026", &[]), Role::Other);
        let m = Mailbox::new("Archive/2025", &s(&["\\HasNoChildren"]), Some("/"));
        assert_eq!((m.name.as_str(), m.role, m.depth("/")), ("2025", Role::Other, 1));
        assert_eq!(Role::parse("Trash"), Some(Role::Trash));
    }
}
