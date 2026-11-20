//! Settings: accounts (add, edit, test, find servers), preferences, the assistant (MCP) and
//! the keyboard shortcuts.

use std::collections::HashMap;

use egui::{Color32, RichText, Stroke, StrokeKind, vec2};
use mailcraft_engine::core::account::{Account, Security};
use mailcraft_engine::core::mailbox::Role;
use mailcraft_engine::core::oauth;
use serde_json::{Value, json};

use crate::chrome::{avatar, color_for};
use crate::icons::{self, Icon};
use crate::theme::Tokens;
use crate::widgets::{icon_text_button, primary_button};
use crate::{Language, MailApp, Request, Screen};

/// The account form.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct AccountForm {
    pub id: Option<String>,
    pub name: String,
    pub email: String,
    pub display_name: String,
    pub username: String,
    pub password: String,
    pub imap_host: String,
    pub imap_port: String,
    pub imap_security: Security,
    pub smtp_host: String,
    pub smtp_port: String,
    pub smtp_security: Security,
    pub signature: String,
    pub signature_html: String,
    pub trust_invalid_certs: bool,
    pub save_sent: bool,
    pub notes: Vec<String>,
    /// The special folders' IMAP paths (empty = detected by name).
    pub sent: String,
    pub drafts: String,
    pub trash: String,
    pub spam: String,
    pub archive: String,
    /// `""` = password, else an OAuth provider id (`google`, `microsoft`).
    pub auth: String,
}

impl AccountForm {
    pub fn from_email(email: &str) -> Self {
        Self { email: email.into(), save_sent: true, ..Self::default() }
    }

    pub fn from_account(a: &Account) -> Self {
        Self {
            id: Some(a.id.clone()),
            name: a.name.clone(),
            email: a.email.clone(),
            display_name: a.display_name.clone(),
            username: a.username.clone(),
            password: String::new(),
            imap_host: a.imap.host.clone(),
            imap_port: a.imap.port.to_string(),
            imap_security: a.imap.security,
            smtp_host: a.smtp.host.clone(),
            smtp_port: a.smtp.port.to_string(),
            smtp_security: a.smtp.security,
            signature: a.signature.clone(),
            signature_html: a.signature_html.clone(),
            trust_invalid_certs: a.trust_invalid_certs,
            save_sent: a.save_sent,
            notes: Vec::new(),
            sent: a.folders.sent.clone(),
            drafts: a.folders.drafts.clone(),
            trash: a.folders.trash.clone(),
            spam: a.folders.spam.clone(),
            archive: a.folders.archive.clone(),
            auth: a.auth.clone(),
        }
    }

    /// Fills the servers, name and notes from an `account.suggest` answer.
    pub fn apply_suggestion(&mut self, v: &Value) {
        self.imap_host = v["imap"]["host"].as_str().unwrap_or("").to_string();
        self.imap_port = v["imap"]["port"].to_string();
        self.imap_security = Security::parse(v["imap"]["security"].as_str().unwrap_or("tls")).unwrap_or_default();
        self.smtp_host = v["smtp"]["host"].as_str().unwrap_or("").to_string();
        self.smtp_port = v["smtp"]["port"].to_string();
        self.smtp_security = Security::parse(v["smtp"]["security"].as_str().unwrap_or("tls")).unwrap_or_default();
        if self.name.trim().is_empty() {
            self.name = v["provider"]
                .as_str()
                .map(str::to_string)
                .unwrap_or_else(|| mailcraft_engine::core::account::domain_of(&self.email).unwrap_or_default());
        }
        self.notes = v["notes"].as_array().map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default();
        // A Google or Microsoft address signs in with the provider unless a password was typed.
        if let Some(o) = v["oauth"].as_str().filter(|o| !o.is_empty())
            && self.auth.is_empty()
            && self.password.is_empty()
        {
            self.auth = o.to_string();
        }
    }

    fn params(&self) -> Value {
        let mut v = json!({
            "email": self.email,
            "name": self.name,
            "display_name": self.display_name,
            "username": self.username,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port.trim().parse::<u32>().unwrap_or(0),
            "imap_security": self.imap_security.name(),
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port.trim().parse::<u32>().unwrap_or(0),
            "smtp_security": self.smtp_security.name(),
            "signature": self.signature,
            "signature_html": self.signature_html,
            "trust_invalid_certs": self.trust_invalid_certs,
            "save_sent": self.save_sent,
            "folders": {"sent": self.sent, "drafts": self.drafts, "trash": self.trash, "spam": self.spam, "archive": self.archive},
            "auth": if self.auth.is_empty() { "password" } else { self.auth.as_str() },
        });
        if !self.password.is_empty() {
            v["password"] = json!(self.password);
        }
        if let Some(id) = &self.id {
            v["id"] = json!(id);
        }
        v
    }
}

#[derive(Default)]
pub struct SettingsState {
    pub editing: Option<AccountForm>,
    pub result: String,
    /// Reload the form from the saved account (after a probe changed the servers).
    pub reload: bool,
    /// The server URL being typed (web app).
    pub server_draft: Option<String>,
    /// The name of the folder being created.
    pub new_folder: String,
    /// Start the OAuth sign-in as soon as the account is saved.
    pub oauth_after_save: bool,
    /// The OAuth client fields being typed, by provider: (client id, client secret).
    pub oauth_clients: HashMap<String, (String, String)>,
    /// The address whose servers were last asked for (once per address).
    pub suggested_for: String,
    /// The time zone being typed (applied on request).
    pub time_offset: Option<String>,
}

fn section(ui: &mut egui::Ui, t: &Tokens, text: &str) {
    ui.add_space(14.0);
    ui.label(RichText::new(text).size(16.0).strong().color(t.ui_text));
    ui.add_space(4.0);
}

fn security_combo(ui: &mut egui::Ui, id: &str, value: &mut Security) {
    egui::ComboBox::from_id_salt(id).selected_text(value.name()).width(100.0).show_ui(ui, |ui| {
        for s in Security::ALL {
            ui.selectable_value(value, s, s.name());
        }
    });
}

pub fn show(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    if app.settings.reload {
        app.settings.reload = false;
        if let Some(id) = app.settings.editing.as_ref().and_then(|f| f.id.clone())
            && let Some(a) = app.session.account(&id).cloned()
        {
            let notes = app.settings.editing.as_ref().map(|f| f.notes.clone()).unwrap_or_default();
            app.settings.editing = Some(AccountForm { notes, ..AccountForm::from_account(&a) });
        }
    }
    egui::CentralPanel::default().frame(egui::Frame::NONE.fill(t.panel)).show(ui, |ui| {
        egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
            egui::Frame::NONE.inner_margin(egui::Margin::symmetric(28, 18)).show(ui, |ui| {
                ui.set_max_width(860.0);
                ui.horizontal(|ui| {
                    ui.label(RichText::new(app.tr("Settings")).size(22.0).strong().color(t.ui_text));
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if !app.session.config().accounts.is_empty() && icon_text_button(ui, t, Icon::Back, app.tr("Done"), true).clicked() {
                            app.screen = Screen::Mail;
                        }
                    });
                });
                server_section(app, ui, t);
                accounts_section(app, ui, t);
                if app.settings.editing.is_some() {
                    account_form(app, ui, t);
                } else {
                    preferences_section(app, ui, t);
                    oauth_section(app, ui, t);
                    assistant_section(app, ui, t);
                    shortcuts_section(app, ui, t);
                }
            });
        });
    });
}

fn accounts_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Accounts"));
    if app.session.config().accounts.is_empty() {
        ui.label(RichText::new(app.tr("No account yet. Add one here, or ask the assistant (MCP) to set it up from a sentence.")).color(t.muted));
    }
    let accounts: Vec<Account> = app.session.config().accounts.clone();
    for a in accounts {
        let (unread, error, last) =
            app.session.state(&a.id).map_or((0, None, 0), |s| (s.by_role(Role::Inbox).map_or(0, |m| m.unread), s.error.clone(), s.last_sync));
        let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(12);
        frame.show(ui, |ui| {
            ui.set_width(ui.available_width());
            // The buttons take the right end; the text is clipped to what is left of it.
            let buttons_w = 300.0_f32;
            let text_w = (ui.available_width() - 36.0 - buttons_w - 24.0).max(160.0);
            ui.horizontal(|ui| {
                avatar(ui, t, &a.name, color_for(&a.email, &a.color), 36.0);
                ui.vertical(|ui| {
                    ui.set_max_width(text_w);
                    ui.spacing_mut().item_spacing.y = 1.0;
                    ui.add(egui::Label::new(RichText::new(&a.name).strong().color(t.ui_text)).truncate());
                    ui.add(
                        egui::Label::new(
                            RichText::new(format!("{} · IMAP {} · SMTP {}", a.email, a.imap.describe(), a.smtp.describe())).small().color(t.muted),
                        )
                        .truncate(),
                    );
                    let when = if last > 0 {
                        mailcraft_engine::core::message::time::long(app.local(last), app.italian())
                    } else {
                        app.tr("never").to_string()
                    };
                    match error {
                        Some(e) => ui.add(
                            egui::Label::new(
                                RichText::new(format!("{}: {}", app.tr("Error"), app.language.tr_message(&e)))
                                    .small()
                                    .color(Color32::from_rgb(0xe5, 0x48, 0x4d)),
                            )
                            .truncate(),
                        ),
                        None => ui.add(
                            egui::Label::new(
                                RichText::new(format!("{}: {when} · {} {unread}", app.tr("Last sync"), app.tr("Inbox"))).small().color(t.muted),
                            )
                            .truncate(),
                        ),
                    };
                });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button(app.tr("Edit Account")).clicked() {
                        app.settings.editing = Some(AccountForm::from_account(&a));
                        app.settings.result.clear();
                    }
                    if ui.button(app.tr("Test connection")).clicked() {
                        app.run("account.test", json!({"id": a.id}));
                        app.settings.result = app.tr("Loading…").to_string();
                    }
                });
            });
        });
        ui.add_space(6.0);
    }
    if app.settings.editing.is_none() && icon_text_button(ui, t, Icon::Plus, app.tr("Add account"), true).clicked() {
        app.settings.editing = Some(AccountForm { save_sent: true, ..AccountForm::default() });
        app.settings.result.clear();
    }
    if !app.settings.result.is_empty() {
        ui.add_space(4.0);
        ui.label(RichText::new(&app.settings.result).color(t.accent));
    }
}

fn account_form(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let Some(mut f) = app.settings.editing.clone() else { return };
    section(ui, t, app.tr(if f.id.is_some() { "Edit Account" } else { "Add account" }));
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(16);
    let mut action: Option<&str> = None;
    frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        egui::Grid::new("account-form").num_columns(2).spacing(vec2(12.0, 8.0)).min_col_width(200.0).show(ui, |ui| {
            let row = |ui: &mut egui::Ui, label: &str, value: &mut String, password: bool| {
                ui.label(RichText::new(label).color(t.muted));
                ui.add(egui::TextEdit::singleline(value).password(password).desired_width(360.0));
                ui.end_row();
            };
            row(ui, app.tr("E-mail address"), &mut f.email, false);
            // Sign-in: a password, or the provider's OAuth (Google, Microsoft 365).
            ui.label(RichText::new(app.tr("Sign-in")).color(t.muted));
            let auth_label = |auth: &str| -> String {
                match oauth::provider(auth) {
                    Some(p) => p.name.to_string(),
                    None => app.tr("Password").to_string(),
                }
            };
            egui::ComboBox::from_id_salt("auth").selected_text(auth_label(&f.auth)).width(200.0).show_ui(ui, |ui| {
                ui.selectable_value(&mut f.auth, String::new(), app.tr("Password"));
                for p in &oauth::PROVIDERS {
                    if ui.selectable_value(&mut f.auth, p.id.to_string(), p.name).changed() && !p.owns_host(&f.imap_host) {
                        // A custom domain on Google Workspace / Microsoft 365: the provider's servers.
                        let (imap, smtp) = p.servers();
                        f.imap_host = imap.host;
                        f.imap_port = imap.port.to_string();
                        f.imap_security = imap.security;
                        f.smtp_host = smtp.host;
                        f.smtp_port = smtp.port.to_string();
                        f.smtp_security = smtp.security;
                    }
                }
            });
            ui.end_row();
            if f.auth.is_empty() {
                row(ui, app.tr("Password"), &mut f.password, true);
                if let Some(p) = oauth::provider_for_email(&f.email) {
                    ui.label("");
                    ui.label(
                        RichText::new(app.language.fill("Not the account password: an app password from the {provider} account (two-step verification on)", &[("provider", p.name)]))
                            .small()
                            .color(t.muted),
                    );
                    ui.end_row();
                }
            } else {
                let provider = oauth::provider(&f.auth).map(|p| p.name).unwrap_or("OAuth");
                let ready = app.session.oauth_ready().contains(&f.auth);
                let signed = f.id.as_ref().is_some_and(|id| app.session.has_password(id));
                ui.label("");
                if ready {
                    ui.horizontal(|ui| {
                        if signed {
                            ui.label(RichText::new(app.language.fill("Signed in with {provider}", &[("provider", provider)])).color(t.accent));
                        }
                        let label = if signed {
                            app.tr("Sign in again").to_string()
                        } else {
                            app.language.fill("Sign in with {provider}", &[("provider", provider)])
                        };
                        if ui.button(label).clicked() {
                            action = Some("oauth");
                        }
                    });
                } else if let Some(p) = oauth::provider(&f.auth) {
                    // No client yet: say so where the person is looking, with the fields right here
                    // and the password alternative, instead of a greyed-out button.
                    ui.vertical(|ui| {
                        ui.label(
                            RichText::new(app.language.fill(
                                "Signing in with {provider} needs its OAuth client, created once (usually by the administrator): paste its id and secret here and save, then the sign-in button appears.",
                                &[("provider", provider)],
                            ))
                            .color(t.ui_text),
                        );
                        oauth_client_form(app, ui, t, p);
                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            ui.label(RichText::new(app.tr("Or, without a client:")).small().color(t.muted));
                            if ui.small_button(app.tr("Use an app password")).clicked() {
                                f.auth = String::new();
                            }
                        });
                    });
                }
                ui.end_row();
            }
            // A known address: fill the servers (and the sign-in method) in, as Thunderbird does.
            if f.imap_host.trim().is_empty()
                && mailcraft_engine::core::account::is_email(&f.email)
                && app.settings.suggested_for != f.email
                && action.is_none()
            {
                action = Some("suggest");
            }
            row(ui, app.tr("Name"), &mut f.name, false);
            row(ui, app.tr("Display name"), &mut f.display_name, false);
            row(ui, app.tr("Username (if not the address)"), &mut f.username, false);
            ui.label(RichText::new(app.tr("IMAP server")).color(t.muted));
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut f.imap_host).hint_text(app.tr("Host")).desired_width(190.0));
                ui.add(egui::TextEdit::singleline(&mut f.imap_port).hint_text(app.tr("Port")).desired_width(56.0));
                security_combo(ui, "imap-sec", &mut f.imap_security);
            });
            ui.end_row();
            ui.label(RichText::new(app.tr("SMTP server")).color(t.muted));
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut f.smtp_host).hint_text(app.tr("Host")).desired_width(190.0));
                ui.add(egui::TextEdit::singleline(&mut f.smtp_port).hint_text(app.tr("Port")).desired_width(56.0));
                security_combo(ui, "smtp-sec", &mut f.smtp_security);
            });
            ui.end_row();
            ui.label(RichText::new(app.tr("Signature")).color(t.muted));
            ui.add(egui::TextEdit::multiline(&mut f.signature).desired_rows(3).desired_width(360.0));
            ui.end_row();
            ui.label(RichText::new(app.tr("Signature (HTML)")).color(t.muted));
            ui.vertical(|ui| {
                ui.add(egui::TextEdit::multiline(&mut f.signature_html).desired_rows(4).desired_width(360.0).font(egui::FontId::monospace(12.0)));
                ui.label(RichText::new(app.tr("Paste the HTML of your signature: logo (<img src=\"https://…\"> or a data: image), bold, links. Messages then go out as text + HTML; the plain signature above is the text version (derived from the HTML when empty).")).small().color(t.muted));
            });
            ui.end_row();
        });
        ui.checkbox(&mut f.save_sent, app.tr("Copy sent mail to the Sent folder"));
        ui.checkbox(&mut f.trust_invalid_certs, app.tr("Accept invalid certificates (local bridges, self-hosted servers)"));
        folders_section(app, ui, t, &mut f);
        if !f.notes.is_empty() {
            ui.add_space(6.0);
            ui.label(RichText::new(app.tr("Provider notes")).small().strong().color(t.muted));
            for n in &f.notes {
                ui.label(RichText::new(n).small().color(t.ui_text));
            }
        }
        ui.add_space(10.0);
        ui.horizontal_wrapped(|ui| {
            if primary_button(ui, t, Some(Icon::Save), app.tr("Save account")).clicked() {
                action = Some("save");
            }
            if ui.button(app.tr("Suggest from the provider")).clicked() {
                action = Some("suggest");
            }
            if f.id.is_some() {
                if ui.button(app.tr("Find servers")).clicked() {
                    action = Some("probe");
                }
                if ui.button(app.tr("Test connection")).clicked() {
                    action = Some("test");
                }
                if ui.button(RichText::new(app.tr("Remove account")).color(Color32::from_rgb(0xe5, 0x48, 0x4d))).clicked() {
                    action = Some("remove");
                }
            }
            if ui.button(app.tr("Cancel")).clicked() {
                action = Some("cancel");
            }
        });
        ui.label(
            RichText::new(app.tr("Everything stays on this computer: the password is kept in a file only you can read.")).small().color(t.muted),
        );
    });
    match action {
        Some("oauth") => {
            app.settings.editing = Some(f.clone());
            match &f.id {
                Some(id) => {
                    app.run("account.oauthStart", json!({"id": id, "provider": f.auth}));
                }
                None => {
                    // Save first, then sign in (handle_result starts the flow).
                    app.settings.oauth_after_save = true;
                    app.run("account.add", f.params());
                }
            }
            return;
        }
        Some("suggest") => {
            // The answer reaches the form through MailApp::handle_result (local or remote).
            app.settings.suggested_for = f.email.clone();
            app.settings.editing = Some(f.clone());
            app.run("account.suggest", json!({"email": f.email, "language": app.language.code()}));
            return;
        }
        Some("save") => {
            let cmd = if f.id.is_some() { "account.update" } else { "account.add" };
            app.settings.editing = Some(f.clone());
            app.run(cmd, f.params());
            return;
        }
        Some("probe") => {
            if let Some(id) = &f.id {
                app.run("account.probe", json!({"id": id}));
                app.settings.result = app.tr("Loading…").to_string();
            }
        }
        Some("test") => {
            if let Some(id) = &f.id {
                if !f.password.is_empty() {
                    app.run("account.setPassword", json!({"id": id, "password": f.password}));
                }
                app.run("account.test", json!({"id": id}));
                app.settings.result = app.tr("Loading…").to_string();
            }
        }
        Some("remove") => {
            if let Some(id) = &f.id {
                app.run("account.remove", json!({"id": id}));
            }
            app.settings.editing = None;
            return;
        }
        Some("cancel") => {
            app.settings.editing = None;
            return;
        }
        _ => {}
    }
    app.settings.editing = Some(f);
}

/// The account's folders: which ones the navigation shows (IMAP subscription), the special
/// folders, a new folder.
fn folders_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, f: &mut AccountForm) {
    ui.add_space(12.0);
    ui.label(RichText::new(app.tr("Folders on the server")).strong().color(t.ui_text));
    let Some(id) = f.id.clone() else {
        ui.label(RichText::new(app.tr("Save the account first: its folders are listed here, with the ones to show in the navigation and the special folders (sent, drafts, trash, spam, archive).")).small().color(t.muted));
        return;
    };
    ui.label(RichText::new(app.tr("Unchecked folders stay on the server but leave the navigation (IMAP subscription). The special folders are found by name; pick them here when the server names them differently.")).small().color(t.muted));
    let italian = app.italian();
    let folders: Vec<(String, String, Role, bool, bool, usize)> = app
        .session
        .state(&id)
        .map(|s| {
            let d = s.delimiter.clone();
            s.mailboxes.iter().map(|m| (m.path.clone(), m.name.clone(), m.role, m.subscribed, m.no_select, m.depth(&d))).collect()
        })
        .unwrap_or_default();
    ui.add_space(4.0);
    ui.horizontal(|ui| {
        if ui.button(app.tr("Refresh folders")).clicked() {
            app.run("mail.sync", json!({"account": id}));
            app.settings.result = app.tr("Loading…").to_string();
        }
        if app.session.jobs_pending() > 0 {
            ui.add(egui::Spinner::new().size(14.0).color(t.accent));
        }
    });
    if folders.is_empty() {
        ui.label(RichText::new(app.tr("Loading…")).small().color(t.muted));
    }
    let inner = egui::Frame::NONE.fill(t.panel).stroke(Stroke::new(1.0, t.border)).corner_radius(8).inner_margin(8);
    inner.show(ui, |ui| {
        ui.set_width(ui.available_width());
        egui::ScrollArea::vertical().id_salt("folders").max_height(260.0).auto_shrink([false, true]).show(ui, |ui| {
            for (path, name, role, subscribed, no_select, depth) in &folders {
                ui.horizontal(|ui| {
                    ui.add_space((*depth as f32 * 14.0).min(56.0));
                    let mut on = *subscribed;
                    let inbox = *role == Role::Inbox;
                    let tip = app.tr(if on { "Shown" } else { "Hidden" }).to_string();
                    if ui.add_enabled(!inbox, egui::Checkbox::new(&mut on, "")).on_hover_text(tip).changed() {
                        app.run("mailbox.subscribe", json!({"account": id, "path": path, "subscribed": on}));
                    }
                    let (r, _) = ui.allocate_exact_size(vec2(16.0, 16.0), egui::Sense::hover());
                    icons::paint(ui.painter(), r, icon_for(*role), if *no_select { t.muted } else { t.ui_text });
                    ui.label(RichText::new(name).color(if *no_select { t.muted } else { t.ui_text }));
                    if *role != Role::Other {
                        ui.label(RichText::new(role.label(italian)).small().color(t.accent));
                    }
                    if *depth > 0 {
                        ui.label(RichText::new(path).small().color(t.muted));
                    }
                });
            }
        });
    });
    ui.add_space(6.0);
    egui::Grid::new("special-folders").num_columns(2).spacing(vec2(12.0, 6.0)).min_col_width(200.0).show(ui, |ui| {
        let paths: Vec<String> = folders.iter().filter(|(.., no_select, _)| !no_select).map(|(p, ..)| p.clone()).collect();
        for (label, role, value) in [
            ("Sent folder", Role::Sent, &mut f.sent),
            ("Drafts folder", Role::Drafts, &mut f.drafts),
            ("Trash folder", Role::Trash, &mut f.trash),
            ("Spam folder", Role::Spam, &mut f.spam),
            ("Archive folder", Role::Archive, &mut f.archive),
        ] {
            ui.label(RichText::new(app.tr(label)).color(t.muted));
            let detected = folders.iter().find(|(_, _, r, ..)| *r == role).map(|(p, ..)| p.clone());
            let auto = match &detected {
                Some(p) => format!("{} ({p})", app.tr("Automatic")),
                None => app.tr("Automatic").to_string(),
            };
            let shown = if value.is_empty() { auto.clone() } else { value.clone() };
            egui::ComboBox::from_id_salt(("special", label)).selected_text(shown).width(360.0).show_ui(ui, |ui| {
                ui.selectable_value(value, String::new(), auto);
                for p in &paths {
                    ui.selectable_value(value, p.clone(), p);
                }
            });
            ui.end_row();
        }
    });
    ui.add_space(6.0);
    ui.horizontal(|ui| {
        ui.label(RichText::new(app.tr("New folder")).color(t.muted));
        let mut name = app.settings.new_folder.clone();
        if ui.add(egui::TextEdit::singleline(&mut name).desired_width(240.0)).changed() {
            app.settings.new_folder = name.clone();
        }
        if ui.add_enabled(!name.trim().is_empty(), egui::Button::new(app.tr("Create"))).clicked() {
            app.run("mailbox.create", json!({"account": id, "name": name.trim()}));
            app.settings.new_folder.clear();
        }
    });
}

fn icon_for(role: Role) -> Icon {
    match role {
        Role::Inbox => Icon::Inbox,
        Role::Sent => Icon::Sent,
        Role::Drafts => Icon::Drafts,
        Role::Trash => Icon::Trash,
        Role::Spam => Icon::Spam,
        Role::Archive | Role::All => Icon::Archive,
        Role::Starred => Icon::Star,
        _ => Icon::Folder,
    }
}

/// The OAuth clients for Google and Microsoft 365: where to create them, the fields to paste
/// them in, the redirect to register.
fn oauth_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Sign in with Google or Microsoft 365 (OAuth)"));
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(16);
    frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.label(RichText::new(app.tr("Gmail and Microsoft 365 mailboxes sign in through the provider, like in Thunderbird or Outlook, instead of a password. Each provider asks the app for a client: create it once in the provider's console (free, a few minutes), paste its id and secret here, then choose the provider as sign-in in the account form.")).color(t.ui_text));
        for p in &oauth::PROVIDERS {
            ui.add_space(8.0);
            oauth_client_form(app, ui, t, p);
        }
    });
}

/// One provider's OAuth client: status, console link, note, the id and secret fields.
fn oauth_client_form(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens, p: &'static oauth::Provider) {
    let italian = app.italian();
    let configured = app.session.oauth_ready().iter().any(|r| r == p.id);
    ui.horizontal(|ui| {
        ui.label(RichText::new(p.name).strong().color(t.ui_text));
        ui.label(RichText::new(app.tr(if configured { "Configured" } else { "Not configured" })).small().color(if configured {
            t.accent
        } else {
            t.muted
        }));
        if ui.small_button(app.tr("Open the provider's console")).clicked() {
            app.requests.push(Request::OpenUrl(p.console.to_string()));
        }
    });
    ui.label(RichText::new(if italian { p.note_it } else { p.note }).small().color(t.muted));
    let server = app.session.server();
    let redirect = if server.is_empty() {
        "http://127.0.0.1 (any port: Desktop app client)".to_string()
    } else {
        format!("{}/api/oauth/callback", server.trim_end_matches('/'))
    };
    ui.label(RichText::new(app.language.fill("Redirect URI to register: {uri}", &[("uri", &redirect)])).small().color(t.muted));
    let (mut id, mut secret) = app.settings.oauth_clients.get(p.id).cloned().unwrap_or_default();
    let mut changed = false;
    egui::Grid::new(("oauth-client", p.id)).num_columns(2).spacing(vec2(8.0, 6.0)).show(ui, |ui| {
        ui.label(RichText::new(app.tr("Client ID")).color(t.muted));
        changed |= ui.add(egui::TextEdit::singleline(&mut id).desired_width(360.0)).changed();
        ui.end_row();
        ui.label(RichText::new(app.tr("Client secret")).color(t.muted));
        changed |= ui.add(egui::TextEdit::singleline(&mut secret).password(true).desired_width(360.0)).changed();
        ui.end_row();
    });
    if ui.add_enabled(!id.trim().is_empty(), egui::Button::new(app.tr("Save client"))).clicked() {
        app.run("oauth.configure", json!({"provider": p.id, "client_id": id.trim(), "client_secret": secret.trim()}));
        app.settings.oauth_clients.remove(p.id);
        app.settings.result = app.tr("Saved").to_string();
    } else if changed {
        app.settings.oauth_clients.insert(p.id.to_string(), (id.clone(), secret.clone()));
    }
}

fn server_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    let server = app.session.server();
    if server.is_empty() {
        return;
    }
    section(ui, t, app.tr("Web app server"));
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(16);
    frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.label(RichText::new(app.tr("The web app keeps your accounts on a mailcraft-cli server (your computer or your own machine). Start it with `mailcraft-cli serve`, then connect here.")).color(t.muted));
        ui.horizontal(|ui| {
            ui.label(RichText::new(app.tr("Server")).color(t.muted));
            let mut url = app.settings.server_draft.clone().unwrap_or(server.clone());
            if ui.add(egui::TextEdit::singleline(&mut url).desired_width(320.0)).changed() {
                app.settings.server_draft = Some(url.clone());
            }
            if ui.button(app.tr("Connect")).clicked() {
                let u = url.trim().to_string();
                app.session.set_server(&u);
                app.settings.server_draft = None;
            }
            match app.session.problem() {
                Some(p) => ui.label(RichText::new(app.language.tr_message(&p)).small().color(Color32::from_rgb(0xe5, 0x48, 0x4d))),
                None => ui.label(RichText::new(app.tr("Connected")).small().color(t.accent)),
            };
        });
    });
}

fn preferences_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Preferences"));
    let prefs = app.session.config().preferences.clone();
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(16);
    frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        egui::Grid::new("prefs").num_columns(2).spacing(vec2(12.0, 10.0)).min_col_width(240.0).show(ui, |ui| {
            ui.label(RichText::new(app.tr("Language")).color(t.muted));
            let current = if prefs.language.is_empty() {
                app.tr("System").to_string()
            } else {
                Language::parse(&prefs.language).map(|l| l.name().to_string()).unwrap_or_default()
            };
            egui::ComboBox::from_id_salt("lang").selected_text(current).show_ui(ui, |ui| {
                if ui.selectable_label(prefs.language.is_empty(), app.tr("System")).clicked() {
                    app.run("prefs.set", json!({"key": "language", "value": "system"}));
                    app.language = Language::system();
                }
                for l in Language::ALL {
                    if ui.selectable_label(prefs.language == l.code(), l.name()).clicked() {
                        app.run("prefs.set", json!({"language": l.code()}));
                        app.language = l;
                    }
                }
            });
            ui.end_row();
            ui.label(RichText::new(app.tr("Theme")).color(t.muted));
            let theme_label = match prefs.theme.as_str() {
                "dark" => app.tr("Dark"),
                "light" => app.tr("Light"),
                _ => app.tr("System"),
            };
            egui::ComboBox::from_id_salt("theme").selected_text(theme_label).show_ui(ui, |ui| {
                for (value, label) in [("system", "System"), ("light", "Light"), ("dark", "Dark")] {
                    if ui.selectable_label(prefs.theme == value, app.tr(label)).clicked() {
                        app.run("prefs.set", json!({"theme": value}));
                        app.dark = value == "dark";
                    }
                }
            });
            ui.end_row();
            ui.label(RichText::new(app.tr("Sync every (minutes, 0 = manual)")).color(t.muted));
            let mut minutes = prefs.sync_minutes;
            if ui.add(egui::DragValue::new(&mut minutes).range(0..=1440)).changed() {
                app.run("prefs.set", json!({"sync_minutes": minutes}));
            }
            ui.end_row();
            ui.label(RichText::new(app.tr("Messages per page")).color(t.muted));
            let mut page = prefs.page_size;
            if ui.add(egui::DragValue::new(&mut page).range(5..=500)).changed() {
                app.run("prefs.set", json!({"page_size": page}));
            }
            ui.end_row();
            ui.label(RichText::new(app.tr("Time zone")).color(t.muted));
            ui.horizontal(|ui| {
                let mut tz = app.settings.time_offset.clone().unwrap_or_else(|| prefs.time_offset.clone());
                if ui.add(egui::TextEdit::singleline(&mut tz).desired_width(90.0).hint_text("auto")).changed() {
                    app.settings.time_offset = Some(tz.clone());
                }
                if app.settings.time_offset.is_some() && ui.button(app.tr("Apply")).clicked() {
                    let v = app.settings.time_offset.take().unwrap_or_default();
                    app.run("prefs.set", json!({"time_offset": v}));
                }
                let effective = mailcraft_engine::core::message::time::offset_text(app.tz());
                let detected = mailcraft_engine::core::message::time::offset_text(mailcraft_engine::core::message::time::host_offset_minutes());
                ui.label(
                    RichText::new(app.language.fill(
                        "Shown: UTC{effective} (auto = this device's zone, now UTC{detected})",
                        &[("effective", &effective), ("detected", &detected)],
                    ))
                    .small()
                    .color(t.muted),
                );
            });
            ui.end_row();
            ui.label(RichText::new(app.tr("Default account")).color(t.muted));
            let names: Vec<(String, String)> = app.session.config().accounts.iter().map(|a| (a.id.clone(), a.name.clone())).collect();
            let current = names.iter().find(|(id, _)| *id == prefs.default_account).map(|(_, n)| n.clone()).unwrap_or_else(|| "—".into());
            egui::ComboBox::from_id_salt("default-account").selected_text(current).show_ui(ui, |ui| {
                for (id, name) in names {
                    if ui.selectable_label(prefs.default_account == id, name).clicked() {
                        app.run("prefs.set", json!({"default_account": id}));
                    }
                }
            });
            ui.end_row();
        });
        for (key, label, value) in [
            ("reading_pane", "Reading pane beside the list", prefs.reading_pane),
            ("conversations", "Group messages into conversations", prefs.conversations),
            ("mark_read_on_open", "Mark messages read when opened", prefs.mark_read_on_open),
            ("assist_suggestions", "Reply suggestions from the assistant under each message", prefs.assist_suggestions),
            (
                "assist_cloud",
                "Let the assistant use cloud models too (otherwise local models only: your mail never leaves the network)",
                prefs.assist_cloud,
            ),
        ] {
            let mut v = value;
            if ui.checkbox(&mut v, app.tr(label)).changed() {
                app.run("prefs.set", json!({key: v}));
            }
        }
    });
}

fn assistant_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Assistant (MCP)"));
    let frame = egui::Frame::NONE.fill(t.card).stroke(Stroke::new(1.0, t.border)).corner_radius(10).inner_margin(16);
    frame.show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.horizontal(|ui| {
            let (rect, _) = ui.allocate_exact_size(vec2(40.0, 40.0), egui::Sense::hover());
            ui.painter().rect_filled(rect, 10.0, t.active);
            icons::paint(ui.painter(), rect.shrink(9.0), Icon::Robot, t.accent);
            ui.vertical(|ui| {
                ui.set_width(ui.available_width() - 20.0);
                ui.label(RichText::new(app.tr("Configure MailCraft in plain language: connect the MCP server to Claude Code, Claude Desktop or any MCP client and say, for example, “set up my Aruba mailbox info@example.it” or “dark theme, sync every 2 minutes”. The assistant can also read, triage and answer your mail.")).color(t.ui_text));
                ui.add_space(6.0);
                ui.label(RichText::new(app.tr("Command for Claude Code:")).small().color(t.muted));
                let cmd = "claude mcp add mailcraft -- mailcraft-cli mcp";
                let (rect, _) = ui.allocate_exact_size(vec2(ui.available_width().min(420.0), 30.0), egui::Sense::hover());
                ui.painter().rect_filled(rect, 6.0, t.panel);
                ui.painter().rect_stroke(rect, 6.0, Stroke::new(1.0, t.border), StrokeKind::Inside);
                ui.painter().text(rect.left_center() + vec2(10.0, 0.0), egui::Align2::LEFT_CENTER, cmd, egui::FontId::monospace(13.0), t.ui_text);
            });
        });
    });
}

fn shortcuts_section(app: &mut MailApp, ui: &mut egui::Ui, t: &Tokens) {
    section(ui, t, app.tr("Keyboard shortcuts"));
    ui.horizontal(|ui| {
        let (r, _) = ui.allocate_exact_size(vec2(18.0, 18.0), egui::Sense::hover());
        icons::paint(ui.painter(), r, Icon::Keyboard, t.muted);
        ui.label(RichText::new(app.tr("c compose · / search · j/k move · Enter open · e archive · # trash · s star · u unread · r reply · a reply all · f forward · Cmd+R sync · Esc close")).small().color(t.muted));
    });
    ui.add_space(20.0);
}
