//! Markdown outlines (headings and nested lists) and plain indented text. Lines like
//! `A → B (label)` or `A -> B: label` after the outline become free links, which is what
//! [`crate::to_markdown`] writes.

use crate::build::{self, Link, Topic};
use crate::{Error, Result};

/// Indentation width, tabs counting as four spaces.
pub(crate) fn indent(line: &str) -> usize {
    line.chars().take_while(|c| c.is_whitespace()).map(|c| if c == '\t' { 4 } else { 1 }).sum()
}

fn bullet(line: &str) -> Option<&str> {
    let t = line.trim_start();
    let rest = ["- ", "* ", "+ "].iter().find_map(|m| t.strip_prefix(m)).or_else(|| {
        let digits = t.chars().take_while(char::is_ascii_digit).count();
        (digits > 0).then(|| t.get(digits..)).flatten().and_then(|r| r.strip_prefix(". ").or_else(|| r.strip_prefix(") ")))
    })?;
    let rest = rest.trim();
    Some(["[ ] ", "[x] ", "[X] "].iter().find_map(|c| rest.strip_prefix(c)).unwrap_or(rest))
}

fn heading(line: &str) -> Option<(usize, &str)> {
    let t = line.trim_start();
    let hashes = t.chars().take_while(|c| *c == '#').count();
    (1..=6)
        .contains(&hashes)
        .then(|| t.get(hashes..))
        .flatten()
        .filter(|r| r.starts_with(' '))
        .map(|r| (hashes, r.trim().trim_end_matches('#').trim()))
}

fn link(line: &str) -> Option<Link> {
    let t = line.trim();
    let (a, rest) = t.split_once(" → ").or_else(|| t.split_once(" -> "))?;
    let (b, label) = if let Some((b, l)) = rest.split_once(": ") {
        (b, l.to_string())
    } else if let (Some(open), true) = (rest.rfind(" ("), rest.ends_with(')')) {
        (rest.get(..open)?, rest.get(open + 2..rest.len() - 1)?.to_string())
    } else {
        (rest, String::new())
    };
    Some(Link { from: a.trim().to_string(), to: b.trim().to_string(), label })
}

/// Strips inline Markdown that reads badly on a canvas.
fn plain(text: &str) -> String {
    let t = text.replace("**", "").replace("__", "").replace('`', "");
    // [label](url) → label
    let mut out = String::new();
    let mut rest = t.as_str();
    while let Some(open) = rest.find('[') {
        let (before, after) = rest.split_at(open);
        out.push_str(before);
        match after.find("](").zip(after.find(')')) {
            Some((mid, close)) if mid < close => {
                out.push_str(after.get(1..mid).unwrap_or_default());
                rest = after.get(close + 1..).unwrap_or_default();
            }
            _ => {
                out.push('[');
                rest = after.get(1..).unwrap_or_default();
            }
        }
    }
    out.push_str(rest);
    out.trim().to_string()
}

/// Depth-tagged items to topic trees; depth jumps are clamped to one level.
pub(crate) fn tree(items: Vec<(usize, Topic)>) -> Vec<Topic> {
    // Build bottom-up with a stack of open topics.
    let mut roots: Vec<Topic> = Vec::new();
    let mut open: Vec<(usize, Topic)> = Vec::new();
    let close = |open: &mut Vec<(usize, Topic)>, roots: &mut Vec<Topic>| {
        if let Some((_, done)) = open.pop() {
            match open.last_mut() {
                Some((_, parent)) => parent.children.push(done),
                None => roots.push(done),
            }
        }
    };
    for (depth, topic) in items {
        while open.last().is_some_and(|(d, _)| *d >= depth) {
            close(&mut open, &mut roots);
        }
        let depth = open.last().map_or(0, |(d, _)| d + 1).min(depth);
        open.push((depth, topic));
    }
    while !open.is_empty() {
        close(&mut open, &mut roots);
    }
    roots
}

/// Ranks indentation widths: the stack of widths seen in the current block.
pub(crate) fn rank(stack: &mut Vec<usize>, width: usize) -> usize {
    while stack.last().is_some_and(|w| *w > width) {
        stack.pop();
    }
    if stack.last() != Some(&width) {
        stack.push(width);
    }
    stack.len().saturating_sub(1)
}

pub fn import(text: &str) -> Result<ideacraft_core::Map> {
    let lines: Vec<&str> = {
        let mut fenced = false;
        text.lines()
            .filter(|l| {
                if l.trim_start().starts_with("```") {
                    fenced = !fenced;
                    return false;
                }
                !fenced && !l.trim().is_empty()
            })
            .collect()
    };
    let structured = lines.iter().any(|l| heading(l).is_some() || bullet(l).is_some());
    let mut items = Vec::new();
    let mut links = Vec::new();
    let mut base = 0usize;
    let mut widths = Vec::new();
    for line in lines {
        if let Some(l) = link(line).filter(|_| bullet(line).is_none()) {
            links.push(l);
        } else if let Some((level, text)) = heading(line) {
            items.push((level - 1, Topic::new(plain(text))));
            base = level;
            widths.clear();
        } else if let Some(text) = bullet(line) {
            items.push((base + rank(&mut widths, indent(line)), Topic::new(plain(text))));
        } else if !structured {
            items.push((rank(&mut widths, indent(line)), Topic::new(plain(line))));
        }
    }
    if items.is_empty() {
        return Err(Error::Format("no outline found".into()));
    }
    let mut roots = tree(items);
    if roots.len() > 1 {
        let mut root = Topic::new("Outline");
        root.children = std::mem::take(&mut roots);
        roots = vec![root];
    }
    let title = roots.first().map(|r| r.label.clone()).unwrap_or_default();
    Ok(build::build(&title, roots, &links))
}

#[cfg(test)]
mod tests {
    #[test]
    fn headings_lists_links_and_inline_markup() {
        let md = "# Study plan\n\n## Maths\n- Algebra\n  - [ ] **Equations**\n  - Matrices\n1. Geometry\n## History\n* [Rome](https://example.org)\n\n```\nnot - a bullet\n```\nAlgebra → Rome (unrelated)\n";
        let m = super::import(md).unwrap();
        assert_eq!(m.title(), "Study plan");
        assert_eq!(m.node_count(), 8);
        let find = |l: &str| m.nodes().find(|n| n.label == l).unwrap().id;
        assert_eq!(m.node(find("Equations")).unwrap().parent, Some(find("Algebra")));
        assert_eq!(m.node(find("Geometry")).unwrap().parent, Some(find("Maths")));
        assert_eq!(m.edges().next().unwrap().label, "unrelated");
    }

    #[test]
    fn plain_indented_text_and_several_roots() {
        let m = super::import("Animals\n\tMammals\n\t\tDog\n\tBirds\nPlants\n").unwrap();
        assert_eq!(m.node_count(), 6, "two top lines hang under an Outline root");
        assert!(super::import("\n\n").is_err());
    }
}
