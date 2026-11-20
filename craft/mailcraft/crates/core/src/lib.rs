//! MailCraft's model (layer L0): account configuration and provider autoconfiguration,
//! mailboxes, message envelopes and bodies, MIME parsing, the backend trait every mail
//! transport implements, and an in-memory mock backend for tests and demos.
//!
//! Nothing here touches the network; `mailcraft-net` implements [`MailBackend`] over IMAP and
//! SMTP, and `mailcraft-engine` drives it with commands.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

pub mod account;
pub mod autoconfig;
pub mod backend;
pub mod link;
pub mod mailbox;
pub mod message;
pub mod mime;
pub mod mock;
pub mod oauth;

pub use account::{Account, AccountId, Config, Preferences, Secrets, Security, Server, SpecialFolders};
pub use backend::{FlagChange, MailBackend, Page, SearchQuery};
pub use mailbox::{Mailbox, Role};
pub use message::{Address, Attachment, Draft, Envelope, Flags, Message};

/// Hard limits that keep hostile input (servers, files, parameters) from exhausting memory.
pub mod limits {
    /// Longest header value, subject or address kept.
    pub const MAX_TEXT: usize = 4_000;
    /// Longest message body kept in memory (bytes of decoded text or HTML).
    pub const MAX_BODY: usize = 4 * 1024 * 1024;
    /// Largest raw message fetched from a server.
    pub const MAX_RAW_MESSAGE: usize = 64 * 1024 * 1024;
    /// Envelopes kept per mailbox in the cache.
    pub const MAX_ENVELOPES: usize = 50_000;
    /// Messages requested per page.
    pub const MAX_PAGE: usize = 500;
    /// Attachments listed per message.
    pub const MAX_ATTACHMENTS: usize = 500;
    /// Accounts in a configuration.
    pub const MAX_ACCOUNTS: usize = 64;
    /// Mailboxes per account.
    pub const MAX_MAILBOXES: usize = 2_000;
    /// Recipients on one message.
    pub const MAX_RECIPIENTS: usize = 500;
    /// Longest attachment file name kept (bytes, clipped at a char boundary).
    pub const MAX_FILE_NAME: usize = 255;
    /// Largest file an agent hands over or gets back as base64 through a tool (bytes).
    pub const MAX_TOOL_FILE: usize = 25 * 1024 * 1024;
}

/// Errors of the model and of every backend.
#[derive(Debug, thiserror::Error, Clone, PartialEq, Eq)]
pub enum Error {
    #[error("{0}")]
    Config(String),
    #[error("no account `{0}`")]
    NoAccount(String),
    #[error("no mailbox `{0}`")]
    NoMailbox(String),
    #[error("no message {0}")]
    NoMessage(u32),
    #[error("not connected")]
    NotConnected,
    #[error("connection failed: {0}")]
    Connect(String),
    #[error("login failed: {0}")]
    Auth(String),
    #[error("server error: {0}")]
    Server(String),
    #[error("invalid message: {0}")]
    Message(String),
    #[error("{0}")]
    Io(String),
}

pub type Result<T> = std::result::Result<T, Error>;

/// Truncates at a char boundary.
pub fn clip(text: &str, max: usize) -> &str {
    if text.len() <= max {
        return text;
    }
    let mut end = max;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    text.get(..end).unwrap_or_default()
}

/// Drops control characters (keeps newlines) and clips.
pub fn clean(text: &str, max: usize) -> String {
    let cleaned: String = text.chars().filter(|c| *c == '\n' || !c.is_control()).collect();
    clip(&cleaned, max).to_string()
}
