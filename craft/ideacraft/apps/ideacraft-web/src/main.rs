//! IdeaCraft in the browser. Build: `cd apps/ideacraft-web && trunk build --release`
//! (trunk generates the JS loader; no handwritten JS).
//!
//! Open uses the browser's file picker (dropping files works too); Save and Export download the
//! file. The open map, language and theme are kept in the browser's storage, so a reload picks up
//! where you left off. URL parameters: `?lang=it`, `?template=swot`.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

#[cfg(target_arch = "wasm32")]
mod web {
    use std::sync::{Arc, Mutex};

    use eframe::wasm_bindgen::JsCast;
    use ideacraft_ui_egui::core::Map;
    use ideacraft_ui_egui::{IdeaApp, Language, Request, SidePanel};
    use serde_json::{Value, json};

    const STORAGE_KEY: &str = "ideacraft";

    /// Files read asynchronously (picker or drop), waiting to be opened on the next frame.
    type Inbox = Arc<Mutex<Vec<(String, Vec<u8>)>>>;

    pub struct App {
        app: IdeaApp,
        inbox: Inbox,
    }

    fn query_param(name: &str) -> Option<String> {
        let search = web_sys::window()?.location().search().ok()?;
        web_sys::UrlSearchParams::new_with_str(&search).ok()?.get(name)
    }

    /// Hands `text` to the browser as a file download.
    fn download(name: &str, text: &str, mime: &str) {
        let Some(document) = web_sys::window().and_then(|w| w.document()) else { return };
        let parts = js_sys::Array::new();
        parts.push(&eframe::wasm_bindgen::JsValue::from_str(text));
        let options = web_sys::BlobPropertyBag::new();
        options.set_type(mime);
        let Ok(blob) = web_sys::Blob::new_with_str_sequence_and_options(&parts, &options) else { return };
        let Ok(url) = web_sys::Url::create_object_url_with_blob(&blob) else { return };
        if let Some(a) = document.create_element("a").ok().and_then(|e| e.dyn_into::<web_sys::HtmlAnchorElement>().ok()) {
            a.set_href(&url);
            a.set_download(name);
            a.click();
        }
        let _ = web_sys::Url::revoke_object_url(&url);
    }

    impl App {
        pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
            let mut app = IdeaApp::default();
            let saved: Option<Value> = cc.storage.and_then(|s| s.get_string(STORAGE_KEY)).and_then(|t| serde_json::from_str(&t).ok());
            let browser_language = web_sys::window().and_then(|w| w.navigator().language()).and_then(|l| Language::parse(&l));
            app.language =
                saved.as_ref().and_then(|v| v.get("language")?.as_str().and_then(Language::parse)).or(browser_language).unwrap_or_default();
            app.dark = saved.as_ref().and_then(|v| v.get("dark")?.as_bool()).unwrap_or_else(|| cc.egui_ctx.system_theme() == Some(egui::Theme::Dark));
            if let Some(l) = query_param("lang").and_then(|l| Language::parse(&l)) {
                app.language = l;
            }
            let restored = saved.as_ref().and_then(|v| v.get("map")?.as_str().and_then(|m| Map::from_json(m).ok()));
            if let Some(id) = query_param("template") {
                app.apply_template(&id);
            } else if let Some(map) = restored.filter(|m| m.node_count() > 0) {
                app.session.replace_map(map);
                app.view.fit_pending = true;
            } else {
                app.new_map();
                app.sidebar = Some(SidePanel::Templates);
            }
            Self { app, inbox: Arc::default() }
        }

        fn serve_requests(&mut self, ctx: &egui::Context) {
            for request in std::mem::take(&mut self.app.requests) {
                match request {
                    Request::Open => {
                        let inbox = self.inbox.clone();
                        let ctx = ctx.clone();
                        let exts = ideacraft_ui_egui::formats::open_extensions();
                        let label = self.app.tr("Maps and outlines").to_string();
                        wasm_bindgen_futures::spawn_local(async move {
                            if let Some(file) = rfd::AsyncFileDialog::new().add_filter(label, &exts).pick_file().await {
                                let bytes = file.read().await;
                                if let Ok(mut q) = inbox.lock() {
                                    q.push((file.file_name(), bytes));
                                }
                                ctx.request_repaint();
                            }
                        });
                    }
                    Request::SaveAs => {
                        if let Some((name, text)) = self.app.export_text(None) {
                            download(&name, &text, "application/json");
                            self.app.session.mark_saved();
                            self.app.status = self.app.language.fill("Saved {name}", &[("name", &name)]);
                        }
                    }
                    Request::Export(format) => {
                        if let Some((name, text)) = self.app.export_text(Some(format)) {
                            download(&name, &text, "text/plain");
                            self.app.status = self.app.language.fill("Exported {name}", &[("name", &name)]);
                        }
                    }
                }
            }
        }
    }

    impl eframe::App for App {
        fn raw_input_hook(&mut self, ctx: &egui::Context, raw: &mut egui::RawInput) {
            for f in std::mem::take(&mut raw.dropped_files) {
                let inbox = self.inbox.clone();
                let ctx = ctx.clone();
                wasm_bindgen_futures::spawn_local(async move {
                    let name = f.path().display().to_string();
                    if let Ok(bytes) = f.bytes_async().await
                        && let Ok(mut q) = inbox.lock()
                    {
                        q.push((name, bytes));
                    }
                    ctx.request_repaint();
                });
            }
        }

        fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
            let files: Vec<(String, Vec<u8>)> = self.inbox.lock().map(|mut q| std::mem::take(&mut *q)).unwrap_or_default();
            for (name, bytes) in files {
                self.app.open_bytes(&name, &bytes);
            }
            self.app.ui(ui);
            let ctx = ui.ctx().clone();
            self.serve_requests(&ctx);
        }

        fn save(&mut self, storage: &mut dyn eframe::Storage) {
            let state = json!({"language": self.app.language.code(), "dark": self.app.dark, "map": self.app.session.map().to_json()});
            storage.set_string(STORAGE_KEY, state.to_string());
        }
    }

    pub fn start() {
        let options = eframe::WebOptions::default();
        wasm_bindgen_futures::spawn_local(async move {
            let Some(canvas) = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.get_element_by_id("ideacraft"))
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
    eprintln!("ideacraft-web runs in the browser: cd apps/ideacraft-web && trunk serve --release");
}
