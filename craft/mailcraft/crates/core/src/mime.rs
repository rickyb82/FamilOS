//! MIME: parsing fetched messages (via `mail-parser`) and building outgoing ones.

use mail_parser::MimeHeaders;

use crate::message::{Address, Attachment, Draft, DraftAttachment, Envelope, Flags, Message, snippet, time};
use crate::{Error, Result, clean, clip, limits};

fn addresses(list: Option<&mail_parser::Address<'_>>) -> Vec<Address> {
    let Some(list) = list else { return Vec::new() };
    list.iter()
        .filter_map(|a| {
            let email = a.address().unwrap_or_default();
            (!email.is_empty()).then(|| Address::new(a.name().unwrap_or_default(), email))
        })
        .take(limits::MAX_RECIPIENTS)
        .collect()
}

fn id_list(v: &mail_parser::HeaderValue<'_>) -> Vec<String> {
    match v {
        mail_parser::HeaderValue::Text(t) => vec![normalise_id(t)],
        mail_parser::HeaderValue::TextList(l) => l.iter().map(|t| normalise_id(t)).collect(),
        _ => Vec::new(),
    }
}

fn normalise_id(id: &str) -> String {
    let t = id.trim();
    if t.is_empty() {
        return String::new();
    }
    let t = t.trim_start_matches('<').trim_end_matches('>');
    clean(&format!("<{t}>"), 500)
}

/// Parses raw RFC 822 bytes into a full message. `uid` and `mailbox` are filled in by the caller.
pub fn parse(raw: &[u8]) -> Result<Message> {
    let raw = raw.get(..raw.len().min(limits::MAX_RAW_MESSAGE)).unwrap_or_default();
    let m = mail_parser::MessageParser::default().parse(raw).ok_or_else(|| Error::Message("unparseable message".into()))?;
    let mut envelope = envelope_of(&m, raw.len());
    let text = m.body_text(0).map(|t| clip(&t, limits::MAX_BODY).to_string()).unwrap_or_default();
    let html = m.body_html(0).map(|t| clip(&t, limits::MAX_BODY).to_string()).unwrap_or_default();
    let mut attachments = Vec::new();
    for (index, part) in m.attachments().enumerate().take(limits::MAX_ATTACHMENTS) {
        let mime = part
            .content_type()
            .map(|c| match c.subtype() {
                Some(s) => format!("{}/{}", c.ctype(), s),
                None => c.ctype().to_string(),
            })
            .unwrap_or_else(|| "application/octet-stream".to_string())
            .to_ascii_lowercase();
        let inline = part.content_disposition().is_some_and(|d| d.ctype().eq_ignore_ascii_case("inline")) && part.content_id().is_some();
        let name = part.attachment_name().map(|n| clean(n, 255)).filter(|n| !n.is_empty()).unwrap_or_else(|| format!("attachment-{}", index + 1));
        attachments.push(Attachment { index, name, mime, size: part.contents().len(), inline });
    }
    envelope.has_attachments = attachments.iter().any(|a| !a.inline);
    if envelope.snippet.is_empty() {
        let plain = if text.trim().is_empty() { html_to_text(&html) } else { text.clone() };
        envelope.snippet = snippet(&plain);
    }
    let reply_to = addresses(m.reply_to());
    let list_unsubscribe = m.header_raw("List-Unsubscribe").map(|v| clean(v.trim(), 2_000)).unwrap_or_default();
    Ok(Message { envelope, text, html, attachments, reply_to, list_unsubscribe })
}

/// The bytes of every non-inline attachment of a raw message, for forwarding and saving.
pub fn attachments(raw: &[u8]) -> Vec<DraftAttachment> {
    let Some(m) = mail_parser::MessageParser::default().parse(raw) else { return Vec::new() };
    m.attachments()
        .enumerate()
        .take(limits::MAX_ATTACHMENTS)
        .map(|(i, part)| DraftAttachment {
            name: part.attachment_name().map(|n| clean(n, 255)).filter(|n| !n.is_empty()).unwrap_or_else(|| format!("attachment-{}", i + 1)),
            mime: part
                .content_type()
                .map(|c| match c.subtype() {
                    Some(s) => format!("{}/{}", c.ctype(), s),
                    None => c.ctype().to_string(),
                })
                .unwrap_or_else(|| "application/octet-stream".into()),
            data: part.contents().to_vec(),
            cid: String::new(),
        })
        .collect()
}

/// Escapes text for HTML and turns lines into `<br>` (links become anchors).
pub fn text_to_html(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 64);
    for (i, line) in text.lines().take(20_000).enumerate() {
        if i > 0 {
            out.push_str("<br>\n");
        }
        for word in line.split(' ').enumerate().map(|(j, w)| (j > 0, w)) {
            if word.0 {
                out.push(' ');
            }
            let w = word.1;
            if (w.starts_with("https://") || w.starts_with("http://")) && w.len() > 8 {
                let url = w.trim_end_matches(['.', ',', ';', ')', '>']);
                let tail = &w[url.len()..];
                out.push_str(&format!("<a href=\"{}\">{}</a>{}", escape_html(url), escape_html(url), escape_html(tail)));
            } else {
                out.push_str(&escape_html(w));
            }
        }
    }
    format!("<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px;white-space:pre-wrap\">{out}</div>")
}

/// The light markup the compose window writes (`**bold**`, `*italic*`, `__underline__`,
/// `- item`, `1. item`, `[text](url)`, `![name](cid:…)`, bare links) rendered as HTML; plain
/// text comes through unchanged apart from line breaks.
pub fn markup_to_html(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 64);
    let mut list: Option<&str> = None;
    let close_list = |out: &mut String, list: &mut Option<&str>| {
        if let Some(tag) = list.take() {
            out.push_str(&format!("</{tag}>"));
        }
    };
    for line in text.lines().take(20_000) {
        let trimmed = line.trim_start();
        let (tag, item) = if let Some(rest) = trimmed.strip_prefix("- ").or_else(|| trimmed.strip_prefix("* ")) {
            ("ul", Some(rest))
        } else if let Some(rest) = numbered_item(trimmed) {
            ("ol", Some(rest))
        } else {
            ("", None)
        };
        match item {
            Some(rest) => {
                if list != Some(tag) {
                    close_list(&mut out, &mut list);
                    out.push_str(&format!("<{tag}>"));
                    list = Some(tag);
                }
                out.push_str(&format!("<li>{}</li>", inline_markup(rest)));
            }
            None => {
                close_list(&mut out, &mut list);
                // Line-level marks: `{align=center}`, `{indent}` (repeatable) at the start.
                let mut rest = trimmed;
                let mut styles: Vec<String> = Vec::new();
                loop {
                    if let Some(r) = rest.strip_prefix("{indent}") {
                        styles.push("margin-left:2em".into());
                        rest = r.trim_start();
                    } else if let Some(r) = rest.strip_prefix("{align=") {
                        let Some((v, after)) = r.split_once('}') else { break };
                        if matches!(v, "left" | "center" | "right" | "justify") {
                            styles.push(format!("text-align:{v}"));
                        }
                        rest = after.trim_start();
                    } else {
                        break;
                    }
                }
                if styles.is_empty() {
                    out.push_str(&inline_markup(line));
                    out.push_str("<br>\n");
                } else {
                    out.push_str(&format!("<div style=\"{}\">{}</div>\n", styles.join(";"), inline_markup(rest)));
                }
            }
        }
    }
    close_list(&mut out, &mut list);
    format!("<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px\">{out}</div>")
}

/// `1. text` → `text`.
fn numbered_item(line: &str) -> Option<&str> {
    let i = line.find(". ")?;
    let digits = line.get(..i)?;
    if i > 0 && digits.chars().all(|c| c.is_ascii_digit()) { line.get(i + 2..) } else { None }
}

/// Does the text use the compose markup (so an HTML part is worth sending)?
pub fn has_markup(text: &str) -> bool {
    text.contains("**")
        || text.contains("{/")
        || text.contains("{indent}")
        || text.contains("{align=")
        || text.contains("__")
        || text.contains("](")
        || text.lines().any(|l| {
            let t = l.trim_start();
            t.starts_with("- ") || t.starts_with("* ") || numbered_item(t).is_some()
        })
        || text.matches('*').count() >= 2
}

/// Inline spans of one line: images, links, bold, italic, underline, bare URLs.
fn inline_markup(line: &str) -> String {
    // Images and links first (their URLs must not be escaped or autolinked twice).
    let mut out = String::new();
    let mut rest = line;
    while let Some(i) = rest.find('[') {
        let image = i > 0 && rest.as_bytes().get(i - 1) == Some(&b'!');
        let Some((text, after)) = rest.get(i + 1..).and_then(|r| r.split_once("](")) else { break };
        let Some((url, tail)) = after.split_once(')') else { break };
        if url.contains(['<', '>', '"', ' ']) {
            break;
        }
        out.push_str(&spans(rest.get(..if image { i - 1 } else { i }).unwrap_or_default()));
        if image {
            out.push_str(&format!("<img src=\"{}\" alt=\"{}\" style=\"max-width:100%\">", escape_html(url), escape_html(text)));
        } else {
            out.push_str(&format!("<a href=\"{}\">{}</a>", escape_html(url), spans(text)));
        }
        rest = tail;
    }
    out.push_str(&spans(rest));
    out
}

/// `**bold**`, `*italic*`, `__underline__` and bare links in text without brackets.
/// `{color=#e53935}…{/color}`, `{size=large}…{/size}`, `{font=serif}…{/font}` as a span style.
fn span_style(key: &str, value: &str) -> Option<String> {
    let v = value.trim();
    match key {
        "color" => {
            let hex = v.trim_start_matches('#');
            (hex.len() == 6 && hex.chars().all(|c| c.is_ascii_hexdigit())).then(|| format!("color:#{hex}"))
        }
        "size" => match v {
            "small" => Some("font-size:11px".into()),
            "large" => Some("font-size:18px".into()),
            "huge" => Some("font-size:24px".into()),
            "normal" => Some("font-size:14px".into()),
            _ => None,
        },
        "font" => match v {
            "serif" => Some("font-family:Georgia,'Times New Roman',serif".into()),
            "sans" => Some("font-family:Helvetica,Arial,sans-serif".into()),
            "mono" => Some("font-family:Menlo,Consolas,monospace".into()),
            _ => None,
        },
        "bg" => {
            let hex = v.trim_start_matches('#');
            (hex.len() == 6 && hex.chars().all(|c| c.is_ascii_hexdigit())).then(|| format!("background:#{hex}"))
        }
        _ => None,
    }
}

fn spans(text: &str) -> String {
    const MARKS: [(&str, &str); 4] = [("**", "b"), ("__", "u"), ("*", "i"), ("{", "span")];
    let mut out = String::new();
    let mut open = [false; 4];
    let mut open_spans = 0usize;
    let mut rest = text;
    while !rest.is_empty() {
        let next = MARKS.iter().enumerate().filter_map(|(k, (m, _))| rest.find(m).map(|i| (i, k))).min();
        let Some((i, k)) = next else {
            out.push_str(&autolink(rest));
            break;
        };
        if k == 3 {
            // A styled span: `{key=value}` opens, `{/key}` closes; anything else is text.
            out.push_str(&autolink(rest.get(..i).unwrap_or_default()));
            let after = rest.get(i + 1..).unwrap_or_default();
            let Some(close) = after.find('}') else {
                out.push('{');
                rest = after;
                continue;
            };
            let inner = after.get(..close).unwrap_or_default();
            rest = after.get(close + 1..).unwrap_or_default();
            if let Some(key) = inner.strip_prefix('/') {
                if open_spans > 0 && matches!(key, "color" | "size" | "font" | "bg") {
                    open_spans -= 1;
                    out.push_str("</span>");
                } else {
                    out.push_str(&escape_html(&format!("{{{inner}}}")));
                }
            } else if let Some((key, value)) = inner.split_once('=')
                && let Some(style) = span_style(key, value)
            {
                open_spans += 1;
                out.push_str(&format!("<span style=\"{style}\">"));
            } else {
                out.push_str(&escape_html(&format!("{{{inner}}}")));
            }
            continue;
        }
        let (mark, tag) = MARKS[k];
        out.push_str(&autolink(rest.get(..i).unwrap_or_default()));
        rest = rest.get(i + mark.len()..).unwrap_or_default();
        if open[k] {
            out.push_str(&format!("</{tag}>"));
        } else if rest.contains(mark) {
            out.push_str(&format!("<{tag}>"));
        } else {
            out.push_str(&escape_html(mark));
            continue;
        }
        open[k] = !open[k];
    }
    for (k, is_open) in open.iter().enumerate().take(3) {
        if *is_open {
            out.push_str(&format!("</{}>", MARKS[k].1));
        }
    }
    for _ in 0..open_spans {
        out.push_str("</span>");
    }
    out
}

fn autolink(text: &str) -> String {
    let mut out = String::new();
    for (j, w) in text.split(' ').enumerate() {
        if j > 0 {
            out.push(' ');
        }
        if (w.starts_with("https://") || w.starts_with("http://")) && w.len() > 8 {
            let url = w.trim_end_matches(['.', ',', ';', ')', '>']);
            let tail = w.get(url.len()..).unwrap_or_default();
            out.push_str(&format!("<a href=\"{}\">{}</a>{}", escape_html(url), escape_html(url), escape_html(tail)));
        } else {
            out.push_str(&escape_html(w));
        }
    }
    out
}

/// The script that makes the printable page print itself when opened; `PRINT_SCRIPT_HASH` is
/// its SHA-256 for the page's Content-Security-Policy (`printf '%s' "$script" | openssl dgst
/// -sha256 -binary | base64`): change both together or the page stops printing.
const PRINT_SCRIPT: &str = "window.onload=function(){window.print()}";
const PRINT_SCRIPT_HASH: &str = "sha256-8UIUAuIdPqhicaAtMhPXbnBNCGBLYwp9LdDkhrWt3K4=";

/// A printable page for a message: the headers, then the body as text (the message's own HTML
/// is reduced to text, never embedded: the page opens in the app's own browser context, so no
/// markup, script, handler, frame or remote image of the message may reach it); it asks the
/// browser to print itself when opened. A Content-Security-Policy allows the page's own style
/// and that one script only.
pub fn printable(title: &str, headers: &[(&str, String)], html: &str, text: &str) -> String {
    let mut rows = String::new();
    for (k, v) in headers {
        if !v.trim().is_empty() {
            rows.push_str(&format!("<tr><th>{}</th><td>{}</td></tr>", escape_html(k), escape_html(v)));
        }
    }
    let text = if text.trim().is_empty() { html_to_text(html) } else { text.to_string() };
    let body = text_to_html(&text);
    format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src '{PRINT_SCRIPT_HASH}'\"><title>{}</title><style>body{{font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#222;margin:2em}}table{{border-collapse:collapse;margin-bottom:1.5em}}th{{text-align:left;padding:2px 12px 2px 0;color:#666;font-weight:normal}}td{{padding:2px 0}}h1{{font-size:18px}}@media print{{body{{margin:0}}}}</style></head><body><h1>{}</h1><table>{rows}</table>{body}<script>{PRINT_SCRIPT}</script></body></html>",
        escape_html(title),
        escape_html(title)
    )
}

pub fn escape_html(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            _ => out.push(c),
        }
    }
    out
}

/// Turns `<img src="data:image/png;base64,…">` in the HTML body into inline attachments
/// referenced by `cid:`, which every mail client shows (data URIs most do not).
pub fn inline_data_images(draft: &mut Draft) {
    let mut html = String::with_capacity(draft.html.len());
    let mut rest = draft.html.as_str();
    let mut n = 0usize;
    while let Some(i) = rest.find("data:image/") {
        let before = rest.get(..i).unwrap_or_default();
        let quote = before.chars().last();
        let after = rest.get(i..).unwrap_or_default();
        let end = match quote {
            Some(q @ ('"' | '\'')) => after.find(q),
            _ => after.find(['"', '\'', ' ', ')', '>']),
        }
        .unwrap_or(after.len());
        let uri = after.get(..end).unwrap_or_default();
        html.push_str(before);
        match uri.split_once(";base64,").and_then(|(head, data)| base64_decode(data.trim()).map(|bytes| (head, bytes))) {
            Some((head, bytes)) if n < 50 && bytes.len() <= 5 * 1024 * 1024 => {
                n += 1;
                let mime = head.trim_start_matches("data:").to_ascii_lowercase();
                let ext = mime.rsplit('/').next().unwrap_or("png").split('+').next().unwrap_or("png");
                let cid = format!("image{n}@mailcraft");
                draft.attachments.push(DraftAttachment { name: format!("image{n}.{ext}"), mime, data: bytes, cid: cid.clone() });
                html.push_str(&format!("cid:{cid}"));
            }
            _ => html.push_str(uri),
        }
        rest = after.get(end..).unwrap_or_default();
    }
    html.push_str(rest);
    draft.html = html;
}

/// Parses only the headers (what the list fetch returns) into an envelope.
pub fn parse_headers(raw_headers: &[u8]) -> Result<Envelope> {
    let mut bytes = raw_headers.get(..raw_headers.len().min(1024 * 1024)).unwrap_or_default().to_vec();
    if !bytes.ends_with(b"\r\n\r\n") && !bytes.ends_with(b"\n\n") {
        bytes.extend_from_slice(b"\r\n\r\n");
    }
    let m = mail_parser::MessageParser::default().parse_headers(&bytes).ok_or_else(|| Error::Message("unparseable headers".into()))?;
    let mut e = envelope_of(&m, 0);
    // Without the body, a multipart/mixed content type is the best hint for attachments.
    e.has_attachments =
        m.content_type().is_some_and(|c| c.ctype().eq_ignore_ascii_case("multipart") && c.subtype().is_some_and(|s| s.eq_ignore_ascii_case("mixed")));
    Ok(e)
}

fn envelope_of(m: &mail_parser::Message<'_>, size: usize) -> Envelope {
    Envelope {
        uid: 0,
        mailbox: String::new(),
        account: String::new(),
        subject: clean(m.subject().unwrap_or_default(), limits::MAX_TEXT),
        from: addresses(m.from()),
        to: addresses(m.to()),
        cc: addresses(m.cc()),
        date: m.date().map(|d| d.to_timestamp()).unwrap_or(0),
        snippet: String::new(),
        flags: Flags::default(),
        size: size.min(u32::MAX as usize) as u32,
        has_attachments: false,
        message_id: m.message_id().map(normalise_id).unwrap_or_default(),
        in_reply_to: id_list(m.in_reply_to()).into_iter().next().unwrap_or_default(),
        references: id_list(m.references()).into_iter().take(100).collect(),
    }
}

/// Reduces HTML to readable text: block elements become line breaks, styles and scripts go,
/// the common entities are decoded.
pub fn html_to_text(html: &str) -> String {
    let mut out = String::with_capacity(html.len() / 2);
    let mut rest = html;
    let mut skip_until: Option<&str> = None;
    while let Some(start) = rest.find('<') {
        let text = rest.get(..start).unwrap_or_default();
        if skip_until.is_none() {
            out.push_str(&decode_entities(text));
        }
        let after = rest.get(start + 1..).unwrap_or_default();
        let Some(end) = after.find('>') else { break };
        let tag = after.get(..end).unwrap_or_default();
        rest = after.get(end + 1..).unwrap_or_default();
        let name = tag.trim_start_matches('/').split(|c: char| c.is_whitespace() || c == '/').next().unwrap_or_default().to_ascii_lowercase();
        if let Some(close) = skip_until {
            if tag.starts_with('/') && name == close {
                skip_until = None;
            }
            continue;
        }
        match name.as_str() {
            "style" | "script" | "head" | "title" => {
                skip_until = Some(if name == "style" {
                    "style"
                } else if name == "script" {
                    "script"
                } else if name == "head" {
                    "head"
                } else {
                    "title"
                })
            }
            "br" => out.push('\n'),
            "p" | "div" | "tr" | "li" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "blockquote" | "table" | "ul" | "ol" | "section" | "article"
            | "header" | "footer" | "pre" => {
                out.push('\n');
            }
            "td" | "th" => out.push(' '),
            "a" if !tag.starts_with('/') => {
                if let Some(href) = attr(tag, "href").filter(|h| h.starts_with("http") && !h.contains('\n')) {
                    out.push_str(&format!("[{href}] "));
                }
            }
            _ => {}
        }
        if out.len() > limits::MAX_BODY {
            break;
        }
    }
    if skip_until.is_none() {
        out.push_str(&decode_entities(rest));
    }
    // Drop blank lines and surrounding spaces.
    let mut cleaned = String::with_capacity(out.len());
    for line in out.lines().map(str::trim).filter(|l| !l.is_empty()) {
        cleaned.push_str(line);
        cleaned.push('\n');
    }
    cleaned.trim().to_string()
}

fn attr<'a>(tag: &'a str, name: &str) -> Option<&'a str> {
    let lower = tag.to_ascii_lowercase();
    let i = lower.find(&format!("{name}="))? + name.len() + 1;
    let rest = tag.get(i..)?;
    let (quote, rest) = match rest.chars().next() {
        Some(q @ ('"' | '\'')) => (Some(q), rest.get(1..)?),
        _ => (None, rest),
    };
    let end = match quote {
        Some(q) => rest.find(q)?,
        None => rest.find(|c: char| c.is_whitespace() || c == '>').unwrap_or(rest.len()),
    };
    rest.get(..end)
}

fn decode_entities(text: &str) -> String {
    if !text.contains('&') {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(i) = rest.find('&') {
        out.push_str(rest.get(..i).unwrap_or_default());
        let after = rest.get(i..).unwrap_or_default();
        let Some(semi) = after.find(';').filter(|s| *s <= 10) else {
            out.push('&');
            rest = after.get(1..).unwrap_or_default();
            continue;
        };
        let entity = after.get(1..semi).unwrap_or_default();
        let decoded = match entity {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" | "#39" => Some('\''),
            "nbsp" | "#160" => Some(' '),
            "eacute" => Some('é'),
            "egrave" => Some('è'),
            "agrave" => Some('à'),
            "ograve" => Some('ò'),
            "ugrave" => Some('ù'),
            "igrave" => Some('ì'),
            "euro" => Some('€'),
            "copy" => Some('©'),
            "hellip" => Some('…'),
            "ndash" => Some('–'),
            "mdash" => Some('—'),
            "laquo" => Some('«'),
            "raquo" => Some('»'),
            e => e
                .strip_prefix('#')
                .and_then(|n| if let Some(h) = n.strip_prefix(['x', 'X']) { u32::from_str_radix(h, 16).ok() } else { n.parse().ok() })
                .and_then(char::from_u32),
        };
        match decoded {
            Some(c) => out.push(c),
            None => out.push_str(after.get(..=semi).unwrap_or_default()),
        }
        rest = after.get(semi + 1..).unwrap_or_default();
    }
    out.push_str(rest);
    out
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn base64_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk.first().copied().unwrap_or(0), chunk.get(1).copied().unwrap_or(0), chunk.get(2).copied().unwrap_or(0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        let idx = |shift: u32| B64.get(((n >> shift) & 63) as usize).copied().unwrap_or(b'A') as char;
        out.push(idx(18));
        out.push(idx(12));
        out.push(if chunk.len() > 1 { idx(6) } else { '=' });
        out.push(if chunk.len() > 2 { idx(0) } else { '=' });
    }
    out
}

pub fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() / 4 * 3);
    let mut acc: u32 = 0;
    let mut bits = 0;
    for c in text.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'=' | b'\n' | b'\r' | b' ' | b'\t' => continue,
            _ => return None,
        };
        acc = (acc << 6) | u32::from(v);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

/// Base64 broken into 76-column lines, as MIME wants.
fn base64_lines(bytes: &[u8]) -> String {
    let b = base64_encode(bytes);
    let mut out = String::with_capacity(b.len() + b.len() / 76 * 2);
    for chunk in b.as_bytes().chunks(76) {
        out.push_str(std::str::from_utf8(chunk).unwrap_or_default());
        out.push_str("\r\n");
    }
    out
}

/// RFC 2047 encoded word when the text is not plain ASCII.
pub fn encode_header_text(text: &str) -> String {
    if text.is_ascii() && !text.contains(['\r', '\n']) {
        return text.to_string();
    }
    let clean: String = text.chars().filter(|c| !c.is_control()).collect();
    format!("=?utf-8?B?{}?=", base64_encode(clean.as_bytes()))
}

fn header_address(a: &Address) -> String {
    if a.name.is_empty() {
        return a.email.clone();
    }
    let name = if a.name.is_ascii() { format!("\"{}\"", a.name.replace('"', "")) } else { encode_header_text(&a.name) };
    format!("{name} <{}>", a.email)
}

fn header_addresses(list: &[Address]) -> String {
    list.iter().map(header_address).collect::<Vec<_>>().join(",\r\n ")
}

/// One MIME part: its headers, a blank line, its encoded content.
fn part(content_type: &str, extra_headers: &str, data: &[u8]) -> String {
    format!("Content-Type: {content_type}\r\n{extra_headers}Content-Transfer-Encoding: base64\r\n\r\n{}", base64_lines(data))
}

/// Wraps parts in a multipart container.
fn multipart(kind: &str, boundary: &str, parts: &[String]) -> String {
    let mut out = format!("Content-Type: multipart/{kind}; boundary=\"{boundary}\"\r\n\r\n");
    for p in parts {
        out.push_str(&format!("--{boundary}\r\n{p}\r\n"));
    }
    out.push_str(&format!("--{boundary}--\r\n"));
    out
}

/// Builds the RFC 822 bytes of a draft: UTF-8 text body (base64), an HTML alternative when
/// `html` is set, inline images (`cid`) in multipart/related, files as multipart/mixed.
/// `message_id` should be unique (`<time.random@domain>`).
pub fn build(draft: &Draft, from: &Address, date: i64, message_id: &str) -> Result<Vec<u8>> {
    if draft.to.is_empty() && draft.cc.is_empty() && draft.bcc.is_empty() {
        return Err(Error::Message("the message has no recipient".into()));
    }
    for a in draft.to.iter().chain(&draft.cc).chain(&draft.bcc) {
        if !crate::account::is_email(&a.email) {
            return Err(Error::Message(format!("`{}` is not an e-mail address", a.email)));
        }
    }
    let mut h = String::new();
    h.push_str(&format!("Date: {}\r\n", time::rfc2822(date)));
    h.push_str(&format!("From: {}\r\n", header_address(from)));
    if !draft.to.is_empty() {
        h.push_str(&format!("To: {}\r\n", header_addresses(&draft.to)));
    }
    if !draft.cc.is_empty() {
        h.push_str(&format!("Cc: {}\r\n", header_addresses(&draft.cc)));
    }
    h.push_str(&format!("Subject: {}\r\n", encode_header_text(draft.subject.trim())));
    h.push_str(&format!("Message-ID: {}\r\n", normalise_id(message_id)));
    if !draft.in_reply_to.is_empty() {
        h.push_str(&format!("In-Reply-To: {}\r\n", normalise_id(&draft.in_reply_to)));
    }
    if !draft.references.is_empty() {
        h.push_str(&format!("References: {}\r\n", draft.references.iter().map(|r| normalise_id(r)).collect::<Vec<_>>().join("\r\n ")));
    }
    h.push_str("MIME-Version: 1.0\r\nX-Mailer: MailCraft\r\n");
    let boundary = |tag: &str| format!("=_MailCraft_{tag}_{:x}_{:x}", date, draft.attachments.len());
    let safe = |a: &DraftAttachment| {
        let name = encode_header_text(&a.name.replace(['"', '\r', '\n'], "")).replace(' ', "_");
        // A type/subtype of token characters only: a line break would start a header of its own.
        let token = |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_alphanumeric() || b"!#$&^_.+-".contains(&b));
        let mime = match a.mime.split_once('/') {
            Some((t, s)) if token(t) && token(s) && a.mime.len() <= 127 => a.mime.to_ascii_lowercase(),
            _ => "application/octet-stream".to_string(),
        };
        (name, mime)
    };
    // The text, or text + HTML alternatives.
    let mut body = part("text/plain; charset=utf-8", "", draft.body.as_bytes());
    if !draft.html.trim().is_empty() {
        let html = part("text/html; charset=utf-8", "", draft.html.as_bytes());
        body = multipart("alternative", &boundary("alt"), &[body, html]);
    }
    // Inline images travel with the HTML in multipart/related.
    let inline: Vec<String> = draft
        .attachments
        .iter()
        .filter(|a| !a.cid.is_empty())
        .take(limits::MAX_ATTACHMENTS)
        .map(|a| {
            let (name, mime) = safe(a);
            part(
                &format!("{mime}; name=\"{name}\""),
                &format!("Content-ID: <{}>\r\nContent-Disposition: inline; filename=\"{name}\"\r\n", a.cid.replace(['<', '>', '\r', '\n'], "")),
                &a.data,
            )
        })
        .collect();
    if !inline.is_empty() {
        let mut parts = vec![body];
        parts.extend(inline);
        body = multipart("related", &boundary("rel"), &parts);
    }
    // Files as multipart/mixed.
    let files: Vec<String> = draft
        .attachments
        .iter()
        .filter(|a| a.cid.is_empty())
        .take(limits::MAX_ATTACHMENTS)
        .map(|a| {
            let (name, mime) = safe(a);
            part(&format!("{mime}; name=\"{name}\""), &format!("Content-Disposition: attachment; filename=\"{name}\"\r\n"), &a.data)
        })
        .collect();
    if !files.is_empty() {
        let mut parts = vec![body];
        parts.extend(files);
        body = multipart("mixed", &boundary("mix"), &parts);
    }
    let mut out = h.into_bytes();
    out.extend_from_slice(body.as_bytes());
    Ok(out)
}

/// Quotes a message for a reply: `On <date>, <sender> wrote:` and `> ` lines.
pub fn quote(message: &Message, italian: bool) -> String {
    let who = message.envelope.from.first().map(Address::display).unwrap_or_default();
    let when = time::long(message.envelope.date, italian);
    let intro = if italian { format!("Il {when}, {who} ha scritto:") } else { format!("On {when}, {who} wrote:") };
    let body = message.body_text();
    let quoted: Vec<String> = body.lines().take(2_000).map(|l| if l.is_empty() { ">".to_string() } else { format!("> {l}") }).collect();
    format!("\n\n{intro}\n{}", quoted.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const RAW: &str = "From: \"Anna Rossi\" <anna@example.com>\r\nTo: luca@example.com, Marco <marco@example.com>\r\nCc: =?utf-8?B?TcOgcmlh?= <m@example.com>\r\nSubject: =?utf-8?Q?Fattura_n=C2=B0_12?=\r\nDate: Mon, 12 Oct 2026 20:01:08 +0200\r\nMessage-ID: <abc@example.com>\r\nIn-Reply-To: <root@example.com>\r\nReferences: <root@example.com> <mid@example.com>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"b1\"\r\n\r\n--b1\r\nContent-Type: multipart/alternative; boundary=\"b2\"\r\n\r\n--b2\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nCiao Luca,\r\nin allegato la fattura.\r\n--b2\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Ciao <b>Luca</b>,<br>in allegato la fattura.</p>\r\n--b2--\r\n--b1\r\nContent-Type: application/pdf; name=\"fattura.pdf\"\r\nContent-Disposition: attachment; filename=\"fattura.pdf\"\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n--b1--\r\n";

    #[test]
    fn parses_a_multipart_message() {
        let m = parse(RAW.as_bytes()).unwrap();
        let e = &m.envelope;
        assert_eq!(e.subject, "Fattura n° 12");
        assert_eq!(e.from[0], Address::new("Anna Rossi", "anna@example.com"));
        assert_eq!(e.to.len(), 2);
        assert_eq!(e.cc[0].name, "Mària");
        assert_eq!(e.date, time::from_civil(2026, 10, 12, 18, 1, 8));
        assert_eq!(e.message_id, "<abc@example.com>");
        assert_eq!(e.in_reply_to, "<root@example.com>");
        assert_eq!(e.references, vec!["<root@example.com>", "<mid@example.com>"]);
        assert!(e.has_attachments);
        assert_eq!(e.snippet, "Ciao Luca, in allegato la fattura.");
        assert!(m.text.starts_with("Ciao Luca"));
        assert!(m.html.contains("<b>Luca</b>"));
        assert_eq!(m.attachments.len(), 1);
        assert_eq!((m.attachments[0].name.as_str(), m.attachments[0].mime.as_str(), m.attachments[0].size), ("fattura.pdf", "application/pdf", 9));
        let headers_only = parse_headers(RAW.split("\r\n\r\n").next().unwrap().as_bytes()).unwrap();
        assert_eq!(headers_only.subject, "Fattura n° 12");
        assert!(headers_only.has_attachments);
        assert!(parse(b"").is_err() || parse(b"").is_ok(), "never panics");
        assert!(parse(&[0xff; 300]).is_ok() || parse(&[0xff; 300]).is_err());
    }

    #[test]
    fn html_becomes_text() {
        let html = "<html><head><style>p{color:red}</style><title>t</title></head><body><p>Ciao &amp; <b>benvenuto</b>!</p><ul><li>uno</li><li>due</li></ul><a href=\"https://x.it/p\">link</a>&nbsp;fine &#8364; &egrave;</body></html>";
        let text = html_to_text(html);
        assert_eq!(text, "Ciao & benvenuto!\nuno\ndue\n[https://x.it/p] link fine € è");
        assert_eq!(decode_entities("a &lt; b &unknown; c"), "a < b &unknown; c");
    }

    #[test]
    fn compose_markup_renders_and_prints() {
        let text = "Ciao **Luca**, ecco l'*offerta* __firmata__:\n- rete\n- switch\n1. lunedì\n2. martedì\nVedi [il sito](https://sirius.example/p) o https://x.it.\n![logo](cid:image1@mailcraft)\na * b < c";
        let html = markup_to_html(text);
        assert!(html.contains("Ciao <b>Luca</b>, ecco l'<i>offerta</i> <u>firmata</u>:<br>"), "{html}");
        assert!(html.contains("<ul><li>rete</li><li>switch</li></ul><ol><li>lunedì</li><li>martedì</li></ol>"), "{html}");
        assert!(html.contains("<a href=\"https://sirius.example/p\">il sito</a> o <a href=\"https://x.it\">https://x.it</a>."), "{html}");
        assert!(html.contains("<img src=\"cid:image1@mailcraft\" alt=\"logo\""), "{html}");
        assert!(html.contains("a * b &lt; c"), "a lone star stays: {html}");
        assert!(has_markup(text) && !has_markup("plain text, nothing else") && has_markup("- a list"));
        let page =
            printable("Offerta", &[("Da", "Anna <a@x.it>".into()), ("A", String::new())], "<p>Ciao</p><script>alert(1)</script><b>fine</b>", "");
        assert!(page.contains("<th>Da</th><td>Anna &lt;a@x.it&gt;</td>") && !page.contains("<th>A</th>"), "{page}");
        assert!(page.contains("Ciao") && page.contains("fine") && !page.contains("<b>") && !page.contains("alert(1)"), "{page}");
        assert!(page.contains("window.print()") && page.contains("Content-Security-Policy") && page.contains("default-src 'none'"), "{page}");
        assert!(printable("t", &[], "", "x < y").contains("x &lt; y"));
        // The message's HTML never reaches the page: handlers, javascript: links, frames and
        // remote images are gone, only the text stays.
        let hostile = "<p>Hi <b>there</b></p><img src=x onerror=alert(1)><a href=\"javascript:alert(1)\">click</a><iframe srcdoc=\"&lt;script&gt;alert(1)&lt;/script&gt;\"></iframe><img src=\"https://tracker.example/p.gif\"><div onclick=\"alert(1)\">x</div>";
        let page = printable("t", &[], hostile, "");
        for bad in ["onerror", "javascript:", "<iframe", "srcdoc", "onclick", "<img", "tracker.example", "<b>"] {
            assert!(!page.contains(bad), "{bad} survived: {page}");
        }
        assert!(page.contains("Hi there") && page.contains("click"), "{page}");
        // A message with a text part prints the text, whatever its HTML holds.
        let page = printable("t", &[], hostile, "plain words");
        assert!(page.contains("plain words") && !page.contains("onerror"), "{page}");
        // Styled spans and line marks (colour, size, font, alignment, indent).
        let h = markup_to_html(
            "{color=#e53935}rosso{/color} {size=large}grande{/size} {font=serif}serif{/font}\n{align=center}centro\n{indent}{indent}dentro\n{x} {/color}",
        );
        assert!(h.contains("<span style=\"color:#e53935\">rosso</span> <span style=\"font-size:18px\">grande</span>"), "{h}");
        assert!(h.contains("<span style=\"font-family:Georgia,'Times New Roman',serif\">serif</span>"), "{h}");
        assert!(
            h.contains("<div style=\"text-align:center\">centro</div>") && h.contains("<div style=\"margin-left:2em;margin-left:2em\">dentro</div>"),
            "{h}"
        );
        assert!(h.contains("{x} {/color}"), "unknown marks stay as text: {h}");
        assert!(has_markup("{align=center}x") && has_markup("a {indent}"));
    }

    #[test]
    fn builds_messages_that_parse_back() {
        let draft = Draft {
            to: vec![Address::new("Luca Bianchi", "luca@example.com")],
            cc: vec![Address::new("Màría", "m@example.com")],
            subject: "Riunione di lunedì".into(),
            body: "Ciao a tutti,\nci vediamo lunedì alle 10.\n\n— Anna".into(),
            in_reply_to: "root@example.com".into(),
            references: vec!["<root@example.com>".into()],
            attachments: vec![crate::message::DraftAttachment {
                name: "agenda.txt".into(),
                mime: "text/plain".into(),
                data: b"1. saluti\n2. piano".to_vec(),
                cid: String::new(),
            }],
            ..Draft::default()
        };
        let raw = build(&draft, &Address::new("Anna Rossi", "anna@example.com"), 1_700_000_000, "<x1@example.com>").unwrap();
        let text = String::from_utf8_lossy(&raw);
        assert!(text.contains("Subject: =?utf-8?B?"));
        assert!(text.contains("In-Reply-To: <root@example.com>"));
        let m = parse(&raw).unwrap();
        assert_eq!(m.envelope.subject, "Riunione di lunedì");
        assert_eq!(m.envelope.to[0].name, "Luca Bianchi");
        assert_eq!(m.envelope.cc[0].name, "Màría");
        assert_eq!(m.text.trim(), draft.body.trim());
        assert_eq!(m.attachments.len(), 1);
        assert_eq!(m.attachments[0].name, "agenda.txt");
        assert_eq!(attachments(&raw)[0].data, b"1. saluti\n2. piano");
        assert_eq!(m.envelope.in_reply_to, "<root@example.com>");
        assert!(build(&Draft::default(), &Address::new("", "a@b.co"), 0, "<i@d>").is_err());
        let bad = Draft { to: vec![Address::new("", "nope")], ..Draft::default() };
        assert!(build(&bad, &Address::new("", "a@b.co"), 0, "<i@d>").is_err());
        assert_eq!(base64_decode(&base64_encode(b"hello, world")).unwrap(), b"hello, world");
        assert!(base64_decode("@@").is_none());
        let q = quote(&m, true);
        assert!(q.contains("ha scritto:") && q.contains("> Ciao a tutti,"));
    }

    #[test]
    fn an_attachment_type_cannot_add_header_lines() {
        let file = |mime: &str| crate::message::DraftAttachment {
            name: "a\r\nX-Evil: 1.txt".into(),
            mime: mime.into(),
            data: b"x".to_vec(),
            cid: String::new(),
        };
        let draft = Draft {
            to: vec![Address::new("", "luca@example.com")],
            body: "x".into(),
            attachments: vec![file("text/plain\r\nBcc: spy@example.org"), file("image/svg+xml"), file("x/\"y")],
            ..Draft::default()
        };
        let raw = String::from_utf8_lossy(&build(&draft, &Address::new("", "anna@example.com"), 0, "<i@d>").unwrap()).into_owned();
        assert!(!raw.contains("Bcc:") && !raw.contains("\r\nX-Evil"), "{raw}");
        assert_eq!(raw.matches("Content-Type: application/octet-stream;").count(), 2, "{raw}");
        assert!(raw.contains("Content-Type: image/svg+xml;"), "{raw}");
    }

    #[test]
    fn html_bodies_with_inline_images_and_files() {
        // A 1x1 PNG as a data URI: it becomes a cid part that every client shows.
        let png = base64_encode(&[0x89, b'P', b'N', b'G', 0, 1, 2, 3]);
        let mut draft = Draft {
            to: vec![Address::new("Luca", "luca@example.com")],
            subject: "Firma".into(),
            body: "Ciao,\nvedi https://example.it/x.\n\n-- \nAnna".into(),
            html: format!(
                "{}<br><br>-- <br><b>Anna Rossi</b> <a href=\"https://sirius.example\">Sirius</a><img src=\"data:image/png;base64,{png}\" alt=\"logo\">",
                text_to_html("Ciao,\nvedi https://example.it/x.")
            ),
            attachments: vec![crate::message::DraftAttachment {
                name: "doc.pdf".into(),
                mime: "application/pdf".into(),
                data: b"%PDF".to_vec(),
                cid: String::new(),
            }],
            ..Draft::default()
        };
        inline_data_images(&mut draft);
        assert_eq!(draft.attachments.len(), 2);
        assert_eq!(draft.attachments[1].cid, "image1@mailcraft");
        assert!(draft.html.contains("src=\"cid:image1@mailcraft\"") && !draft.html.contains("data:image"));
        let raw = build(&draft, &Address::new("Anna", "anna@example.com"), 1_700_000_000, "<h1@example.com>").unwrap();
        let text = String::from_utf8_lossy(&raw);
        assert!(text.contains("multipart/mixed") && text.contains("multipart/related") && text.contains("multipart/alternative"));
        assert!(text.contains("Content-ID: <image1@mailcraft>"));
        let m = parse(&raw).unwrap();
        assert_eq!(m.text.trim(), draft.body.trim());
        assert!(m.html.contains("<b>Anna Rossi</b>") && m.html.contains("cid:image1@mailcraft"));
        assert!(m.html.contains("<a href=\"https://example.it/x\">https://example.it/x</a>."));
        let listed: Vec<&str> = m.attachments.iter().filter(|a| !a.inline).map(|a| a.name.as_str()).collect();
        assert_eq!(listed, vec!["doc.pdf"], "the logo is inline, not a file: {:?}", m.attachments);
        assert!(m.envelope.has_attachments);
        assert_eq!(
            text_to_html("a < b & \"c\""),
            "<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px;white-space:pre-wrap\">a &lt; b &amp; &quot;c&quot;</div>"
        );
    }
}
