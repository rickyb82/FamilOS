//! MailCraft desktop app.
//!
//! Usage: `mailcraft [--demo] [--home DIR] [--lang en|it]`. The configuration lives in the
//! platform configuration directory (or `MAILCRAFT_HOME`); `--demo` shows the built-in mailbox.
#![cfg_attr(all(target_os = "windows", not(debug_assertions)), windows_subsystem = "windows")]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use mailcraft_ui_egui::engine::{Paths, Session};
use mailcraft_ui_egui::{Language, MailApp, Request};

struct App(MailApp);

impl App {
    /// File dialogs and links requested by the UI.
    fn serve_requests(&mut self) {
        for request in std::mem::take(&mut self.0.requests) {
            match request {
                Request::AttachFiles => {
                    if let Some(paths) = rfd::FileDialog::new().pick_files()
                        && let Some(c) = &mut self.0.compose
                    {
                        for path in paths.into_iter().take(50) {
                            if let Ok(data) = std::fs::read(&path) {
                                let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "attachment".into());
                                c.attachments.push(mailcraft_ui_egui::core::message::DraftAttachment {
                                    name,
                                    mime: "application/octet-stream".into(),
                                    data,
                                    cid: String::new(),
                                });
                            }
                        }
                    }
                }
                Request::SaveAttachment { name, data } => {
                    if let Some(path) = rfd::FileDialog::new().set_file_name(&name).save_file() {
                        match std::fs::write(&path, &data) {
                            Ok(()) => self.0.status = format!("{} {}", self.0.tr("Saved"), path.display()),
                            Err(e) => self.0.status = format!("{}: {e}", path.display()),
                        }
                    }
                }
                Request::OpenUrl(url) => {
                    // Links come from messages (List-Unsubscribe) and servers: web and mail only.
                    if let Some(url) = mailcraft_ui_egui::core::link::openable(&url) {
                        open_with_system(url, false);
                    }
                }
                Request::Assist { token, .. } => {
                    // The assistant lives in the framework behind the web app: not on the desktop.
                    self.0.assist_answer(token, Err(self.0.tr("The assistant is available in the web app only").to_string()));
                }
                Request::Print { title, html } => {
                    // A page that prints itself, opened in the browser.
                    let name = format!("mailcraft-print-{}.html", mailcraft_ui_egui::core::message::time::now());
                    let path = std::env::temp_dir().join(name);
                    match std::fs::write(&path, html) {
                        Ok(()) => open_with_system(&path.display().to_string(), true),
                        Err(e) => self.0.status = format!("{title}: {e}"),
                    }
                }
            }
        }
    }
}

/// Opens a link (checked with `core::link::openable`) or one of our own files (`file`) with
/// the system's default application, as a single argument and never through a shell: on
/// Windows `cmd /C start` would run whatever follows an `&` in the link.
fn open_with_system(target: &str, file: bool) {
    let mut command = if cfg!(target_os = "macos") {
        std::process::Command::new("open")
    } else if cfg!(windows) && file {
        std::process::Command::new("explorer.exe")
    } else if cfg!(windows) {
        let mut c = std::process::Command::new("rundll32.exe");
        c.arg("url.dll,FileProtocolHandler");
        c
    } else {
        std::process::Command::new("xdg-open")
    };
    let _ = command.arg(target).spawn();
}

impl eframe::App for App {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let title = self.0.window_title();
        ui.ctx().send_viewport_cmd(egui::ViewportCommand::Title(title));
        self.0.ui(ui);
        self.serve_requests();
    }
}

const USAGE: &str = "Usage: mailcraft [--demo] [--home DIR] [--lang en|it]";

fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut demo = false;
    let mut home: Option<String> = None;
    let mut lang: Option<Language> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--demo" => demo = true,
            "--home" => home = it.next().cloned(),
            "--lang" => lang = it.next().and_then(|l| Language::parse(l)),
            "--help" | "-h" => {
                println!("{USAGE}");
                return Ok(());
            }
            _ => return Err(USAGE.to_string()),
        }
    }
    let session = if demo {
        Session::demo()
    } else {
        let paths = match home {
            Some(h) => Paths::under(std::path::Path::new(&h)),
            None => Paths::default_dirs(),
        };
        Session::with_broker(mailcraft_net::factory(), mailcraft_net::broker(), Some(paths))
    };
    let mut app = MailApp::new(Box::new(session));
    if let Some(l) = lang {
        app.language = l;
    }
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1380.0, 880.0]).with_min_inner_size([900.0, 600.0]).with_title("MailCraft"),
        ..Default::default()
    };
    eframe::run_native("MailCraft", options, Box::new(move |_cc| Ok(Box::new(App(app))))).map_err(|e| e.to_string())
}
