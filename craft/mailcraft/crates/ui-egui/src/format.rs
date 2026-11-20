//! The compose window's formatting: a light markup (`**bold**`, `*italic*`, `__underline__`,
//! `- item`, `1. item`, `[text](url)`, `![name](cid:…)`) applied to the selection of the body
//! and rendered as HTML when the message goes out (`core::mime::markup_to_html`).

use egui::text::{CCursor, CCursorRange};

/// The selected character range of a text edit, ordered.
pub fn selection(ctx: &egui::Context, id: egui::Id) -> Option<(usize, usize)> {
    let state = egui::TextEdit::load_state(ctx, id)?;
    let r = state.cursor.char_range()?;
    let (a, b) = (r.primary.index.0, r.secondary.index.0);
    Some((a.min(b), a.max(b)))
}

/// Puts the caret at a character index and gives the text edit the focus back.
pub fn set_caret(ctx: &egui::Context, id: egui::Id, index: usize) {
    let mut state = egui::TextEdit::load_state(ctx, id).unwrap_or_default();
    state.cursor.set_char_range(Some(CCursorRange::one(CCursor::new(index))));
    state.store(ctx, id);
    ctx.memory_mut(|m| m.request_focus(id));
}

fn byte_at(s: &str, chars: usize) -> usize {
    s.char_indices().nth(chars).map(|(i, _)| i).unwrap_or(s.len())
}

/// Wraps the selection (or inserts an empty pair at the caret) in `prefix`/`suffix`; returns
/// the character index the caret should go to (after the wrapped text, or inside the pair).
pub fn wrap(text: &mut String, sel: Option<(usize, usize)>, prefix: &str, suffix: &str) -> usize {
    let (a, b) = sel.unwrap_or((text.chars().count(), text.chars().count()));
    let (ba, bb) = (byte_at(text, a), byte_at(text, b));
    let inner = text.get(ba..bb).unwrap_or_default().to_string();
    let replacement = format!("{prefix}{inner}{suffix}");
    text.replace_range(ba..bb, &replacement);
    if inner.is_empty() { a + prefix.chars().count() } else { a + replacement.chars().count() }
}

/// Starts every line touched by the selection (or the caret's line) as a list item.
pub fn list(text: &mut String, sel: Option<(usize, usize)>, numbered: bool) -> usize {
    let (a, b) = sel.unwrap_or((text.chars().count(), text.chars().count()));
    let (ba, bb) = (byte_at(text, a), byte_at(text, b));
    let start = text.get(..ba).and_then(|s| s.rfind('\n')).map_or(0, |i| i + 1);
    let end = text.get(bb..).and_then(|s| s.find('\n')).map_or(text.len(), |i| bb + i);
    let block = text.get(start..end).unwrap_or_default().to_string();
    let lines: Vec<String> = block
        .split('\n')
        .enumerate()
        .map(|(i, l)| {
            let bare = l.trim_start_matches("- ").trim_start_matches("* ");
            let bare = bare.find(". ").filter(|i| *i > 0 && bare[..*i].chars().all(|c| c.is_ascii_digit())).map_or(bare, |i| &bare[i + 2..]);
            if numbered { format!("{}. {bare}", i + 1) } else { format!("- {bare}") }
        })
        .collect();
    let replacement = lines.join("\n");
    text.replace_range(start..end, &replacement);
    text.get(..start).map_or(0, |s| s.chars().count()) + replacement.chars().count()
}

/// Wraps the selection in a styled span: `{key=value}…{/key}`.
pub fn span(text: &mut String, sel: Option<(usize, usize)>, key: &str, value: &str) -> usize {
    wrap(text, sel, &format!("{{{key}={value}}}"), &format!("{{/{key}}}"))
}

/// Puts a mark at the start of every line touched by the selection (alignment, indent).
/// An alignment replaces a previous one; `{indent}` adds up.
pub fn line_mark(text: &mut String, sel: Option<(usize, usize)>, mark: &str) -> usize {
    let (a, b) = sel.unwrap_or((text.chars().count(), text.chars().count()));
    let (ba, bb) = (byte_at(text, a), byte_at(text, b));
    let start = text.get(..ba).and_then(|s| s.rfind('\n')).map_or(0, |i| i + 1);
    let end = text.get(bb..).and_then(|s| s.find('\n')).map_or(text.len(), |i| bb + i);
    let block = text.get(start..end).unwrap_or_default().to_string();
    let lines: Vec<String> = block
        .split('\n')
        .map(|l| {
            let mut l = l.to_string();
            if mark.starts_with("{align=") {
                while let Some(i) = l.find("{align=") {
                    let Some(j) = l[i..].find('}') else { break };
                    l.replace_range(i..i + j + 1, "");
                }
                if mark == "{align=left}" {
                    return l.trim_start().to_string();
                }
            }
            format!("{mark}{}", l.trim_start())
        })
        .collect();
    let replacement = lines.join("\n");
    text.replace_range(start..end, &replacement);
    text.get(..start).map_or(0, |s| s.chars().count()) + replacement.chars().count()
}

/// Undo or redo the body's last edit through the text edit's own history.
pub fn undo(ctx: &egui::Context, id: egui::Id, text: &mut String, redo: bool) -> bool {
    let Some(mut state) = egui::TextEdit::load_state(ctx, id) else { return false };
    let range = state.cursor.char_range().unwrap_or_default();
    let current = (range, text.clone());
    let mut undoer = state.undoer();
    let previous = if redo { undoer.redo(&current).cloned() } else { undoer.undo(&current).cloned() };
    state.set_undoer(undoer);
    match previous {
        Some((range, t)) => {
            *text = t;
            state.cursor.set_char_range(Some(range));
            state.store(ctx, id);
            ctx.memory_mut(|m| m.request_focus(id));
            true
        }
        None => false,
    }
}

/// `[selection](https://)` with the caret ready for the address.
pub fn link(text: &mut String, sel: Option<(usize, usize)>) -> usize {
    let (a, b) = sel.unwrap_or((text.chars().count(), text.chars().count()));
    let (ba, bb) = (byte_at(text, a), byte_at(text, b));
    let inner = text.get(ba..bb).unwrap_or_default().to_string();
    let label = if inner.is_empty() { "link".to_string() } else { inner };
    let replacement = format!("[{label}](https://)");
    text.replace_range(ba..bb, &replacement);
    a + replacement.chars().count() - 1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markup_is_applied_to_the_selection() {
        let mut t = "Ciao Luca, è urgente".to_string();
        assert_eq!(wrap(&mut t, Some((5, 9)), "**", "**"), 13);
        assert_eq!(t, "Ciao **Luca**, è urgente");
        assert_eq!(wrap(&mut t, None, "*", "*"), t.chars().count() - 1);
        assert!(t.ends_with("urgente**"));
        let mut l = "uno\ndue\ntre".to_string();
        assert_eq!(list(&mut l, Some((0, 6)), false), 11);
        assert_eq!(l, "- uno\n- due\ntre");
        list(&mut l, Some((0, 14)), true);
        assert_eq!(l, "1. uno\n2. due\n3. tre");
        let mut k = "vedi qui".to_string();
        assert_eq!(link(&mut k, Some((5, 8))), 19);
        assert_eq!(k, "vedi [qui](https://)");
        let mut c = "rosso e blu".to_string();
        span(&mut c, Some((0, 5)), "color", "#e53935");
        assert_eq!(c, "{color=#e53935}rosso{/color} e blu");
        let mut a = "uno\ndue".to_string();
        line_mark(&mut a, Some((0, 1)), "{align=center}");
        assert_eq!(a, "{align=center}uno\ndue");
        line_mark(&mut a, Some((0, 1)), "{align=right}");
        assert_eq!(a, "{align=right}uno\ndue");
        line_mark(&mut a, Some((0, 1)), "{align=left}");
        assert_eq!(a, "uno\ndue");
        line_mark(&mut a, Some((5, 6)), "{indent}");
        line_mark(&mut a, Some((5, 6)), "{indent}");
        assert_eq!(a, "uno\n{indent}{indent}due");
    }
}
