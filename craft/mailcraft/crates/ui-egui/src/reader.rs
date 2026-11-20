//! The reading pane: the open conversation, each message expandable, with actions.

use egui::{Align, Color32, FontId, Layout, RichText, Sense, Stroke, StrokeKind, vec2};
use mailcraft_engine::core::message::{Envelope, join_addresses, time};
use serde_json::json;

use crate::chrome::{avatar, color_for};
use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{divider, icon_button};
use crate::{MailApp, Request};

/// The members of the open conversation, oldest first.
fn members(app: &MailApp, account: &str, mailbox: &str, uid: u32) -> Vec<Envelope> {
    let Some(st) = app.session.state(account) else { return Vec::new() };
    if app.session.config().preferences.conversations
        && app.session.view().query.is_empty()
        && let Some(c) = st.conversations(mailbox).into_iter().find(|c| c.members.contains(&uid))
    {
        let mut list: Vec<Envelope> = c.members.iter().filter_map(|u| st.envelope(mailbox, *u).cloned()).collect();
        list.sort_by(|a, b| a.date.cmp(&b.date).then(a.uid.cmp(&b.uid)));
        return list;
    }
    st.envelope(mailbox, uid).cloned().or_else(|| app.session.view().results.iter().find(|e| e.uid == uid).cloned()).into_iter().collect()
}

pub fn show(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let Some((mailbox, uid)) = app.session.view().open.clone() else {
        ui.centered_and_justified(|ui| ui.label(RichText::new(app.tr("Select a message to read it")).color(t.muted)));
        return;
    };
    let italian = app.italian();
    let account = app.open_account();
    let list = members(app, &account, &mailbox, uid);
    let subject = list.last().map(|e| e.subject.clone()).unwrap_or_default();
    let starred = list.iter().any(|e| e.flags.flagged);
    // Action bar.
    egui::Frame::NONE.fill(t.chrome).inner_margin(egui::Margin::symmetric(10, 6)).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.horizontal(|ui| {
            if icon_button(ui, t, Icon::Back, app.tr("Back"), true, false).clicked() {
                app.close_reader();
            }
            divider(ui, t);
            if icon_button(ui, t, Icon::Reply, &app.tip("mail.reply"), true, false).clicked() {
                app.compose_reply(false);
            }
            if icon_button(ui, t, Icon::ReplyAll, app.tr("Reply all"), true, false).clicked() {
                app.compose_reply(true);
            }
            if icon_button(ui, t, Icon::Forward, &app.tip("mail.forward"), true, false).clicked() {
                app.compose_forward();
            }
            divider(ui, t);
            if icon_button(ui, t, Icon::Archive, &app.tip("mail.archive"), true, false).clicked() {
                app.act("mail.archive", json!({"uids": [uid]}));
            }
            if icon_button(ui, t, Icon::Trash, &app.tip("mail.trash"), true, false).clicked() {
                app.act("mail.trash", json!({"uids": [uid]}));
            }
            if icon_button(ui, t, Icon::Spam, &app.tip("mail.spam"), true, false).clicked() {
                app.act("mail.spam", json!({"uids": [uid]}));
            }
            if icon_button(
                ui,
                t,
                if starred { Icon::StarFilled } else { Icon::Star },
                app.tr(if starred { "Remove star" } else { "Add star" }),
                true,
                starred,
            )
            .clicked()
            {
                app.act("mail.star", json!({"uids": [uid], "starred": !starred}));
            }
            if icon_button(ui, t, Icon::Mail, app.tr("Mark as unread"), true, false).clicked() {
                app.act("mail.markRead", json!({"uids": [uid], "read": false}));
                app.close_reader();
            }
            let mv = icon_button(ui, t, Icon::Move, app.tr("Move to"), true, false);
            crate::list::move_menu(app, &mv);
            if icon_button(ui, t, Icon::Print, app.tr("Print"), true, false).clicked() {
                app.print_open_message();
            }
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                if icon_button(ui, t, Icon::Close, app.tr("Close"), true, false).clicked() {
                    app.close_reader();
                }
            });
        });
    });
    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        egui::Frame::NONE.inner_margin(egui::Margin::symmetric(20, 14)).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.label(RichText::new(if subject.trim().is_empty() { "(no subject)" } else { &subject }).size(20.0).strong().color(t.ui_text));
            if list.len() > 1 {
                ui.label(RichText::new(format!("{} · {}", app.tr("Conversation"), list.len())).small().color(t.muted));
            }
            ui.add_space(8.0);
            for e in &list {
                message_card(app, ui, t, &account, &mailbox, e, italian, list.len() == 1);
                ui.add_space(8.0);
            }
            suggestion_chips(app, ui, t, &account, &mailbox, uid);
        });
    });
}

/// The assistant's reply suggestions under the message, like Gmail's smart replies: a click
/// opens the reply with that sentence.
fn suggestion_chips(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, account: &str, mailbox: &str, uid: u32) {
    if !app.assist_available() || !app.session.config().preferences.assist_suggestions {
        return;
    }
    let key = (account.to_string(), mailbox.to_string(), uid);
    let Some(list) = app.assist.suggestions.get(&key).cloned() else { return };
    if list.is_empty() {
        if app.assist.busy {
            ui.horizontal(|ui| {
                ui.add(egui::Spinner::new().size(12.0).color(t.muted));
                ui.label(RichText::new(app.tr("Suggested replies…")).small().color(t.muted));
            });
        }
        return;
    }
    let mut chosen: Option<String> = None;
    ui.horizontal_wrapped(|ui| {
        for s in &list {
            let (rect, resp) = ui.allocate_exact_size(
                vec2(ui.painter().layout_no_wrap(s.clone(), FontId::proportional(13.0), t.ui_text).size().x + 28.0, 32.0),
                Sense::click(),
            );
            ui.painter().rect_filled(rect, 16.0, if resp.hovered() { t.hover } else { t.panel });
            ui.painter().rect_stroke(rect, 16.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
            ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, s, FontId::proportional(13.0), t.ui_text);
            if resp.on_hover_text(app.tr("Reply with this")).clicked() {
                chosen = Some(s.clone());
            }
        }
    });
    if let Some(s) = chosen {
        app.reply_with(&s);
    }
}

#[allow(clippy::too_many_arguments)]
fn message_card(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, account: &str, mailbox: &str, e: &Envelope, italian: bool, only: bool) {
    let expanded = only || app.expanded.contains(&e.uid);
    let message = app.session.state(account).and_then(|s| s.message(mailbox, e.uid)).cloned();
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(14);
    let inner = frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        let from = e.from.first().cloned().unwrap_or_default();
        ui.horizontal(|ui| {
            avatar(ui, t, from.short(), color_for(&from.email, ""), 36.0);
            ui.vertical(|ui| {
                ui.spacing_mut().item_spacing.y = 1.0;
                ui.horizontal(|ui| {
                    ui.label(RichText::new(from.short()).strong().color(t.ui_text));
                    ui.label(RichText::new(format!("<{}>", from.email)).small().color(t.muted));
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        ui.label(RichText::new(time::long(app.local(e.date), italian)).small().color(t.muted));
                        if e.has_attachments {
                            let (r, _) = ui.allocate_exact_size(vec2(16.0, 16.0), Sense::hover());
                            icons::paint(ui.painter(), r, Icon::Attachment, t.muted);
                        }
                    });
                });
                if expanded {
                    ui.label(RichText::new(format!("{}: {}", app.tr("To"), join_addresses(&e.to))).small().color(t.muted));
                    if !e.cc.is_empty() {
                        ui.label(RichText::new(format!("{}: {}", app.tr("Cc"), join_addresses(&e.cc))).small().color(t.muted));
                    }
                } else {
                    ui.label(RichText::new(&e.snippet).small().color(t.muted));
                }
            });
        });
        if !expanded {
            return;
        }
        ui.add_space(10.0);
        ui.separator();
        ui.add_space(6.0);
        match message {
            Some(m) => {
                let text = m.body_text();
                ui.add(egui::Label::new(RichText::new(text).color(t.ui_text).font(FontId::proportional(14.0))).wrap().selectable(true));
                let attachments: Vec<_> = m.attachments.iter().filter(|a| !a.inline).cloned().collect();
                if !attachments.is_empty() {
                    ui.add_space(12.0);
                    ui.label(RichText::new(app.tr("Attachments")).small().strong().color(t.muted));
                    ui.horizontal_wrapped(|ui| {
                        for a in attachments {
                            let label = format!("{} · {}", a.name, human_size(a.size));
                            let (rect, resp) = ui.allocate_exact_size(
                                vec2(ui.painter().layout_no_wrap(label.clone(), FontId::proportional(13.0), t.ui_text).size().x + 44.0, 32.0),
                                Sense::click(),
                            );
                            ui.painter().rect_filled(rect, 8.0, if resp.hovered() { t.hover } else { t.panel });
                            ui.painter().rect_stroke(rect, 8.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
                            icons::paint(
                                ui.painter(),
                                egui::Rect::from_center_size(rect.left_center() + vec2(18.0, 0.0), vec2(16.0, 16.0)),
                                Icon::Download,
                                t.accent,
                            );
                            ui.painter().text(
                                rect.left_center() + vec2(32.0, 0.0),
                                egui::Align2::LEFT_CENTER,
                                label,
                                FontId::proportional(13.0),
                                t.ui_text,
                            );
                            if resp.on_hover_text(app.tr("Save")).clicked() {
                                app.run("mail.attachment", json!({"uid": e.uid, "mailbox": mailbox, "account": account, "index": a.index}));
                            }
                        }
                    });
                }
                if !m.list_unsubscribe.is_empty()
                    && let Some(url) = m.list_unsubscribe.split(['<', '>', ',']).map(str::trim).find(|u| u.starts_with("http"))
                {
                    ui.add_space(6.0);
                    if ui.link(RichText::new("Unsubscribe").small().color(t.muted)).clicked() {
                        app.requests.push(Request::OpenUrl(url.to_string()));
                    }
                }
            }
            None => {
                ui.horizontal(|ui| {
                    ui.add(egui::Spinner::new().size(16.0).color(t.accent));
                    ui.label(RichText::new(app.tr("Loading…")).color(t.muted));
                });
            }
        }
    });
    if !expanded && inner.response.interact(Sense::click()).clicked() {
        app.expanded.push(e.uid);
        app.run("mail.open", json!({"uid": e.uid, "mailbox": mailbox, "account": account}));
    }
    let _ = Color32::TRANSPARENT;
}

pub fn human_size(bytes: usize) -> String {
    if bytes >= 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / 1024.0 / 1024.0)
    } else if bytes >= 1024 {
        format!("{} KB", bytes / 1024)
    } else {
        format!("{bytes} B")
    }
}
