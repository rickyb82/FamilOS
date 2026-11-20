//! The left rail and its panels: Templates, Shapes, Elements, Layout, Layers, Check and Files.

use egui::{Color32, FontId, Rect, RichText, Sense, Stroke, StrokeKind, pos2, vec2};
use ideacraft_engine::core::route::glyph_box;
use ideacraft_engine::core::{Rgb, Shape, stencil};
use ideacraft_engine::formats::templates;
use serde_json::json;

use crate::gallery::thumbnail;
use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{icon_button, icon_text_button, primary_button, rail_item};
use crate::{ExportFormat, IdeaApp, Request, SidePanel};

const RAIL_W: f32 = 76.0;
const PANEL_W: f32 = 340.0;

pub fn show(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let rail = egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(4, 10));
    egui::Panel::left("rail").exact_size(RAIL_W).resizable(false).frame(rail).show(ui, |ui| {
        ui.vertical_centered(|ui| {
            for (panel, icon, label) in [
                (SidePanel::Templates, Icon::Templates, "Templates"),
                (SidePanel::Shapes, Icon::Shapes, "Shapes"),
                (SidePanel::Elements, Icon::Elements, "Elements"),
                (SidePanel::Layout, Icon::Layout, "Layout"),
                (SidePanel::Layers, Icon::Layers, "Layers"),
                (SidePanel::Check, Icon::Check, "Check"),
                (SidePanel::Files, Icon::Files, "Files"),
            ] {
                let active = app.sidebar == Some(panel);
                if rail_item(ui, t, icon, app.tr(label), active).clicked() {
                    app.sidebar = if active { None } else { Some(panel) };
                }
            }
        });
    });
    let Some(panel) = app.sidebar else { return };
    let frame = egui::Frame::NONE.fill(t.panel).inner_margin(egui::Margin::symmetric(16, 14));
    egui::Panel::left("side_panel").exact_size(PANEL_W).resizable(false).frame(frame).show(ui, |ui| {
        ui.horizontal(|ui| {
            let title = match panel {
                SidePanel::Templates => "Templates",
                SidePanel::Shapes => "Shapes",
                SidePanel::Elements => "Elements",
                SidePanel::Layout => "Layout",
                SidePanel::Layers => "Layers",
                SidePanel::Check => "Check",
                SidePanel::Files => "Files",
            };
            ui.label(RichText::new(app.tr(title)).heading().strong().color(t.ui_text));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if icon_button(ui, t, Icon::Close, app.tr("Close"), true, false).clicked() {
                    app.sidebar = None;
                }
            });
        });
        ui.add_space(6.0);
        egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| match panel {
            SidePanel::Templates => templates_panel(app, ui, t),
            SidePanel::Shapes => shapes_panel(app, ui, t),
            SidePanel::Elements => elements_panel(app, ui, t),
            SidePanel::Layout => layout_panel(app, ui, t),
            SidePanel::Layers => layers_panel(app, ui, t),
            SidePanel::Check => check_panel(app, ui, t),
            SidePanel::Files => files_panel(app, ui, t),
        });
    });
}

fn section(ui: &mut egui::Ui, t: &Tokens, text: &str) {
    ui.add_space(10.0);
    ui.label(RichText::new(text).small().strong().color(t.muted));
    ui.add_space(2.0);
}

fn templates_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let lang = app.language.templates();
    if app.template_previews.len() != templates::ALL.len() || app.previews_language != app.language {
        app.template_previews = templates::ALL.iter().map(|tpl| tpl.build_in(lang)).collect();
        app.previews_language = app.language;
    }
    ui.label(RichText::new(app.tr("Pick a starting point. Everything stays editable.")).color(t.muted));
    if let Some(id) = app.confirm_template {
        ui.add_space(8.0);
        egui::Frame::NONE.fill(t.active).corner_radius(10).inner_margin(10).show(ui, |ui| {
            ui.label(RichText::new(app.tr("Replace the current map? Unsaved changes will be lost.")).color(t.ui_text));
            ui.horizontal(|ui| {
                if primary_button(ui, t, None, app.tr("Replace")).clicked() {
                    app.apply_template(id);
                    app.confirm_template = None;
                }
                if ui.button(app.tr("Cancel")).clicked() {
                    app.confirm_template = None;
                }
            });
        });
    }
    ui.add_space(8.0);
    let card = vec2((ui.available_width() - 10.0) / 2.0, 124.0);
    egui::Grid::new("templates").spacing(vec2(10.0, 10.0)).show(ui, |ui| {
        for (i, tpl) in templates::ALL.iter().enumerate() {
            let (rect, resp) = ui.allocate_exact_size(card, Sense::click());
            let hover = resp.hovered();
            let p = ui.painter();
            p.rect_filled(rect.translate(vec2(0.0, 2.0)), 10.0, Color32::from_black_alpha(if t.dark { 40 } else { 12 }));
            p.rect_filled(rect, 10.0, t.card);
            p.rect_stroke(rect, 10.0, Stroke::new(if hover { 2.0 } else { 1.0 }, if hover { t.accent } else { t.border }), StrokeKind::Inside);
            let thumb = Rect::from_min_size(rect.min + vec2(6.0, 6.0), vec2(card.x - 12.0, 78.0));
            if let Some(map) = app.template_previews.get(i) {
                thumbnail(p, map, thumb, t);
            }
            let text = p.with_clip_rect(rect.shrink(4.0));
            text.text(pos2(rect.left() + 10.0, thumb.bottom() + 8.0), egui::Align2::LEFT_TOP, tpl.name(lang), FontId::proportional(13.0), t.ui_text);
            text.text(
                pos2(rect.left() + 10.0, thumb.bottom() + 25.0),
                egui::Align2::LEFT_TOP,
                tpl.category(lang),
                FontId::proportional(11.0),
                t.muted,
            );
            if resp.on_hover_text(tpl.description(lang)).clicked() {
                let map = app.session.map();
                if app.session.is_dirty() && map.node_count() > 0 {
                    app.confirm_template = Some(tpl.id);
                } else {
                    app.apply_template(tpl.id);
                }
            }
            if i % 2 == 1 {
                ui.end_row();
            }
        }
    });
}

/// Link colours offered in the link bar.
pub const LINK_COLORS: [Rgb; 8] = [
    Rgb(0xe0, 0x6a, 0x2c),
    Rgb(0x4a, 0x4f, 0x5e),
    Rgb(0x5b, 0x5b, 0xf0),
    Rgb(0x22, 0xa0, 0x6b),
    Rgb(0xe5, 0x48, 0x4d),
    Rgb(0xe3, 0xa0, 0x08),
    Rgb(0x8a, 0x90, 0xa3),
    Rgb(0x1f, 0x9a, 0xd6),
];

/// Draws a small preview of a shape (and stencil glyph) in `r`.
pub fn shape_preview(p: &egui::Painter, r: Rect, shape: Shape, glyph: Option<stencil::Glyph>, fill: Color32, ink: Color32) {
    let (w, h) = (r.width(), r.height());
    let stroke = Stroke::new(1.0, ink.gamma_multiply(0.5));
    match shape {
        Shape::Text => {
            p.text(r.center(), egui::Align2::CENTER_CENTER, "Aa", FontId::proportional(h * 0.6), ink);
        }
        Shape::Container => {
            p.rect_filled(r, 4.0, fill.gamma_multiply(0.6));
            let c = [r.left_top(), r.right_top(), r.right_bottom(), r.left_bottom(), r.left_top()];
            p.extend(egui::Shape::dashed_line(&c, stroke, 4.0, 3.0));
        }
        Shape::Ellipse => {
            p.add(egui::Shape::ellipse_filled(r.center(), r.size() / 2.0, fill));
            p.add(egui::Shape::ellipse_stroke(r.center(), r.size() / 2.0, stroke));
        }
        s => match s.polygon(w, h) {
            Some(poly) => {
                let pts: Vec<egui::Pos2> = poly.iter().map(|q| r.center() + vec2(q.x, q.y)).collect();
                p.add(egui::Shape::convex_polygon(pts.clone(), fill, Stroke::NONE));
                p.add(egui::Shape::closed_line(pts, stroke));
            }
            None => {
                let rr = match s {
                    Shape::Rect | Shape::Subprocess => 0.0,
                    Shape::Pill => h / 2.0,
                    _ => 5.0,
                };
                p.rect_filled(r, rr, fill);
                p.rect_stroke(r, rr, stroke, StrokeKind::Inside);
            }
        },
    }
    for line in shape.inner_strokes(w, h) {
        p.add(egui::Shape::line(line.iter().map(|q| r.center() + vec2(q.x, q.y)).collect(), stroke));
    }
    if let Some(g) = glyph {
        let (gx, gy, gw, gh) = glyph_box(w, h, false);
        let at = |(x, y): &(f32, f32)| r.center() + vec2(gx + x * gw, gy + y * gh);
        let st = Stroke::new(1.3, ink);
        for line in g.lines {
            p.add(egui::Shape::line(line.iter().map(at).collect(), st));
        }
        for (x, y, rad) in g.circles {
            p.circle_stroke(at(&(*x, *y)), rad * gw, st);
        }
        for poly in g.fills {
            p.add(egui::Shape::convex_polygon(poly.iter().map(at).collect(), ink, Stroke::NONE));
        }
    }
}

/// The shape library: every stencil by category; a click adds it to the diagram.
fn shapes_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let italian = app.language == crate::Language::It;
    ui.label(
        RichText::new(app.tr("Click a shape to add it. Drag it on the canvas, resize it from its corners, Shift+drag to connect."))
            .small()
            .color(t.muted),
    );
    let mut filter = app.shape_filter.clone();
    if ui.add(egui::TextEdit::singleline(&mut filter).hint_text(app.tr("Search shapes")).desired_width(f32::INFINITY)).changed() {
        app.shape_filter = filter.clone();
    }
    let needle = filter.trim().to_ascii_lowercase();
    for category in stencil::Category::ALL {
        let items: Vec<&stencil::Stencil> = stencil::in_category(category)
            .filter(|s| needle.is_empty() || s.name(italian).to_ascii_lowercase().contains(&needle) || s.id.contains(&needle))
            .collect();
        if items.is_empty() {
            continue;
        }
        section(ui, t, if italian { category.name_it() } else { category.name() });
        ui.horizontal_wrapped(|ui| {
            ui.spacing_mut().item_spacing = vec2(8.0, 8.0);
            for st in items {
                let (rect, resp) = ui.allocate_exact_size(vec2(92.0, 78.0), Sense::click());
                let p = ui.painter();
                p.rect_filled(rect, 8.0, if resp.hovered() { t.hover } else { t.card });
                p.rect_stroke(rect, 8.0, Stroke::new(1.0, if resp.hovered() { t.accent } else { t.border }), StrokeKind::Inside);
                let preview =
                    Rect::from_center_size(rect.center() - vec2(0.0, 10.0), vec2(if st.shape == Shape::Container { 56.0 } else { 46.0 }, 30.0));
                let glyph = (!st.glyph.is_empty()).then_some(st.glyph);
                shape_preview(p, preview, st.shape, glyph, Color32::from_rgb(st.color.0, st.color.1, st.color.2), t.ui_text);
                let name = p.with_clip_rect(rect.shrink(3.0));
                name.text(
                    pos2(rect.center().x, rect.bottom() - 8.0),
                    egui::Align2::CENTER_BOTTOM,
                    st.name(italian),
                    FontId::proportional(11.0),
                    t.ui_text,
                );
                if resp.on_hover_text(st.name(italian)).clicked() {
                    app.add_shape_in_view(st.id);
                }
            }
        });
    }
}

fn elements_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let has_sel = !app.session.selection().is_empty();
    let diagram = app.is_diagram();
    section(ui, t, app.tr("Add"));
    if icon_text_button(ui, t, Icon::Plus, app.tr("New idea"), true).clicked() {
        app.run_and_edit("node.add", json!({}));
    }
    if diagram {
        if icon_text_button(ui, t, Icon::Shapes, app.tr("Shape library"), true).clicked() {
            app.sidebar = Some(SidePanel::Shapes);
        }
    } else {
        if icon_text_button(ui, t, Icon::Child, app.tr("Child idea"), has_sel).on_hover_text(app.tip("node.addChild")).clicked() {
            app.run_and_edit("node.addChild", json!({}));
        }
        if icon_text_button(ui, t, Icon::Sibling, app.tr("Same level"), has_sel).on_hover_text(app.tip("node.addSibling")).clicked() {
            app.run_and_edit("node.addSibling", json!({}));
        }
    }
    let link = icon_text_button(ui, t, Icon::Link, app.tr("Link ideas"), true);
    if app.link_mode {
        ui.painter().rect_stroke(link.rect, 8.0, Stroke::new(1.5, t.accent), StrokeKind::Inside);
    }
    if link.on_hover_text(app.tr("Drag from one idea to another to link them (or hold Shift while dragging)")).clicked() {
        app.link_mode = !app.link_mode;
    }
    if !has_sel {
        ui.add_space(8.0);
        ui.label(RichText::new(app.tr("Select an idea to change its shape and colour.")).small().color(t.muted));
    }
    // Properties of the selected shape.
    let single = match app.session.selection() {
        [only] => app.session.map().node(*only).cloned().map(|n| (*only, n)),
        _ => None,
    };
    if let Some((only, n)) = single {
        section(ui, t, app.tr("Properties"));
        let italian = app.language == crate::Language::It;
        if let Some(st) = n.kind.as_deref().and_then(stencil::find) {
            ui.label(RichText::new(st.name(italian)).small().color(t.muted));
        }
        let (mut w, mut h) = n.size.map(|s| (s.w, s.h)).unwrap_or((0.0, 0.0));
        ui.horizontal(|ui| {
            ui.label(RichText::new(app.tr("Size")).small().color(t.muted));
            let cw = ui.add(egui::DragValue::new(&mut w).range(16.0..=4000.0).speed(1.0).suffix(" w"));
            let ch = ui.add(egui::DragValue::new(&mut h).range(16.0..=4000.0).speed(1.0).suffix(" h"));
            if (cw.changed() || ch.changed()) && w >= 16.0 && h >= 16.0 {
                app.run("node.resize", json!({"id": only.0, "w": w, "h": h}));
            }
            if n.size.is_some() && ui.small_button(app.tr("Auto")).on_hover_text(app.tr("Fit the text")).clicked() {
                app.run("node.resize", json!({"id": only.0}));
            }
        });
        egui::Grid::new("props").num_columns(3).spacing(vec2(6.0, 4.0)).show(ui, |ui| {
            for (k, v) in &n.props {
                ui.label(RichText::new(k).small().color(t.muted));
                let mut value = v.clone();
                let te = ui.add(egui::TextEdit::singleline(&mut value).desired_width(120.0));
                if te.lost_focus() && value != *v {
                    app.run("node.setProps", json!({"id": only.0, "props": {k: value}}));
                }
                if icon_button(ui, t, Icon::Close, app.tr("Remove"), true, false).clicked() {
                    app.run("node.setProps", json!({"id": only.0, "props": {k: ""}}));
                }
                ui.end_row();
            }
            let (mut name, mut value) = app.new_prop.clone();
            ui.add(egui::TextEdit::singleline(&mut name).hint_text(app.tr("name")).desired_width(80.0));
            let ve = ui.add(egui::TextEdit::singleline(&mut value).hint_text(app.tr("value")).desired_width(120.0));
            let add = icon_button(ui, t, Icon::Plus, app.tr("Add property"), !name.trim().is_empty(), false).clicked();
            app.new_prop = (name.clone(), value.clone());
            if (add || (ve.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)))) && !name.trim().is_empty() {
                app.run("node.setProps", json!({"id": only.0, "props": {name.trim(): value}}));
                app.new_prop = (String::new(), String::new());
            }
            ui.end_row();
        });
        ui.label(RichText::new(app.tr("A `status` of up, warning or down shows a light on the shape.")).small().color(t.muted));
    }
    section(ui, t, app.tr("Shape"));
    let current = app.session.selection().first().and_then(|id| app.session.map().node(*id)).map(|n| n.shape);
    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing = vec2(6.0, 6.0);
        for shape in Shape::ALL {
            let (rect, resp) = ui.allocate_exact_size(vec2(58.0, 44.0), if has_sel { Sense::click() } else { Sense::hover() });
            let p = ui.painter();
            let on = current == Some(shape);
            p.rect_filled(
                rect,
                8.0,
                if on {
                    t.active
                } else if resp.hovered() && has_sel {
                    t.hover
                } else {
                    t.card
                },
            );
            p.rect_stroke(rect, 8.0, Stroke::new(1.0, if on { t.accent } else { t.border }), StrokeKind::Inside);
            let r = Rect::from_center_size(rect.center(), vec2(40.0, 24.0));
            shape_preview(p, r, shape, None, Color32::from_rgb(0xb3, 0xd4, 0xff), t.ui_text);
            if resp.on_hover_text(app.tr(shape.name())).clicked() {
                app.run("node.setShape", json!({"shape": shape.name()}));
            }
        }
    });
    section(ui, t, app.tr("Colour"));
    swatches(app, ui, t, has_sel, 30.0);
}

/// The fill palette; clicking sets the selection's colour.
pub fn swatches(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens, enabled: bool, size: f32) {
    let current = app.session.selection().first().and_then(|id| app.session.map().node(*id)).map(|n| n.color);
    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing = vec2(6.0, 6.0);
        for color in Rgb::PALETTE {
            let (rect, resp) = ui.allocate_exact_size(vec2(size, size), if enabled { Sense::click() } else { Sense::hover() });
            let p = ui.painter();
            p.circle_filled(rect.center(), size / 2.0 - 1.0, Color32::from_rgb(color.0, color.1, color.2));
            let on = current == Some(color);
            let stroke = if on {
                Stroke::new(2.5, t.accent)
            } else if resp.hovered() && enabled {
                Stroke::new(1.5, t.ui_text)
            } else {
                Stroke::new(1.0, t.border)
            };
            p.circle_stroke(rect.center(), size / 2.0 - 1.0, stroke);
            if resp.on_hover_text(color.hex()).clicked() {
                app.run("node.setColor", json!({"color": color.hex()}));
            }
        }
    });
}

fn big_choice(ui: &mut egui::Ui, t: &Tokens, icon: Icon, title: &str, text: &str) -> egui::Response {
    let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 64.0), Sense::click());
    let p = ui.painter();
    p.rect_filled(rect, 10.0, if resp.hovered() { t.hover } else { t.card });
    p.rect_stroke(rect, 10.0, Stroke::new(1.0, if resp.hovered() { t.accent } else { t.border }), StrokeKind::Inside);
    let tile = Rect::from_min_size(rect.min + vec2(10.0, 10.0), vec2(44.0, 44.0));
    p.rect_filled(tile, 8.0, t.active);
    icons::paint(p, tile.shrink(10.0), icon, t.accent);
    p.text(pos2(tile.right() + 12.0, rect.top() + 14.0), egui::Align2::LEFT_TOP, title, FontId::proportional(14.0), t.ui_text);
    p.text(pos2(tile.right() + 12.0, rect.top() + 34.0), egui::Align2::LEFT_TOP, text, FontId::proportional(11.5), t.muted);
    resp
}

fn layout_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    if app.is_diagram() {
        let n = app.session.selection().len();
        section(ui, t, app.tr("Align the selected shapes"));
        ui.horizontal(|ui| {
            for (icon, edge, tip) in [
                (Icon::AlignLeft, "left", "Align left"),
                (Icon::AlignCenterX, "centerX", "Align centres"),
                (Icon::AlignRight, "right", "Align right"),
                (Icon::AlignTop, "top", "Align top"),
                (Icon::AlignCenterY, "centerY", "Align middles"),
                (Icon::AlignBottom, "bottom", "Align bottom"),
            ] {
                if icon_button(ui, t, icon, app.tr(tip), n >= 2, false).clicked() {
                    app.run("arrange.align", json!({"edge": edge}));
                }
            }
        });
        ui.horizontal(|ui| {
            if icon_button(ui, t, Icon::DistributeX, app.tr("Distribute horizontally"), n >= 3, false).clicked() {
                app.run("arrange.distribute", json!({"axis": "x"}));
            }
            if icon_button(ui, t, Icon::DistributeY, app.tr("Distribute vertically"), n >= 3, false).clicked() {
                app.run("arrange.distribute", json!({"axis": "y"}));
            }
            if icon_button(ui, t, Icon::Grid, app.tr("Snap selection to grid"), n >= 1, false).clicked() {
                app.run("arrange.snap", json!({}));
            }
            if icon_button(ui, t, Icon::Duplicate, &app.tip("edit.duplicate"), n >= 1, false).clicked() {
                app.run("edit.duplicate", json!({}));
            }
            if icon_button(ui, t, Icon::ToFront, app.tr("Bring to front"), n >= 1, false).clicked() {
                app.run("node.setZ", json!({"z": "front"}));
            }
            if icon_button(ui, t, Icon::ToBack, app.tr("Send to back"), n >= 1, false).clicked() {
                app.run("node.setZ", json!({"z": "back"}));
            }
        });
        let mut snap = app.snap;
        if ui.checkbox(&mut snap, app.tr("Snap to grid while moving and resizing")).changed() {
            app.snap = snap;
        }
    }
    section(ui, t, app.tr("Arrange the whole map"));
    if big_choice(ui, t, Icon::Tree, app.tr("Tree"), app.tr("Branches from left to right")).on_hover_text(app.tip("layout.tree")).clicked() {
        app.run("layout.tree", json!({}));
        app.view.fit_pending = true;
    }
    ui.add_space(6.0);
    if big_choice(ui, t, Icon::Radial, app.tr("Radial"), app.tr("Ideas in a circle around the centre"))
        .on_hover_text(app.tip("layout.radial"))
        .clicked()
    {
        app.run("layout.radial", json!({}));
        app.view.fit_pending = true;
    }
    section(ui, t, app.tr("View"));
    if icon_text_button(ui, t, Icon::Fit, app.tr("Fit to window"), true).clicked() {
        app.view.fit_pending = true;
    }
    let has_sel = !app.session.selection().is_empty();
    if icon_text_button(ui, t, Icon::Collapse, app.tr("Collapse or expand branch"), has_sel).on_hover_text(app.tip("node.toggleCollapse")).clicked() {
        app.run("node.toggleCollapse", json!({}));
    }
}

/// One layer's row: visibility, lock, name (click to select its shapes), count, rename,
/// move-selection-here and remove.
struct LayerRow {
    name: String,
    visible: bool,
    locked: bool,
    nodes: usize,
}

fn layers_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    ui.label(RichText::new(app.tr("Layers hold shapes. Hide a layer to declutter, lock it to keep its shapes out of the way.")).color(t.muted));
    let map = app.session.map();
    let rows: Vec<LayerRow> = map
        .layers()
        .iter()
        .map(|l| LayerRow {
            name: l.name.clone(),
            visible: l.visible,
            locked: l.locked,
            nodes: map.nodes().filter(|n| n.layer.as_deref() == Some(l.name.as_str())).count(),
        })
        .collect();
    let unlayered = map.nodes().filter(|n| n.layer.is_none()).count();
    let has_sel = !app.session.selection().is_empty();
    let sel_layers: Vec<Option<String>> = app.session.selection().iter().filter_map(|id| map.node(*id)).map(|n| n.layer.clone()).collect();
    section(ui, t, app.tr("Layers"));
    // The default layer: everything without a layer.
    ui.horizontal(|ui| {
        ui.add_space(56.0);
        let on_it = has_sel && sel_layers.iter().all(Option::is_none);
        let name = RichText::new(app.tr("Default layer")).color(if on_it { t.accent } else { t.ui_text });
        if ui.add(egui::Label::new(name).sense(Sense::click())).on_hover_text(app.tr("Select its shapes")).clicked() {
            let ids: Vec<u64> = app.session.map().nodes().filter(|n| n.layer.is_none()).map(|n| n.id.0).collect();
            app.edge_selection = None;
            app.run("selection.set", json!({"ids": ids}));
        }
        ui.label(RichText::new(unlayered.to_string()).small().color(t.muted));
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if icon_button(ui, t, Icon::Elements, app.tr("Move the selection to this layer"), has_sel && !on_it, false).clicked() {
                app.run("node.setLayer", json!({"layer": null}));
            }
        });
    });
    for row in rows {
        ui.horizontal(|ui| {
            let (eye, eye_tip) = if row.visible { (Icon::Eye, "Hide layer") } else { (Icon::EyeOff, "Show layer") };
            if icon_button(ui, t, eye, app.tr(eye_tip), true, !row.visible).clicked() {
                app.run("layer.setVisible", json!({"name": row.name, "visible": !row.visible}));
            }
            let (lock, lock_tip) = if row.locked { (Icon::Lock, "Unlock layer") } else { (Icon::Unlock, "Lock layer") };
            if icon_button(ui, t, lock, app.tr(lock_tip), true, row.locked).clicked() {
                app.run("layer.setLocked", json!({"name": row.name, "locked": !row.locked}));
            }
            let renaming = app.rename_layer.as_ref().is_some_and(|(n, _)| *n == row.name);
            if renaming {
                let mut draft = app.rename_layer.as_ref().map(|(_, d)| d.clone()).unwrap_or_default();
                let te = ui.add(egui::TextEdit::singleline(&mut draft).desired_width(130.0));
                if te.changed() {
                    app.rename_layer = Some((row.name.clone(), draft.clone()));
                }
                if te.lost_focus() {
                    app.rename_layer = None;
                    if ui.input(|i| i.key_pressed(egui::Key::Enter)) && !draft.trim().is_empty() && draft.trim() != row.name {
                        app.run("layer.rename", json!({"name": row.name, "to": draft.trim()}));
                    }
                } else {
                    te.request_focus();
                }
            } else {
                let on_it = has_sel && sel_layers.iter().all(|l| l.as_deref() == Some(row.name.as_str()));
                let color = if on_it {
                    t.accent
                } else if row.visible {
                    t.ui_text
                } else {
                    t.muted
                };
                let name = RichText::new(&row.name).color(color);
                if ui.add(egui::Label::new(name).sense(Sense::click()).truncate()).on_hover_text(app.tr("Select its shapes")).clicked() {
                    let ids: Vec<u64> = app.session.map().nodes().filter(|n| n.layer.as_deref() == Some(row.name.as_str())).map(|n| n.id.0).collect();
                    app.edge_selection = None;
                    app.run("selection.set", json!({"ids": ids}));
                }
                ui.label(RichText::new(row.nodes.to_string()).small().color(t.muted));
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if icon_button(ui, t, Icon::Trash, app.tr("Remove layer (its shapes go to the default layer)"), true, false).clicked() {
                    app.run("layer.remove", json!({"name": row.name}));
                }
                if icon_button(ui, t, Icon::Edit, app.tr("Rename layer"), true, renaming).clicked() {
                    app.rename_layer = if renaming { None } else { Some((row.name.clone(), row.name.clone())) };
                }
                let on_it = sel_layers.iter().all(|l| l.as_deref() == Some(row.name.as_str()));
                if icon_button(ui, t, Icon::Elements, app.tr("Move the selection to this layer"), has_sel && !on_it, false).clicked() {
                    app.run("node.setLayer", json!({"layer": row.name}));
                }
            });
        });
    }
    section(ui, t, app.tr("New layer"));
    ui.horizontal(|ui| {
        let mut name = app.new_layer.clone();
        let te = ui.add(egui::TextEdit::singleline(&mut name).desired_width(200.0).hint_text(app.tr("Layer name")));
        if te.changed() {
            app.new_layer = name.clone();
        }
        let submit = te.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
        let add = icon_button(ui, t, Icon::Plus, app.tr("Add layer"), !name.trim().is_empty(), false).clicked();
        if (submit || add) && !name.trim().is_empty() {
            let params = if has_sel { json!({"layer": name.trim()}) } else { json!({"ids": [], "layer": name.trim()}) };
            let ok = if has_sel { app.run("node.setLayer", params).is_some() } else { app.run("layer.add", json!({"name": name.trim()})).is_some() };
            if ok {
                app.new_layer.clear();
            }
        }
    });
    ui.label(RichText::new(app.tr("With shapes selected, the new layer takes them.")).small().color(t.muted));
    section(ui, t, app.tr("Groups"));
    let n = app.session.selection().len();
    let grouped = app.session.selection().iter().any(|id| app.session.map().node(*id).is_some_and(|n| n.group.is_some()));
    ui.horizontal(|ui| {
        if icon_text_button(ui, t, Icon::Group, app.tr("Group"), n >= 2).on_hover_text(app.tip("group.create")).clicked() {
            app.run("group.create", json!({}));
        }
        if icon_text_button(ui, t, Icon::Ungroup, app.tr("Ungroup"), grouped).on_hover_text(app.tip("group.ungroup")).clicked() {
            app.run("group.ungroup", json!({}));
        }
    });
    ui.label(RichText::new(app.tr("Grouped shapes select and move together.")).small().color(t.muted));
}

fn severity_color(severity: &str) -> Color32 {
    match severity {
        "error" => Color32::from_rgb(0xe5, 0x48, 0x4d),
        "warning" => Color32::from_rgb(0xe3, 0xa0, 0x08),
        _ => Color32::from_rgb(0x4a, 0x8f, 0xe0),
    }
}

fn check_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    ui.label(
        RichText::new(app.tr("Checks the network shapes' properties: IP addresses and masks, gateways, VLANs and the interfaces on links."))
            .color(t.muted),
    );
    ui.add_space(8.0);
    if primary_button(ui, t, Some(Icon::Check), app.tr("Check network")).on_hover_text(app.tip("net.validate")).clicked() {
        app.check_network();
    }
    ui.add_space(4.0);
    ui.label(
        RichText::new(app.tr("Properties read: ip (10.0.1.5/24), mask, gateway, vlan; interfaces like Gi0/1=10.0.1.1/24 vlan 10; link labels like VLAN 10 or Gi0/1 - eth0."))
            .small()
            .color(t.muted),
    );
    let Some(report) = app.check.clone() else { return };
    section(ui, t, app.tr("Result"));
    if report.findings.is_empty() {
        ui.label(RichText::new(app.tr("No problems found")).color(severity_color("ok")));
        return;
    }
    let summary = app.language.fill(
        "{errors} errors · {warnings} warnings · {infos} notes",
        &[("errors", &report.errors.to_string()), ("warnings", &report.warnings.to_string()), ("infos", &report.infos.to_string())],
    );
    ui.label(RichText::new(summary).strong().color(t.ui_text));
    ui.add_space(4.0);
    for (i, finding) in report.findings.iter().enumerate() {
        let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(8).inner_margin(8);
        let inner = frame.show(ui, |ui| {
            ui.horizontal(|ui| {
                let (dot, _) = ui.allocate_exact_size(vec2(10.0, 10.0), Sense::hover());
                ui.painter().circle_filled(dot.center(), 4.0, severity_color(&finding.severity));
                ui.vertical(|ui| {
                    ui.set_width(ui.available_width());
                    ui.label(RichText::new(&finding.message).color(t.ui_text));
                    ui.label(RichText::new(app.tr(code_label(&finding.code))).small().color(t.muted));
                });
            });
        });
        let click = ui.interact(inner.response.rect, ui.id().with(("finding", i)), Sense::click());
        if click.hovered() {
            ui.painter().rect_stroke(inner.response.rect, 8.0, Stroke::new(1.0, t.accent), StrokeKind::Inside);
        }
        if click.on_hover_text(app.tr("Select the shapes involved")).clicked() {
            app.show_finding(finding);
        }
        ui.add_space(4.0);
    }
}

/// A finding code as a short interface label.
fn code_label(code: &str) -> &'static str {
    match code {
        "bad-address" => "Invalid address",
        "bad-mask" => "Invalid mask",
        "duplicate-address" => "Duplicate address",
        "network-address" => "Network address used as a host",
        "broadcast-address" => "Broadcast address used as a host",
        "subnet-mismatch" => "Subnet mismatch",
        "gateway-outside-subnet" => "Gateway outside the subnet",
        "bad-vlan" => "Invalid VLAN",
        "vlan-mismatch" => "VLAN mismatch",
        "unknown-interface" => "Unknown interface",
        "interface-reused" => "Interface used twice",
        "duplicate-interface" => "Duplicate interface",
        "no-address" => "No address",
        "isolated" => "Isolated device",
        _ => "Note",
    }
}

fn files_panel(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Map"));
    if icon_text_button(ui, t, Icon::Plus, app.tr("New"), true).clicked() {
        app.new_map();
    }
    if icon_text_button(ui, t, Icon::Upload, app.tr("Open or import…"), true).clicked() {
        app.requests.push(Request::Open);
    }
    if icon_text_button(ui, t, Icon::Save, app.tr("Save"), true).clicked() {
        app.save_or_ask();
    }
    if icon_text_button(ui, t, Icon::Doc, app.tr("Save as…"), true).clicked() {
        app.requests.push(Request::SaveAs);
    }
    section(ui, t, app.tr("Export as"));
    for f in ExportFormat::ALL {
        if icon_text_button(ui, t, Icon::Download, app.tr(f.label()), true).clicked() {
            app.requests.push(Request::Export(f));
        }
    }
    section(ui, t, app.tr("Opens and imports"));
    for f in ideacraft_engine::formats::Format::ALL {
        let exts: Vec<String> = f.extensions().iter().map(|e| format!(".{e}")).collect();
        ui.label(RichText::new(format!("{} · {}", app.tr(f.name()), exts.join(" "))).small().color(t.muted));
    }
}
