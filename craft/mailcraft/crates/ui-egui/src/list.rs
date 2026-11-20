//! The message list: conversations (or messages) of the mailbox in view, search results, or
//! the merged list of every account (the unified view).

use std::collections::HashMap;

use egui::{Align2, Color32, FontId, Rect, RichText, Sense, Stroke, StrokeKind, pos2, vec2};
use mailcraft_engine::core::mailbox::Role;
use mailcraft_engine::core::message::{Envelope, time};
use serde_json::json;

use crate::MailApp;
use crate::chrome::color_for;
use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{divider, icon_button};

/// One row: a conversation or a single message, with the account and mailbox it lives in.
#[derive(Clone, Debug)]
pub struct Row {
    pub uid: u32,
    pub account: String,
    pub mailbox: String,
    pub env: Envelope,
    pub count: usize,
    pub unread: u32,
    pub flagged: bool,
    pub has_attachments: bool,
    pub senders: String,
}

fn single(e: &Envelope, account: &str, mailbox: &str) -> Row {
    Row {
        uid: e.uid,
        account: account.to_string(),
        mailbox: mailbox.to_string(),
        env: e.clone(),
        count: 1,
        unread: u32::from(!e.flags.seen),
        flagged: e.flags.flagged,
        has_attachments: e.has_attachments,
        senders: e.sender().to_string(),
    }
}

pub fn rows(app: &MailApp) -> Vec<Row> {
    let view = app.session.view();
    if view.unified() {
        // Every account at once: the engine tags each envelope with its account.
        let list = if view.query.is_empty() { app.unified_rows() } else { view.results.clone() };
        return list.iter().map(|e| single(e, &e.account, &e.mailbox)).collect();
    }
    let account = view.account.as_str();
    let Some(st) = app.session.current() else { return Vec::new() };
    if !view.query.is_empty() {
        return view.results.iter().map(|e| single(e, account, &e.mailbox)).collect();
    }
    if app.session.config().preferences.conversations {
        st.conversations(&view.mailbox)
            .into_iter()
            .filter_map(|c| {
                let env = st.envelope(&view.mailbox, c.latest)?.clone();
                let senders = if c.senders.len() > 1 {
                    c.senders.iter().take(3).map(|s| s.split(' ').next().unwrap_or(s).to_string()).collect::<Vec<_>>().join(", ")
                } else {
                    env.sender().to_string()
                };
                Some(Row {
                    uid: c.latest,
                    account: account.to_string(),
                    mailbox: view.mailbox.clone(),
                    env,
                    count: c.members.len(),
                    unread: c.unread,
                    flagged: c.flagged,
                    has_attachments: c.has_attachments,
                    senders,
                })
            })
            .collect()
    } else {
        st.list(&view.mailbox).iter().map(|e| single(e, account, &view.mailbox)).collect()
    }
}

/// The uids shown, in order.
pub fn visible_uids(app: &MailApp) -> Vec<u32> {
    rows(app).into_iter().map(|r| r.uid).collect()
}

/// `(account, mailbox, uid)` of every row shown, in order (what j/k walk).
pub fn visible_keys(app: &MailApp) -> Vec<(String, String, u32)> {
    rows(app).into_iter().map(|r| (r.account, r.mailbox, r.uid)).collect()
}

fn truncate(painter: &egui::Painter, text: &str, font: FontId, color: Color32, max_w: f32) -> std::sync::Arc<egui::Galley> {
    let mut job = egui::text::LayoutJob::simple_singleline(text.to_string(), font, color);
    job.wrap = egui::text::TextWrapping::truncate_at_width(max_w.max(10.0));
    painter.layout_job(job)
}

pub fn show(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let view = app.session.view().clone();
    let account = view.account.clone();
    let unified = view.unified();
    let role = view.unified_role();
    let italian = app.italian();
    let is_drafts = if unified { role == Role::Drafts } else { app.session.folder_for(&account, Role::Drafts).is_some_and(|d| d == view.mailbox) };
    let is_trash = if unified { role == Role::Trash } else { app.session.folder_for(&account, Role::Trash).is_some_and(|d| d == view.mailbox) };
    // The accounts' chips (name initial and colour) for the unified list.
    let chips: HashMap<String, (String, Color32)> = if unified {
        app.session.config().accounts.iter().map(|a| (a.id.clone(), (a.name.clone(), color_for(&a.email, &a.color)))).collect()
    } else {
        HashMap::new()
    };
    // Header: mailbox name and the selection bar.
    egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(12, 6)).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.horizontal(|ui| {
            let all = visible_uids(app);
            let selected = view.selection.len();
            let mut all_on = selected > 0 && selected >= all.len();
            if ui.checkbox(&mut all_on, "").on_hover_text(app.tr("Select all")).changed() {
                if all_on {
                    app.run("selection.all", json!({}));
                } else {
                    app.run("selection.clear", json!({}));
                }
            }
            if selected > 0 {
                let who = if unified {
                    app.session.account(&view.selection_account).map(|a| format!(" · {}", a.name)).unwrap_or_default()
                } else {
                    String::new()
                };
                ui.label(RichText::new(format!("{selected} {}{who}", app.tr("selected"))).color(t.muted));
                divider(ui, t);
                if !is_trash && icon_button(ui, t, Icon::Archive, &app.tip("mail.archive"), true, false).clicked() {
                    app.act("mail.archive", json!({}));
                }
                if icon_button(ui, t, Icon::Trash, &app.tip("mail.trash"), true, false).clicked() {
                    app.act("mail.trash", json!({}));
                }
                if icon_button(ui, t, Icon::Spam, &app.tip("mail.spam"), true, false).clicked() {
                    app.act("mail.spam", json!({}));
                }
                if icon_button(ui, t, Icon::MailOpen, app.tr("Mark as read"), true, false).clicked() {
                    app.act("mail.markRead", json!({"read": true}));
                }
                if icon_button(ui, t, Icon::Mail, app.tr("Mark as unread"), true, false).clicked() {
                    app.act("mail.markRead", json!({"read": false}));
                }
                if icon_button(ui, t, Icon::StarFilled, app.tr("Add star"), true, false).clicked() {
                    app.act("mail.star", json!({"starred": true}));
                }
                let mv = icon_button(ui, t, Icon::Move, app.tr("Move to"), true, false);
                move_menu(app, &mv);
            } else {
                let title = if unified {
                    format!("{} · {}", role.label(italian), app.tr("All accounts"))
                } else {
                    app.session
                        .current()
                        .and_then(|s| s.mailbox(&view.mailbox))
                        .map(|m| if m.role == Role::Other { m.name.clone() } else { m.role.label(italian).to_string() })
                        .unwrap_or_else(|| view.mailbox.clone())
                };
                let title = if view.query.is_empty() { title } else { format!("{title} · “{}”", view.query) };
                ui.label(RichText::new(title).strong().color(t.ui_text));
                let n = all.len();
                ui.label(RichText::new(format!("{n} {}", app.tr("messages"))).small().color(t.muted));
            }
        });
    });
    let rows = rows(app);
    let open_key = view.open.as_ref().map(|(m, u)| (app.open_account(), m.clone(), *u));
    let tz = app.tz();
    let now = time::shifted(time::now(), tz);
    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        if rows.is_empty() {
            ui.add_space(60.0);
            ui.vertical_centered(|ui| {
                let busy = app.session.jobs_pending() > 0;
                let text = if busy {
                    app.tr("Loading…")
                } else if view.query.is_empty() {
                    app.tr("No messages")
                } else {
                    app.tr("No messages match the search")
                };
                ui.label(RichText::new(text).color(t.muted));
            });
            return;
        }
        let width = ui.available_width();
        for row in &rows {
            let (rect, resp) = ui.allocate_exact_size(vec2(width, 48.0), Sense::click());
            let same_place = |a: &str, m: &str| !unified || (a == row.account && m == row.mailbox);
            let selected = view.selection.contains(&row.uid) && same_place(&view.selection_account, &view.selection_mailbox);
            let open = open_key.as_ref().is_some_and(|(a, m, u)| *u == row.uid && same_place(a, m));
            let p = ui.painter();
            let fill = if selected {
                t.active
            } else if open || resp.hovered() {
                t.hover
            } else if row.unread > 0 {
                t.card
            } else {
                t.panel
            };
            p.rect_filled(rect, 0.0, fill);
            p.hline(rect.x_range(), rect.bottom(), Stroke::new(1.0, t.border));
            // Checkbox and star.
            let cb = Rect::from_center_size(pos2(rect.left() + 22.0, rect.center().y), vec2(16.0, 16.0));
            p.rect_stroke(cb, 3.0, Stroke::new(1.2, if selected { t.accent } else { t.muted }), StrokeKind::Inside);
            if selected {
                p.rect_filled(cb.shrink(1.0), 2.0, t.accent);
                icons::paint(p, cb.shrink(2.0), Icon::Check, t.on_accent);
            }
            let star = Rect::from_center_size(pos2(rect.left() + 48.0, rect.center().y), vec2(18.0, 18.0));
            icons::paint(
                p,
                star,
                if row.flagged { Icon::StarFilled } else { Icon::Star },
                if row.flagged { Color32::from_rgb(0xf4, 0xb4, 0x00) } else { t.muted },
            );
            let mut x = rect.left() + 68.0;
            // The account chip (unified view): its colour and initial.
            let mut chip_rect = None;
            if let Some((name, color)) = chips.get(&row.account) {
                let c = Rect::from_center_size(pos2(x + 10.0, rect.center().y), vec2(20.0, 20.0));
                p.circle_filled(c.center(), 10.0, *color);
                let initial: String = name.chars().find(|c| c.is_alphanumeric()).map(|c| c.to_uppercase().to_string()).unwrap_or_else(|| "?".into());
                p.text(c.center(), Align2::CENTER_CENTER, initial, FontId::proportional(11.0), t.on_accent);
                chip_rect = Some((c, name.clone()));
                x += 28.0;
            }
            let bold = row.unread > 0;
            let text_color = if bold { t.ui_text } else { t.muted.lerp_to_gamma(t.ui_text, 0.55) };
            let sender_font = if bold { FontId::new(14.0, egui::FontFamily::Proportional) } else { FontId::proportional(14.0) };
            let sender_w = 190.0_f32.min(width * 0.25);
            let sender = truncate(p, &row.senders, sender_font, text_color, sender_w);
            if bold {
                p.galley(pos2(x, rect.center().y - sender.size().y / 2.0), sender.clone(), t.ui_text);
                p.galley(pos2(x + 0.5, rect.center().y - sender.size().y / 2.0), sender, t.ui_text);
            } else {
                p.galley(pos2(x, rect.center().y - sender.size().y / 2.0), sender, text_color);
            }
            x += sender_w + 8.0;
            if row.count > 1 {
                p.text(pos2(x, rect.center().y), Align2::LEFT_CENTER, row.count.to_string(), FontId::proportional(12.0), t.muted);
                x += 18.0;
            }
            let date = time::short(time::shifted(row.env.date, tz), now, italian);
            let right = rect.right() - 14.0;
            let date_w = 64.0;
            let mut right_edge = right - date_w;
            if row.has_attachments {
                icons::paint(p, Rect::from_center_size(pos2(right_edge - 10.0, rect.center().y), vec2(16.0, 16.0)), Icon::Attachment, t.muted);
                right_edge -= 24.0;
            }
            let subject = if row.env.subject.trim().is_empty() { "(no subject)".to_string() } else { row.env.subject.clone() };
            let subject_font = FontId::proportional(14.0);
            let subj = truncate(p, &subject, subject_font.clone(), text_color, (right_edge - x - 8.0).max(40.0));
            if bold {
                p.galley(pos2(x, rect.center().y - subj.size().y / 2.0), subj.clone(), t.ui_text);
                p.galley(pos2(x + 0.5, rect.center().y - subj.size().y / 2.0), subj.clone(), t.ui_text);
            } else {
                p.galley(pos2(x, rect.center().y - subj.size().y / 2.0), subj.clone(), text_color);
            }
            let sx = x + subj.size().x + 6.0;
            if !row.env.snippet.is_empty() && sx < right_edge - 40.0 {
                let snip = truncate(p, &format!("– {}", row.env.snippet), FontId::proportional(13.0), t.muted, right_edge - sx - 4.0);
                p.galley(pos2(sx, rect.center().y - snip.size().y / 2.0), snip, t.muted);
            }
            if let Some((c, name)) = &chip_rect {
                ui.interact(*c, ui.id().with(("row-chip", &row.account, row.uid)), Sense::hover()).on_hover_text(name);
            }
            let place = json!({"account": row.account, "mailbox": row.mailbox});
            // Hover actions replace the date.
            if resp.hovered() && !selected {
                let mut hx = right;
                for (icon, cmd, tip) in [
                    (Icon::Trash, "mail.trash", "Delete"),
                    (Icon::Archive, "mail.archive", "Archive"),
                    (
                        if row.unread > 0 { Icon::MailOpen } else { Icon::Mail },
                        "mail.markRead",
                        if row.unread > 0 { "Mark as read" } else { "Mark as unread" },
                    ),
                ] {
                    let r = Rect::from_center_size(pos2(hx - 12.0, rect.center().y), vec2(26.0, 26.0));
                    let hr = ui.interact(r, ui.id().with(("row-act", &row.account, row.uid, cmd)), Sense::click());
                    if hr.hovered() {
                        ui.painter().rect_filled(r, 6.0, t.active);
                    }
                    icons::paint(ui.painter(), r.shrink(4.0), icon, t.ui_text);
                    if hr.on_hover_text(app.tr(tip)).clicked() {
                        let mut params = place.clone();
                        params["uids"] = json!([row.uid]);
                        if cmd == "mail.markRead" {
                            params["read"] = json!(row.unread > 0);
                        }
                        app.act(cmd, params);
                    }
                    hx -= 30.0;
                }
            } else {
                p.text(pos2(right, rect.center().y), Align2::RIGHT_CENTER, date, FontId::proportional(12.5), if bold { t.ui_text } else { t.muted });
            }
            // Clicks: the checkbox toggles, the star stars, the row opens.
            if let Some(pointer) = resp.interact_pointer_pos().filter(|_| resp.clicked()) {
                if cb.expand(6.0).contains(pointer) || ui.input(|i| i.modifiers.command || i.modifiers.shift) {
                    // A selection spans one account and mailbox: a row elsewhere starts a new one.
                    let mut sel = if same_place(&view.selection_account, &view.selection_mailbox) { view.selection.clone() } else { Vec::new() };
                    if let Some(i) = sel.iter().position(|u| *u == row.uid) {
                        sel.remove(i);
                    } else {
                        sel.push(row.uid);
                    }
                    let mut params = place.clone();
                    params["uids"] = json!(sel);
                    app.run("selection.set", params);
                } else if star.expand(6.0).contains(pointer) {
                    let mut params = place.clone();
                    params["uids"] = json!([row.uid]);
                    params["starred"] = json!(!row.flagged);
                    app.act("mail.star", params);
                } else if is_drafts {
                    app.edit_draft(&row.account, &row.mailbox, row.uid);
                } else {
                    let mut params = place.clone();
                    params["uids"] = json!([row.uid]);
                    app.run("selection.set", params);
                    app.open_at(&row.account, &row.mailbox, row.uid);
                }
            }
        }
        // More (one mailbox at a time; the unified list shows each account's first page).
        if view.query.is_empty() && !unified {
            let size = app.session.config().preferences.page_size as usize;
            let cached = app.session.current().map_or(0, |s| s.list(&view.mailbox).len());
            let total = app.session.current().and_then(|s| s.mailbox(&view.mailbox)).map_or(0, |m| m.total as usize);
            if total > cached && cached > 0 {
                ui.add_space(8.0);
                ui.vertical_centered(|ui| {
                    let more = (total - cached).min(size);
                    if ui.button(app.language.fill("Show {n} more messages", &[("n", &more.to_string())])).clicked() {
                        let page = cached / size;
                        app.run("mail.list", json!({"page": page, "refresh": true}));
                    }
                });
                ui.add_space(8.0);
            }
        }
    });
}

/// The "move to" folder menu on a button: the folders of the account that would act.
pub fn move_menu(app: &mut MailApp, button: &egui::Response) {
    let account = app.acting_account();
    let folders: Vec<(String, String)> = app
        .session
        .state(&account)
        .map(|s| s.mailboxes.iter().filter(|m| !m.no_select).map(|m| (m.path.clone(), m.name.clone())).collect())
        .unwrap_or_default();
    let view = app.session.view();
    let current = if view.unified() {
        if view.selection.is_empty() { view.open.as_ref().map(|(m, _)| m.clone()).unwrap_or_default() } else { view.selection_mailbox.clone() }
    } else {
        view.mailbox.clone()
    };
    egui::Popup::menu(button).show(|ui| {
        ui.set_min_width(180.0);
        for (path, name) in folders {
            if path == current {
                continue;
            }
            if ui.button(name).clicked() {
                app.act("mail.move", json!({"to": path, "account": account}));
                ui.close();
            }
        }
    });
}
