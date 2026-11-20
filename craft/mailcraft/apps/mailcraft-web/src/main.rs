//! MailCraft in the browser. Build: `cd apps/mailcraft-web && trunk build --release` (trunk
//! generates the JS loader; no handwritten JS).
//!
//! The browser cannot speak IMAP, so the page mirrors a `mailcraft-cli serve` server (your
//! computer by default: `http://127.0.0.1:7878`, or the page's own origin when the server serves
//! the page). URL parameters: `?server=http://host:7878`, `?lang=it`. The token comes in the
//! fragment (`#token=…`, which the browser never sends to a server; `?token=…` still works) and
//! is taken out of the address bar at once. On its own page it is kept in the browser's local
//! storage; inside another page (Emily's iframe, whose per-user token it is) only in memory.
//! Attachments come in through the browser's file picker and go out as downloads.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

/// What the page reads from its address, free of the browser so it is tested natively.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
mod link {
    /// `%41` and `+` decoded (tokens are plain, but links get mangled).
    fn decode(text: &str) -> String {
        let bytes = text.as_bytes();
        let mut out = Vec::with_capacity(bytes.len());
        let mut i = 0;
        while let Some(&b) = bytes.get(i) {
            let hex = bytes.get(i + 1..i + 3).and_then(|h| std::str::from_utf8(h).ok()).and_then(|h| u8::from_str_radix(h, 16).ok());
            match (b, hex) {
                (b'%', Some(v)) => {
                    out.push(v);
                    i += 3;
                }
                (b'+', _) => {
                    out.push(b' ');
                    i += 1;
                }
                _ => {
                    out.push(b);
                    i += 1;
                }
            }
        }
        String::from_utf8_lossy(&out).into_owned()
    }

    /// `key`'s value in a query (`?a=1&b=2`) or a fragment (`#a=1`), if not empty.
    pub fn param(text: &str, key: &str) -> Option<String> {
        text.trim_start_matches(['?', '#'])
            .split('&')
            .find_map(|kv| kv.split_once('=').filter(|(k, _)| *k == key).map(|(_, v)| decode(v)))
            .filter(|v| !v.is_empty())
    }

    /// The token a link carries: the fragment first, then the query.
    pub fn token(query: &str, fragment: &str) -> Option<String> {
        param(fragment, "token").or_else(|| param(query, "token"))
    }

    /// The query or fragment without `key` (`?lang=it`), empty when nothing is left.
    pub fn without(text: &str, mark: char, key: &str) -> String {
        let kept: Vec<&str> =
            text.trim_start_matches(mark).split('&').filter(|kv| !kv.is_empty() && kv.split_once('=').map_or(*kv, |(k, _)| k) != key).collect();
        if kept.is_empty() { String::new() } else { format!("{mark}{}", kept.join("&")) }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn the_token_comes_from_the_fragment_first_and_leaves_the_address() {
            assert_eq!(token("?server=x&token=q", "#token=f").as_deref(), Some("f"), "the fragment wins");
            assert_eq!(token("?token=abc%2B1&lang=it", "").as_deref(), Some("abc+1"));
            assert_eq!(token("?token=", "#lang=it"), None);
            assert_eq!(token("", "#token=%zz"), Some("%zz".into()));
            assert_eq!(without("?server=x&token=q&lang=it", '?', "token"), "?server=x&lang=it");
            assert_eq!(without("#token=f", '#', "token"), "");
            assert_eq!(without("", '#', "token"), "");
            assert_eq!(without("?tokens=1", '?', "token"), "?tokens=1");
        }
    }
}

#[cfg(target_arch = "wasm32")]
mod web {
    use std::sync::{Arc, Mutex};

    use eframe::wasm_bindgen::JsCast;
    use mailcraft_remote::RemoteSession;
    use mailcraft_ui_egui::core::message::DraftAttachment;
    use mailcraft_ui_egui::{Language, MailApp, Request};
    use serde_json::{Value, json};

    use crate::link;

    const STORAGE_KEY: &str = "mailcraft";
    const DEFAULT_SERVER: &str = "http://127.0.0.1:7878";

    /// Files picked asynchronously, waiting to be attached on the next frame.
    type Inbox = Arc<Mutex<Vec<(String, Vec<u8>)>>>;
    /// Assistant answers waiting for the next frame.
    type Answers = Arc<Mutex<Vec<(u64, Result<Value, String>)>>>;

    pub struct App {
        app: MailApp,
        inbox: Inbox,
        answers: Answers,
        token: String,
        /// The token may be kept in local storage: the page is not inside another page.
        keep_token: bool,
    }

    /// Whether the page runs inside another page (Emily's iframe): its token is then the host's
    /// per-user token, never stored.
    fn embedded() -> bool {
        let Some(w) = web_sys::window() else { return false };
        match w.top() {
            Ok(Some(top)) => !js_sys::Object::is(w.as_ref(), top.as_ref()),
            // A top window that cannot be read belongs to another page.
            _ => true,
        }
    }

    /// The token the link carries (`#token=…` first, then `?token=…`), taken out of the
    /// address bar at once so it stays in no history entry.
    fn link_token() -> Option<String> {
        let w = web_sys::window()?;
        let loc = w.location();
        let (query, fragment) = (loc.search().unwrap_or_default(), loc.hash().unwrap_or_default());
        let token = link::token(&query, &fragment)?;
        let clean =
            format!("{}{}{}", loc.pathname().unwrap_or_default(), link::without(&query, '?', "token"), link::without(&fragment, '#', "token"));
        if let Ok(h) = w.history() {
            let _ = h.replace_state_with_url(&eframe::wasm_bindgen::JsValue::NULL, "", Some(&clean));
        }
        Some(token)
    }

    /// Removes a token an earlier version stored inside a frame.
    fn forget_stored_token() {
        let Some(storage) = web_sys::window().and_then(|w| w.local_storage().ok().flatten()) else { return };
        let Some(mut v) = storage.get_item(STORAGE_KEY).ok().flatten().and_then(|t| serde_json::from_str::<Value>(&t).ok()) else { return };
        if v.as_object_mut().and_then(|o| o.remove("token")).is_some() {
            let _ = storage.set_item(STORAGE_KEY, &v.to_string());
        }
    }

    /// `POST`s to the framework's assistant with the session cookie (same origin).
    fn assist(answers: &Answers, ctx: &egui::Context, token: u64, url: &str, body: &Value) {
        let url = if url.starts_with("http://") || url.starts_with("https://") {
            url.to_string()
        } else {
            let origin = web_sys::window().and_then(|w| w.location().origin().ok()).unwrap_or_default();
            format!("{origin}/{}", url.trim_start_matches('/'))
        };
        let mut req = ehttp::Request::post(url, body.to_string().into_bytes());
        req.headers = ehttp::Headers::new(&[("Accept", "application/json"), ("Content-Type", "application/json"), ("X-Requested-With", "FamilAI")]);
        req.credentials = ehttp::Credentials::Include;
        let answers = answers.clone();
        let ctx = ctx.clone();
        ehttp::fetch(req, move |result| {
            let out = match result {
                Ok(r) => match serde_json::from_slice::<Value>(&r.bytes) {
                    Ok(v) if r.ok => Ok(v),
                    Ok(v) => Err(v
                        .get("error")
                        .and_then(Value::as_str)
                        .or_else(|| v.get("message").and_then(Value::as_str))
                        .map(str::to_string)
                        .unwrap_or_else(|| format!("assistant: HTTP {}", r.status))),
                    Err(_) => Err(format!("assistant: HTTP {} {}", r.status, r.status_text)),
                },
                Err(e) => Err(format!("assistant: {e}")),
            };
            if let Ok(mut q) = answers.lock() {
                q.push((token, out));
            }
            ctx.request_repaint();
        });
    }

    fn query_param(name: &str) -> Option<String> {
        let search = web_sys::window()?.location().search().ok()?;
        web_sys::UrlSearchParams::new_with_str(&search).ok()?.get(name)
    }

    /// The page's origin when it is served by mailcraft-cli itself (not a static host).
    fn own_server() -> Option<String> {
        let origin = web_sys::window()?.location().origin().ok()?;
        let host = web_sys::window()?.location().hostname().ok()?;
        (!host.ends_with("github.io") && !origin.starts_with("file:")).then_some(origin)
    }

    /// Hands `bytes` to the browser as a file download.
    fn download(name: &str, bytes: &[u8]) {
        let Some(document) = web_sys::window().and_then(|w| w.document()) else { return };
        let array = js_sys::Uint8Array::from(bytes);
        let parts = js_sys::Array::new();
        parts.push(&array.buffer());
        let options = web_sys::BlobPropertyBag::new();
        options.set_type("application/octet-stream");
        let Ok(blob) = web_sys::Blob::new_with_buffer_source_sequence_and_options(&parts, &options) else { return };
        let Ok(url) = web_sys::Url::create_object_url_with_blob(&blob) else { return };
        if let Some(a) = document.create_element("a").ok().and_then(|e| e.dyn_into::<web_sys::HtmlAnchorElement>().ok()) {
            a.set_href(&url);
            a.set_download(name);
            a.click();
        }
        let _ = web_sys::Url::revoke_object_url(&url);
    }

    fn mime_for(name: &str) -> &'static str {
        match name.rsplit('.').next().unwrap_or_default().to_ascii_lowercase().as_str() {
            "pdf" => "application/pdf",
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "gif" => "image/gif",
            "txt" | "md" => "text/plain",
            "csv" => "text/csv",
            "zip" => "application/zip",
            _ => "application/octet-stream",
        }
    }

    impl App {
        pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
            let saved: Option<Value> = cc.storage.and_then(|s| s.get_string(STORAGE_KEY)).and_then(|t| serde_json::from_str(&t).ok());
            let server = query_param("server")
                .or_else(|| saved.as_ref().and_then(|v| v.get("server")?.as_str().map(str::to_string)))
                .or_else(own_server)
                .unwrap_or_else(|| DEFAULT_SERVER.to_string());
            let keep_token = !embedded();
            if !keep_token {
                forget_stored_token();
            }
            let stored = || saved.as_ref().and_then(|v| v.get("token")?.as_str().map(str::to_string));
            let token = link_token().or_else(|| if keep_token { stored() } else { None }).unwrap_or_default();
            let mut app = MailApp::new(Box::new(RemoteSession::new(&server, &token)));
            let browser_language = web_sys::window().and_then(|w| w.navigator().language()).and_then(|l| Language::parse(&l));
            app.language =
                saved.as_ref().and_then(|v| v.get("language")?.as_str().and_then(Language::parse)).or(browser_language).unwrap_or_default();
            if let Some(l) = query_param("lang").and_then(|l| Language::parse(&l)) {
                app.language = l;
            }
            app.dark = saved.as_ref().and_then(|v| v.get("dark")?.as_bool()).unwrap_or_else(|| cc.egui_ctx.system_theme() == Some(egui::Theme::Dark));
            Self { app, inbox: Arc::default(), answers: Arc::default(), token, keep_token }
        }

        fn serve_requests(&mut self, ctx: &egui::Context) {
            for request in std::mem::take(&mut self.app.requests) {
                match request {
                    Request::AttachFiles => {
                        let inbox = self.inbox.clone();
                        let ctx = ctx.clone();
                        wasm_bindgen_futures::spawn_local(async move {
                            let files = rfd::AsyncFileDialog::new().pick_files().await.unwrap_or_default();
                            for file in files.into_iter().take(50) {
                                let bytes = file.read().await;
                                if let Ok(mut q) = inbox.lock() {
                                    q.push((file.file_name(), bytes));
                                }
                            }
                            ctx.request_repaint();
                        });
                    }
                    Request::SaveAttachment { name, data } => download(&name, &data),
                    Request::OpenUrl(url) => {
                        if (url.starts_with("http://") || url.starts_with("https://"))
                            && let Some(w) = web_sys::window()
                        {
                            let _ = w.open_with_url_and_target(&url, "_blank");
                        }
                    }
                    Request::Assist { token, url, body } => assist(&self.answers, ctx, token, &url, &body),
                    Request::Print { html, .. } => {
                        // A page that prints itself, opened in a new tab.
                        let parts = js_sys::Array::new();
                        parts.push(&eframe::wasm_bindgen::JsValue::from_str(&html));
                        let options = web_sys::BlobPropertyBag::new();
                        options.set_type("text/html");
                        if let Ok(blob) = web_sys::Blob::new_with_str_sequence_and_options(&parts, &options)
                            && let Ok(url) = web_sys::Url::create_object_url_with_blob(&blob)
                            && let Some(w) = web_sys::window()
                        {
                            let _ = w.open_with_url_and_target(&url, "_blank");
                        }
                    }
                }
            }
        }
    }

    impl eframe::App for App {
        fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
            let answers: Vec<(u64, Result<Value, String>)> = self.answers.lock().map(|mut q| std::mem::take(&mut *q)).unwrap_or_default();
            for (token, result) in answers {
                self.app.assist_answer(token, result);
            }
            let files: Vec<(String, Vec<u8>)> = self.inbox.lock().map(|mut q| std::mem::take(&mut *q)).unwrap_or_default();
            if !files.is_empty()
                && let Some(c) = &mut self.app.compose
            {
                for (name, data) in files {
                    c.attachments.push(DraftAttachment { mime: mime_for(&name).into(), name, data, cid: String::new() });
                }
            }
            self.app.ui(ui);
            let ctx = ui.ctx().clone();
            self.serve_requests(&ctx);
            // The remote session polls the server; keep frames coming while it does.
            ctx.request_repaint_after(std::time::Duration::from_millis(500));
        }

        fn save(&mut self, storage: &mut dyn eframe::Storage) {
            let mut state = json!({"language": self.app.language.code(), "dark": self.app.dark, "server": self.app.session.server()});
            if self.keep_token && !self.token.is_empty() {
                state["token"] = json!(self.token);
            }
            storage.set_string(STORAGE_KEY, state.to_string());
        }
    }

    pub fn start() {
        let options = eframe::WebOptions::default();
        wasm_bindgen_futures::spawn_local(async move {
            let Some(canvas) = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.get_element_by_id("mailcraft"))
                .and_then(|e| e.dyn_into::<web_sys::HtmlCanvasElement>().ok())
            else {
                return;
            };
            let started = eframe::WebRunner::new().start(canvas, options, Box::new(|cc| Ok(Box::new(App::new(cc))))).await;
            if let Err(e) = started {
                eframe::web_sys::console::error_1(&e);
            }
        });
    }
}

#[cfg(target_arch = "wasm32")]
fn main() {
    web::start();
}

#[cfg(not(target_arch = "wasm32"))]
fn main() {
    eprintln!("mailcraft-web runs in the browser: cd apps/mailcraft-web && trunk serve --release (and mailcraft-cli serve)");
}
