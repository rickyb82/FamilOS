//! The compose window: a card docked at the bottom right, like Gmail's.

use egui::{Align, FontId, Layout, RichText, Sense, Stroke, StrokeKind, vec2};

use serde_json::json;

use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{icon_button, primary_button};
use crate::{MailApp, Request, format};

/// Files picked after "Insert image" become inline images referenced from the body.
fn adopt_inline_images(c: &mut crate::Compose) {
    let Some(from) = c.inline_from else { return };
    if c.attachments.len() <= from {
        return;
    }
    let mut n = c.attachments.iter().filter(|a| !a.cid.is_empty()).count();
    for a in c.attachments.iter_mut().skip(from) {
        let ext = a.name.rsplit('.').next().unwrap_or_default().to_ascii_lowercase();
        let image_mime = match ext.as_str() {
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "gif" => "image/gif",
            "webp" => "image/webp",
            "svg" => "image/svg+xml",
            _ if a.mime.starts_with("image/") => a.mime.as_str(),
            _ => continue,
        };
        n += 1;
        a.mime = image_mime.to_string();
        a.cid = format!("image{n}@mailcraft");
        if !c.body.ends_with('\n') && !c.body.is_empty() {
            c.body.push('\n');
        }
        c.body.push_str(&format!("![{}](cid:{})\n", a.name, a.cid));
    }
    c.inline_from = None;
}

/// The text colours offered by the colour picker (original palette).
const COLORS: [(&str, &str); 12] = [
    ("#000000", "Black"),
    ("#5f6368", "Gray"),
    ("#c5221f", "Red"),
    ("#e37400", "Orange"),
    ("#f9ab00", "Yellow"),
    ("#188038", "Green"),
    ("#007b83", "Teal"),
    ("#1a73e8", "Blue"),
    ("#8430ce", "Purple"),
    ("#b31412", "Dark red"),
    ("#0b5394", "Dark blue"),
    ("#ffffff", "White"),
];

/// A few emoji to drop into the text.
const EMOJI: [&str; 24] =
    ["😀", "😊", "😂", "😉", "😍", "🤔", "😎", "😢", "👍", "👎", "👋", "🙏", "👏", "💪", "🎉", "❤️", "🔥", "✅", "⭐", "📎", "📅", "☕", "🚀", "🙂"];

/// Gmail's formatting row (shown by "Aa"): font, size, bold/italic/underline, text colour,
/// alignment, lists, indent, undo/redo. Each button marks the selection up.
fn format_row(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, c: &mut crate::Compose, body_id: egui::Id) {
    let ctx = ui.ctx().clone();
    let sel = c.selection.or_else(|| format::selection(&ctx, body_id));
    let mut caret: Option<usize> = None;
    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing.x = 2.0;
        // Font family and size: a value applies to the selection as a styled span.
        let fonts = [("sans", "Sans Serif"), ("serif", "Serif"), ("mono", "Monospace")];
        let mut font: Option<&str> = None;
        egui::ComboBox::from_id_salt("compose-font").selected_text(RichText::new("Sans Serif").size(12.5)).width(96.0).show_ui(ui, |ui| {
            for (key, name) in fonts {
                if ui.selectable_label(false, name).clicked() {
                    font = Some(key);
                }
            }
        });
        if let Some(f) = font {
            caret = Some(format::span(&mut c.body, sel, "font", f));
        }
        let sizes = [("small", "Small"), ("normal", "Normal"), ("large", "Large"), ("huge", "Huge")];
        let mut size: Option<&str> = None;
        egui::ComboBox::from_id_salt("compose-size").selected_text(RichText::new("Tt").size(12.5)).width(52.0).show_ui(ui, |ui| {
            for (key, name) in sizes {
                if ui.selectable_label(false, app.tr(name)).clicked() {
                    size = Some(key);
                }
            }
        });
        if let Some(s) = size {
            caret = Some(format::span(&mut c.body, sel, "size", s));
        }
        if icon_button(ui, t, Icon::Bold, app.tr("Bold"), true, false).clicked() {
            caret = Some(format::wrap(&mut c.body, sel, "**", "**"));
        }
        if icon_button(ui, t, Icon::Italic, app.tr("Italic"), true, false).clicked() {
            caret = Some(format::wrap(&mut c.body, sel, "*", "*"));
        }
        if icon_button(ui, t, Icon::Underline, app.tr("Underline"), true, false).clicked() {
            caret = Some(format::wrap(&mut c.body, sel, "__", "__"));
        }
        // Text colour: a palette popup.
        let color_btn = icon_button(ui, t, Icon::TextColor, app.tr("Text color"), true, false);
        let mut color: Option<&str> = None;
        egui::Popup::menu(&color_btn).show(|ui| {
            ui.set_min_width(170.0);
            egui::Grid::new("compose-color-grid").spacing(vec2(4.0, 4.0)).show(ui, |ui| {
                for (i, (hex, name)) in COLORS.iter().enumerate() {
                    let (rect, resp) = ui.allocate_exact_size(vec2(22.0, 22.0), Sense::click());
                    let rgb = egui::Color32::from_hex(hex).unwrap_or(t.ui_text);
                    ui.painter().rect_filled(rect, 4.0, rgb);
                    ui.painter().rect_stroke(rect, 4.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
                    if resp.on_hover_text(app.tr(name)).clicked() {
                        color = Some(hex);
                        ui.close();
                    }
                    if i % 6 == 5 {
                        ui.end_row();
                    }
                }
            });
        });
        if let Some(hex) = color {
            caret = Some(format::span(&mut c.body, sel, "color", hex));
        }
        // Alignment: a popup with the three options.
        let align_btn = icon_button(ui, t, Icon::AlignLeft, app.tr("Align"), true, false);
        let mut align: Option<&str> = None;
        egui::Popup::menu(&align_btn).show(|ui| {
            ui.horizontal(|ui| {
                for (icon, tip, mark) in [
                    (Icon::AlignLeft, "Align left", "{align=left}"),
                    (Icon::AlignCenter, "Align center", "{align=center}"),
                    (Icon::AlignRight, "Align right", "{align=right}"),
                ] {
                    if icon_button(ui, t, icon, app.tr(tip), true, false).clicked() {
                        align = Some(mark);
                        ui.close();
                    }
                }
            });
        });
        if let Some(m) = align {
            caret = Some(format::line_mark(&mut c.body, sel, m));
        }
        if icon_button(ui, t, Icon::ListNumber, app.tr("Numbered list"), true, false).clicked() {
            caret = Some(format::list(&mut c.body, sel, true));
        }
        if icon_button(ui, t, Icon::ListBullet, app.tr("Bulleted list"), true, false).clicked() {
            caret = Some(format::list(&mut c.body, sel, false));
        }
        if icon_button(ui, t, Icon::Indent, app.tr("Indent"), true, false).clicked() {
            caret = Some(format::line_mark(&mut c.body, sel, "{indent}"));
        }
        if icon_button(ui, t, Icon::Undo, app.tr("Undo"), true, false).clicked() && format::undo(&ctx, body_id, &mut c.body, false) {
            caret = format::selection(&ctx, body_id).map(|(a, _)| a);
        }
        if icon_button(ui, t, Icon::Redo, app.tr("Redo"), true, false).clicked() && format::undo(&ctx, body_id, &mut c.body, true) {
            caret = format::selection(&ctx, body_id).map(|(a, _)| a);
        }
    });
    if let Some(i) = caret {
        c.refocus = Some(i);
    }
}

/// Inserts `s` at the caret (or at the end) and puts the caret after it.
fn insert_at_caret(ctx: &egui::Context, body_id: egui::Id, c: &mut crate::Compose, s: &str) {
    let sel = c.selection.or_else(|| format::selection(ctx, body_id));
    let body = &mut c.body;
    let caret = format::wrap(body, sel.map(|(a, _)| (a, a)), s, "");
    c.refocus = Some(caret);
}

/// What the bottom row asked for.
#[derive(Default)]
struct Actions {
    send: bool,
    save: bool,
    discard: bool,
}

/// Gmail's bottom row: Send (with a menu), "Aa", attach, link, emoji, image, signature, print,
/// more, and the bin at the right.
fn action_row(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, c: &mut crate::Compose, body_id: egui::Id) -> Actions {
    let mut out = Actions::default();
    let ctx = ui.ctx().clone();
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 2.0;
        if primary_button(ui, t, Some(Icon::Send), app.tr("Send")).on_hover_text(app.tip("mail.send")).clicked() {
            out.send = true;
        }
        let more_send = icon_button(ui, t, Icon::Chevron, app.tr("More send options"), true, false);
        egui::Popup::menu(&more_send).align(egui::RectAlign::TOP_START).show(|ui| {
            ui.set_min_width(160.0);
            if ui.button(app.tr("Send")).clicked() {
                out.send = true;
                ui.close();
            }
            if ui.button(app.tip("draft.save")).clicked() {
                out.save = true;
                ui.close();
            }
        });
        ui.add_space(6.0);
        if icon_button(ui, t, Icon::TextFormat, app.tr("Formatting options"), true, !c.hide_format).clicked() {
            c.hide_format = !c.hide_format;
        }
        if icon_button(ui, t, Icon::Attachment, app.tr("Attach files"), true, false).clicked() {
            app.requests.push(Request::AttachFiles);
        }
        if icon_button(ui, t, Icon::Link, app.tr("Insert link"), true, false).clicked() {
            let sel = c.selection.or_else(|| format::selection(&ctx, body_id));
            c.refocus = Some(format::link(&mut c.body, sel));
        }
        let emoji_btn = icon_button(ui, t, Icon::Emoji, app.tr("Insert emoji"), true, false);
        let mut emoji: Option<&str> = None;
        egui::Popup::menu(&emoji_btn).align(egui::RectAlign::TOP_START).show(|ui| {
            ui.set_min_width(220.0);
            egui::Grid::new("compose-emoji-grid").spacing(vec2(2.0, 2.0)).show(ui, |ui| {
                for (i, e) in EMOJI.iter().enumerate() {
                    if ui.add(egui::Button::new(RichText::new(*e).size(16.0)).frame(false)).clicked() {
                        emoji = Some(e);
                        ui.close();
                    }
                    if i % 8 == 7 {
                        ui.end_row();
                    }
                }
            });
        });
        if let Some(e) = emoji {
            insert_at_caret(&ctx, body_id, c, e);
        }
        if icon_button(ui, t, Icon::Image, app.tr("Insert image"), true, false).clicked() {
            c.inline_from = Some(c.attachments.len());
            app.requests.push(Request::AttachFiles);
        }
        if icon_button(ui, t, Icon::Signature, app.tr("Insert signature"), true, false).clicked() {
            let signature = app
                .session
                .account(&c.from_account)
                .or_else(|| app.session.account(&app.session.view().account))
                .map(|a| a.signature_text())
                .unwrap_or_default();
            if signature.is_empty() {
                app.status = app.tr("No signature set for this account: add one in Settings").to_string();
            } else {
                insert_at_caret(&ctx, body_id, c, &format!("\n\n{signature}\n"));
            }
        }
        if icon_button(ui, t, Icon::Print, app.tr("Print"), true, false).clicked() {
            app.compose = Some(c.clone());
            app.print_compose();
        }
        let more_btn = icon_button(ui, t, Icon::More, app.tr("More options"), true, false);
        egui::Popup::menu(&more_btn).align(egui::RectAlign::TOP_START).show(|ui| {
            ui.set_min_width(160.0);
            if ui.button(app.tip("draft.save")).clicked() {
                out.save = true;
                ui.close();
            }
            if ui.button(app.tr("Print")).clicked() {
                app.compose = Some(c.clone());
                app.print_compose();
                ui.close();
            }
            if ui.button(app.tr("Discard")).clicked() {
                out.discard = true;
                ui.close();
            }
        });
        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
            if icon_button(ui, t, Icon::Trash, app.tr("Discard"), true, false).clicked() {
                out.discard = true;
            }
        });
    });
    out
}

/// A recipients field with completion: while a name or address is typed after the last comma,
/// the people seen in mail are offered below it (click, or Enter for the first).
fn address_field(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, label: &str, value: &mut String, hint: &str, extra: impl FnOnce(&mut egui::Ui)) {
    let resp = ui
        .horizontal(|ui| {
            ui.add_sized(vec2(44.0, 24.0), egui::Label::new(RichText::new(label).color(t.muted)));
            let w = ui.available_width() - 80.0;
            let r = ui.add(egui::TextEdit::singleline(value).hint_text(hint).desired_width(w).frame(egui::Frame::NONE));
            extra(ui);
            r
        })
        .inner;
    ui.painter().hline(ui.min_rect().x_range(), ui.cursor().top(), Stroke::new(1.0, t.border));
    let token = value.rsplit(',').next().unwrap_or_default().trim().to_string();
    if resp.has_focus() && token.chars().count() >= 2 && app.contact_query != token {
        app.contact_query = token.clone();
        app.contact_hits.clear();
        app.run("contacts.search", json!({"query": token, "limit": 6}));
    }
    if token.is_empty() || app.contact_query != token || app.contact_hits.is_empty() {
        return;
    }
    if ui.input(|i| i.key_pressed(egui::Key::Escape)) {
        app.contact_hits.clear();
        return;
    }
    let lower = value.to_ascii_lowercase();
    let hits: Vec<(String, String)> = app.contact_hits.iter().filter(|(_, email)| !lower.contains(&email.to_ascii_lowercase())).cloned().collect();
    let mut chosen: Option<String> = None;
    if resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
        chosen = hits.first().map(|h| h.0.clone());
    }
    egui::Frame::NONE.fill(t.panel).stroke(Stroke::new(1.0, t.border)).corner_radius(8).inner_margin(4).show(ui, |ui| {
        ui.set_width(ui.available_width());
        for (display, _) in &hits {
            if ui.selectable_label(false, display).clicked() {
                chosen = Some(display.clone());
            }
        }
    });
    if let Some(display) = chosen {
        MailApp::complete_recipient(value, &display);
        app.contact_hits.clear();
        app.contact_query.clear();
        resp.request_focus();
    }
}

pub fn show(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let Some(mut c) = app.compose.clone() else { return };
    adopt_inline_images(&mut c);
    let ctx = ui.ctx().clone();
    let title = if c.reply_to.is_some() {
        format!("{}: {}", app.tr("Reply"), c.subject)
    } else if c.forward_of.is_some() {
        format!("{}: {}", app.tr("Forward"), c.subject)
    } else if c.subject.trim().is_empty() {
        app.tr("New message").to_string()
    } else {
        c.subject.clone()
    };
    let width = 600.0_f32.min(ctx.content_rect().width() - 40.0);
    let mut close = false;
    let mut send = false;
    let mut save = false;
    let mut discard = false;
    // Docked at the bottom right, dragged by its title bar anywhere on the screen.
    let screen = ctx.content_rect();
    let min_x = -(screen.width() - width - 48.0).max(0.0);
    let min_y = -(screen.height() - 120.0).max(0.0);
    c.offset = vec2(c.offset.x.clamp(min_x, 0.0), c.offset.y.clamp(min_y, 0.0));
    let home = vec2(-24.0, -34.0) + c.offset;
    egui::Area::new(egui::Id::new("compose")).anchor(egui::Align2::RIGHT_BOTTOM, home).order(egui::Order::Middle).fade_in(false).show(&ctx, |ui| {
        egui::Frame::NONE
            .fill(t.card)
            .stroke(Stroke::new(1.0, t.border))
            .corner_radius(12)
            .shadow(egui::epaint::Shadow { offset: [0, 4], blur: 18, spread: 0, color: egui::Color32::from_black_alpha(50) })
            .show(ui, |ui| {
                ui.set_width(width);
                // Title bar.
                let (bar, resp) = ui.allocate_exact_size(vec2(width, 40.0), Sense::click_and_drag());
                if resp.dragged() {
                    c.offset += resp.drag_delta();
                }
                if resp.hovered() {
                    ui.ctx().set_cursor_icon(egui::CursorIcon::Grab);
                }
                ui.painter().rect_filled(
                    bar,
                    egui::CornerRadius { nw: 12, ne: 12, sw: 0, se: 0 },
                    if t.dark { t.chrome } else { egui::Color32::from_rgb(0x40, 0x42, 0x4a) },
                );
                ui.painter().text(
                    bar.left_center() + vec2(14.0, 0.0),
                    egui::Align2::LEFT_CENTER,
                    &title,
                    FontId::proportional(14.0),
                    egui::Color32::WHITE,
                );
                let mut child =
                    ui.new_child(egui::UiBuilder::new().max_rect(bar.shrink2(vec2(6.0, 4.0))).layout(Layout::right_to_left(Align::Center)));
                let light = Tokens { ui_text: egui::Color32::WHITE, hover: egui::Color32::from_white_alpha(30), ..*t };
                if icon_button(&mut child, &light, Icon::Close, app.tr("Close"), true, false).clicked() {
                    close = true;
                }
                if icon_button(&mut child, &light, Icon::Chevron, if c.minimized { "▲" } else { "▼" }, true, false).clicked() {
                    c.minimized = !c.minimized;
                }
                if resp.double_clicked() {
                    c.minimized = !c.minimized;
                }
                if c.minimized {
                    return;
                }
                egui::Frame::NONE.inner_margin(egui::Margin::symmetric(14, 8)).show(ui, |ui| {
                    ui.set_width(width - 28.0);
                    // From: the sending account, when there is more than one.
                    let accounts: Vec<(String, String, String)> =
                        app.session.config().accounts.iter().map(|a| (a.id.clone(), a.name.clone(), a.email.clone())).collect();
                    if accounts.len() > 1 {
                        if c.from_account.is_empty() || !accounts.iter().any(|(id, ..)| *id == c.from_account) {
                            c.from_account = app.acting_account();
                        }
                        ui.horizontal(|ui| {
                            ui.add_sized(vec2(44.0, 24.0), egui::Label::new(RichText::new(app.tr("From")).color(t.muted)));
                            let current = accounts
                                .iter()
                                .find(|(id, ..)| *id == c.from_account)
                                .map(|(_, name, email)| format!("{name} <{email}>"))
                                .unwrap_or_default();
                            egui::ComboBox::from_id_salt("compose-from").selected_text(current).width(ui.available_width() - 70.0).show_ui(
                                ui,
                                |ui| {
                                    for (id, name, email) in &accounts {
                                        ui.selectable_value(&mut c.from_account, id.clone(), format!("{name} <{email}>"));
                                    }
                                },
                            );
                        });
                        ui.painter().hline(ui.min_rect().x_range(), ui.cursor().top(), Stroke::new(1.0, t.border));
                    }
                    let field = |ui: &mut egui::Ui, label: &str, value: &mut String, hint: &str| {
                        ui.horizontal(|ui| {
                            ui.add_sized(vec2(44.0, 24.0), egui::Label::new(RichText::new(label).color(t.muted)));
                            let w = ui.available_width() - 70.0;
                            ui.add(egui::TextEdit::singleline(value).hint_text(hint).desired_width(w).frame(egui::Frame::NONE));
                        });
                        ui.painter().hline(ui.min_rect().x_range(), ui.cursor().top(), Stroke::new(1.0, t.border));
                    };
                    let (to_l, cc_l, bcc_l, hint) =
                        (app.tr("To").to_string(), app.tr("Cc").to_string(), app.tr("Bcc").to_string(), app.tr("Recipients").to_string());
                    let mut show_cc = c.show_cc;
                    address_field(app, ui, t, &to_l, &mut c.to, &hint, |ui| {
                        if !show_cc && ui.small_button("Cc/Bcc").clicked() {
                            show_cc = true;
                        }
                    });
                    c.show_cc = show_cc;
                    if c.show_cc {
                        address_field(app, ui, t, &cc_l, &mut c.cc, "", |_| {});
                        address_field(app, ui, t, &bcc_l, &mut c.bcc, "", |_| {});
                    }
                    field(ui, "", &mut c.subject, app.tr("Subject"));
                    ui.add_space(4.0);
                    let body_id = egui::Id::new("compose-body");
                    if !c.hide_format {
                        format_row(app, ui, t, &mut c, body_id);
                    }
                    if let Some(i) = c.refocus {
                        // A click frame would take the focus straight back: wait for a quiet one.
                        if ui.input(|inp| inp.pointer.any_click() || inp.pointer.any_pressed() || inp.pointer.any_down()) {
                            ui.ctx().request_repaint();
                        } else {
                            format::set_caret(ui.ctx(), body_id, i);
                            c.refocus = None;
                        }
                    }
                    // The text is shown as it will look: marks hidden, styles drawn.
                    let palette = crate::richtext::Palette { text: t.ui_text, muted: t.muted, link: t.link };
                    let mut layouter = |ui: &egui::Ui, buf: &dyn egui::TextBuffer, wrap_width: f32| {
                        let job = crate::richtext::layout(ui, buf.as_str(), wrap_width, 14.0, palette, false);
                        ui.ctx().fonts_mut(|f| f.layout_job(job))
                    };
                    let out = egui::TextEdit::multiline(&mut c.body)
                        .id(body_id)
                        .desired_rows(14)
                        .desired_width(width - 28.0)
                        .frame(egui::Frame::NONE)
                        .font(FontId::proportional(14.0))
                        .layouter(&mut layouter)
                        .show(ui);
                    let body = out.response.response;
                    if ui.memory(|m| m.has_focus(body_id)) {
                        c.selection = out.cursor_range.map(|r| {
                            let (a, b) = (r.primary.index.0, r.secondary.index.0);
                            (a.min(b), a.max(b))
                        });
                    }
                    if c.body.contains("**") {
                        // Faux bold: the bold runs painted once more, half a point to the right.
                        let wrap = out.galley.job.wrap.max_width;
                        let job = crate::richtext::layout(ui, &c.body, wrap, 14.0, palette, true);
                        let bold = ui.ctx().fonts_mut(|f| f.layout_job(job));
                        ui.painter().with_clip_rect(out.text_clip_rect).galley(out.galley_pos + vec2(0.6, 0.0), bold, egui::Color32::TRANSPARENT);
                    }
                    if c.focus_body {
                        body.request_focus();
                        if let Some(mut state) = egui::TextEdit::load_state(ui.ctx(), body.id) {
                            state.cursor.set_char_range(Some(egui::text::CCursorRange::one(egui::text::CCursor::new(0))));
                            state.store(ui.ctx(), body.id);
                        }
                        c.focus_body = false;
                    }
                    if app.assist_available() {
                        // "Write with the assistant": what to say, in plain words.
                        let hint_say = app.tr("What do you want to say?").to_string();
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("✨").size(14.0));
                            let w = ui.available_width() - 200.0;
                            ui.add(egui::TextEdit::singleline(&mut app.assist.instruction).hint_text(hint_say.clone()).desired_width(w));
                            let ready = !app.assist.instruction.trim().is_empty() && !app.assist.busy;
                            if ui.add_enabled(ready, egui::Button::new(app.tr("Write with the assistant"))).clicked() {
                                app.compose = Some(c.clone());
                                app.assist_draft();
                            }
                            if app.assist.busy {
                                ui.add(egui::Spinner::new().size(14.0).color(t.accent));
                            }
                        });
                    }
                    if !c.forward_attachments.is_empty() {
                        ui.label(
                            RichText::new(
                                app.language.fill("Original attachments included: {names}", &[("names", &c.forward_attachments.join(", "))]),
                            )
                            .small()
                            .color(t.muted),
                        );
                    }
                    if !c.attachments.is_empty() {
                        ui.horizontal_wrapped(|ui| {
                            let mut remove = None;
                            for (i, a) in c.attachments.iter().enumerate() {
                                let label =
                                    format!("{}{} · {}", if a.cid.is_empty() { "" } else { "🖼 " }, a.name, crate::reader::human_size(a.data.len()));
                                let (rect, resp) = ui.allocate_exact_size(
                                    vec2(ui.painter().layout_no_wrap(label.clone(), FontId::proportional(12.5), t.ui_text).size().x + 44.0, 28.0),
                                    Sense::click(),
                                );
                                ui.painter().rect_filled(rect, 8.0, t.panel);
                                ui.painter().rect_stroke(rect, 8.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
                                icons::paint(
                                    ui.painter(),
                                    egui::Rect::from_center_size(rect.left_center() + vec2(16.0, 0.0), vec2(14.0, 14.0)),
                                    Icon::Attachment,
                                    t.muted,
                                );
                                ui.painter().text(
                                    rect.left_center() + vec2(28.0, 0.0),
                                    egui::Align2::LEFT_CENTER,
                                    label,
                                    FontId::proportional(12.5),
                                    t.ui_text,
                                );
                                icons::paint(
                                    ui.painter(),
                                    egui::Rect::from_center_size(rect.right_center() - vec2(12.0, 0.0), vec2(12.0, 12.0)),
                                    Icon::Close,
                                    t.muted,
                                );
                                if resp.on_hover_text(app.tr("Discard")).clicked() {
                                    remove = Some(i);
                                }
                            }
                            if let Some(i) = remove {
                                c.attachments.remove(i);
                            }
                        });
                    }
                    ui.add_space(6.0);
                    let actions = action_row(app, ui, t, &mut c, body_id);
                    send = actions.send;
                    save = actions.save;
                    discard = actions.discard;
                });
            });
    });
    app.compose = Some(c);
    if send {
        app.send_compose();
    } else if save {
        app.save_draft();
    } else if discard {
        app.discard_compose();
    } else if close {
        // Closing keeps the text as a draft when there is something to keep.
        if app.compose.as_ref().is_some_and(|c| !c.body.trim().is_empty() || !c.subject.trim().is_empty() || !c.to.trim().is_empty()) {
            app.save_draft();
        }
        app.compose = None;
    }
}
