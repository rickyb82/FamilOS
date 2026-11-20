//! Interface languages. Labels are written in English in the code and looked up here; anything
//! without a translation shows in English. Messages with values use `{name}` placeholders.

mod it;

/// An interface language.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Language {
    #[default]
    En,
    It,
}

impl Language {
    pub const ALL: [Self; 2] = [Self::En, Self::It];

    pub const fn name(self) -> &'static str {
        match self {
            Self::En => "English",
            Self::It => "Italiano",
        }
    }

    pub const fn code(self) -> &'static str {
        match self {
            Self::En => "en",
            Self::It => "it",
        }
    }

    /// Reads a code or a locale tag (`it`, `it-IT`, `it_CH.UTF-8`).
    pub fn parse(tag: &str) -> Option<Self> {
        match tag.trim().split(['-', '_', '.']).next()?.to_ascii_lowercase().as_str() {
            "en" => Some(Self::En),
            "it" => Some(Self::It),
            _ => None,
        }
    }

    /// The system's language when supported, else English.
    pub fn system() -> Self {
        sys_locale::get_locale().and_then(|tag| Self::parse(&tag)).unwrap_or_default()
    }

    /// Translates an interface label.
    pub fn tr(self, text: &str) -> &str {
        match self {
            Self::En => text,
            Self::It => it::translate(text).unwrap_or(text),
        }
    }

    /// Translates a message and fills its `{name}` placeholders.
    pub fn fill(self, text: &str, values: &[(&str, &str)]) -> String {
        let mut out = self.tr(text).to_string();
        for (k, v) in values {
            out = out.replace(&format!("{{{k}}}"), v);
        }
        out
    }

    /// Translates the engine's error messages by their leading phrase, keeping the details.
    pub fn tr_message(self, text: &str) -> String {
        if self == Self::En {
            return text.to_string();
        }
        for (en, itx) in it::MESSAGES {
            if let Some(rest) = text.strip_prefix(en) {
                return format!("{itx}{rest}");
            }
        }
        text.to_string()
    }
}

/// Every label the interface translates (kept in step with the `it` table by a test).
pub const LABELS: &[&str] = &[
    "Compose",
    "Search mail",
    "Sync",
    "Settings",
    "Light theme",
    "Dark theme",
    "Language",
    "Inbox",
    "Starred",
    "Sent",
    "Drafts",
    "Archive",
    "Spam",
    "Trash",
    "All mail",
    "Important",
    "Folders",
    "Accounts",
    "Add account",
    "All accounts",
    "Message sent, but the copy to the Sent folder failed: {error}",
    "Sign-in",
    "Sign in with {provider}",
    "Signed in with {provider}",
    "Sign in again",
    "Signing in with {provider} needs its OAuth client, created once (usually by the administrator): paste its id and secret here and save, then the sign-in button appears.",
    "Or, without a client:",
    "Use an app password",
    "Not the account password: an app password from the {provider} account (two-step verification on)",
    "Complete the sign-in in the browser, then come back here (return address registered with the provider: {redirect})",
    "Signed in",
    "Sign in with Google or Microsoft 365 (OAuth)",
    "Gmail and Microsoft 365 mailboxes sign in through the provider, like in Thunderbird or Outlook, instead of a password. Each provider asks the app for a client: create it once in the provider's console (free, a few minutes), paste its id and secret here, then choose the provider as sign-in in the account form.",
    "Redirect URI to register: {uri}",
    "Configured",
    "Not configured",
    "Open the provider's console",
    "Client ID",
    "Client secret",
    "Save client",
    "Folders on the server",
    "Shown",
    "Hidden",
    "Automatic",
    "Sent folder",
    "Drafts folder",
    "Trash folder",
    "Spam folder",
    "Archive folder",
    "Refresh folders",
    "New folder",
    "Create",
    "Save the account first: its folders are listed here, with the ones to show in the navigation and the special folders (sent, drafts, trash, spam, archive).",
    "Unchecked folders stay on the server but leave the navigation (IMAP subscription). The special folders are found by name; pick them here when the server names them differently.",
    "Folder",
    "{n} accounts",
    "No messages",
    "No messages match the search",
    "Loading…",
    "Syncing…",
    "Synced {time}",
    "Sending…",
    "Saving draft…",
    "Message sent",
    "Draft saved",
    "Archived",
    "Moved to the trash",
    "Reported as spam",
    "Moved",
    "Deleted forever",
    "Marked",
    "Star changed",
    "Undo",
    "Reply",
    "Reply all",
    "Forward",
    "Mark as read",
    "Mark as unread",
    "Add star",
    "Remove star",
    "Move to",
    "Delete forever",
    "Back",
    "Close",
    "To",
    "Cc",
    "Bcc",
    "From",
    "Date",
    "Subject",
    "New message",
    "Recipients",
    "Send",
    "Save draft",
    "Discard",
    "Attach files",
    "Print",
    "Time zone",
    "Apply",
    "Shown: UTC{effective} (auto = this device's zone, now UTC{detected})",
    "Suggested replies…",
    "The assistant is available in the web app only",
    "Reply with this",
    "What do you want to say?",
    "Write with the assistant",
    "Reply suggestions from the assistant under each message",
    "Let the assistant use cloud models too (otherwise local models only: your mail never leaves the network)",
    "Bold",
    "Italic",
    "Underline",
    "Bulleted list",
    "Numbered list",
    "Insert link",
    "Insert image",
    "Small",
    "Normal",
    "Large",
    "Huge",
    "Text color",
    "Align",
    "Align left",
    "Align center",
    "Align right",
    "Indent",
    "Redo",
    "More send options",
    "Formatting options",
    "Insert emoji",
    "Insert signature",
    "More options",
    "No signature set for this account: add one in Settings",
    "Black",
    "Gray",
    "Red",
    "Orange",
    "Yellow",
    "Green",
    "Teal",
    "Blue",
    "Purple",
    "Dark red",
    "Dark blue",
    "White",
    "Formatting: **bold**, *italic*, __underline__, lists with - or 1., links [text](url); the message goes out as text and HTML",
    "Attachments",
    "Save",
    "Add at least one recipient",
    "Wait for the message to load",
    "---------- Forwarded message ----------",
    "Connection OK: IMAP and SMTP accept the login",
    "Servers found",
    "No {role} folder on this account",
    "Select a message to read it",
    "messages",
    "selected",
    "Select all",
    "Clear selection",
    "Edit draft",
    "Account",
    "Name",
    "E-mail address",
    "Display name",
    "Username (if not the address)",
    "Password",
    "IMAP server",
    "SMTP server",
    "Host",
    "Port",
    "Security",
    "Signature",
    "Signature (HTML)",
    "Paste the HTML of your signature: logo (<img src=\"https://…\"> or a data: image), bold, links. Messages then go out as text + HTML; the plain signature above is the text version (derived from the HTML when empty).",
    "Accept invalid certificates (local bridges, self-hosted servers)",
    "Copy sent mail to the Sent folder",
    "Suggest from the provider",
    "Find servers",
    "Test connection",
    "Remove account",
    "Save account",
    "Cancel",
    "Preferences",
    "Theme",
    "System",
    "Light",
    "Dark",
    "Sync every (minutes, 0 = manual)",
    "Messages per page",
    "Reading pane beside the list",
    "Group messages into conversations",
    "Mark messages read when opened",
    "Default account",
    "Assistant (MCP)",
    "Configure MailCraft in plain language: connect the MCP server to Claude Code, Claude Desktop or any MCP client and say, for example, “set up my Aruba mailbox info@example.it” or “dark theme, sync every 2 minutes”. The assistant can also read, triage and answer your mail.",
    "Command for Claude Code:",
    "Keyboard shortcuts",
    "c compose · / search · j/k move · Enter open · e archive · # trash · s star · u unread · r reply · a reply all · f forward · Cmd+R sync · Esc close",
    "Mailboxes: {n} · Unread: {unread}",
    "No account yet. Add one here, or ask the assistant (MCP) to set it up from a sentence.",
    "Provider notes",
    "Last sync",
    "never",
    "Error",
    "Show {n} more messages",
    "Conversation",
    "Delete draft",
    "Saved",
    "Open folder",
    "Everything stays on this computer: the password is kept in a file only you can read.",
    "Done",
    "Mailbox",
    "Original attachments included: {names}",
    "Web app server",
    "The web app keeps your accounts on a mailcraft-cli server (your computer or your own machine). Start it with `mailcraft-cli serve`, then connect here.",
    "Server",
    "Connect",
    "Connected",
    "Server unreachable ({url}): {error}. Start it with: mailcraft-cli serve",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_label_has_an_italian_translation() {
        for (i, label) in LABELS.iter().enumerate() {
            assert!(LABELS.iter().take(i).all(|o| o != label), "duplicate {label}");
            assert!(it::translate(label).is_some_and(|t| !t.is_empty()), "missing Italian: {label}");
        }
        assert_eq!(Language::It.tr("Inbox"), "Posta in arrivo");
        assert_eq!(Language::En.tr("Inbox"), "Inbox");
        assert_eq!(Language::It.fill("Synced {time}", &[("time", "10:00")]), "Sincronizzato alle 10:00");
        assert_eq!(Language::It.tr_message("login failed: bad"), "accesso non riuscito: bad");
        assert_eq!(Language::parse("it-IT"), Some(Language::It));
        assert_eq!(Language::parse("de"), None);
    }
}
