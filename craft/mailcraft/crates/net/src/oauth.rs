//! The OAuth broker over HTTPS: exchanges authorization codes and refreshes tokens at the
//! provider's token endpoint (`ehttp`, blocking: the engine's worker calls it).

use mailcraft_core::oauth::{ClientCredentials, OAuthBroker, Provider, Tokens, exchange_body, refresh_body};
use mailcraft_core::{Error, Result};

/// Talks to the provider's token endpoint.
#[derive(Default)]
pub struct HttpBroker;

impl HttpBroker {
    fn post(&self, url: &str, body: String, old_refresh: &str) -> Result<Tokens> {
        let mut req = ehttp::Request::post(url, body.into_bytes());
        // `Request::post` presets `text/plain` and `Headers::insert` adds rather than replaces:
        // the token endpoint must see the form type alone.
        req.headers = ehttp::Headers::new(&[("Accept", "application/json"), ("Content-Type", "application/x-www-form-urlencoded")]);
        let resp = ehttp::fetch_blocking(&req).map_err(|e| Error::Connect(format!("OAuth token endpoint: {e}")))?;
        let v: serde_json::Value = serde_json::from_slice(&resp.bytes).map_err(|_| {
            let body = String::from_utf8_lossy(&resp.bytes);
            Error::Auth(format!("OAuth token endpoint answered {} without JSON: {}", resp.status, mailcraft_core::clip(body.trim(), 300)))
        })?;
        Tokens::from_response(&v, old_refresh, mailcraft_core::message::time::now())
            .map_err(|e| if resp.ok { e } else { Error::Auth(format!("{e} (HTTP {})", resp.status)) })
    }
}

impl OAuthBroker for HttpBroker {
    fn exchange(&self, provider: &Provider, client: &ClientCredentials, code: &str, verifier: &str, redirect: &str) -> Result<Tokens> {
        self.post(provider.token_url, exchange_body(client, code, verifier, redirect), "")
    }

    fn refresh(&self, provider: &Provider, client: &ClientCredentials, refresh_token: &str) -> Result<Tokens> {
        self.post(provider.token_url, refresh_body(client, refresh_token), refresh_token)
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::*;

    /// A one-shot token endpoint: answers tokens only to a form-encoded request.
    fn token_endpoint(answer: &'static str) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().expect("addr").port();
        std::thread::spawn(move || {
            let Ok((mut s, _)) = listener.accept() else { return };
            // Headers, then the body up to Content-Length (they may arrive in separate reads).
            let mut raw = Vec::new();
            let mut buf = [0u8; 4096];
            let (head, body) = loop {
                let n = s.read(&mut buf).unwrap_or(0);
                if n == 0 {
                    break (String::from_utf8_lossy(&raw).to_string(), String::new());
                }
                raw.extend_from_slice(&buf[..n]);
                let text = String::from_utf8_lossy(&raw).to_string();
                if let Some((head, body)) = text.split_once("\r\n\r\n") {
                    let want: usize = head
                        .lines()
                        .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap_or(0)))
                        .unwrap_or(0);
                    if body.len() >= want {
                        break (head.to_string(), body.to_string());
                    }
                }
            };
            let (head, body) = (head.as_str(), body.as_str());
            let types: Vec<&str> = head.lines().filter(|l| l.to_ascii_lowercase().starts_with("content-type:")).collect();
            let ok = types.len() == 1 && types[0].to_ascii_lowercase().contains("application/x-www-form-urlencoded") && body.contains("grant_type=");
            let (status, out) = if ok {
                ("200 OK", answer.to_string())
            } else {
                (
                    "400 Bad Request",
                    format!(r#"{{"error": {{"status": "INVALID_ARGUMENT", "message": "Invalid JSON payload received: {types:?}"}}}}"#),
                )
            };
            let _ = s.write_all(
                format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{out}", out.len())
                    .as_bytes(),
            );
        });
        port
    }

    #[test]
    fn posts_a_form_and_reads_tokens_and_errors() {
        let port = token_endpoint(r#"{"access_token": "at", "refresh_token": "rt", "expires_in": 3599}"#);
        let google = Provider { token_url: "", ..*mailcraft_core::oauth::provider("google").expect("google") };
        let client = ClientCredentials { client_id: "cid".into(), client_secret: "sec".into() };
        let url = format!("http://127.0.0.1:{port}/token");
        let t = HttpBroker.post(&url, exchange_body(&client, "c0de", "ver", "http://127.0.0.1:1/"), "").expect("tokens");
        assert_eq!((t.access_token.as_str(), t.refresh_token.as_str()), ("at", "rt"));
        let port = token_endpoint(r#"{"error": "invalid_grant", "error_description": "Bad Request"}"#);
        let e = HttpBroker.post(&format!("http://127.0.0.1:{port}/token"), refresh_body(&client, "rt"), "rt").unwrap_err().to_string();
        assert!(e.contains("invalid_grant Bad Request"), "{e}");
        let _ = google;
    }
}
