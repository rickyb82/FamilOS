#!/usr/bin/env python3
"""Piccola patch alle app web ArtCraft (apps/<app>-web) per integrarle in FamilAI.

Uso: familai-web-patch.py <cartella-repo> <app>      (app: wordcraft | gridcraft | pdfcraft | deckcraft | ideacraft)

Aggiunge tre parametri all'URL della pagina:
  ?open=<url>&name=<file>   apre il documento scaricandolo dal server (con i cookie di sessione)
  ?save=<url>               File > Salva / Esporta spediscono il file al server con una POST invece di scaricarlo
  ?lang=<codice>            lingua dell'interfaccia all'avvio (es. it), al posto di quella del browser
Per GridCraft aggiunge anche l'interfaccia italiana (gridcraft-it.rs) se il sorgente non la contiene ancora.
Senza questi parametri l'app si comporta come l'originale. Eseguito dal Dockerfile.web sulla copia del repository;
si ferma con errore se il sorgente e' cambiato e gli ancoraggi non si trovano piu' (aggiornare gli ancoraggi qui).
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
app = sys.argv[2] if len(sys.argv) > 2 else "wordcraft"
web_rs = root / f"apps/{app}-web/src/web.rs"
cargo = root / f"apps/{app}-web/Cargo.toml"
UI_CRATE = f"{app}_ui_egui"

COMMON = r'''
    thread_local! {
        static HOST_REVISIONS: std::cell::RefCell<std::collections::HashMap<String, String>> =
            std::cell::RefCell::new(std::collections::HashMap::new());
    }
    fn param(key: &str) -> Option<String> {
        let search = web_sys::window().and_then(|w| w.location().search().ok())?;
        let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
        params.get(key).filter(|v| !v.is_empty())
    }

    pub fn save_url() -> Option<String> {
        param("save")
    }

    pub fn lang() -> Option<String> {
        param("lang")
    }

    pub fn open_from_query(inbox: Inbox, ctx: egui::Context) {
        let Some(url) = param("open") else {
            return;
        };
        let name = param("name").unwrap_or_else(|| url.rsplit('/').next().unwrap_or("document").to_string());
        wasm_bindgen_futures::spawn_local(async move {
            match fetch_bytes(&url).await {
                Ok(bytes) => {
                    inbox.lock().unwrap_or_else(|e| e.into_inner()).push((name, bytes));
                    ctx.request_repaint();
                }
                Err(e) => log::error!("FamilAI: couldn't open {url}: {e}"),
            }
        });
    }

    async fn fetch_bytes(url: &str) -> Result<Vec<u8>, String> {
        let js = |e: wasm_bindgen::JsValue| format!("{e:?}");
        let window = web_sys::window().ok_or("no window")?;
        let opts = web_sys::RequestInit::new();
        opts.set_credentials(web_sys::RequestCredentials::Include);
        let resp = wasm_bindgen_futures::JsFuture::from(window.fetch_with_str_and_init(url, &opts)).await.map_err(js)?;
        let resp: web_sys::Response = resp.dyn_into().map_err(|_| "not a Response".to_string())?;
        if !resp.ok() {
            return Err(format!("HTTP {}", resp.status()));
        }
        let buf = wasm_bindgen_futures::JsFuture::from(resp.array_buffer().map_err(js)?).await.map_err(js)?;
        Ok(js_sys::Uint8Array::new(&buf).to_vec())
    }

    /// Tells the FamilAI page around the iframe how a save went (`{type: "familai:saved"|"familai:save_failed", name, error}`).
    fn notify(kind: &str, name: &str, error: Option<&str>) {
        let Some(window) = web_sys::window() else { return };
        let Some(parent) = window.parent().ok().flatten() else { return };
        let Ok(origin) = window.location().origin() else { return };
        let m = js_sys::Object::new();
        let _ = js_sys::Reflect::set(&m, &"type".into(), &kind.into());
        let _ = js_sys::Reflect::set(&m, &"name".into(), &name.into());
        if let Some(e) = error {
            let _ = js_sys::Reflect::set(&m, &"error".into(), &e.into());
        }
        let _ = parent.post_message(&m, &origin);
    }

    async fn post_bytes(url: &str, name: &str, bytes: &[u8]) -> Result<(), String> {
        let js = |e: wasm_bindgen::JsValue| format!("{e:?}");
        let window = web_sys::window().ok_or("no window")?;
        let headers = web_sys::Headers::new().map_err(js)?;
        headers.set("Content-Type", "application/octet-stream").map_err(js)?;
        // FamilAI accetta scritture solo con questo header (protezione CSRF)
        headers.set("X-Requested-With", "FamilAI").map_err(js)?;
        let encoded = js_sys::encode_uri_component(name).as_string().unwrap_or_default();
        headers.set("X-Filename", &encoded).map_err(js)?;
        let revision = HOST_REVISIONS.with(|r| r.borrow().get(url).cloned())
            .or_else(|| param("revision")).unwrap_or_else(|| "new".to_string());
        headers.set("If-Match", &revision).map_err(js)?;
        let opts = web_sys::RequestInit::new();
        opts.set_method("POST");
        opts.set_credentials(web_sys::RequestCredentials::Include);
        opts.set_headers(&headers);
        opts.set_body(&js_sys::Uint8Array::from(bytes));
        let resp = wasm_bindgen_futures::JsFuture::from(window.fetch_with_str_and_init(url, &opts)).await.map_err(js)?;
        let resp: web_sys::Response = resp.dyn_into().map_err(|_| "not a Response".to_string())?;
        if !resp.ok() {
            let status = resp.status();
            let body = match resp.text() {
                Ok(p) => wasm_bindgen_futures::JsFuture::from(p).await.ok().and_then(|v| v.as_string()).unwrap_or_default(),
                Err(_) => String::new(),
            };
            return Err(format!("HTTP {status} {}", body.chars().take(200).collect::<String>()));
        }
        let body = wasm_bindgen_futures::JsFuture::from(resp.text().map_err(js)?).await.map_err(js)?;
        if let Some(text) = body.as_string() {
            if let Ok(data) = js_sys::JSON::parse(&text) {
                // Exported copies have a new ID: they must not advance the source revision.
                let id = js_sys::Reflect::get(&data, &"id".into()).ok().and_then(|v| v.as_string());
                let source = web_sys::UrlSearchParams::new_with_str(url.split('?').nth(1).unwrap_or("")).ok()
                    .and_then(|q| q.get("file_id"));
                if id == source {
                    if let Some(sha) = js_sys::Reflect::get(&data, &"sha256".into()).ok().and_then(|v| v.as_string()) {
                        HOST_REVISIONS.with(|r| { r.borrow_mut().insert(url.to_string(), sha); });
                    }
                }
            }
        }
        Ok(())
    }
}
'''

# WordCraft tiene il conto dei salvataggi in corso (PickerSaves); GridCraft no.
SAVE_WORDCRAFT = r'''
    pub fn save_remote(saves: &Rc<PickerSaves>, ctx: &egui::Context, url: &str, name: &str, bytes: &[u8]) -> Result<(), String> {
        let saves = saves.clone();
        let ctx = ctx.clone();
        let url = url.to_string();
        let name = name.to_string();
        let bytes = bytes.to_vec();
        saves.pending.set(saves.pending.get().saturating_add(1));
        wasm_bindgen_futures::spawn_local(async move {
            match post_bytes(&url, &name, &bytes).await {
                Ok(()) => {
                    log::info!("FamilAI: saved {name}");
                    notify("familai:saved", &name, None);
                }
                Err(e) => {
                    log::error!("FamilAI: couldn't save {name}: {e}");
                    notify("familai:save_failed", &name, Some(&e));
                    saves.unsaved.borrow_mut().push((name, Some(e)));
                }
            }
            saves.pending.set(saves.pending.get().saturating_sub(1));
            ctx.request_repaint();
        });
        Ok(())
    }
'''
SAVE_SIMPLE = r'''
    pub fn save_remote(url: &str, name: &str, bytes: &[u8]) -> Result<(), String> {
        let url = url.to_string();
        let name = name.to_string();
        let bytes = bytes.to_vec();
        wasm_bindgen_futures::spawn_local(async move {
            match post_bytes(&url, &name, &bytes).await {
                Ok(()) => {
                    log::info!("FamilAI: saved {name}");
                    notify("familai:saved", &name, None);
                }
                Err(e) => {
                    log::error!("FamilAI: couldn't save {name}: {e}");
                    notify("familai:save_failed", &name, Some(&e));
                }
            }
        });
        Ok(())
    }
'''

HEAD_WORDCRAFT = '''

/// Integrazione FamilAI: `?open=<url>&name=<file>` apre un documento preso dal server;
/// `?save=<url>` fa si' che Salva/Esporta spediscano il file al server (POST) invece di scaricarlo.
mod familai {
    use std::rc::Rc;

    use wasm_bindgen::JsCast as _;
    use wordcraft_ui_egui::Inbox;

    use super::PickerSaves;
'''
HEAD_SIMPLE = f'''

/// Integrazione FamilAI: `?open=<url>&name=<file>` apre un file preso dal server;
/// `?save=<url>` fa si' che Salva/Esporta spediscano il file al server (POST) invece di scaricarlo.
mod familai {{
    use wasm_bindgen::JsCast as _;
    use {UI_CRATE}::Inbox;
'''

FEATURES = ', "UrlSearchParams", "RequestInit", "RequestCredentials", "Response", "Headers"]'


def patch(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    if new in s:
        print(f"  = {label}: gia' applicata")
        return
    if old not in s:
        sys.exit(f"!! {label}: ancoraggio non trovato in {path} ({app} e' cambiato: aggiornare la patch)")
    path.write_text(s.replace(old, new, 1))
    print(f"  + {label}")


def italian(root: Path) -> None:
    """GridCraft: interfaccia italiana (Language::It) se il repository non la contiene gia'."""
    i18n = root / "crates/ui-egui/src/i18n.rs"
    s = i18n.read_text()
    if "mod it;" in s:
        print("  = gridcraft: italiano gia' presente nel sorgente")
        return
    src = Path(__file__).with_name("gridcraft-it.rs")
    (root / "crates/ui-egui/src/i18n/it.rs").write_text(src.read_text())
    edits = [
        ("mod fr;\nmod ko;", "mod fr;\nmod it;\nmod ko;"),
        ("    Cs,\n    Fr,\n}", "    Cs,\n    Fr,\n    It,\n}"),
        ("pub const ALL: [Self; 10] = [Self::En, Self::Es, Self::Zh, Self::Ja, Self::Ko, Self::Ru, Self::PtBr, Self::De, Self::Cs, Self::Fr];",
         "pub const ALL: [Self; 11] = [Self::En, Self::Es, Self::Zh, Self::Ja, Self::Ko, Self::Ru, Self::PtBr, Self::De, Self::Cs, Self::Fr, Self::It];"),
        ('            Self::Fr => "Français",\n        }', '            Self::Fr => "Français",\n            Self::It => "Italiano",\n        }'),
        ('            Self::Fr => "fr",\n        }', '            Self::Fr => "fr",\n            Self::It => "it",\n        }'),
        ('            "fr" => Some(Self::Fr),\n            _ => None,', '            "fr" => Some(Self::Fr),\n            "it" => Some(Self::It),\n            _ => None,'),
        ("            Self::Fr => fr::translate(text),\n        };", "            Self::Fr => fr::translate(text),\n            Self::It => it::translate(text),\n        };"),
    ]
    for old, new in edits:
        if old not in s:
            sys.exit(f"!! gridcraft italiano: ancoraggio non trovato in i18n.rs: {old[:50]!r}")
        s = s.replace(old, new, 1)
    i18n.write_text(s)
    theme = root / "crates/ui-egui/src/theme.rs"
    t = theme.read_text()
    theme.write_text(t.replace("Language::De, Language::Cs] {", "Language::De, Language::Cs, Language::It] {", 1))
    print("  + gridcraft: interfaccia italiana (it.rs + Language::It)")


def italian_deckcraft(root: Path) -> None:
    """DeckCraft non ha un catalogo di traduzioni: le etichette sono letterali nel codice. Qui si aggiunge un modulo
    `familai_it::tr()` (inglese -> italiano, da deckcraft-it.json) e lo si applica nei punti in cui le etichette vengono
    disegnate (pulsanti della barra, schede, menu, label/pulsanti/tooltip con testo letterale). Gli id dei comandi e i
    confronti nel codice restano in inglese: cambia solo cio' che si vede."""
    import json
    import re

    ui = root / "crates/ui-egui/src"
    mod = ui / "familai_it.rs"
    if mod.exists():
        print("  = deckcraft: italiano gia' applicato")
        return
    table = json.loads(Path(__file__).with_name("deckcraft-it.json").read_text(encoding="utf-8"))
    # nel JSON gli a capo dei pulsanti grandi sono scritti come \\n: qui diventano veri a capo, come nel sorgente Rust
    table = {k.replace("\\n", "\n"): v.replace("\\n", "\n") for k, v in table.items() if not k.startswith("_") and v and v != k}

    def lit(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    arms = "\n".join(f"        {lit(k)} => {lit(v)}," for k, v in sorted(table.items()))
    mod.write_text(
        "//! FamilAI: Italian interface labels, applied where labels are drawn. Command ids stay English.\n\n"
        "/// The Italian label for an English one; unknown text comes back unchanged.\n"
        "pub fn tr(s: &str) -> &str {\n    match s {\n" + arms + "\n        _ => s,\n    }\n}\n",
        encoding="utf-8")
    lib_rs = ui / "lib.rs"
    patch(lib_rs,
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\n",
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\npub mod familai_it;\n",
          "lib.rs: mod familai_it")
    widgets = ui / "widgets.rs"
    for sig in ("pub fn big_button_ex(ui: &mut Ui, icon: Icon, label: &str, enabled: bool, split: bool, selected: bool) -> (Response, Option<Response>) {\n",
                "pub fn small_button_ex(ui: &mut Ui, icon: Icon, label: &str, enabled: bool, selected: bool, split: bool) -> (Response, Option<Response>) {\n"):
        patch(widgets, sig, sig + "    let label = crate::familai_it::tr(label);\n", "widgets.rs: etichette pulsanti")
    patch(widgets,
          "pub fn icon_toggle(ui: &mut Ui, icon: Icon, tip: &str, enabled: bool, on: bool) -> Response {\n",
          "pub fn icon_toggle(ui: &mut Ui, icon: Icon, tip: &str, enabled: bool, on: bool) -> Response {\n    let tip = crate::familai_it::tr(tip);\n",
          "widgets.rs: tooltip interruttori")
    ribbon = ui / "ribbon.rs"
    patch(ribbon,
          "fn tip(id: &str, label: &str) -> String {\n",
          "fn tip(id: &str, label: &str) -> String {\n    let label = crate::familai_it::tr(label);\n",
          "ribbon.rs: tooltip")
    patch(ribbon,
          "            let w = ui.fonts_mut(|f| f.layout_no_wrap(name.to_string(), font.clone(), t.text).size().x) + 18.0;\n",
          "            let w = ui.fonts_mut(|f| f.layout_no_wrap(crate::familai_it::tr(name).to_string(), font.clone(), t.text).size().x) + 18.0;\n",
          "ribbon.rs: larghezza schede")
    patch(ribbon,
          "            ui.painter().text(r.center(), Align2::CENTER_CENTER, name, f, col);\n",
          "            ui.painter().text(r.center(), Align2::CENTER_CENTER, crate::familai_it::tr(name), f, col);\n",
          "ribbon.rs: nomi schede")
    # label/pulsanti/tooltip con testo letterale: ui.label("Testo") -> ui.label(crate::familai_it::tr("Testo"))
    one = re.compile(r'(\.(?:label|heading|small|strong|button|small_button|collapsing|on_hover_text|hint_text)\()("(?:[^"\\]|\\.)*")')
    two = re.compile(r'(\.(?:selectable_label|checkbox)\([^,()\n]*,\s*)("(?:[^"\\]|\\.)*")')
    other = re.compile(r'(\b(?:set_status|buttons\(ui,\s*|egui::Window::new)\(?)("(?:[^"\\]|\\.)*")')
    total = 0

    def wrap(m):
        nonlocal total
        raw = m.group(2)
        key = raw[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        if key not in table:
            return m.group(0)
        total += 1
        return f"{m.group(1)}crate::familai_it::tr({raw})"

    for f in sorted(ui.glob("*.rs")):
        if f.name in ("familai_it.rs", "icons.rs", "textures.rs", "control.rs"):
            continue
        s = f.read_text(encoding="utf-8")
        s2 = other.sub(wrap, two.sub(wrap, one.sub(wrap, s)))
        if s2 != s:
            f.write_text(s2, encoding="utf-8")
    print(f"  + deckcraft: interfaccia italiana ({len(table)} etichette, {total} chiamate riscritte)")


print(f"Patch FamilAI per {app} web in {root}")
if app == "wordcraft":
    patch(cargo, '"MediaQueryList"]', '"MediaQueryList"' + FEATURES, "Cargo.toml: feature web-sys")
    patch(web_rs,
          "                    if let Err(e) = listen_for_pasted_pictures(inbox.clone(), cc.egui_ctx.clone()) {\n",
          "                    familai::open_from_query(inbox.clone(), cc.egui_ctx.clone());\n"
          "                    if let Err(e) = listen_for_pasted_pictures(inbox.clone(), cc.egui_ctx.clone()) {\n",
          "web.rs: ?open=")
    patch(web_rs,
          '    let window = web_sys::window().ok_or("no window")?;\n\n    // On browsers supporting the File System Access API',
          '    let window = web_sys::window().ok_or("no window")?;\n'
          '    if let Some(save_url) = familai::save_url() {\n'
          '        return familai::save_remote(saves, ctx, &save_url, &name, bytes);\n'
          '    }\n\n    // On browsers supporting the File System Access API',
          "web.rs: ?save=")
    patch(web_rs,
          "                    app.autosave = false;\n",
          "                    app.autosave = false;\n"
          "                    if let Some(lang) = familai::lang() {\n"
          "                        app.ui.language = lang;\n"
          "                    }\n",
          "web.rs: ?lang=")
    patch(web_rs,
          "                    app.autosave = false;\n",
          "                    // FamilAI: con ?save= il salvataggio va al server, quindi AutoSave puo' restare acceso\n"
          "                    let host_save = familai::save_url().is_some();\n"
          "                    wordcraft_ui_egui::FAMILAI_HOST_SAVE.store(host_save, std::sync::atomic::Ordering::Relaxed);\n"
          "                    app.autosave = host_save;\n",
          "web.rs: AutoSave con ?save=")
    patch(web_rs, "\nfn mime_for(", HEAD_WORDCRAFT + SAVE_WORDCRAFT + COMMON + "\nfn mime_for(", "web.rs: modulo familai")
    lib_rs = root / "crates/ui-egui/src/lib.rs"
    patch(lib_rs,
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\n",
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\n\n"
          "/// FamilAI: true when the page saves to a server (`?save=`), so AutoSave is allowed in the browser.\n"
          "pub static FAMILAI_HOST_SAVE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);\n",
          "lib.rs: flag FAMILAI_HOST_SAVE")
    patch(lib_rs,
          "        if self.services.download.is_some() {\n            return Some(AutoSaveBlock::Browser);\n        }\n",
          "        let host_save = FAMILAI_HOST_SAVE.load(std::sync::atomic::Ordering::Relaxed);\n"
          "        if self.services.download.is_some() && !host_save {\n            return Some(AutoSaveBlock::Browser);\n        }\n",
          "lib.rs: AutoSave nel browser con host save")
    patch(lib_rs,
          '            if let Err(e) = self.session.run("file.save", &json!({})) {\n                log::warn!("AutoSave failed: {e}");',
          '            // FamilAI: through WordApp::run, so in the browser the save goes to the host (`?save=`) like a manual one\n'
          '            let autosave_result = if FAMILAI_HOST_SAVE.load(std::sync::atomic::Ordering::Relaxed) {\n'
          '                self.run("file.save", json!({})).map(|_| ())\n'
          '            } else {\n'
          '                self.session.run("file.save", &json!({})).map(|_| ()).map_err(|e| e.to_string())\n'
          '            };\n'
          '            if let Err(e) = autosave_result {\n                log::warn!("AutoSave failed: {e}");',
          "lib.rs: AutoSave via WordApp::run")
    patch(lib_rs,
          "        if !self.saved_here() {\n            return Some(AutoSaveBlock::NotSavedHere);\n        }\n        None\n",
          "        if !self.saved_here() && !host_save {\n            return Some(AutoSaveBlock::NotSavedHere);\n        }\n        None\n",
          "lib.rs: AutoSave anche prima del primo salvataggio manuale")
elif app == "gridcraft":
    patch(cargo, '"ClipboardItem", "Navigator"]', '"ClipboardItem", "Navigator"' + FEATURES, "Cargo.toml: feature web-sys")
    patch(web_rs,
          "                    let inbox: Inbox = Inbox::default();\n                    let mut session = Session::new();\n",
          "                    let inbox: Inbox = Inbox::default();\n"
          "                    familai::open_from_query(inbox.clone(), cc.egui_ctx.clone());\n"
          "                    let mut session = Session::new();\n",
          "web.rs: ?open=")
    patch(web_rs,
          'fn download(name: &str, bytes: &[u8]) -> Result<(), String> {\n'
          '    let js = |e: wasm_bindgen::JsValue| format!("{e:?}");\n'
          '    let window = web_sys::window().ok_or("no window")?;\n',
          'fn download(name: &str, bytes: &[u8]) -> Result<(), String> {\n'
          '    if let Some(save_url) = familai::save_url() {\n'
          '        return familai::save_remote(&save_url, name, bytes);\n'
          '    }\n'
          '    let js = |e: wasm_bindgen::JsValue| format!("{e:?}");\n'
          '    let window = web_sys::window().ok_or("no window")?;\n',
          "web.rs: ?save=")
    patch(web_rs,
          "                    SheetApp::setup_context_for_language(&cc.egui_ctx, false, app.ui.language);\n",
          "                    if let Some(lang) = familai::lang().and_then(|c| gridcraft_ui_egui::i18n::Language::parse(&c)) {\n"
          "                        app.ui.language = lang;\n"
          "                    }\n"
          "                    SheetApp::setup_context_for_language(&cc.egui_ctx, false, app.ui.language);\n",
          "web.rs: ?lang=")
    patch(web_rs, "\nfn mime_for(", HEAD_SIMPLE + SAVE_SIMPLE + COMMON + "\nfn mime_for(", "web.rs: modulo familai")
    italian(root)
elif app == "pdfcraft":
    # PdfCraft ha gia' tutto: ?file=<url> apre dal server e ?host=parent spedisce i salvataggi alla pagina che lo
    # incornicia via postMessage (pdfcraft:save); FamilAI li riceve nella pagina Editor. Nessuna patch.
    print("  = pdfcraft: ?file= e ?host=parent sono nativi")
    patch(root / "apps/pdfcraft-web/src/main.rs",
          "                    // `?file=<url>` opens a PDF from a URL (same-origin or CORS-enabled).\n",
          "                    // FamilAI: `?lang=<code>` picks the interface language instead of the browser's.\n"
          "                    if let Some(lang) = query_param(\"lang\").filter(|l| !l.is_empty()) {\n"
          "                        app.language = lang;\n"
          "                    }\n"
          "                    // `?file=<url>` opens a PDF from a URL (same-origin or CORS-enabled).\n",
          "main.rs: ?lang=")
elif app == "deckcraft":
    patch(cargo, '"CssStyleDeclaration"]', '"CssStyleDeclaration"' + FEATURES, "Cargo.toml: feature web-sys")
    patch(web_rs,
          "                    let inbox: Inbox = Inbox::default();\n"
          "                    let mut app = SlideApp::new(Session::new(), services(inbox.clone(), cc.egui_ctx.clone()));\n"
          "                    let q = query();\n"
          "                    if !q.contains(\"blank\") {\n",
          "                    let inbox: Inbox = Inbox::default();\n"
          "                    let mut app = SlideApp::new(Session::new(), services(inbox.clone(), cc.egui_ctx.clone()));\n"
          "                    // FamilAI: ?open= apre dal server (niente deck di esempio); i .pptx si risalvano come .pptx\n"
          "                    familai::open_from_query(inbox.clone(), cc.egui_ctx.clone());\n"
          "                    deckcraft_ui_egui::FAMILAI_SAVE_PPTX.store(familai::save_as_pptx(), std::sync::atomic::Ordering::Relaxed);\n"
          "                    let q = query();\n"
          "                    if !q.contains(\"blank\") && !q.contains(\"open=\") {\n",
          "web.rs: ?open=")
    patch(web_rs,
          "    let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| \"deckcraft\".into());\n"
          "    let window = web_sys::window().ok_or(\"no window\")?;\n",
          "    let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| \"deckcraft\".into());\n"
          "    if let Some(save_url) = familai::save_url() {\n"
          "        return familai::save_remote(&save_url, &name, bytes);\n"
          "    }\n"
          "    let window = web_sys::window().ok_or(\"no window\")?;\n",
          "web.rs: ?save=")
    head_deck = HEAD_SIMPLE + (
        "\n    /// `?name=` ends with .pptx: Save keeps the PowerPoint format instead of DeckCraft's own.\n"
        "    pub fn save_as_pptx() -> bool {\n"
        "        param(\"name\").is_some_and(|n| n.to_ascii_lowercase().ends_with(\".pptx\"))\n"
        "    }\n")
    patch(web_rs, "\nfn mime_for(", head_deck + SAVE_SIMPLE + COMMON + "\nfn mime_for(", "web.rs: modulo familai")
    lib_rs = root / "crates/ui-egui/src/lib.rs"
    patch(lib_rs,
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\n",
          "pub type Inbox = std::sync::Arc<std::sync::Mutex<Vec<(String, Vec<u8>)>>>;\n\n"
          "/// FamilAI: Save in the browser keeps the .pptx format when the file opened was a PowerPoint.\n"
          "pub static FAMILAI_SAVE_PPTX: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);\n",
          "lib.rs: flag FAMILAI_SAVE_PPTX")
    patch(lib_rs,
          "        } else {\n            self.save_as_dialog(\"deckcraft\");\n        }\n",
          "        } else {\n            self.save_as_dialog(if FAMILAI_SAVE_PPTX.load(std::sync::atomic::Ordering::Relaxed) { \"pptx\" } else { \"deckcraft\" });\n        }\n",
          "lib.rs: Salva in .pptx")
    italian_deckcraft(root)
elif app == "ideacraft":
    main_rs = root / "apps/ideacraft-web/src/main.rs"
    patch(cargo, '"UrlSearchParams", "Navigator"]', '"UrlSearchParams", "Navigator", "RequestInit", "RequestCredentials", "Response", "Headers"]',
          "Cargo.toml: feature web-sys")
    patch(main_rs,
          "            if let Some(id) = query_param(\"template\") {\n",
          "            if query_param(\"open\").is_some() {\n"
          "                // FamilAI: il file arriva da ?open=, niente mappa salvata nel browser ne' galleria dei modelli\n"
          "                app.new_map();\n"
          "            } else if let Some(id) = query_param(\"template\") {\n",
          "main.rs: ?open= (niente galleria)")
    patch(main_rs,
          "            Self { app, inbox: Arc::default() }\n",
          "            let inbox: Inbox = Arc::default();\n"
          "            familai::open_from_query(inbox.clone(), cc.egui_ctx.clone());\n"
          "            Self { app, inbox }\n",
          "main.rs: ?open=")
    patch(main_rs,
          "    fn download(name: &str, text: &str, mime: &str) {\n",
          "    fn download(name: &str, text: &str, mime: &str) {\n"
          "        if let Some(save_url) = familai::save_url() {\n"
          "            let _ = familai::save_remote(&save_url, name, text.as_bytes());\n"
          "            return;\n"
          "        }\n",
          "main.rs: ?save=")
    head_idea = (
        "\n    /// Integrazione FamilAI: `?open=<url>&name=<file>` apre una mappa presa dal server;\n"
        "    /// `?save=<url>` fa si' che Salva/Esporta spediscano il file al server (POST) invece di scaricarlo.\n"
        "    mod familai {\n"
        "        use eframe::wasm_bindgen;\n"
        "        use wasm_bindgen::JsCast as _;\n\n"
        "        use super::Inbox;\n\n"
        "        mod log {\n"
        "            macro_rules! error { ($($t:tt)*) => { eframe::web_sys::console::error_1(&format!($($t)*).into()) } }\n"
        "            macro_rules! info { ($($t:tt)*) => { eframe::web_sys::console::log_1(&format!($($t)*).into()) } }\n"
        "            pub(crate) use {error, info};\n"
        "        }\n")
    patch(main_rs, "\n    pub fn start() {\n", head_idea + SAVE_SIMPLE + COMMON + "\n    pub fn start() {\n", "main.rs: modulo familai")
else:
    sys.exit(f"!! app sconosciuta: {app}")
print("Patch completata.")
