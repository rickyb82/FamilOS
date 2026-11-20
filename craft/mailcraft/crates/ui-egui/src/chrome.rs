//! The top bar (logo, search, actions, account), the status bar and toasts.

use egui::{Align, Color32, FontId, Layout, Rect, RichText, Sense, Stroke, StrokeKind, pos2, vec2};
use serde_json::json;

use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::icon_button;
use crate::{Language, MailApp, Screen};

/// `HH:MM` now, in the shown time zone (an offset from UTC in minutes).
pub fn clock(offset_minutes: i32) -> String {
    use mailcraft_engine::core::message::time;
    let (.., h, m, _, _) = time::civil(time::shifted(time::now(), offset_minutes));
    format!("{h:02}:{m:02}")
}

/// A coloured circle with an initial, for accounts and senders.
pub fn avatar(ui: &mut egui::Ui, t: &Tokens, name: &str, color: Color32, size: f32) -> egui::Response {
    let (rect, resp) = ui.allocate_exact_size(vec2(size, size), Sense::click());
    let p = ui.painter();
    p.circle_filled(rect.center(), size / 2.0, color);
    let initial: String = name.chars().find(|c| c.is_alphanumeric()).map(|c| c.to_uppercase().to_string()).unwrap_or_else(|| "?".into());
    p.text(rect.center(), egui::Align2::CENTER_CENTER, initial, FontId::proportional(size * 0.5), t.on_accent);
    resp
}

/// The avatar of "every account": a stacked-inbox mark in the accent colour.
pub fn unified_avatar(ui: &mut egui::Ui, t: &Tokens, size: f32) -> egui::Response {
    let (rect, resp) = ui.allocate_exact_size(vec2(size, size), Sense::click());
    let p = ui.painter();
    p.circle_filled(rect.center(), size / 2.0, t.accent);
    icons::paint(p, rect.shrink(size * 0.24), Icon::Inbox, t.on_accent);
    resp
}

/// A stable colour for a name (senders) or the account's own colour.
pub fn color_for(name: &str, custom: &str) -> Color32 {
    if let Some(c) = parse_hex(custom) {
        return c;
    }
    const PALETTE: [Color32; 8] = [
        Color32::from_rgb(0x4a, 0x6c, 0xf7),
        Color32::from_rgb(0xe0, 0x6a, 0x2c),
        Color32::from_rgb(0x22, 0xa0, 0x6b),
        Color32::from_rgb(0xb0, 0x4a, 0xd6),
        Color32::from_rgb(0xd6, 0x3a, 0x5e),
        Color32::from_rgb(0x1f, 0x9a, 0xb8),
        Color32::from_rgb(0xc9, 0x8a, 0x00),
        Color32::from_rgb(0x5a, 0x6e, 0x8c),
    ];
    let h = name.bytes().fold(7u32, |h, b| h.wrapping_mul(31).wrapping_add(u32::from(b)));
    PALETTE[(h % 8) as usize]
}

pub fn parse_hex(s: &str) -> Option<Color32> {
    let h = s.trim().trim_start_matches('#');
    if h.len() != 6 {
        return None;
    }
    let v = u32::from_str_radix(h, 16).ok()?;
    Some(Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8))
}

pub fn top_bar(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let frame = egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(14, 8));
    egui::Panel::top("top").frame(frame).show(ui, |ui| {
        ui.horizontal(|ui| {
            let (rect, resp) = ui.allocate_exact_size(vec2(30.0, 30.0), Sense::click());
            icons::paint(ui.painter(), rect.shrink(2.0), Icon::Logo, t.accent);
            if resp.on_hover_text("MailCraft").clicked() {
                app.screen = Screen::Mail;
            }
            ui.label(RichText::new("MailCraft").size(18.0).strong().color(t.ui_text));
            ui.add_space(18.0);
            // Search field.
            let width = (ui.available_width() - 420.0).clamp(240.0, 720.0);
            let (rect, _) = ui.allocate_exact_size(vec2(width, 36.0), Sense::hover());
            ui.painter().rect_filled(rect, 18.0, t.panel);
            ui.painter().rect_stroke(rect, 18.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
            icons::paint(ui.painter(), Rect::from_center_size(pos2(rect.left() + 20.0, rect.center().y), vec2(18.0, 18.0)), Icon::Search, t.muted);
            let field = Rect::from_min_max(pos2(rect.left() + 38.0, rect.top() + 4.0), pos2(rect.right() - 34.0, rect.bottom() - 4.0));
            let hint = app.tr("Search mail").to_string();
            let te = ui.put(
                field,
                egui::TextEdit::singleline(&mut app.search)
                    .hint_text(hint)
                    .frame(egui::Frame::NONE)
                    .desired_width(field.width())
                    .font(FontId::proportional(14.0)),
            );
            if app.search_focus {
                te.request_focus();
                app.search_focus = false;
            }
            if te.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
                let q = app.search.clone();
                app.reading_full = false;
                app.run("mail.search", json!({"query": q}));
            }
            if !app.search.is_empty() {
                let close = Rect::from_center_size(pos2(rect.right() - 18.0, rect.center().y), vec2(24.0, 24.0));
                let r = ui.interact(close, ui.id().with("clear-search"), Sense::click());
                icons::paint(ui.painter(), close.shrink(4.0), Icon::Close, t.muted);
                if r.clicked() {
                    app.search.clear();
                    app.run("mail.search", json!({"query": ""}));
                }
            }
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                // Account chip: click for the Gmail-style account switcher.
                let account = app.session.view().account.clone();
                if app.unified() {
                    let chip = unified_avatar(ui, t, 30.0).on_hover_text(app.tr("All accounts"));
                    account_menu(app, &chip, t);
                    ui.label(RichText::new(app.tr("All accounts")).color(t.muted));
                    ui.add_space(6.0);
                } else if let Some(a) = app.session.account(&account).cloned() {
                    let color = color_for(&a.email, &a.color);
                    let chip = avatar(ui, t, &a.name, color, 30.0).on_hover_text(format!("{} <{}>", a.name, a.email));
                    account_menu(app, &chip, t);
                    ui.label(RichText::new(&a.name).color(t.muted));
                    ui.add_space(6.0);
                }
                if icon_button(ui, t, Icon::Settings, app.tr("Settings"), true, app.screen == Screen::Settings).clicked() {
                    app.screen = if app.screen == Screen::Settings { Screen::Mail } else { Screen::Settings };
                }
                let (icon, tip) = if app.dark { (Icon::Sun, "Light theme") } else { (Icon::Moon, "Dark theme") };
                if icon_button(ui, t, icon, app.tr(tip), true, false).clicked() {
                    app.dark = !app.dark;
                    let theme = if app.dark { "dark" } else { "light" };
                    app.run("prefs.set", json!({"theme": theme}));
                }
                let lang = icon_button(ui, t, Icon::Globe, &format!("{}: {}", app.tr("Language"), app.language.name()), true, false);
                egui::Popup::menu(&lang).show(|ui| {
                    for l in Language::ALL {
                        if ui.selectable_label(app.language == l, l.name()).clicked() {
                            app.language = l;
                            app.run("prefs.set", json!({"language": l.code()}));
                            ui.close();
                        }
                    }
                });
                let busy = app.session.jobs_pending() > 0;
                if busy {
                    ui.add(egui::Spinner::new().size(18.0).color(t.accent));
                } else if icon_button(ui, t, Icon::Sync, &app.tip("mail.sync"), true, false).clicked() {
                    app.sync(true);
                }
            });
        });
    });
}

/// The account switcher under the avatar: "All accounts" (the unified inbox), every account
/// with its unread count, then "Add account" and "Settings".
fn account_menu(app: &mut MailApp, chip: &egui::Response, t: &Tokens) {
    let current = app.session.view().account.clone();
    let unified = app.unified();
    let total = app.total_unread();
    let accounts: Vec<(String, String, String, String, u32)> = app
        .session
        .config()
        .accounts
        .iter()
        .map(|a| {
            (
                a.id.clone(),
                a.name.clone(),
                a.email.clone(),
                a.color.clone(),
                app.session.state(&a.id).and_then(|s| s.by_role(mailcraft_engine::core::mailbox::Role::Inbox)).map_or(0, |m| m.unread),
            )
        })
        .collect();
    egui::Popup::menu(chip).show(|ui| {
        ui.set_min_width(260.0);
        if accounts.len() > 1 {
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 44.0), Sense::click());
            if unified {
                ui.painter().rect_filled(rect, 8.0, t.active);
            } else if resp.hovered() {
                ui.painter().rect_filled(rect, 8.0, t.hover);
            }
            let mut child = ui.new_child(egui::UiBuilder::new().max_rect(rect.shrink2(vec2(8.0, 4.0))).layout(Layout::left_to_right(Align::Center)));
            unified_avatar(&mut child, t, 30.0);
            child.vertical(|ui| {
                ui.spacing_mut().item_spacing.y = 0.0;
                ui.label(RichText::new(app.tr("All accounts")).color(if unified { t.accent } else { t.ui_text }));
                ui.label(RichText::new(app.language.fill("{n} accounts", &[("n", &accounts.len().to_string())])).small().color(t.muted));
            });
            if total > 0 {
                ui.painter().text(
                    rect.right_center() - vec2(10.0, 0.0),
                    egui::Align2::RIGHT_CENTER,
                    total.to_string(),
                    FontId::proportional(12.0),
                    t.muted,
                );
            }
            if resp.clicked() {
                app.select_account(mailcraft_engine::ALL);
                ui.close();
            }
            ui.separator();
        }
        for (id, name, email, color, unread) in accounts {
            let active = !unified && id == current;
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 44.0), Sense::click());
            if active {
                ui.painter().rect_filled(rect, 8.0, t.active);
            } else if resp.hovered() {
                ui.painter().rect_filled(rect, 8.0, t.hover);
            }
            let mut child = ui.new_child(egui::UiBuilder::new().max_rect(rect.shrink2(vec2(8.0, 4.0))).layout(Layout::left_to_right(Align::Center)));
            avatar(&mut child, t, &name, color_for(&email, &color), 30.0);
            child.vertical(|ui| {
                ui.spacing_mut().item_spacing.y = 0.0;
                ui.label(RichText::new(&name).color(if active { t.accent } else { t.ui_text }));
                ui.label(RichText::new(&email).small().color(t.muted));
            });
            if unread > 0 {
                ui.painter().text(
                    rect.right_center() - vec2(10.0, 0.0),
                    egui::Align2::RIGHT_CENTER,
                    unread.to_string(),
                    FontId::proportional(12.0),
                    t.muted,
                );
            }
            if resp.clicked() {
                app.select_account(&id);
                ui.close();
            }
        }
        ui.separator();
        if ui.button(format!("+ {}", app.tr("Add account"))).clicked() {
            app.screen = Screen::Settings;
            app.settings.editing = Some(crate::settings::AccountForm::from_email(""));
            ui.close();
        }
        if ui.button(app.tr("Settings")).clicked() {
            app.screen = Screen::Settings;
            ui.close();
        }
    });
}

pub fn status_bar(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let frame = egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(14, 4));
    egui::Panel::bottom("status").frame(frame).show(ui, |ui| {
        ui.horizontal(|ui| {
            let account = app.session.view().account.clone();
            let error = app
                .session
                .problem()
                .filter(|p| p != "connecting")
                .map(|p| {
                    app.language.fill(
                        "Server unreachable ({url}): {error}. Start it with: mailcraft-cli serve",
                        &[("url", &app.session.server()), ("error", &p)],
                    )
                })
                .or_else(|| {
                    if app.unified() {
                        app.session.config().accounts.iter().find_map(|a| app.session.state(&a.id).and_then(|s| s.error.clone()))
                    } else {
                        app.session.state(&account).and_then(|s| s.error.clone())
                    }
                });
            match error {
                Some(e) => {
                    let (r, _) = ui.allocate_exact_size(vec2(16.0, 16.0), Sense::hover());
                    icons::paint(ui.painter(), r, Icon::Warning, Color32::from_rgb(0xe5, 0x48, 0x4d));
                    ui.label(RichText::new(app.language.tr_message(&e)).small().color(Color32::from_rgb(0xe5, 0x48, 0x4d)));
                }
                None => {
                    ui.label(RichText::new(&app.status).small().color(t.muted));
                }
            }
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                let (n, unread) = if app.unified() {
                    let n = app.session.config().accounts.iter().map(|a| app.session.state(&a.id).map_or(0, |s| s.mailboxes.len())).sum::<usize>();
                    (n, app.total_unread())
                } else {
                    let st = app.session.current();
                    st.map_or((0, 0), |s| (s.mailboxes.len(), s.by_role(mailcraft_engine::core::mailbox::Role::Inbox).map_or(0, |m| m.unread)))
                };
                ui.label(
                    RichText::new(app.language.fill("Mailboxes: {n} · Unread: {unread}", &[("n", &n.to_string()), ("unread", &unread.to_string())]))
                        .small()
                        .color(t.muted),
                );
            });
        });
    });
}

/// A transient message at the bottom centre.
pub fn toast(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let Some((text, since)) = app.toast.clone() else { return };
    if since.elapsed().as_secs_f32() > 4.0 {
        app.toast = None;
        return;
    }
    ui.ctx().request_repaint_after(std::time::Duration::from_millis(250));
    egui::Area::new(egui::Id::new("toast")).anchor(egui::Align2::CENTER_BOTTOM, vec2(0.0, -44.0)).order(egui::Order::Foreground).show(
        ui.ctx(),
        |ui| {
            egui::Frame::NONE
                .fill(if t.dark { Color32::from_rgb(0xe8, 0xe9, 0xee) } else { Color32::from_rgb(0x2a, 0x2c, 0x35) })
                .corner_radius(8)
                .inner_margin(egui::Margin::symmetric(16, 10))
                .show(ui, |ui| {
                    ui.label(RichText::new(text).color(if t.dark { Color32::from_rgb(0x22, 0x25, 0x30) } else { Color32::WHITE }));
                });
        },
    );
}
