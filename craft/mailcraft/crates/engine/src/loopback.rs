//! The loopback redirect for OAuth sign-in on this machine: a tiny HTTP listener on
//! `127.0.0.1` that takes the browser's return (`?code=…&state=…`), shows a "you can close this
//! window" page and hands the callback to the session (which finishes the flow in `pump`).

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;

use mailcraft_core::oauth::parse_callback;

/// What the browser brought back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Callback {
    pub state: String,
    pub code: String,
    pub error: String,
}

/// A running listener: its port and whether it is still waiting.
pub struct Loopback {
    pub port: u16,
    alive: Arc<AtomicBool>,
}

impl Loopback {
    pub fn alive(&self) -> bool {
        self.alive.load(Ordering::Relaxed)
    }

    pub fn redirect(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }
}

pub const PAGE_OK: &str = "<!doctype html><html><head><meta charset=\"utf-8\"><title>MailCraft</title></head>\
<body style=\"font-family:sans-serif;margin:3em;color:#222\"><h2>MailCraft</h2>\
<p>Accesso completato: puoi chiudere questa finestra e tornare a MailCraft.</p>\
<p>Sign-in complete: you can close this window and go back to MailCraft.</p></body></html>";

pub const PAGE_ERR: &str = "<!doctype html><html><head><meta charset=\"utf-8\"><title>MailCraft</title></head>\
<body style=\"font-family:sans-serif;margin:3em;color:#222\"><h2>MailCraft</h2>\
<p>Accesso non riuscito: torna a MailCraft e riprova.</p><p>Sign-in failed: go back to MailCraft and try again.</p></body></html>";

/// Starts listening on a free port; the thread ends after the first callback.
pub fn start(tx: Sender<Callback>) -> std::io::Result<Loopback> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    let alive = Arc::new(AtomicBool::new(true));
    let flag = alive.clone();
    std::thread::Builder::new().name("mailcraft-oauth".into()).spawn(move || {
        // Browsers also ask for /favicon.ico: answer anything until the callback arrives.
        for _ in 0..64 {
            let Ok((mut stream, _)) = listener.accept() else { break };
            let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(10)));
            let Ok(reader) = stream.try_clone() else { continue };
            let mut line = String::new();
            let _ = BufReader::new(reader).read_line(&mut line);
            let path = line.split_whitespace().nth(1).unwrap_or("/");
            let query = path.split_once('?').map(|(_, q)| q).unwrap_or("");
            let params = parse_callback(query);
            let state = params.get("state").cloned().unwrap_or_default();
            if state.is_empty() {
                let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                continue;
            }
            let cb =
                Callback { state, code: params.get("code").cloned().unwrap_or_default(), error: params.get("error").cloned().unwrap_or_default() };
            let page = if cb.error.is_empty() && !cb.code.is_empty() { PAGE_OK } else { PAGE_ERR };
            let _ = stream.write_all(
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{page}",
                    page.len()
                )
                .as_bytes(),
            );
            let _ = tx.send(cb);
            break;
        }
        flag.store(false, Ordering::Relaxed);
    })?;
    Ok(Loopback { port, alive })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpStream;

    #[test]
    fn takes_the_browser_back_and_ends() {
        let (tx, rx) = std::sync::mpsc::channel();
        let lb = start(tx).expect("listen");
        assert!(lb.alive());
        let mut s = TcpStream::connect(("127.0.0.1", lb.port)).expect("connect");
        s.write_all(b"GET /favicon.ico HTTP/1.1\r\nHost: x\r\n\r\n").expect("write");
        let mut buf = String::new();
        let _ = s.read_to_string(&mut buf);
        assert!(buf.starts_with("HTTP/1.1 404"));
        let mut s = TcpStream::connect(("127.0.0.1", lb.port)).expect("connect");
        s.write_all(b"GET /?state=st1&code=4%2Fabc&scope=x HTTP/1.1\r\nHost: x\r\n\r\n").expect("write");
        let mut buf = String::new();
        let _ = s.read_to_string(&mut buf);
        assert!(buf.contains("Accesso completato"));
        let cb = rx.recv_timeout(std::time::Duration::from_secs(5)).expect("callback");
        assert_eq!(cb, Callback { state: "st1".into(), code: "4/abc".into(), error: String::new() });
        for _ in 0..100 {
            if !lb.alive() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(!lb.alive());
    }
}
