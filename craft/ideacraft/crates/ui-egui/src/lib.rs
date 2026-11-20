//! IdeaCraft's egui front end (layer L6). It is thin: it draws the engine's state and acts only
//! through [`IdeaApp::run`], so every action is also available to the CLI and automation.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod canvas;
mod chrome;
mod gallery;
pub mod i18n;
pub mod icons;
mod sidebar;
pub mod theme;
mod widgets;

use ideacraft_engine::Session;
use ideacraft_engine::core::{Map, NodeId};
use serde_json::{Value, json};

pub use canvas::View;
pub use gallery::thumbnail;
pub use i18n::Language;
pub use ideacraft_engine::{core, formats};

/// The whole app state the UI keeps on top of the engine session.
pub struct IdeaApp {
    pub session: Session,
    /// Where the map is saved (desktop only).
    pub path: Option<std::path::PathBuf>,
    pub view: View,
    canvas: canvas::CanvasState,
    /// Text being edited: node and draft.
    editing: Option<(NodeId, String)>,
    title_draft: Option<String>,
    /// Drag from node to node draws a link instead of moving.
    pub link_mode: bool,
    pub status: String,
    /// Interface language (also used for new maps from templates).
    pub language: Language,
    /// Dark theme.
    pub dark: bool,
    /// The theme last applied to egui.
    applied_dark: Option<bool>,
    /// The open side panel, if any.
    pub sidebar: Option<SidePanel>,
    /// A template waiting for "replace the current map?".
    confirm_template: Option<&'static str>,
    template_previews: Vec<Map>,
    previews_language: Language,
    /// File dialogs the host should show (the UI crate has no file-dialog dependency).
    pub requests: Vec<Request>,
    /// The selected link (links and ideas are never selected together).
    pub edge_selection: Option<ideacraft_engine::core::EdgeId>,
    edge_label_draft: Option<String>,
    focus_edge_label: bool,
    /// Snap moves and resizes to the grid in diagram mode.
    pub snap: bool,
    /// A property being added in the Elements panel: (name, value).
    new_prop: (String, String),
    /// Search text of the shape library.
    pub shape_filter: String,
    /// How many shapes were added at the view centre since the view last moved (to fan them out).
    added_at_center: u32,
    /// The name typed for a new layer in the Layers panel.
    pub new_layer: String,
    /// A layer being renamed in the Layers panel: (current name, draft).
    rename_layer: Option<(String, String)>,
    /// The last network check, shown in the Check panel.
    pub check: Option<CheckReport>,
}

/// The panels of the left rail.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SidePanel {
    Templates,
    Shapes,
    Elements,
    Layout,
    Layers,
    Check,
    Files,
}

/// The result of the last network check (`net.validate`), kept for the Check panel.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CheckReport {
    pub errors: usize,
    pub warnings: usize,
    pub infos: usize,
    pub findings: Vec<CheckFinding>,
}

/// One finding of the network check, in the interface language.
#[derive(Clone, Debug, PartialEq)]
pub struct CheckFinding {
    pub code: String,
    /// `error`, `warning` or `info`.
    pub severity: String,
    pub message: String,
    pub nodes: Vec<u64>,
    pub edges: Vec<u64>,
}

/// Something only the host app can do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Request {
    /// Pick a file to open (IdeaCraft or any importable format).
    Open,
    /// Pick where to save the map.
    SaveAs,
    /// Pick where to export the map in another format.
    Export(ExportFormat),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExportFormat {
    Markdown,
    Opml,
    FreeMind,
    DrawIo,
    Svg,
}

impl ExportFormat {
    pub const ALL: [Self; 5] = [Self::Markdown, Self::Opml, Self::FreeMind, Self::DrawIo, Self::Svg];

    pub const fn label(self) -> &'static str {
        match self {
            // Translated with `Language::tr` where shown.
            Self::Markdown => "Markdown outline (.md)",
            Self::Opml => "OPML (.opml)",
            Self::FreeMind => "FreeMind / Freeplane (.mm)",
            Self::DrawIo => "draw.io / diagrams.net (.drawio)",
            Self::Svg => "SVG picture (.svg)",
        }
    }

    pub const fn extension(self) -> &'static str {
        match self {
            Self::Markdown => "md",
            Self::Opml => "opml",
            Self::FreeMind => "mm",
            Self::DrawIo => "drawio",
            Self::Svg => "svg",
        }
    }

    /// The command's `format` parameter.
    pub const fn command_format(self) -> &'static str {
        match self {
            Self::Markdown => "markdown",
            Self::Opml => "opml",
            Self::FreeMind => "freemind",
            Self::DrawIo => "drawio",
            Self::Svg => "svg",
        }
    }
}

impl Default for IdeaApp {
    fn default() -> Self {
        Self::new(Map::default())
    }
}

impl IdeaApp {
    pub fn new(map: Map) -> Self {
        Self {
            session: Session::new(map),
            path: None,
            view: View::default(),
            canvas: canvas::CanvasState::default(),
            editing: None,
            title_draft: None,
            link_mode: false,
            status: String::new(),
            language: Language::En,
            dark: false,
            applied_dark: None,
            sidebar: None,
            confirm_template: None,
            template_previews: Vec::new(),
            previews_language: Language::En,
            requests: Vec::new(),
            edge_selection: None,
            edge_label_draft: None,
            focus_edge_label: false,
            snap: true,
            new_prop: (String::new(), String::new()),
            shape_filter: String::new(),
            added_at_center: 0,
            new_layer: String::new(),
            rename_layer: None,
            check: None,
        }
    }

    /// Runs the network check (`net.validate`) in the interface language and keeps the report
    /// for the Check panel; the status bar gets the summary.
    pub fn check_network(&mut self) -> Option<&CheckReport> {
        self.commit_edit();
        let v = self.run("net.validate", json!({"language": self.language.code()}))?;
        let count = |k: &str| v.get(k).and_then(Value::as_u64).unwrap_or(0) as usize;
        let findings = v
            .get("findings")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .map(|f| CheckFinding {
                        code: f.get("code").and_then(Value::as_str).unwrap_or_default().to_string(),
                        severity: f.get("severity").and_then(Value::as_str).unwrap_or("info").to_string(),
                        message: f.get("message").and_then(Value::as_str).unwrap_or_default().to_string(),
                        nodes: f.get("nodes").and_then(Value::as_array).map(|n| n.iter().filter_map(Value::as_u64).collect()).unwrap_or_default(),
                        edges: f.get("edges").and_then(Value::as_array).map(|n| n.iter().filter_map(Value::as_u64).collect()).unwrap_or_default(),
                    })
                    .collect()
            })
            .unwrap_or_default();
        let report = CheckReport { errors: count("errors"), warnings: count("warnings"), infos: count("infos"), findings };
        self.status = if report.findings.is_empty() {
            self.tr("Network check: no problems found").to_string()
        } else {
            self.language.fill(
                "Network check: {errors} errors, {warnings} warnings, {infos} notes",
                &[("errors", &report.errors.to_string()), ("warnings", &report.warnings.to_string()), ("infos", &report.infos.to_string())],
            )
        };
        self.check = Some(report);
        self.check.as_ref()
    }

    /// Selects a finding's shapes (or its link) so the user sees where the problem is.
    pub fn show_finding(&mut self, finding: &CheckFinding) {
        self.commit_edit();
        if finding.nodes.is_empty() {
            if let Some(e) = finding.edges.first() {
                self.run("selection.clear", json!({}));
                self.edge_selection = Some(ideacraft_engine::core::EdgeId(*e));
            }
        } else {
            self.edge_selection = None;
            self.run("selection.set", json!({"ids": finding.nodes}));
        }
    }

    /// Adds a stencil near the centre of the view (each new one a little further down-right),
    /// switching the map to diagram mode, and selects it.
    pub fn add_shape_in_view(&mut self, kind: &str) {
        self.commit_edit();
        let step = self.added_at_center as f32 * 30.0;
        let centre = -self.view.pan / self.view.zoom;
        let x = ((centre.x + step) / canvas::GRID).round() * canvas::GRID;
        let y = ((centre.y + step) / canvas::GRID).round() * canvas::GRID;
        if self.run("node.addShape", json!({"kind": kind, "x": x, "y": y, "language": self.language.code()})).is_some() {
            self.added_at_center = (self.added_at_center + 1) % 8;
        }
    }

    /// Switches between mind-map and diagram mode.
    pub fn set_mode(&mut self, diagram: bool) {
        let mode = if diagram { "diagram" } else { "map" };
        self.run("map.setMode", json!({"mode": mode}));
    }

    /// The map is a free diagram (shapes and connectors) rather than a mind map.
    pub fn is_diagram(&self) -> bool {
        self.session.map().mode() == ideacraft_engine::core::Mode::Diagram
    }

    /// Runs a command: the UI's own (`app.language.set`) or an engine command. Failures go to
    /// the status bar.
    pub fn run(&mut self, id: &str, params: Value) -> Option<Value> {
        if id == "app.language.set" {
            let code = params.get("language").or_else(|| params.get("code")).and_then(Value::as_str).unwrap_or_default();
            return match Language::parse(code) {
                Some(l) => {
                    self.language = l;
                    Some(json!({}))
                }
                None => {
                    self.status = format!("`language` must be one of: {}", Language::ALL.map(Language::code).join(", "));
                    None
                }
            };
        }
        match self.session.execute(id, &params) {
            Ok(v) => Some(v),
            Err(e) => {
                self.status = self.language.tr(&e.to_string()).to_string();
                None
            }
        }
    }

    /// Translates an interface label into the current language.
    pub fn tr<'a>(&self, text: &'a str) -> &'a str {
        self.language.tr(text)
    }

    /// A command's translated label and shortcut, for tooltips.
    pub fn tip(&self, command: &str) -> String {
        match ideacraft_engine::command(command) {
            Some(c) if !c.shortcut.is_empty() => format!("{} ({})", self.tr(c.label), c.shortcut),
            Some(c) => self.tr(c.label).to_string(),
            None => command.to_string(),
        }
    }

    /// Saves where the map came from, or asks the host where (Save As).
    pub fn save_or_ask(&mut self) {
        #[cfg(not(target_arch = "wasm32"))]
        if self.path.is_some() {
            self.save();
            return;
        }
        self.requests.push(Request::SaveAs);
    }

    /// Replaces the map with a template in the interface language.
    pub fn apply_template(&mut self, id: &str) {
        self.commit_edit();
        if self.run("map.newFromTemplate", json!({"template": id, "language": self.language.code()})).is_some() {
            self.path = None;
            self.view.fit_pending = true;
        }
    }

    /// The map as text in an export format (or IdeaCraft JSON with `None`), with a file name.
    pub fn export_text(&mut self, format: Option<ExportFormat>) -> Option<(String, String)> {
        self.commit_edit();
        let stem: String = self
            .session
            .map()
            .title()
            .chars()
            .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == ' ' { c } else { '-' })
            .take(80)
            .collect();
        let stem = if stem.trim().is_empty() { "map".to_string() } else { stem.trim().to_string() };
        match format {
            None => Some((format!("{stem}.ideacraft.json"), self.session.map().to_json())),
            Some(f) => {
                let text = self.run("map.export", json!({"format": f.command_format()})).and_then(|v| v["text"].as_str().map(str::to_string))?;
                Some((format!("{stem}.{}", f.extension()), text))
            }
        }
    }

    /// Starts a new empty map, titled in the interface language.
    pub fn new_map(&mut self) {
        let title = self.tr("Untitled map").to_string();
        self.run("map.new", json!({"title": title}));
        self.path = None;
        self.view.fit_pending = true;
    }

    /// Runs a command that creates a node, then starts editing its text.
    fn run_and_edit(&mut self, id: &str, params: Value) {
        if let Some(new) = self.run(id, params).and_then(|v| v["id"].as_u64()) {
            // A new idea starts with an empty draft: typing replaces the default label.
            self.editing = Some((NodeId(new), String::new()));
        }
    }

    fn begin_edit(&mut self, id: NodeId) {
        if let Some(n) = self.session.map().node(id) {
            self.editing = Some((id, n.label.clone()));
        }
    }

    fn commit_edit(&mut self) {
        if let Some((id, text)) = self.editing.take() {
            // An empty draft keeps the current label.
            if !text.trim().is_empty() && self.session.map().node(id).is_some_and(|n| n.label != text) {
                self.run("node.setLabel", json!({"id": id.0, "label": text}));
            }
        }
    }

    /// Opens a file's bytes: an IdeaCraft map or any importable format.
    pub fn open_bytes(&mut self, name: &str, bytes: &[u8]) -> bool {
        match ideacraft_engine::formats::import_bytes(name, bytes) {
            Ok((map, format)) => {
                self.session.replace_map(map);
                self.view.fit_pending = true;
                self.editing = None;
                let l = self.language;
                self.status = if format == ideacraft_engine::formats::Format::IdeaCraft {
                    l.fill("Opened {name}", &[("name", name)])
                } else {
                    l.fill("Imported {name} ({format})", &[("name", name), ("format", l.tr(format.name()))])
                };
                true
            }
            Err(e) => {
                self.status = self.language.fill("Couldn't open {name}: {error}", &[("name", name), ("error", &e.to_string())]);
                false
            }
        }
    }

    /// Opens a map or imports another format (desktop). Imported files save as a new
    /// `.ideacraft.json` next to the original, never over it.
    #[cfg(not(target_arch = "wasm32"))]
    pub fn open_path(&mut self, path: &std::path::Path) {
        let name = path.display().to_string();
        let bytes = match std::fs::metadata(path).map(|m| m.len()) {
            Ok(len) if len > ideacraft_engine::formats::MAX_FILE_BYTES as u64 => {
                let error = self.tr("file too large").to_string();
                self.status = self.language.fill("Couldn't open {name}: {error}", &[("name", &name), ("error", &error)]);
                return;
            }
            _ => std::fs::read(path),
        };
        match bytes {
            Ok(bytes) if self.open_bytes(&name, &bytes) => {
                let native = name.to_ascii_lowercase().ends_with(".ideacraft.json");
                self.path = Some(if native { path.to_path_buf() } else { path.with_extension("ideacraft.json") });
                if !native {
                    self.session.mark_dirty();
                }
            }
            Ok(_) => {}
            Err(e) => self.status = self.language.fill("Couldn't open {name}: {error}", &[("name", &name), ("error", &e.to_string())]),
        }
    }

    /// Writes the map in another format (desktop).
    #[cfg(not(target_arch = "wasm32"))]
    pub fn export_to(&mut self, path: &std::path::Path, format: ExportFormat) {
        let text = self.run("map.export", json!({"format": format.command_format()})).and_then(|v| v["text"].as_str().map(str::to_string));
        if let Some(text) = text {
            self.status = match std::fs::write(path, text) {
                Ok(()) => self.language.fill("Exported {name}", &[("name", &path.display().to_string())]),
                Err(e) => self.language.fill("Couldn't export {name}: {error}", &[("name", &path.display().to_string()), ("error", &e.to_string())]),
            };
        }
    }

    /// Saves to a chosen path (desktop).
    #[cfg(not(target_arch = "wasm32"))]
    pub fn save_as(&mut self, path: std::path::PathBuf) {
        self.path = Some(path);
        self.save();
    }

    /// Saves to [`Self::path`], or to `<title>.ideacraft.json` in the current folder.
    #[cfg(not(target_arch = "wasm32"))]
    pub fn save(&mut self) {
        self.commit_edit();
        let path = self.path.clone().unwrap_or_else(|| {
            let stem: String =
                self.session.map().title().chars().map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '-' }).take(80).collect();
            std::path::PathBuf::from(format!("{}.ideacraft.json", if stem.is_empty() { "map".into() } else { stem }))
        });
        match std::fs::write(&path, self.session.map().to_json()) {
            Ok(()) => {
                self.session.mark_saved();
                self.status = self.language.fill("Saved {name}", &[("name", &path.display().to_string())]);
                self.path = Some(path);
            }
            Err(e) => {
                self.status = self.language.fill("Couldn't save {name}: {error}", &[("name", &path.display().to_string()), ("error", &e.to_string())])
            }
        }
    }

    /// Window title: map title, with a dot while unsaved.
    pub fn window_title(&self) -> String {
        format!("{}{} — IdeaCraft", if self.session.is_dirty() { "• " } else { "" }, self.session.map().title())
    }

    /// Draws one frame.
    pub fn ui(&mut self, ui: &mut egui::Ui) {
        let tokens = theme::Tokens::for_dark_mode(self.dark);
        if self.applied_dark != Some(self.dark) {
            theme::apply(ui.ctx(), &tokens);
            self.applied_dark = Some(self.dark);
        }
        canvas::keyboard(self, ui);
        chrome::top_bar(self, ui, &tokens);
        chrome::status_bar(self, ui, &tokens);
        sidebar::show(self, ui, &tokens);
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(tokens.canvas)).show(ui, |ui| canvas::show(self, ui, &tokens));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(app: &mut IdeaApp, ctx: &egui::Context, events: Vec<egui::Event>) {
        let input = egui::RawInput {
            screen_rect: Some(egui::Rect::from_min_size(egui::Pos2::ZERO, egui::vec2(1200.0, 800.0))),
            events,
            ..Default::default()
        };
        let mut out = ctx.run_ui(input, |ui| app.ui(ui));
        out.textures_delta.clear();
    }

    fn key(key: egui::Key, modifiers: egui::Modifiers) -> egui::Event {
        egui::Event::Key { key, physical_key: None, pressed: true, repeat: false, modifiers }
    }

    #[test]
    fn renders_the_sample_and_survives_keyboard_editing() {
        let ctx = egui::Context::default();
        let mut app = IdeaApp::new(Map::sample());
        for _ in 0..3 {
            frame(&mut app, &ctx, vec![]);
        }
        let n = app.session.map().node_count();
        let root = app.session.map().roots()[0];
        app.run("selection.set", json!({"ids": [root.0]}));
        frame(&mut app, &ctx, vec![key(egui::Key::Tab, egui::Modifiers::NONE)]);
        assert_eq!(app.session.map().node_count(), n + 1, "Tab adds a child");
        assert!(app.editing.is_some(), "and starts editing it");
        frame(&mut app, &ctx, vec![egui::Event::Text("Nuova idea".into())]);
        frame(&mut app, &ctx, vec![key(egui::Key::Enter, egui::Modifiers::NONE)]);
        frame(&mut app, &ctx, vec![]);
        assert!(app.editing.is_none());
        let sel = app.session.selection()[0];
        assert_eq!(app.session.map().node(sel).unwrap().label, "Nuova idea");
        frame(&mut app, &ctx, vec![key(egui::Key::Delete, egui::Modifiers::NONE)]);
        assert_eq!(app.session.map().node_count(), n);
        frame(&mut app, &ctx, vec![key(egui::Key::Z, egui::Modifiers::COMMAND)]);
        assert_eq!(app.session.map().node_count(), n + 1, "Cmd+Z undoes the delete");
        for _ in 0..2 {
            frame(
                &mut app,
                &ctx,
                vec![
                    egui::Event::Zoom(1.5),
                    egui::Event::MouseWheel {
                        unit: egui::MouseWheelUnit::Point,
                        delta: egui::vec2(10.0, -30.0),
                        modifiers: egui::Modifiers::NONE,
                        phase: egui::TouchPhase::Move,
                    },
                ],
            );
        }
    }

    #[test]
    fn template_gallery_renders_and_open_bytes_imports() {
        let ctx = egui::Context::default();
        let mut app = IdeaApp { sidebar: Some(SidePanel::Templates), ..IdeaApp::default() };
        frame(&mut app, &ctx, vec![]);
        frame(&mut app, &ctx, vec![]);
        assert_eq!(app.template_previews.len(), ideacraft_engine::formats::templates::ALL.len());
        assert!(app.open_bytes("trip.opml", b"<opml><body><outline text='Trip'><outline text='Pack'/></outline></body></opml>"));
        assert_eq!(app.session.map().node_count(), 2);
        assert!(!app.open_bytes("bad.mm", b"<nope"));
        assert_eq!(app.session.map().node_count(), 2, "a failed import keeps the map");
    }

    #[test]
    fn italian_interface_and_templates() {
        let ctx = egui::Context::default();
        let mut app = IdeaApp { sidebar: Some(SidePanel::Templates), ..IdeaApp::default() };
        assert!(app.run("app.language.set", json!({"language": "it"})).is_some());
        assert_eq!(app.language, Language::It);
        assert!(app.run("app.language.set", json!({"language": "xx"})).is_none());
        frame(&mut app, &ctx, vec![]);
        assert_eq!(app.template_previews.first().map(|m| m.title()), Some("Brainstorming"));
        assert!(app.template_previews.iter().any(|m| m.title() == "Analisi SWOT"), "previews follow the language");
        app.new_map();
        assert_eq!(app.session.map().title(), "Mappa senza titolo");
        app.run("node.delete", json!({}));
        assert_eq!(app.status, "Seleziona prima un’idea", "common engine errors are translated");
        frame(&mut app, &ctx, vec![]);
    }

    fn pointer(app: &mut IdeaApp, ctx: &egui::Context, events: Vec<egui::Event>) {
        frame(app, ctx, events);
        frame(app, ctx, vec![]);
    }

    #[test]
    fn diagram_mode_marquee_resize_and_tooltips() {
        use egui::{Event, PointerButton, Pos2, pos2};
        let ctx = egui::Context::default();
        let mut app = IdeaApp { sidebar: Some(SidePanel::Shapes), ..IdeaApp::default() };
        app.run("map.new", json!({"title": "net"}));
        app.add_shape_in_view("net.router");
        app.add_shape_in_view("net.switch");
        assert!(app.is_diagram());
        assert_eq!(app.session.map().node_count(), 2);
        app.run("node.setProps", json!({"id": 1, "props": {"ip": "10.0.0.256", "status": "down"}}));
        app.run("node.move", json!({"id": 2, "x": 400, "y": 0}));
        app.view.fit_pending = false;
        app.view.pan = egui::Vec2::ZERO;
        app.view.zoom = 1.0;
        for _ in 0..2 {
            frame(&mut app, &ctx, vec![]);
        }
        // Map (0, 0) is drawn at the canvas centre (pan is zero, zoom is one).
        let origin = app.canvas.last_canvas.center();
        let r = app.session.map().node(ideacraft_engine::core::NodeId(1)).unwrap().pos;
        let at = |p: ideacraft_engine::core::Point| pos2(origin.x + p.x, origin.y + p.y);
        let press =
            |p: Pos2, down: bool| Event::PointerButton { pos: p, button: PointerButton::Primary, pressed: down, modifiers: egui::Modifiers::NONE };
        // Marquee: drag over an empty area that covers both shapes.
        let (a, b) = (at(ideacraft_engine::core::Point::new(-200.0, -200.0)), at(ideacraft_engine::core::Point::new(520.0, 120.0)));
        pointer(&mut app, &ctx, vec![Event::PointerMoved(a), press(a, true)]);
        pointer(&mut app, &ctx, vec![Event::PointerMoved(pos2(a.x + 30.0, a.y + 30.0))]);
        pointer(&mut app, &ctx, vec![Event::PointerMoved(b)]);
        pointer(&mut app, &ctx, vec![press(b, false)]);
        assert_eq!(app.session.selection().len(), 2, "marquee selects both shapes");
        // Hover shows the properties tooltip (no panic, hover tracked).
        pointer(&mut app, &ctx, vec![Event::PointerMoved(at(r))]);
        assert_eq!(app.canvas.hover, Some(ideacraft_engine::core::NodeId(1)));
        // Resize from the bottom-right corner of the router (selected alone).
        app.run("selection.set", json!({"ids": [1]}));
        frame(&mut app, &ctx, vec![]);
        let size = app.session.map().node(ideacraft_engine::core::NodeId(1)).unwrap().size.unwrap();
        let corner = at(ideacraft_engine::core::Point::new(r.x + size.w / 2.0, r.y + size.h / 2.0));
        pointer(&mut app, &ctx, vec![Event::PointerMoved(corner), press(corner, true)]);
        // Drags start after a few pixels of motion, where the handle is still under the pointer.
        pointer(&mut app, &ctx, vec![Event::PointerMoved(pos2(corner.x + 3.0, corner.y + 3.0))]);
        pointer(&mut app, &ctx, vec![Event::PointerMoved(pos2(corner.x + 40.0, corner.y + 40.0))]);
        let end = pos2(corner.x + 60.0, corner.y + 40.0);
        pointer(&mut app, &ctx, vec![Event::PointerMoved(end), press(end, false)]);
        let grown = app.session.map().node(ideacraft_engine::core::NodeId(1)).unwrap().size.unwrap();
        assert!(grown.w > size.w && grown.h > size.h, "{size:?} → {grown:?}");
        assert_eq!(grown.w % canvas::GRID, 0.0, "snapped to the grid");
        // Link and style it through the link bar's commands; select the link by clicking it.
        app.run("edge.add", json!({"from": 1, "to": 2}));
        app.run("selection.clear", json!({}));
        let s = app.session.map().node(ideacraft_engine::core::NodeId(2)).unwrap().pos;
        let mid = at(ideacraft_engine::core::Point::new((r.x + s.x) / 2.0, (r.y + s.y) / 2.0));
        pointer(&mut app, &ctx, vec![Event::PointerMoved(mid), press(mid, true), press(mid, false)]);
        assert!(app.edge_selection.is_some(), "clicking a link selects it");
        frame(&mut app, &ctx, vec![key(egui::Key::Delete, egui::Modifiers::NONE)]);
        assert_eq!(app.session.map().edge_count(), 0, "Delete removes the selected link");
        // Groups: Cmd+G groups both shapes, clicking one then selects both, Cmd+Shift+G ungroups.
        let node = |app: &IdeaApp, id: u64| app.session.map().node(ideacraft_engine::core::NodeId(id)).cloned().unwrap();
        app.run("selection.set", json!({"ids": [1, 2]}));
        frame(&mut app, &ctx, vec![key(egui::Key::G, egui::Modifiers::COMMAND)]);
        assert!(node(&app, 1).group.is_some() && node(&app, 1).group == node(&app, 2).group, "Cmd+G groups the selection");
        app.run("selection.clear", json!({}));
        pointer(&mut app, &ctx, vec![Event::PointerMoved(at(r)), press(at(r), true), press(at(r), false)]);
        assert_eq!(app.session.selection().len(), 2, "clicking a grouped shape selects its group");
        frame(&mut app, &ctx, vec![key(egui::Key::G, egui::Modifiers::COMMAND | egui::Modifiers::SHIFT)]);
        assert!(node(&app, 1).group.is_none(), "Cmd+Shift+G ungroups");
        // Layers: shapes on a locked layer are not picked on the canvas; hidden ones are not drawn.
        app.run("node.setLayer", json!({"ids": [1], "layer": "Core"}));
        app.run("layer.setLocked", json!({"name": "Core", "locked": true}));
        app.run("selection.clear", json!({}));
        pointer(&mut app, &ctx, vec![Event::PointerMoved(at(r)), press(at(r), true), press(at(r), false)]);
        assert!(app.session.selection().is_empty(), "a locked shape is not selectable");
        app.run("layer.setLocked", json!({"name": "Core", "locked": false}));
        app.run("layer.setVisible", json!({"name": "Core", "visible": false}));
        frame(&mut app, &ctx, vec![]);
        assert!(!app.session.map().is_visible(ideacraft_engine::core::NodeId(1)));
        app.run("layer.setVisible", json!({"name": "Core", "visible": true}));
        // The network check finds the router's missing mask and reports in Italian.
        app.language = Language::It;
        let report = app.check_network().cloned().unwrap();
        assert!(!report.findings.is_empty(), "{report:?}");
        assert!(app.status.starts_with("Verifica rete"), "{}", app.status);
        let first = report.findings[0].clone();
        app.show_finding(&first);
        assert!(!app.session.selection().is_empty() || app.edge_selection.is_some());
        app.dark = true;
        frame(&mut app, &ctx, vec![]);
        for panel in [SidePanel::Elements, SidePanel::Layout, SidePanel::Layers, SidePanel::Check] {
            app.sidebar = Some(panel);
            frame(&mut app, &ctx, vec![]);
        }
        app.rename_layer = Some(("Core".into(), "Backbone".into()));
        app.new_layer = "Edge".into();
        frame(&mut app, &ctx, vec![]);
    }

    #[test]
    fn renders_an_empty_map_in_dark_mode() {
        let ctx = egui::Context::default();
        ctx.set_visuals(egui::Visuals::dark());
        let mut app = IdeaApp::default();
        frame(&mut app, &ctx, vec![]);
        frame(&mut app, &ctx, vec![key(egui::Key::Tab, egui::Modifiers::NONE), key(egui::Key::Space, egui::Modifiers::NONE)]);
        assert_eq!(app.session.map().node_count(), 0, "nothing selected, nothing added");
    }
}
