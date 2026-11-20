//! Shows the compose markup as it will look: bold, sizes, colours, lists, links and alignment
//! are drawn in the text box itself and the marks (`**`, `{size=large}`…) are hidden from view.
//!
//! The text stays the plain markup (`core::mime::markup_to_html` renders the same rules into
//! the message), so cursor and edits keep working on it: a mark is laid out as a transparent
//! sliver instead of being removed.

use egui::text::{LayoutJob, LayoutSection, TextFormat};
use egui::{Color32, FontFamily, FontId, Stroke};

/// The look of a run of text.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Style {
    pub bold: bool,
    pub italic: bool,
    pub underline: bool,
    /// Font size as a factor of the base size (1.0 = normal).
    pub size: f32,
    pub color: Option<Color32>,
    pub background: Option<Color32>,
    pub mono: bool,
    pub link: bool,
    /// A mark of the markup (hidden), a list bullet or an image name (muted).
    pub mark: bool,
    pub muted: bool,
}

/// A run of the text: a byte range with one look.
#[derive(Clone, Debug, PartialEq)]
pub struct Piece {
    pub start: usize,
    pub end: usize,
    pub style: Style,
    /// Extra space before the piece (indent, centred lines); only on the first piece of a line.
    pub leading: f32,
    /// A centred (0.5) or right-aligned (1.0) line: the leading space is computed from its width.
    pub align: f32,
}

/// Up to this many lines are styled; a longer text is shown plain beyond it.
const MAX_LINES: usize = 5_000;
/// Width of one `{indent}` in points.
const INDENT: f32 = 24.0;

/// Splits the text into styled pieces that cover every byte, in order.
pub fn pieces(text: &str) -> Vec<Piece> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    let mut lines = 0usize;
    for line in text.split_inclusive('\n') {
        let start = pos;
        pos += line.len();
        let body = line.strip_suffix('\n').unwrap_or(line);
        lines += 1;
        if lines > MAX_LINES {
            push(&mut out, start, pos, Style::plain());
            continue;
        }
        line_pieces(&mut out, start, body);
        if body.len() < line.len() {
            push(&mut out, start + body.len(), pos, Style::plain());
        }
    }
    // Every piece carries a style; neighbouring equal ones merge so the job stays small.
    let mut merged: Vec<Piece> = Vec::with_capacity(out.len());
    for p in out {
        match merged.last_mut() {
            Some(last) if last.end == p.start && last.style == p.style && p.leading == 0.0 && p.align == 0.0 => last.end = p.end,
            _ => merged.push(p),
        }
    }
    merged
}

fn push(out: &mut Vec<Piece>, start: usize, end: usize, style: Style) {
    if end > start {
        out.push(Piece { start, end, style, leading: 0.0, align: 0.0 });
    }
}

impl Style {
    fn plain() -> Self {
        Self { size: 1.0, ..Self::default() }
    }

    fn mark() -> Self {
        Self { mark: true, size: 1.0, ..Self::default() }
    }
}

/// One line: list bullets, `{indent}`/`{align=…}` marks, then the inline spans.
fn line_pieces(out: &mut Vec<Piece>, base: usize, line: &str) {
    let first = out.len();
    let indent = line.len() - line.trim_start().len();
    let trimmed = line.trim_start();
    push(out, base, base + indent, Style::plain());
    let mut at = indent;
    let mut leading = 0.0;
    let mut align = 0.0;
    // List bullets: `- `, `* `, `1. ` are shown muted, their item inset a little.
    let bullet = if trimmed.starts_with("- ") || trimmed.starts_with("* ") { 2 } else { numbered_prefix(trimmed).unwrap_or(0) };
    if bullet > 0 {
        push(out, base + at, base + at + bullet, Style { muted: true, bold: true, ..Style::plain() });
        leading = INDENT / 2.0;
        at += bullet;
    } else {
        let mut rest = trimmed;
        loop {
            if let Some(r) = rest.strip_prefix("{indent}") {
                push(out, base + at, base + at + "{indent}".len(), Style::mark());
                at += "{indent}".len();
                leading += INDENT;
                let skipped = r.len() - r.trim_start().len();
                push(out, base + at, base + at + skipped, Style::plain());
                at += skipped;
                rest = r.trim_start();
            } else if let Some(r) = rest.strip_prefix("{align=")
                && let Some((v, after)) = r.split_once('}')
            {
                let len = "{align=".len() + v.len() + 1;
                push(out, base + at, base + at + len, Style::mark());
                at += len;
                align = match v {
                    "center" => 0.5,
                    "right" => 1.0,
                    _ => 0.0,
                };
                let skipped = after.len() - after.trim_start().len();
                push(out, base + at, base + at + skipped, Style::plain());
                at += skipped;
                rest = after.trim_start();
            } else {
                break;
            }
        }
    }
    inline_pieces(out, base + at, line.get(at..).unwrap_or_default());
    if let Some(p) = out.get_mut(first) {
        p.leading = leading;
        p.align = align;
    }
}

/// `1. ` → 3 (the length of the prefix).
fn numbered_prefix(line: &str) -> Option<usize> {
    let i = line.find(". ")?;
    let digits = line.get(..i)?;
    (i > 0 && i <= 4 && digits.chars().all(|c| c.is_ascii_digit())).then_some(i + 2)
}

/// Images, links, `**bold**`, `*italic*`, `__underline__`, `{key=value}…{/key}` and bare URLs.
fn inline_pieces(out: &mut Vec<Piece>, base: usize, text: &str) {
    let mut style = Style::plain();
    let mut open_spans: Vec<Style> = Vec::new();
    let mut at = 0usize;
    let mut plain_from = 0usize;
    let flush = |out: &mut Vec<Piece>, from: usize, to: usize, style: &Style| {
        words(out, base + from, text.get(from..to).unwrap_or_default(), style);
    };
    while at < text.len() {
        let rest = text.get(at..).unwrap_or_default();
        let Some(c) = rest.chars().next() else { break };
        // A link or an image: `[text](url)` / `![name](url)`.
        if c == '[' || (c == '!' && rest.starts_with("![")) {
            let image = c == '!';
            let open = if image { 2 } else { 1 };
            if let Some((label, after)) = rest.get(open..).and_then(|r| r.split_once("]("))
                && let Some((url, _)) = after.split_once(')')
                && !url.contains(['<', '>', '"', ' '])
            {
                flush(out, plain_from, at, &style);
                let label_start = at + open;
                let url_start = label_start + label.len() + 2;
                let end = url_start + url.len() + 1;
                push(out, base + at, base + label_start, Style::mark());
                if image {
                    push(out, base + label_start, base + label_start + label.len(), Style { muted: true, italic: true, ..style.clone() });
                    push(out, base + label_start + label.len(), base + end, Style::mark());
                } else {
                    push(out, base + label_start, base + label_start + label.len(), Style { link: true, ..style.clone() });
                    push(out, base + label_start + label.len(), base + url_start, Style::mark());
                    push(out, base + url_start, base + url_start + url.len(), Style { muted: true, size: 0.75, ..Style::plain() });
                    push(out, base + url_start + url.len(), base + end, Style::mark());
                }
                at = end;
                plain_from = at;
                continue;
            }
            at += c.len_utf8();
            continue;
        }
        // A styled span: `{key=value}` opens, `{/key}` closes.
        if c == '{' {
            if let Some(close) = rest.find('}') {
                let inner = rest.get(1..close).unwrap_or_default();
                let next = if let Some(key) = inner.strip_prefix('/') {
                    (matches!(key, "color" | "size" | "font" | "bg") && !open_spans.is_empty()).then(|| open_spans.pop().unwrap_or_else(Style::plain))
                } else {
                    inner.split_once('=').and_then(|(k, v)| span_style(&style, k, v)).inspect(|_| open_spans.push(style.clone()))
                };
                if let Some(s) = next {
                    flush(out, plain_from, at, &style);
                    push(out, base + at, base + at + close + 1, Style::mark());
                    style = s;
                    at += close + 1;
                    plain_from = at;
                    continue;
                }
            }
            at += 1;
            continue;
        }
        // `**`, `__`, `*`: a mark opens only when it closes later on the line.
        let mark = if rest.starts_with("**") {
            Some(("**", 0))
        } else if rest.starts_with("__") {
            Some(("__", 1))
        } else if c == '*' {
            Some(("*", 2))
        } else {
            None
        };
        if let Some((m, k)) = mark {
            let open = match k {
                0 => style.bold,
                1 => style.underline,
                _ => style.italic,
            };
            let after = rest.get(m.len()..).unwrap_or_default();
            if open || after.contains(m) {
                flush(out, plain_from, at, &style);
                push(out, base + at, base + at + m.len(), Style::mark());
                match k {
                    0 => style.bold = !style.bold,
                    1 => style.underline = !style.underline,
                    _ => style.italic = !style.italic,
                }
                at += m.len();
                plain_from = at;
                continue;
            }
            at += m.len();
            continue;
        }
        at += c.len_utf8();
    }
    flush(out, plain_from, text.len(), &style);
}

/// Plain words, with bare `https://` links shown as links.
fn words(out: &mut Vec<Piece>, base: usize, text: &str, style: &Style) {
    let mut at = 0usize;
    for w in text.split_inclusive(' ') {
        let word = w.trim_end_matches(' ');
        if (word.starts_with("https://") || word.starts_with("http://")) && word.len() > 8 {
            let url = word.trim_end_matches(['.', ',', ';', ')', '>']);
            push(out, base + at, base + at + url.len(), Style { link: true, ..style.clone() });
            push(out, base + at + url.len(), base + at + w.len(), style.clone());
        } else {
            push(out, base + at, base + at + w.len(), style.clone());
        }
        at += w.len();
    }
}

fn span_style(current: &Style, key: &str, value: &str) -> Option<Style> {
    let v = value.trim();
    let mut s = current.clone();
    match key {
        "color" => s.color = Some(hex_color(v)?),
        "bg" => s.background = Some(hex_color(v)?),
        "size" => {
            s.size = match v {
                "small" => 11.0 / 14.0,
                "normal" => 1.0,
                "large" => 18.0 / 14.0,
                "huge" => 24.0 / 14.0,
                _ => return None,
            }
        }
        "font" => match v {
            "mono" => s.mono = true,
            "sans" | "serif" => s.mono = false,
            _ => return None,
        },
        _ => return None,
    }
    Some(s)
}

fn hex_color(v: &str) -> Option<Color32> {
    let hex = v.trim_start_matches('#');
    if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let n = u32::from_str_radix(hex, 16).ok()?;
    Some(Color32::from_rgb((n >> 16) as u8, (n >> 8) as u8, n as u8))
}

/// Colours the layout takes from the theme.
#[derive(Clone, Copy)]
pub struct Palette {
    pub text: Color32,
    pub muted: Color32,
    pub link: Color32,
}

/// The layout job for the text box: `base` is the normal font size. With `bold_only`, every
/// run but the bold ones is transparent: painted again a little to the right over the text, it
/// thickens the bold runs (egui ships no bold face).
pub fn layout(ui: &egui::Ui, text: &str, wrap_width: f32, base: f32, palette: Palette, bold_only: bool) -> LayoutJob {
    let mut job = LayoutJob { text: text.to_string(), ..Default::default() };
    job.wrap.max_width = wrap_width;
    let row_height = ui.ctx().fonts_mut(|f| f.row_height(&FontId::proportional(base)));
    let pieces = pieces(text);
    for p in &pieces {
        let mut leading = p.leading;
        if p.align > 0.0 {
            // Centre or right-align the line: its visible width decides the space before it.
            let line_end = text.get(p.start..).and_then(|s| s.find('\n')).map_or(text.len(), |n| p.start + n);
            let visible: String = pieces
                .iter()
                .filter(|q| q.start >= p.start && q.end <= line_end && !q.style.mark)
                .map(|q| text.get(q.start..q.end).unwrap_or_default())
                .collect();
            let width = ui.ctx().fonts_mut(|f| f.layout_no_wrap(visible, FontId::proportional(base), palette.text).size().x);
            leading += ((wrap_width - width - 4.0) * p.align).max(0.0);
        }
        let mut format = format(&p.style, base, row_height, palette, bold_only);
        if p.style.mark {
            // Pull the sliver's letters onto each other so the mark takes no room at all.
            let run = text.get(p.start..p.end).unwrap_or_default();
            let chars = run.chars().count().max(1) as f32;
            let width = ui.ctx().fonts_mut(|f| f.layout_no_wrap(run.to_string(), format.font_id.clone(), Color32::TRANSPARENT).size().x);
            format.extra_letter_spacing = -(width / chars);
        }
        job.sections.push(LayoutSection { leading_space: leading, byte_range: egui::text::ByteIndex(p.start)..egui::text::ByteIndex(p.end), format });
    }
    job
}

fn format(s: &Style, base: f32, row_height: f32, palette: Palette, bold_only: bool) -> TextFormat {
    if s.mark {
        // A sliver of transparent text: the characters stay for the cursor, but cannot be seen.
        return TextFormat { font_id: FontId::proportional(1.0), color: Color32::TRANSPARENT, line_height: Some(row_height), ..Default::default() };
    }
    let size = (base * s.size).clamp(6.0, 48.0);
    let family = if s.mono { FontFamily::Monospace } else { FontFamily::Proportional };
    let mut color = if s.link {
        palette.link
    } else if s.muted {
        palette.muted
    } else {
        s.color.unwrap_or(palette.text)
    };
    let mut background = s.background.unwrap_or(Color32::TRANSPARENT);
    if bold_only && !s.bold {
        color = Color32::TRANSPARENT;
        background = Color32::TRANSPARENT;
    }
    TextFormat {
        font_id: FontId::new(size, family),
        color,
        background: if bold_only { Color32::TRANSPARENT } else { background },
        italics: s.italic,
        underline: if (s.underline || s.link) && !bold_only { Stroke::new(1.0, color) } else { Stroke::NONE },
        ..Default::default()
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::panic, clippy::indexing_slicing)]
mod tests {
    use super::*;

    fn visible(text: &str) -> String {
        pieces(text).iter().filter(|p| !p.style.mark).map(|p| &text[p.start..p.end]).collect()
    }

    #[test]
    fn marks_are_hidden_and_styles_apply() {
        let text = "Ciao **tutti**, *oggi* __sì__ {color=#ff0000}rosso{/color} {size=huge}grande{/size}\n- uno\n1. due\n{indent}{align=center}centro\n[sito](https://a.it) https://b.it fine";
        let ps = pieces(text);
        // Pieces cover every byte in order.
        let mut at = 0;
        for p in &ps {
            assert_eq!(p.start, at);
            at = p.end;
        }
        assert_eq!(at, text.len());
        assert_eq!(visible(text), "Ciao tutti, oggi sì rosso grande\n- uno\n1. due\ncentro\nsitohttps://a.it https://b.it fine");
        let of = |s: &str| ps.iter().find(|p| text[p.start..p.end].starts_with(s)).cloned().unwrap_or_else(|| panic!("no piece {s}"));
        assert!(of("tutti").style.bold);
        assert!(of("oggi").style.italic);
        assert!(of("sì").style.underline);
        assert_eq!(of("rosso").style.color, Some(Color32::from_rgb(255, 0, 0)));
        assert!(of("grande").style.size > 1.5);
        assert!(of("- ").style.muted && of("1. ").style.muted);
        assert!(of("sito").style.link && of("https://b.it").style.link);
        assert!(of("https://a.it").style.muted);
        let centre = of("centro");
        let first = ps.iter().find(|p| p.start == text.find("{indent}").unwrap_or(0)).cloned().unwrap_or(centre.clone());
        assert_eq!(first.align, 0.5);
        assert_eq!(first.leading, INDENT);
    }

    #[test]
    fn unmatched_marks_stay_visible() {
        assert_eq!(visible("a * b, c __ d, {x} {/color} [e](f g)"), "a * b, c __ d, {x} {/color} [e](f g)");
        assert_eq!(visible(""), "");
        assert_eq!(visible("**"), "**");
        assert_eq!(visible("{size=huge}"), "");
    }

    #[test]
    fn bold_without_text_hides_both_marks() {
        // The toolbar inserts `****` with the caret in the middle: both halves are marks.
        assert_eq!(visible("****"), "");
        assert_eq!(visible("**x**"), "x");
    }
}
