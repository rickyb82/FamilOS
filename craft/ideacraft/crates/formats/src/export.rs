//! Writing maps for other tools.

use ideacraft_core::{Map, NodeId};

use crate::xml::escape;

/// Depth-first walk of every tree: (node, depth), children top to bottom.
fn walk(map: &Map) -> Vec<(NodeId, usize)> {
    let mut out = Vec::new();
    let mut stack: Vec<(NodeId, usize)> = map.roots().into_iter().rev().map(|r| (r, 0)).collect();
    while let Some((id, depth)) = stack.pop() {
        if out.len() >= ideacraft_core::limits::MAX_NODES {
            break;
        }
        out.push((id, depth));
        for kid in map.children(id).into_iter().rev() {
            stack.push((kid, depth + 1));
        }
    }
    out
}

fn one_line(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A Markdown outline: `# title`, nested bullets, then `A → B (label)` lines for free links.
/// [`crate::import_text`] reads it back.
pub fn to_markdown(map: &Map) -> String {
    // One tree: its root is the heading. Several: the map title is, and the trees hang under it.
    let single = map.roots().len() == 1;
    let mut out = if single { String::new() } else { format!("# {}\n\n", one_line(map.title())) };
    for (id, depth) in walk(map) {
        if let Some(n) = map.node(id) {
            if single && depth == 0 {
                out.push_str(&format!("# {}\n\n", one_line(&n.label)));
            } else {
                let depth = if single { depth - 1 } else { depth };
                out.push_str(&format!("{}- {}\n", "  ".repeat(depth.min(64)), one_line(&n.label)));
            }
        }
    }
    if map.edge_count() > 0 {
        out.push('\n');
    }
    for e in map.edges() {
        let name = |id| map.node(id).map(|n| one_line(&n.label)).unwrap_or_default();
        let label = if e.label.is_empty() { String::new() } else { format!(" ({})", one_line(&e.label)) };
        out.push_str(&format!("{} → {}{label}\n", name(e.from), name(e.to)));
    }
    out
}

fn nested(map: &Map, open: impl Fn(NodeId, &mut String), close: &str) -> String {
    let mut out = String::new();
    let mut depth_stack: Vec<usize> = Vec::new();
    for (id, depth) in walk(map) {
        while depth_stack.last().is_some_and(|d| *d >= depth) {
            depth_stack.pop();
            out.push_str(close);
        }
        open(id, &mut out);
        depth_stack.push(depth);
    }
    for _ in depth_stack {
        out.push_str(close);
    }
    out
}

/// OPML 2.0 outline (trees only; links are not part of OPML).
pub fn to_opml(map: &Map) -> String {
    let body = nested(
        map,
        |id, out| out.push_str(&format!("<outline text=\"{}\">", map.node(id).map(|n| escape(&n.label)).unwrap_or_default())),
        "</outline>\n",
    );
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<opml version=\"2.0\">\n<head><title>{}</title></head>\n<body>\n{body}</body>\n</opml>\n",
        escape(map.title())
    )
}

/// FreeMind 1.0 `.mm`, which FreeMind, Freeplane and many other tools open. Several trees are
/// gathered under one node named after the map.
pub fn to_freemind(map: &Map) -> String {
    let several = map.roots().len() != 1;
    let node = |id: NodeId, out: &mut String| {
        let Some(n) = map.node(id) else { return };
        out.push_str(&format!("<node ID=\"ID_{}\" TEXT=\"{}\" BACKGROUND_COLOR=\"{}\"", n.id.0, escape(&n.label), n.color.hex()));
        // Main branches say which side of the root they are on, as FreeMind expects.
        if let Some(root) = n.parent.and_then(|p| map.node(p)).filter(|p| p.parent.is_none()) {
            out.push_str(if n.pos.x < root.pos.x { " POSITION=\"left\"" } else { " POSITION=\"right\"" });
        }
        if n.collapsed {
            out.push_str(" FOLDED=\"true\"");
        }
        out.push('>');
        for e in map.edges().filter(|e| e.from == id) {
            out.push_str(&format!("<arrowlink DESTINATION=\"ID_{}\" MIDDLE_LABEL=\"{}\"/>", e.to.0, escape(&e.label)));
        }
    };
    let body = nested(map, node, "</node>\n");
    let body = if several { format!("<node TEXT=\"{}\">\n{body}</node>\n", escape(map.title())) } else { body };
    format!("<map version=\"1.0.1\">\n{body}</map>\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Format;

    #[test]
    fn exports_read_back() {
        let map = Map::sample();
        for (text, format) in [(to_markdown(&map), Format::Markdown), (to_opml(&map), Format::Opml), (to_freemind(&map), Format::FreeMind)] {
            let back = crate::import_text(&text, format).unwrap();
            assert_eq!(back.node_count(), map.node_count(), "{format:?}");
            if format != Format::Opml {
                assert_eq!(back.edge_count(), map.edge_count(), "{format:?}");
            }
        }
        assert!(to_freemind(&map).contains("FOLDED") == map.nodes().any(|n| n.collapsed));
    }
}
