//! Sending through SMTP with `lettre`.

use lettre::transport::smtp::authentication::{Credentials, Mechanism};
use lettre::transport::smtp::client::{Tls, TlsParameters};
use lettre::{SmtpTransport, Transport};
use mailcraft_core::account::{Account, Security};
use mailcraft_core::{Error, Result};

/// Connects and logs in to the SMTP server without sending anything.
pub fn test_connection(account: &Account, password: &str) -> Result<()> {
    let transport = transport(account, password)?;
    match transport.test_connection() {
        Ok(true) => Ok(()),
        Ok(false) => Err(Error::Connect("the SMTP server did not answer".into())),
        Err(e) => Err(classify(e)),
    }
}

fn classify(e: lettre::transport::smtp::Error) -> Error {
    let text = e.to_string();
    if text.contains("535") || (e.is_permanent() && text.to_ascii_lowercase().contains("auth")) {
        Error::Auth(text)
    } else if e.is_client() || text.to_ascii_lowercase().contains("connection") || text.to_ascii_lowercase().contains("tls") {
        Error::Connect(text)
    } else {
        Error::Server(text)
    }
}

fn transport(account: &Account, password: &str) -> Result<SmtpTransport> {
    let host = account.smtp.host.as_str();
    let tls = |kind: fn(TlsParameters) -> Tls| -> Result<Tls> {
        let mut p = TlsParameters::builder(host.to_string());
        if account.trust_invalid_certs {
            p = p.dangerous_accept_invalid_certs(true).dangerous_accept_invalid_hostnames(true);
        }
        Ok(kind(p.build_rustls().map_err(|e| Error::Connect(format!("TLS setup: {e}")))?))
    };
    let builder = match account.smtp.security {
        Security::Tls => SmtpTransport::relay(host).map_err(|e| Error::Connect(e.to_string()))?.port(account.smtp.port).tls(tls(Tls::Wrapper)?),
        Security::StartTls => SmtpTransport::relay(host).map_err(|e| Error::Connect(e.to_string()))?.port(account.smtp.port).tls(tls(Tls::Required)?),
        Security::None => SmtpTransport::builder_dangerous(host).port(account.smtp.port).tls(Tls::None),
    };
    let builder = if password.is_empty() {
        builder
    } else if account.oauth_provider().is_some() {
        builder.credentials(Credentials::new(account.login().to_string(), password.to_string())).authentication(vec![Mechanism::Xoauth2])
    } else {
        builder.credentials(Credentials::new(account.login().to_string(), password.to_string()))
    };
    Ok(builder.timeout(Some(std::time::Duration::from_secs(60))).build())
}

/// Sends raw RFC 822 bytes from `from` to `to` through the account's SMTP server.
pub fn send_raw(account: &Account, password: &str, from: &str, to: &[String], raw: &[u8]) -> Result<()> {
    let transport = transport(account, password)?;
    let from_addr: lettre::Address = from.trim().parse().map_err(|e| Error::Message(format!("sender `{from}`: {e}")))?;
    let mut to_addrs = Vec::with_capacity(to.len());
    for t in to {
        to_addrs.push(t.trim().parse::<lettre::Address>().map_err(|e| Error::Message(format!("recipient `{t}`: {e}")))?);
    }
    let envelope = lettre::address::Envelope::new(Some(from_addr), to_addrs).map_err(|e| Error::Message(e.to_string()))?;
    transport.send_raw(&envelope, raw).map(|_| ()).map_err(classify)
}
