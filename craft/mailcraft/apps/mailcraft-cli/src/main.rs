//! MailCraft headless.
//!
//! ```text
//! mailcraft-cli mcp [--demo]                        # MCP server on stdio, for AI agents
//! mailcraft-cli serve [--bind 127.0.0.1:7878] [--static DIR] [--token SECRET] [--open] [--users-dir DIR] [--public-url URL] [--assist-url URL] [--allow-origin ORIGIN]…   # the web app's server
//! mailcraft-cli mcp --server URL --token T                # MCP for one user of a multi-user server
//! mailcraft-cli accounts                            # configured accounts and their status
//! mailcraft-cli suggest EMAIL [--lang it]           # IMAP/SMTP settings for an address
//! mailcraft-cli add EMAIL [--password PW] [--name N] [--imap host:port[:tls|starttls|none]] [--smtp host:port[:sec]] [--no-test]
//! mailcraft-cli oauth google|microsoft CLIENT_ID [CLIENT_SECRET]   # the OAuth client created in the provider's console
//! mailcraft-cli login EMAIL [--provider google|microsoft]   # sign in with Google / Microsoft in the browser
//! mailcraft-cli test [ACCOUNT]                      # log in to IMAP and SMTP
//! mailcraft-cli remove ACCOUNT
//! mailcraft-cli set KEY VALUE                       # a preference (theme, language, sync_minutes…)
//! mailcraft-cli sync [ACCOUNT]
//! mailcraft-cli list [--account A] [--mailbox M] [--unread] [--page N]
//! mailcraft-cli search QUERY [--account A] [--mailbox M]
//! mailcraft-cli read UID [--account A] [--mailbox M]
//! mailcraft-cli send --to X [--cc Y] --subject S (--body B | --body-file F) [--attach PATH]… [--account A]
//! mailcraft-cli run COMMAND [JSON]                  # any engine command
//! mailcraft-cli commands                            # every command and its parameters
//! ```
//! `--home DIR` (or `MAILCRAFT_HOME`) selects the configuration directory; `--demo` uses the
//! built-in demo mailbox instead of real servers.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod serve;

use std::process::ExitCode;

use mailcraft_engine::core::backend::BackendFactory;
use mailcraft_engine::core::message::time;
use mailcraft_engine::{COMMANDS, Paths, Session};
use serde_json::{Value, json};

const USAGE: &str = "Usage:
  mailcraft-cli mcp [--demo]
  mailcraft-cli serve [--bind 127.0.0.1:7878] [--static DIR] [--token SECRET] [--open] [--users-dir DIR] [--public-url URL] [--assist-url URL] [--allow-origin ORIGIN]…
  mailcraft-cli mcp --server URL --token USER_TOKEN
  mailcraft-cli accounts
  mailcraft-cli suggest EMAIL [--lang it]
  mailcraft-cli add EMAIL [--password PW] [--name N] [--imap host:port[:sec]] [--smtp host:port[:sec]] [--no-test]
  mailcraft-cli oauth google|microsoft CLIENT_ID [CLIENT_SECRET]
  mailcraft-cli login EMAIL [--provider google|microsoft]
  mailcraft-cli test [ACCOUNT]
  mailcraft-cli remove ACCOUNT
  mailcraft-cli set KEY VALUE
  mailcraft-cli sync [ACCOUNT]
  mailcraft-cli list [--account A] [--mailbox M] [--unread] [--page N]
  mailcraft-cli search QUERY [--account A] [--mailbox M]
  mailcraft-cli read UID [--account A] [--mailbox M]
  mailcraft-cli send --to X [--cc Y] --subject S (--body B | --body-file F) [--attach PATH]... [--account A]
  mailcraft-cli run COMMAND [JSON]
  mailcraft-cli commands
Options: --home DIR (configuration directory), --demo (the built-in demo mailbox), --lang en|it";

struct Opts {
    args: Vec<String>,
    demo: bool,
    home: Option<String>,
    lang: Option<String>,
}

fn parse(raw: &[String]) -> Opts {
    let mut o = Opts { args: Vec::new(), demo: false, home: None, lang: None };
    let mut it = raw.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--demo" => o.demo = true,
            "--home" => o.home = it.next().cloned(),
            "--lang" => o.lang = it.next().cloned(),
            _ => o.args.push(a.clone()),
        }
    }
    o
}

/// `--key value` after the positional arguments; flags without a value (`--unread`) are `true`.
fn flag(args: &[String], key: &str) -> Option<String> {
    let i = args.iter().position(|a| a == key)?;
    match args.get(i + 1) {
        Some(v) if !v.starts_with("--") => Some(v.clone()),
        _ => Some("true".into()),
    }
}

fn flags_all(args: &[String], key: &str) -> Vec<String> {
    args.iter().enumerate().filter(|(_, a)| *a == key).filter_map(|(i, _)| args.get(i + 1).cloned()).collect()
}

/// Opens a URL in the system browser (best effort): a web or mail address in one piece
/// (`core::link::openable`), passed as a single argument and never through a shell (on
/// Windows `cmd /C start` would run whatever follows an `&`).
fn open_browser(url: &str) {
    let Some(url) = mailcraft_engine::core::link::openable(url) else { return };
    let mut command = if cfg!(target_os = "macos") {
        std::process::Command::new("open")
    } else if cfg!(windows) {
        let mut c = std::process::Command::new("rundll32.exe");
        c.arg("url.dll,FileProtocolHandler");
        c
    } else {
        std::process::Command::new("xdg-open")
    };
    let _ = command.arg(url).spawn();
}

fn session(o: &Opts) -> Session {
    if o.demo {
        return Session::demo();
    }
    let paths = match &o.home {
        Some(h) => Paths::under(std::path::Path::new(h)),
        None => Paths::default_dirs(),
    };
    let factory: BackendFactory = mailcraft_net::factory();
    let mut s = Session::with_broker(factory, mailcraft_net::broker(), Some(paths));
    if let Some(l) = &o.lang {
        let _ = s.execute("prefs.set", &json!({"language": l}));
    }
    s
}

fn server(args: &[&str]) -> Result<Value, String> {
    let (host, rest) = args.first().copied().unwrap_or_default().split_once(':').ok_or("expected host:port[:security]")?;
    let (port, sec) = match rest.split_once(':') {
        Some((p, s)) => (p, Some(s)),
        None => (rest, None),
    };
    let port: u32 = port.parse().map_err(|_| "the port must be a number".to_string())?;
    let mut v = json!({"host": host, "port": port});
    if let Some(s) = sec {
        v["security"] = json!(s);
    }
    Ok(v)
}

fn print_list(v: &Value, italian: bool, offset: i32) {
    let tz = offset;
    let now = time::shifted(time::now(), tz);
    let list = v["envelopes"].as_array().cloned().unwrap_or_default();
    if list.is_empty() {
        println!("(no messages)");
    }
    for e in list {
        let flags = format!(
            "{}{}{}",
            if e["unread"] == true { "●" } else { " " },
            if e["starred"] == true { "★" } else { " " },
            if e["has_attachments"] == true { "📎" } else { " " }
        );
        let from = e["from"].as_array().and_then(|a| a.first()).and_then(Value::as_str).unwrap_or("");
        println!(
            "[{:>5}] {flags} {:>9}  {:<28} {}",
            e["uid"],
            time::short(time::shifted(e["date"].as_i64().unwrap_or(0), tz), now, italian),
            from.split(" <").next().unwrap_or(from).chars().take(28).collect::<String>(),
            e["subject"].as_str().unwrap_or("")
        );
    }
}

fn run(raw: &[String]) -> Result<(), String> {
    let o = parse(raw);
    let args: Vec<&str> = o.args.iter().map(String::as_str).collect();
    let rest = |n: usize| o.args.get(n..).map(|s| s.to_vec()).unwrap_or_default();
    match args.first().copied() {
        Some("mcp") => {
            let tail = rest(1);
            let mut server = match flag(&tail, "--server") {
                Some(url) => mailcraft_mcp::Server::new(mailcraft_remote::RemoteSession::new(&url, &flag(&tail, "--token").unwrap_or_default())),
                None => mailcraft_mcp::Server::new(session(&o)),
            };
            let stdin = std::io::stdin();
            let stdout = std::io::stdout();
            server.serve(stdin.lock(), stdout.lock()).map_err(|e| e.to_string())
        }
        Some("serve") => {
            let tail = rest(1);
            let static_dir = flag(&tail, "--static").map(std::path::PathBuf::from).or_else(|| {
                // The web app built with trunk, when it sits next to the sources or the binary.
                ["dist/web", "../dist/web"].iter().map(std::path::PathBuf::from).find(|p| p.join("index.html").is_file())
            });
            serve::run(
                session(&o),
                serve::Options {
                    bind: flag(&tail, "--bind").unwrap_or_else(|| "127.0.0.1:7878".into()),
                    token: flag(&tail, "--token").filter(|t| t != "true").or_else(|| std::env::var("MAILCRAFT_TOKEN").ok()).unwrap_or_default(),
                    static_dir,
                    open: flag(&tail, "--open").is_some(),
                    users_dir: flag(&tail, "--users-dir").map(std::path::PathBuf::from),
                    demo: o.demo,
                    lang: o.lang.clone(),
                    public_url: flag(&tail, "--public-url").or_else(|| std::env::var("MAILCRAFT_PUBLIC_URL").ok()).filter(|u| !u.trim().is_empty()),
                    assist_url: flag(&tail, "--assist-url").or_else(|| std::env::var("MAILCRAFT_ASSIST_URL").ok()).filter(|u| !u.trim().is_empty()),
                    allow_origins: serve::parse_origins(
                        &flags_all(&tail, "--allow-origin")
                            .into_iter()
                            .chain(std::env::var("MAILCRAFT_ALLOW_ORIGINS").ok())
                            .collect::<Vec<_>>()
                            .join(","),
                    ),
                },
            )
        }
        Some("commands") => {
            for c in COMMANDS {
                println!("{:<22} {:<22} {}", c.id, c.label, c.params);
            }
            Ok(())
        }
        Some("accounts") => {
            let mut s = session(&o);
            let v = s.execute("account.list", &json!({})).map_err(|e| e.to_string())?;
            let list = v["accounts"].as_array().cloned().unwrap_or_default();
            if list.is_empty() {
                println!("No accounts. Add one: mailcraft-cli add you@example.com --password … (or ask the MCP assistant).");
            }
            for a in list {
                println!(
                    "{:<24} {:<32} IMAP {}:{} ({})  SMTP {}:{} ({})  password: {}  unread: {}{}",
                    a["id"].as_str().unwrap_or(""),
                    a["email"].as_str().unwrap_or(""),
                    a["imap"]["host"].as_str().unwrap_or(""),
                    a["imap"]["port"],
                    a["imap"]["security"].as_str().unwrap_or(""),
                    a["smtp"]["host"].as_str().unwrap_or(""),
                    a["smtp"]["port"],
                    a["smtp"]["security"].as_str().unwrap_or(""),
                    if a["has_password"] == true { "set" } else { "missing" },
                    a["status"]["unread"],
                    a["status"]["error"].as_str().map(|e| format!("  error: {e}")).unwrap_or_default()
                );
            }
            Ok(())
        }
        Some("suggest") => {
            let email = args.get(1).ok_or(USAGE)?;
            let mut s = session(&o);
            let v =
                s.execute("account.suggest", &json!({"email": email, "language": o.lang.clone().unwrap_or_default()})).map_err(|e| e.to_string())?;
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            Ok(())
        }
        Some("add") => {
            let email = args.get(1).ok_or(USAGE)?;
            let tail = rest(2);
            let mut prm = json!({"email": email});
            for key in ["password", "name", "display-name", "username", "signature"] {
                if let Some(v) = flag(&tail, &format!("--{key}")) {
                    prm[key.replace('-', "_")] = json!(v);
                }
            }
            if let Some(v) = flag(&tail, "--imap") {
                let sv = server(&[&v])?;
                prm["imap_host"] = sv["host"].clone();
                prm["imap_port"] = sv["port"].clone();
                if let Some(sec) = sv.get("security") {
                    prm["imap_security"] = sec.clone();
                }
            }
            if let Some(v) = flag(&tail, "--smtp") {
                let sv = server(&[&v])?;
                prm["smtp_host"] = sv["host"].clone();
                prm["smtp_port"] = sv["port"].clone();
                if let Some(sec) = sv.get("security") {
                    prm["smtp_security"] = sec.clone();
                }
            }
            if flag(&tail, "--trust-invalid-certs").is_some() {
                prm["trust_invalid_certs"] = json!(true);
            }
            let mut s = session(&o);
            let v = s.execute("account.add", &prm).map_err(|e| e.to_string())?;
            let id = v["id"].as_str().unwrap_or_default().to_string();
            println!(
                "Saved account {id}: IMAP {}:{}  SMTP {}:{}",
                v["account"]["imap"]["host"], v["account"]["imap"]["port"], v["account"]["smtp"]["host"], v["account"]["smtp"]["port"]
            );
            for n in v["suggestion"]["notes"].as_array().into_iter().flatten().filter_map(Value::as_str) {
                println!("note: {n}");
            }
            if flag(&tail, "--no-test").is_some() || flag(&tail, "--password").is_none() {
                return Ok(());
            }
            if v["suggestion"]["confidence"] == "guess" && flag(&tail, "--imap").is_none() {
                match s.run_blocking("account.probe", json!({"id": id})) {
                    Ok(p) => println!("Servers found: IMAP {}  SMTP {}", p["imap"], p["smtp"]),
                    Err(e) => return Err(format!("no server accepted the login: {e}")),
                }
            }
            let t = s.run_blocking("account.test", json!({"id": id})).map_err(|e| e.to_string())?;
            println!("IMAP: {} ({} mailboxes)  SMTP: {}", t["imap"].as_str().unwrap_or(""), t["mailboxes"], t["smtp"].as_str().unwrap_or(""));
            Ok(())
        }
        Some("oauth") => {
            let provider = args.get(1).ok_or(USAGE)?;
            let client_id = args.get(2).cloned().unwrap_or_default();
            let client_secret = args.get(3).cloned().unwrap_or_default();
            let mut s = session(&o);
            let v = s
                .execute("oauth.configure", &json!({"provider": provider, "client_id": client_id, "client_secret": client_secret}))
                .map_err(|e| e.to_string())?;
            println!(
                "{}: {}",
                v["provider"].as_str().unwrap_or(provider),
                if v["configured"] == true { "client configured" } else { "client removed" }
            );
            Ok(())
        }
        Some("login") => {
            let email = args.get(1).ok_or(USAGE)?;
            let tail = rest(2);
            let mut s = session(&o);
            if s.config().find(email).is_none() {
                s.execute("account.add", &json!({"email": email})).map_err(|e| e.to_string())?;
            }
            let mut prm = json!({"email": email});
            if let Some(p) = flag(&tail, "--provider") {
                prm["provider"] = json!(p);
            }
            let v = s.execute("account.oauthStart", &prm).map_err(|e| e.to_string())?;
            let url = v["url"].as_str().unwrap_or_default().to_string();
            println!("Open this address in your browser and sign in (waiting up to 5 minutes):\n{url}");
            open_browser(&url);
            let started = std::time::Instant::now();
            while started.elapsed() < std::time::Duration::from_secs(300) {
                for done in s.pump() {
                    if done.command == "account.oauthFinish" {
                        done.result.map_err(|e| e.to_string())?;
                        println!("Signed in as {email}.");
                        // Let the first sync finish so the folders are cached.
                        let until = std::time::Instant::now() + std::time::Duration::from_secs(60);
                        while s.jobs_pending() > 0 && std::time::Instant::now() < until {
                            std::thread::sleep(std::time::Duration::from_millis(100));
                            let _ = s.pump();
                        }
                        let v = s.execute("account.list", &json!({})).map_err(|e| e.to_string())?;
                        if let Some(a) = v["accounts"].as_array().and_then(|l| l.iter().find(|a| a["email"] == *email)) {
                            println!("Folders: {} · unread {}", a["status"]["mailboxes"], a["status"]["unread"]);
                        }
                        return Ok(());
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            Err("the sign-in did not finish in 5 minutes".into())
        }
        Some("test") => {
            let mut s = session(&o);
            let mut prm = json!({});
            if let Some(a) = args.get(1) {
                prm["id"] = json!(a);
            }
            let t = s.run_blocking("account.test", prm).map_err(|e| e.to_string())?;
            println!("IMAP: {} ({} mailboxes)  SMTP: {}", t["imap"].as_str().unwrap_or(""), t["mailboxes"], t["smtp"].as_str().unwrap_or(""));
            if t["ok"] == true { Ok(()) } else { Err(String::new()) }
        }
        Some("remove") => {
            let id = args.get(1).ok_or(USAGE)?;
            let mut s = session(&o);
            s.execute("account.remove", &json!({"id": id})).map_err(|e| e.to_string())?;
            println!("Removed {id}");
            Ok(())
        }
        Some("set") => {
            let (k, v) = (args.get(1).ok_or(USAGE)?, args.get(2).ok_or(USAGE)?);
            let mut s = session(&o);
            s.execute("prefs.set", &json!({"key": k, "value": v})).map_err(|e| e.to_string())?;
            println!("{k} = {v}");
            Ok(())
        }
        Some("sync") => {
            let mut s = session(&o);
            let mut prm = json!({"all": true});
            if let Some(a) = args.get(1) {
                prm = json!({"account": a});
            }
            let v = s.run_blocking("mail.sync", prm).map_err(|e| e.to_string())?;
            println!("Synced {}", v["mailbox"].as_str().unwrap_or(""));
            print_list(&v, s.italian(), s.config().preferences.offset_minutes());
            Ok(())
        }
        Some("list") | Some("search") => {
            let tail = rest(1);
            let mut s = session(&o);
            let mut prm = json!({});
            for key in ["account", "mailbox", "page"] {
                if let Some(v) = flag(&tail, &format!("--{key}")) {
                    prm[key] = json!(v);
                }
            }
            let v = if args[0] == "search" {
                prm["query"] = json!(args.get(1).ok_or(USAGE)?);
                s.run_blocking("mail.search", prm).map_err(|e| e.to_string())?
            } else {
                prm["refresh"] = json!(true);
                s.run_blocking("mail.list", prm).map_err(|e| e.to_string())?
            };
            let mut v = v;
            if flag(&tail, "--unread").is_some()
                && let Some(list) = v["envelopes"].as_array_mut()
            {
                list.retain(|e| e["unread"] == true);
            }
            print_list(&v, s.italian(), s.config().preferences.offset_minutes());
            Ok(())
        }
        Some("read") => {
            let uid: u32 = args.get(1).and_then(|u| u.parse().ok()).ok_or("UID must be a number")?;
            let tail = rest(2);
            let mut s = session(&o);
            let mut prm = json!({"uid": uid});
            for key in ["account", "mailbox"] {
                if let Some(v) = flag(&tail, &format!("--{key}")) {
                    prm[key] = json!(v);
                }
            }
            let m = s.run_blocking("mail.open", prm).map_err(|e| e.to_string())?;
            let join = |k: &str| m[k].as_array().map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")).unwrap_or_default();
            println!(
                "From: {}\nTo: {}\nDate: {}\nSubject: {}\n",
                join("from"),
                join("to"),
                m["date_text"].as_str().unwrap_or(""),
                m["subject"].as_str().unwrap_or("")
            );
            println!("{}", m["text"].as_str().unwrap_or(""));
            if let Some(list) = m["attachments"].as_array().filter(|a| !a.is_empty()) {
                println!("\nAttachments:");
                for a in list {
                    println!("  [{}] {} ({}, {} bytes)", a["index"], a["name"].as_str().unwrap_or(""), a["mime"].as_str().unwrap_or(""), a["size"]);
                }
            }
            Ok(())
        }
        Some("send") => {
            let tail = rest(1);
            let to = flag(&tail, "--to").ok_or("--to is required")?;
            let subject = flag(&tail, "--subject").unwrap_or_default();
            let body = match (flag(&tail, "--body"), flag(&tail, "--body-file")) {
                (Some(b), _) => b,
                (None, Some(f)) => std::fs::read_to_string(&f).map_err(|e| format!("{f}: {e}"))?,
                (None, None) => return Err("--body or --body-file is required".into()),
            };
            let mut prm = json!({"to": to, "subject": subject, "body": body});
            if let Some(cc) = flag(&tail, "--cc") {
                prm["cc"] = json!(cc);
            }
            if let Some(a) = flag(&tail, "--account") {
                prm["account"] = json!(a);
            }
            let attachments: Vec<Value> = flags_all(&tail, "--attach").into_iter().map(|p| json!({"path": p})).collect();
            if !attachments.is_empty() {
                prm["attachments"] = json!(attachments);
            }
            let mut s = session(&o);
            let v = s.run_blocking("mail.send", prm).map_err(|e| e.to_string())?;
            println!("Sent {}", v["message_id"].as_str().unwrap_or(""));
            Ok(())
        }
        Some("run") => {
            let cmd = args.get(1).ok_or(USAGE)?;
            let params: Value = match args.get(2) {
                Some(j) => serde_json::from_str(j).map_err(|e| format!("invalid JSON: {e}"))?,
                None => json!({}),
            };
            let mut s = session(&o);
            let v = s.run_blocking(cmd, params).map_err(|e| e.to_string())?;
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            Ok(())
        }
        _ => Err(USAGE.into()),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("{e}");
            }
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn home() -> String {
        let dir = std::env::temp_dir().join(format!("mailcraft-cli-{}", std::process::id()));
        dir.display().to_string()
    }

    #[test]
    fn demo_commands_work_and_bad_arguments_are_errors_not_panics() {
        let h = home();
        let ok = |args: &[&str]| run(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert!(ok(&["--demo", "accounts"]).is_ok());
        assert!(ok(&["--demo", "list", "--unread"]).is_ok());
        assert!(ok(&["--demo", "search", "fattura"]).is_ok());
        assert!(ok(&["--demo", "read", "1"]).is_ok());
        assert!(ok(&["--demo", "send", "--to", "a@b.co", "--subject", "x", "--body", "y"]).is_ok());
        assert!(ok(&["--demo", "run", "view.get"]).is_ok());
        assert!(ok(&["commands"]).is_ok());
        assert!(ok(&["--home", &h, "suggest", "anna@gmail.com", "--lang", "it"]).is_ok());
        assert!(ok(&["--home", &h, "add", "anna@gmail.com", "--name", "Anna", "--no-test"]).is_ok());
        assert!(ok(&["--home", &h, "set", "theme", "dark"]).is_ok());
        assert!(ok(&["--home", &h, "accounts"]).is_ok());
        assert!(ok(&["--home", &h, "remove", "anna-gmail-com"]).is_ok());
        for args in [
            vec![],
            vec!["nope"],
            vec!["suggest"],
            vec!["suggest", "bad"],
            vec!["read", "x"],
            vec!["send"],
            vec!["run"],
            vec!["run", "x", "{"],
            vec!["add", "x@y.z", "--imap", "nohost"],
            vec!["set", "theme"],
        ] {
            let mut full = vec!["--home", h.as_str()];
            full.extend(args.iter());
            let full: Vec<String> = full.into_iter().map(String::from).collect();
            assert!(run(&full).is_err(), "{full:?}");
        }
        let _ = std::fs::remove_dir_all(&h);
    }
}
