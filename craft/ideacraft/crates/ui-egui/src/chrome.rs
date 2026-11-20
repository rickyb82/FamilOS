//! The top bar (file menu, title, undo/redo, language, theme, Export) and the status bar.

use egui::{Align, Layout, RichText, Stroke, vec2};
use serde_json::json;

use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{divider, icon_button, primary_button};
use crate::{ExportFormat, IdeaApp, Request, SidePanel};

pub const TOP_BAR_H: f32 = 52.0;

fn file_menu(app: &mut IdeaApp, ui: &mut egui::Ui) {
    if ui.button(app.tr("New")).clicked() {
        app.new_map();
        ui.close();
    }
    if ui.button(app.tr("Templates…")).clicked() {
        app.sidebar = Some(SidePanel::Templates);
        ui.close();
    }
    if ui.button(app.tr("Open or import…")).clicked() {
        app.requests.push(Request::Open);
        ui.close();
    }
    ui.separator();
    if ui.button(app.tr("Save")).clicked() {
        app.save_or_ask();
        ui.close();
    }
    if ui.button(app.tr("Save as…")).clicked() {
        app.requests.push(Request::SaveAs);
        ui.close();
    }
    ui.separator();
    export_items(app, ui);
}

fn export_items(app: &mut IdeaApp, ui: &mut egui::Ui) {
    ui.label(RichText::new(app.tr("Export as")).small().color(ui.visuals().weak_text_color()));
    for f in ExportFormat::ALL {
        if ui.button(app.tr(f.label())).clicked() {
            app.requests.push(Request::Export(f));
            ui.close();
        }
    }
}

pub fn top_bar(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let frame = egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(12, 0)).stroke(Stroke::NONE);
    egui::Panel::top("top_bar").exact_size(TOP_BAR_H).frame(frame).show(ui, |ui| {
        ui.horizontal_centered(|ui| {
            let (logo, _) = ui.allocate_exact_size(vec2(28.0, 28.0), egui::Sense::hover());
            icons::paint(ui.painter(), logo, Icon::Logo, t.ui_text);
            ui.label(RichText::new("IdeaCraft").strong().size(16.0).color(t.ui_text));
            ui.add_space(6.0);
            ui.menu_button(app.tr("File"), |ui| file_menu(app, ui));
            divider(ui, t);
            let mut title = app.title_draft.clone().unwrap_or_else(|| app.session.map().title().to_string());
            let edit = ui.add(
                egui::TextEdit::singleline(&mut title)
                    .desired_width(220.0)
                    .frame(egui::Frame::NONE)
                    .hint_text(app.tr("Map title"))
                    .font(egui::TextStyle::Body),
            );
            if edit.hovered() || edit.has_focus() {
                ui.painter().rect_stroke(
                    edit.rect.expand(4.0),
                    6.0,
                    Stroke::new(1.0, if edit.has_focus() { t.accent } else { t.border }),
                    egui::StrokeKind::Outside,
                );
            }
            if edit.changed() {
                app.title_draft = Some(title.clone());
            }
            if edit.lost_focus()
                && let Some(t) = app.title_draft.take().filter(|t| t != app.session.map().title())
            {
                app.run("map.setTitle", json!({"title": t}));
            }
            if app.session.is_dirty() {
                ui.label(RichText::new("•").color(t.muted)).on_hover_text(app.tr("Unsaved changes"));
            }
            divider(ui, t);
            let diagram = app.is_diagram();
            if icon_button(ui, t, Icon::MindMap, app.tr("Mind map mode"), true, !diagram).clicked() && diagram {
                app.set_mode(false);
            }
            if icon_button(ui, t, Icon::Diagram, app.tr("Diagram mode"), true, diagram).clicked() && !diagram {
                app.set_mode(true);
                if app.sidebar.is_none() {
                    app.sidebar = Some(SidePanel::Shapes);
                }
            }
            divider(ui, t);
            let (can_undo, can_redo) = (app.session.can_undo(), app.session.can_redo());
            if icon_button(ui, t, Icon::Undo, &app.tip("edit.undo"), can_undo, false).clicked() {
                app.commit_edit();
                app.run("edit.undo", json!({}));
            }
            if icon_button(ui, t, Icon::Redo, &app.tip("edit.redo"), can_redo, false).clicked() {
                app.run("edit.redo", json!({}));
            }
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                let export = primary_button(ui, t, Some(Icon::Download), app.tr("Export"));
                egui::Popup::menu(&export).show(|ui| {
                    ui.set_min_width(220.0);
                    export_items(app, ui);
                    ui.separator();
                    if ui.button(app.tr("Save as…")).clicked() {
                        app.requests.push(Request::SaveAs);
                        ui.close();
                    }
                });
                ui.add_space(4.0);
                let (icon, tip) = if app.dark { (Icon::Sun, app.tr("Light theme")) } else { (Icon::Moon, app.tr("Dark theme")) };
                if icon_button(ui, t, icon, tip, true, false).clicked() {
                    app.dark = !app.dark;
                }
                let globe = icon_button(ui, t, Icon::Globe, app.tr("Language"), true, false);
                egui::Popup::menu(&globe).show(|ui| {
                    for l in crate::Language::ALL {
                        if ui.selectable_label(l == app.language, l.name()).clicked() {
                            app.run("app.language.set", json!({"language": l.code()}));
                            ui.close();
                        }
                    }
                });
                ui.label(RichText::new(app.language.name()).small().color(t.muted));
            });
        });
    });
}

pub fn status_bar(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let frame = egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(12, 4));
    egui::Panel::bottom("status").frame(frame).show(ui, |ui| {
        ui.horizontal(|ui| {
            let map = app.session.map();
            let counts = app.language.fill(
                "{ideas} ideas · {links} links · {selected} selected",
                &[
                    ("ideas", &map.node_count().to_string()),
                    ("links", &map.edge_count().to_string()),
                    ("selected", &app.session.selection().len().to_string()),
                ],
            );
            ui.label(RichText::new(counts).small().color(t.muted));
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                let hint = app.tr("Double-click: new idea / edit · Tab: child · Enter: sibling · Shift+drag: link · Space: collapse");
                let text = if app.status.is_empty() { hint.to_string() } else { app.status.clone() };
                ui.label(RichText::new(text).small().color(t.muted));
            });
        });
    });
}
