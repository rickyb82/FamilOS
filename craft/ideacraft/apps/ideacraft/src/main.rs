//! IdeaCraft desktop app.
//!
//! Usage: `ideacraft [--sample | --template <id>] [file]`. The file can be an IdeaCraft map or any
//! importable format (FreeMind/Freeplane `.mm`, XMind, CmapTools `.cxl`, OPML, Markdown, Mermaid).
#![cfg_attr(all(target_os = "windows", not(debug_assertions)), windows_subsystem = "windows")]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use ideacraft_ui_egui::core::Map;
use ideacraft_ui_egui::{IdeaApp, Language, Request};
use serde_json::json;

/// The app and the preferences last written to `ui.json`.
struct App(IdeaApp, (Language, bool));

impl App {
    /// File dialogs requested by the UI.
    fn serve_requests(&mut self) {
        for request in std::mem::take(&mut self.0.requests) {
            match request {
                Request::Open => {
                    let exts = ideacraft_ui_egui::formats::open_extensions();
                    let (maps, all) = (self.0.tr("Maps and outlines").to_string(), self.0.tr("All files").to_string());
                    if let Some(path) = rfd::FileDialog::new().add_filter(maps, &exts).add_filter(all, &["*"]).pick_file() {
                        self.0.open_path(&path);
                    }
                }
                Request::SaveAs => {
                    let name = format!("{}.ideacraft.json", self.0.session.map().title());
                    if let Some(path) = rfd::FileDialog::new().add_filter(self.0.tr("IdeaCraft map"), &["json"]).set_file_name(name).save_file() {
                        self.0.save_as(path);
                    }
                }
                Request::Export(format) => {
                    let name = format!("{}.{}", self.0.session.map().title(), format.extension());
                    if let Some(path) =
                        rfd::FileDialog::new().add_filter(self.0.tr(format.label()), &[format.extension()]).set_file_name(name).save_file()
                    {
                        self.0.export_to(&path, format);
                    }
                }
            }
        }
    }
}

impl eframe::App for App {
    fn raw_input_hook(&mut self, _ctx: &egui::Context, raw: &mut egui::RawInput) {
        // Files dropped on the window open (or import) as maps.
        for f in std::mem::take(&mut raw.dropped_files) {
            let path = f.path().to_path_buf();
            if !path.as_os_str().is_empty() {
                self.0.open_path(&path);
            }
        }
    }
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let title = self.0.window_title();
        ui.ctx().send_viewport_cmd(egui::ViewportCommand::Title(title));
        self.0.ui(ui);
        self.serve_requests();
        let prefs = (self.0.language, self.0.dark);
        if prefs != self.1 {
            save_prefs(prefs.0, prefs.1);
            self.1 = prefs;
        }
    }
}

const USAGE: &str = "Usage: ideacraft [--lang en|it] [--sample | --template <id>] [file]";

/// Where preferences live: `~/.config/ideacraft`, `~/Library/Application Support/IdeaCraft` or
/// `%APPDATA%\\IdeaCraft`.
fn config_dir() -> Option<std::path::PathBuf> {
    if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join("Library/Application Support/IdeaCraft"))
    } else if cfg!(windows) {
        std::env::var_os("APPDATA").map(|a| std::path::PathBuf::from(a).join("IdeaCraft"))
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".config")))
            .map(|c| c.join("ideacraft"))
    }
}

/// Saved preferences (`ui.json`: `{"language": "it", "dark": false}`).
fn saved_prefs() -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(config_dir()?.join("ui.json")).ok()?;
    serde_json::from_str(&text).ok()
}

fn save_prefs(language: Language, dark: bool) {
    if let Some(dir) = config_dir() {
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::write(dir.join("ui.json"), json!({"language": language.code(), "dark": dark}).to_string());
    }
}

fn main() -> eframe::Result {
    let mut app = IdeaApp::default();
    // The saved language, else the system's on first run.
    let prefs = saved_prefs();
    app.language = prefs.as_ref().and_then(|p| p.get("language")?.as_str().and_then(Language::parse)).unwrap_or_else(Language::system);
    app.dark = prefs.as_ref().and_then(|p| p.get("dark")?.as_bool()).unwrap_or(false);
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(i) = args.iter().position(|a| a == "--lang") {
        if let Some(l) = args.get(i + 1).and_then(|c| Language::parse(c)) {
            app.language = l;
        }
        args.drain(i..(i + 2).min(args.len()));
    }
    let mut opened = false;
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--sample" => {
                let language = app.language;
                app = IdeaApp::new(Map::sample());
                app.language = language;
                opened = true;
            }
            "--template" => {
                let id = args.next().unwrap_or_default();
                opened = app.run("map.newFromTemplate", json!({"template": id, "language": app.language.code()})).is_some();
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                return Ok(());
            }
            path => {
                app.open_path(std::path::Path::new(path));
                opened = true;
            }
        }
    }
    // A fresh start offers the templates, on an empty map titled in the interface language.
    if !opened {
        app.new_map();
        app.sidebar = Some(ideacraft_ui_egui::SidePanel::Templates);
    }
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_title("IdeaCraft").with_inner_size([1280.0, 800.0]).with_min_inner_size([640.0, 400.0]),
        ..Default::default()
    };
    let prefs = (app.language, app.dark);
    eframe::run_native("IdeaCraft", options, Box::new(move |_cc| Ok(Box::new(App(app, prefs)))))
}
