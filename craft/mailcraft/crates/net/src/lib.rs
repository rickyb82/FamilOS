//! MailCraft's transport (layer L1): [`ImapSmtpBackend`] implements the core's
//! [`MailBackend`] over IMAP (the `imap` crate) and SMTP (`lettre`), both on rustls.
//!
//! Every call blocks; the engine runs them on its worker thread. Nothing here is UI-aware.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod imap_backend;
mod oauth;
mod smtp;
mod tls;

#[cfg(test)]
mod fake;

pub use imap_backend::ImapSmtpBackend;
pub use oauth::HttpBroker;
pub use smtp::{send_raw, test_connection};

use mailcraft_core::backend::BackendFactory;
use mailcraft_core::oauth::OAuthBroker;

/// The factory the apps hand to the engine: one IMAP/SMTP backend per account.
pub fn factory() -> BackendFactory {
    Box::new(|_account| Box::new(ImapSmtpBackend::default()))
}

/// The OAuth broker the apps hand to the engine (Google, Microsoft 365 sign-in).
pub fn broker() -> std::sync::Arc<dyn OAuthBroker> {
    std::sync::Arc::new(HttpBroker)
}
