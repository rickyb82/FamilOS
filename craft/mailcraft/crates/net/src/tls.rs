//! TLS for the IMAP connection with rustls and the system's root certificates. The `imap`
//! crate's own TLS feature pins an old rustls, so the stream is made here and handed to its
//! plain `Client`: implicit TLS (port 993), or STARTTLS after the plain greeting.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use imap::extensions::idle::SetReadTimeout;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::CryptoProvider;
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{ClientConfig, ClientConnection, DigitallySignedStruct, RootCertStore, SignatureScheme, StreamOwned};

/// Longest line read before the TLS handshake (greeting, STARTTLS answer).
const MAX_LINE: usize = 8 * 1024;
/// Untagged lines accepted while waiting for the answer to STARTTLS.
const MAX_LINES: usize = 64;
/// How long the plain exchange and the handshake may wait for the server.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(60);

/// A TLS connection the `imap` crate can drive.
pub struct TlsStream(StreamOwned<ClientConnection, TcpStream>);

impl Read for TlsStream {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        self.0.read(buf)
    }
}

impl Write for TlsStream {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.0.flush()
    }
}

impl SetReadTimeout for TlsStream {
    fn set_read_timeout(&mut self, timeout: Option<Duration>) -> imap::Result<()> {
        self.0.sock.set_read_timeout(timeout).map_err(imap::Error::Io)
    }
}

fn provider() -> Arc<CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

/// The system's trusted roots, loaded once.
fn roots() -> Arc<RootCertStore> {
    static ROOTS: OnceLock<Arc<RootCertStore>> = OnceLock::new();
    ROOTS
        .get_or_init(|| {
            let mut store = RootCertStore::empty();
            store.add_parsable_certificates(rustls_native_certs::load_native_certs().certs);
            Arc::new(store)
        })
        .clone()
}

/// Accepts any certificate (`trust_invalid_certs`, off by default: local bridges, self-hosted
/// servers) but still checks that the server holds the certificate's key.
#[derive(Debug)]
struct AnyCertificate(Arc<CryptoProvider>);

impl ServerCertVerifier for AnyCertificate {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(message, cert, dss, &self.0.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

fn config(trust_invalid_certs: bool) -> Result<Arc<ClientConfig>, String> {
    let provider = provider();
    let builder = ClientConfig::builder_with_provider(provider.clone()).with_safe_default_protocol_versions().map_err(|e| e.to_string())?;
    let config = if trust_invalid_certs {
        builder.dangerous().with_custom_certificate_verifier(Arc::new(AnyCertificate(provider))).with_no_client_auth()
    } else {
        builder.with_root_certificates(roots()).with_no_client_auth()
    };
    Ok(Arc::new(config))
}

/// Wraps a connected socket in TLS for `host` and completes the handshake, so a certificate
/// problem is a connection error and not a failure of the first command.
pub fn wrap(host: &str, tcp: TcpStream, trust_invalid_certs: bool) -> Result<TlsStream, String> {
    let name = ServerName::try_from(host.to_string()).map_err(|e| format!("`{host}`: {e}"))?;
    let conn = ClientConnection::new(config(trust_invalid_certs)?, name).map_err(|e| format!("TLS: {e}"))?;
    let _ = tcp.set_read_timeout(Some(HANDSHAKE_TIMEOUT));
    let mut stream = StreamOwned::new(conn, tcp);
    let mut rounds = 0;
    while stream.conn.is_handshaking() {
        rounds += 1;
        if rounds > 64 {
            return Err("TLS: the handshake does not end".into());
        }
        stream.conn.complete_io(&mut stream.sock).map_err(|e| format!("TLS: {e}"))?;
    }
    let _ = stream.sock.set_read_timeout(None);
    Ok(TlsStream(stream))
}

/// One line of the plain exchange, a byte at a time so nothing after it is consumed.
fn read_line(tcp: &mut TcpStream) -> Result<String, String> {
    let mut line = Vec::new();
    while line.len() < MAX_LINE {
        let mut byte = [0u8; 1];
        match tcp.read(&mut byte) {
            Ok(0) => return Err("the server closed the connection".into()),
            Ok(_) => {
                let [b] = byte;
                if b == b'\n' {
                    return Ok(String::from_utf8_lossy(&line).trim_end().to_string());
                }
                line.push(b);
            }
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("the server sent an overlong line".into())
}

/// The plain part of STARTTLS (RFC 3501 §6.2.1): the greeting, then `STARTTLS` and its OK.
/// After `Ok` the next bytes on the socket are the TLS handshake.
pub fn starttls(tcp: &mut TcpStream) -> Result<(), String> {
    let _ = tcp.set_read_timeout(Some(HANDSHAKE_TIMEOUT));
    let greeting = read_line(tcp)?;
    if !greeting.get(..4).is_some_and(|g| g.eq_ignore_ascii_case("* OK")) {
        return Err(format!("unexpected greeting: {}", mailcraft_core::clip(&greeting, 200)));
    }
    tcp.write_all(b"s1 STARTTLS\r\n").map_err(|e| e.to_string())?;
    for _ in 0..MAX_LINES {
        let line = read_line(tcp)?;
        if let Some(answer) = line.strip_prefix("s1 ") {
            if answer.get(..2).is_some_and(|a| a.eq_ignore_ascii_case("OK")) {
                return Ok(());
            }
            return Err(format!("the server refused STARTTLS: {}", mailcraft_core::clip(answer, 200)));
        }
    }
    Err("no answer to STARTTLS".into())
}
