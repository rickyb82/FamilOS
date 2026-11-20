//! A [`MailSession`] that lives in a browser (or any client) and mirrors a `mailcraft-cli serve`
//! server: commands go to `POST /api/cmd`, state comes back from `GET /api/snapshot?since=N`
//! (only when it changed). Works natively and on wasm32 through `ehttp`.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use mailcraft_engine::core::account::Config;
use mailcraft_engine::store::AccountState;
use mailcraft_engine::{Completed, Error, MailSession, Result, Snapshot, View};
use serde_json::{Value, json};

/// What arrived from the server, to be applied on the next `pump`.
enum Incoming {
    Snapshot(Box<Snapshot>),
    Unchanged,
    /// A command's immediate answer (`{"job"}` or the value for local commands).
    Answer {
        command: String,
        result: std::result::Result<Value, String>,
    },
    Failed(String),
}

type Inbox = Arc<Mutex<Vec<Incoming>>>;

/// The remote session.
pub struct RemoteSession {
    base: String,
    token: String,
    config: Config,
    passwords: Vec<String>,
    oauth_ready: Vec<String>,
    assist_url: String,
    view: View,
    states: BTreeMap<String, AccountState>,
    revision: u64,
    jobs_pending: usize,
    inbox: Inbox,
    polling: bool,
    problem: Option<String>,
    /// Commands sent whose answers have not arrived yet.
    in_flight: usize,
    /// Set when the next poll should go out as soon as possible.
    poll_now: bool,
    last_poll: f64,
    clock: Box<dyn Fn() -> f64 + Send>,
    /// Job ids reported by immediate answers, so their completion is attributed to the command.
    commands_by_job: BTreeMap<u64, String>,
}

fn now_seconds() -> f64 {
    web_time::SystemTime::now().duration_since(web_time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// How often the state is polled when nothing is happening (seconds).
const IDLE_POLL: f64 = 2.0;
/// How often while jobs are pending.
const BUSY_POLL: f64 = 0.4;

impl RemoteSession {
    /// Connects to `base` (`http://127.0.0.1:7878`) with an optional bearer token.
    pub fn new(base: &str, token: &str) -> Self {
        let mut s = Self {
            base: base.trim_end_matches('/').to_string(),
            token: token.to_string(),
            config: Config::default(),
            passwords: Vec::new(),
            oauth_ready: Vec::new(),
            assist_url: String::new(),
            view: View::default(),
            states: BTreeMap::new(),
            revision: 0,
            jobs_pending: 0,
            inbox: Arc::default(),
            polling: false,
            problem: Some("connecting".into()),
            in_flight: 0,
            poll_now: true,
            last_poll: 0.0,
            clock: Box::new(now_seconds),
            commands_by_job: BTreeMap::new(),
        };
        s.poll();
        s
    }

    fn request(&self, mut req: ehttp::Request) -> ehttp::Request {
        if !self.token.is_empty() {
            req.headers.insert("Authorization", format!("Bearer {}", self.token));
        }
        req
    }

    fn poll(&mut self) {
        if self.polling {
            return;
        }
        self.polling = true;
        self.poll_now = false;
        self.last_poll = (self.clock)();
        let inbox = self.inbox.clone();
        let req = self.request(ehttp::Request::get(format!("{}/api/snapshot?since={}", self.base, self.revision)));
        ehttp::fetch(req, move |result| {
            let incoming = match result {
                Ok(r) if r.ok => match serde_json::from_slice::<Value>(&r.bytes) {
                    Ok(v) if v.get("unchanged").is_some() => Incoming::Unchanged,
                    Ok(v) => match serde_json::from_value::<Snapshot>(v) {
                        Ok(s) => Incoming::Snapshot(Box::new(s)),
                        Err(e) => Incoming::Failed(format!("bad snapshot: {e}")),
                    },
                    Err(e) => Incoming::Failed(format!("bad snapshot: {e}")),
                },
                Ok(r) => Incoming::Failed(format!("server answered {} {}", r.status, r.status_text)),
                Err(e) => Incoming::Failed(e),
            };
            if let Ok(mut q) = inbox.lock() {
                q.push(incoming);
            }
        });
    }

    /// Fetches and applies the snapshot now (native only); `None` when unchanged.
    #[cfg(not(target_arch = "wasm32"))]
    fn refresh_blocking(&mut self) -> Result<Option<Snapshot>> {
        let req = self.request(ehttp::Request::get(format!("{}/api/snapshot?since={}", self.base, self.revision)));
        let r = ehttp::fetch_blocking(&req).map_err(|e| Error::Core(mailcraft_engine::core::Error::Connect(e)))?;
        if !r.ok {
            return Err(Error::Core(mailcraft_engine::core::Error::Server(format!("server answered {} {}", r.status, r.status_text))));
        }
        let v: Value = serde_json::from_slice(&r.bytes).map_err(|e| Error::Param(format!("bad snapshot: {e}")))?;
        if v.get("unchanged").is_some() {
            return Ok(None);
        }
        let s: Snapshot = serde_json::from_value(v).map_err(|e| Error::Param(format!("bad snapshot: {e}")))?;
        self.apply(s.clone());
        Ok(Some(s))
    }

    fn apply(&mut self, s: Snapshot) {
        self.revision = s.revision;
        self.config = s.config;
        self.passwords = s.passwords;
        self.oauth_ready = s.oauth_ready;
        self.assist_url = s.assist_url;
        self.view = s.view;
        self.states = s.states.into_iter().map(|(k, v)| (k, v.into_state())).collect();
        self.jobs_pending = s.jobs_pending;
        self.problem = None;
    }
}

impl MailSession for RemoteSession {
    fn config(&self) -> &Config {
        &self.config
    }

    fn view(&self) -> &View {
        &self.view
    }

    fn state(&self, account: &str) -> Option<&AccountState> {
        self.states.get(account)
    }

    fn has_password(&self, id: &str) -> bool {
        self.passwords.iter().any(|p| p == id)
    }

    fn oauth_ready(&self) -> Vec<String> {
        self.oauth_ready.clone()
    }

    fn assist_url(&self) -> String {
        self.assist_url.clone()
    }

    fn jobs_pending(&self) -> usize {
        self.jobs_pending + self.in_flight
    }

    fn problem(&self) -> Option<String> {
        self.problem.clone()
    }

    fn server(&self) -> String {
        self.base.clone()
    }

    fn set_server(&mut self, url: &str) {
        self.base = url.trim_end_matches('/').to_string();
        self.revision = 0;
        self.problem = Some("connecting".into());
        self.poll_now = true;
    }

    fn execute(&mut self, id: &str, params: &Value) -> Result<Value> {
        let body = json!({"command": id, "params": params}).to_string().into_bytes();
        let mut req = ehttp::Request::post(format!("{}/api/cmd", self.base), body);
        req.headers = ehttp::Headers::new(&[("Accept", "application/json"), ("Content-Type", "application/json")]);
        let req = self.request(req);
        let inbox = self.inbox.clone();
        let command = id.to_string();
        self.in_flight += 1;
        ehttp::fetch(req, move |result| {
            let incoming = match result {
                Ok(r) => match serde_json::from_slice::<Value>(&r.bytes) {
                    Ok(v) if v.get("error").is_some() => {
                        Incoming::Answer { command, result: Err(v["error"].as_str().unwrap_or("error").to_string()) }
                    }
                    Ok(v) => Incoming::Answer { command, result: Ok(v.get("result").cloned().unwrap_or(Value::Null)) },
                    Err(e) => Incoming::Answer { command, result: Err(format!("bad answer: {e}")) },
                },
                Err(e) => Incoming::Failed(e),
            };
            if let Ok(mut q) = inbox.lock() {
                q.push(incoming);
            }
        });
        Ok(json!({"queued": true}))
    }

    fn pump(&mut self) -> Vec<Completed> {
        let incoming: Vec<Incoming> = self.inbox.lock().map(|mut q| std::mem::take(&mut *q)).unwrap_or_default();
        let mut out = Vec::new();
        for i in incoming {
            match i {
                Incoming::Snapshot(s) => {
                    self.polling = false;
                    for c in &s.completed {
                        let mut done = Completed::from(c);
                        if done.command.is_empty() {
                            done.command = self.commands_by_job.remove(&c.job).unwrap_or_default();
                        } else {
                            self.commands_by_job.remove(&c.job);
                        }
                        out.push(done);
                    }
                    self.apply(*s);
                }
                Incoming::Unchanged => {
                    self.polling = false;
                    self.problem = None;
                }
                Incoming::Answer { command, result } => {
                    self.in_flight = self.in_flight.saturating_sub(1);
                    self.poll_now = true;
                    match result {
                        Ok(v) => {
                            if let Some(j) = v.get("job").and_then(Value::as_u64) {
                                self.commands_by_job.insert(j, command.clone());
                            }
                            for j in v.get("jobs").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_u64) {
                                self.commands_by_job.insert(j, command.clone());
                            }
                            // Local commands answer at once: report them as completed now.
                            if v.get("job").is_none() && v.get("jobs").is_none() {
                                out.push(Completed { job: 0, command, account: self.view.account.clone(), result: Ok(v) });
                            }
                        }
                        Err(e) => out.push(Completed { job: 0, command, account: self.view.account.clone(), result: Err(Error::Param(e)) }),
                    }
                }
                Incoming::Failed(e) => {
                    self.polling = false;
                    self.in_flight = self.in_flight.saturating_sub(1).min(self.in_flight);
                    self.problem = Some(e);
                }
            }
        }
        let now = (self.clock)();
        let interval = if self.jobs_pending > 0 || self.in_flight > 0 { BUSY_POLL } else { IDLE_POLL };
        if self.poll_now || now - self.last_poll >= interval || (self.problem.is_some() && now - self.last_poll >= IDLE_POLL) {
            self.poll();
        }
        out
    }

    #[cfg(target_arch = "wasm32")]
    fn run_blocking(&mut self, id: &str, params: Value) -> Result<Value> {
        // A remote session cannot block the browser's thread: callers use pump().
        let _ = self.execute(id, &params)?;
        Err(Error::Param("remote sessions answer through pump(), not run_blocking".into()))
    }

    /// Native clients (the MCP server with `--server`) can wait: the command goes out with a
    /// blocking request and the snapshot is polled until its jobs complete.
    #[cfg(not(target_arch = "wasm32"))]
    fn run_blocking(&mut self, id: &str, params: Value) -> Result<Value> {
        let body = json!({"command": id, "params": params}).to_string().into_bytes();
        let mut req = ehttp::Request::post(format!("{}/api/cmd", self.base), body);
        req.headers = ehttp::Headers::new(&[("Accept", "application/json"), ("Content-Type", "application/json")]);
        let req = self.request(req);
        let r = ehttp::fetch_blocking(&req).map_err(|e| Error::Core(mailcraft_engine::core::Error::Connect(e)))?;
        let v: Value = serde_json::from_slice(&r.bytes).map_err(|e| Error::Param(format!("bad answer: {e}")))?;
        if let Some(e) = v.get("error").and_then(Value::as_str) {
            return Err(Error::Param(e.to_string()));
        }
        let v = v.get("result").cloned().unwrap_or(Value::Null);
        let mut jobs: Vec<u64> = v.get("jobs").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
        jobs.extend(v.get("job").and_then(Value::as_u64));
        if jobs.is_empty() {
            self.refresh_blocking()?;
            return Ok(v);
        }
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(180);
        let mut last = v;
        let mut remaining = jobs;
        while !remaining.is_empty() {
            if std::time::Instant::now() > deadline {
                return Err(Error::Timeout(remaining[0]));
            }
            std::thread::sleep(std::time::Duration::from_millis(150));
            let Some(s) = self.refresh_blocking()? else { continue };
            for c in &s.completed {
                if let Some(i) = remaining.iter().position(|j| *j == c.job) {
                    remaining.remove(i);
                    if !c.error.is_empty() {
                        return Err(Error::Param(c.error.clone()));
                    }
                    last = c.result.clone();
                }
            }
        }
        Ok(last)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unreachable_server_is_a_problem_not_a_panic() {
        let mut s = RemoteSession::new("http://127.0.0.1:1", "");
        assert_eq!(s.problem(), Some("connecting".into()));
        for _ in 0..50 {
            let _ = s.pump();
            std::thread::sleep(std::time::Duration::from_millis(20));
            if s.problem().is_some_and(|p| p != "connecting") {
                break;
            }
        }
        assert!(s.problem().is_some_and(|p| p != "connecting"), "{:?}", s.problem());
        assert!(s.execute("view.get", &json!({})).is_ok());
        assert!(s.run_blocking("view.get", json!({})).is_err());
        assert_eq!(s.server(), "http://127.0.0.1:1");
    }
}
