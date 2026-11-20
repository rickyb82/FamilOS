//! Mermaid text: `mindmap` diagrams (indentation tree with node shapes) and simple
//! `flowchart`/`graph` diagrams (`A[Text] --> B` and `A -->|label| B`).

use std::collections::HashMap;

use ideacraft_core::Shape;

use crate::build::{self, Link, Topic};
use crate::markdown::{indent, rank, tree};
use crate::{Error, Result};

const OPENERS: [(&str, &str, Shape); 8] = [
    ("((", "))", Shape::Ellipse),
    ("))", "((", Shape::Ellipse),
    ("{{", "}}", Shape::Rounded),
    ("(", ")", Shape::Pill),
    (")", "(", Shape::Pill),
    ("[", "]", Shape::Rect),
    ("{", "}", Shape::Rounded),
    (">", "]", Shape::Rect),
];

fn unquote(text: &str) -> String {
    let t = text.trim();
    let t = t.strip_prefix('"').and_then(|t| t.strip_suffix('"')).unwrap_or(t);
    let t = t.strip_prefix('`').and_then(|t| t.strip_suffix('`')).unwrap_or(t);
    t.replace("<br>", "\n").replace("<br/>", "\n").trim().to_string()
}

/// `id[text]`, `id((text))`… → (id, text, shape). A bare word is its own id and text.
fn node(spec: &str) -> (String, String, Option<Shape>) {
    let spec = spec.trim();
    let spec = spec.split_once(":::").map_or(spec, |(s, _)| s).trim();
    let id_len = spec.find(|c: char| !(c.is_alphanumeric() || c == '_' || c == '-')).unwrap_or(spec.len());
    let (id, rest) = spec.split_at(id_len);
    for (open, close, shape) in OPENERS {
        if let Some(inner) = rest.strip_prefix(open).and_then(|r| r.strip_suffix(close)) {
            let inner = inner.trim_matches(|c| "()[]{}".contains(c));
            return (if id.is_empty() { unquote(inner) } else { id.to_string() }, unquote(inner), Some(shape));
        }
    }
    (spec.to_string(), unquote(spec), None)
}

fn mindmap(lines: &[&str]) -> Result<ideacraft_core::Map> {
    let mut widths = Vec::new();
    let mut items = Vec::new();
    for line in lines {
        let t = line.trim();
        if t.starts_with("::icon(") || t.starts_with(":::") {
            continue;
        }
        let (_, text, shape) = node(t);
        let mut topic = Topic::new(text);
        topic.shape = shape;
        items.push((rank(&mut widths, indent(line)), topic));
    }
    let roots = tree(items);
    let title = roots.first().map(|r| r.label.clone()).ok_or_else(|| Error::Format("empty mindmap".into()))?;
    Ok(build::build(&title, roots, &[]))
}

/// Splits `A -->|x| B -- y --> C` into nodes and (arrow label) pairs.
fn chain(line: &str) -> Vec<(String, Option<String>)> {
    let mut parts: Vec<(String, Option<String>)> = Vec::new();
    let mut rest = line.trim();
    loop {
        let arrow = ["-.->", "-->", "==>", "---", "-.-", "===", "--o", "--x", "-- ", "== "]
            .iter()
            .filter_map(|a| rest.find(a).map(|i| (i, *a)))
            .min_by_key(|(i, _)| *i);
        let Some((at, a)) = arrow else {
            parts.push((rest.to_string(), None));
            break;
        };
        let (left, after) = rest.split_at(at);
        let mut after = after.get(a.len()..).unwrap_or_default();
        let mut label = None;
        if a.ends_with(' ') {
            // `-- label -->` form.
            if let Some(end) = after.find("-->").or_else(|| after.find("==>")).or_else(|| after.find("---")) {
                label = Some(after.get(..end).unwrap_or_default().trim().to_string());
                after = after.get(end + 3..).unwrap_or_default();
            }
        }
        let after = after.trim_start_matches(['>', '-', '.', '=']).trim_start();
        let after = if let Some(r) = after.strip_prefix('|') {
            match r.split_once('|') {
                Some((l, r)) => {
                    label = Some(l.trim().to_string());
                    r
                }
                None => r,
            }
        } else {
            after
        };
        parts.push((left.to_string(), Some(label.unwrap_or_default())));
        if after.trim().is_empty() || parts.len() > 64 {
            break;
        }
        rest = after.trim();
    }
    parts
}

fn flowchart(lines: &[&str]) -> Result<ideacraft_core::Map> {
    let mut order: Vec<String> = Vec::new();
    let mut nodes: HashMap<String, Topic> = HashMap::new();
    let mut edges: Vec<(String, String, String)> = Vec::new();
    for line in lines {
        let t = line.trim();
        if t.is_empty()
            || t.starts_with("%%")
            || ["subgraph", "end", "style", "classDef", "class ", "click", "linkStyle"].iter().any(|k| t.starts_with(k))
        {
            continue;
        }
        let parts = chain(t.trim_end_matches(';'));
        // Ideas on the left of the last arrow, with that arrow's label.
        let mut prev: Vec<(String, String)> = Vec::new();
        for (group, arrow) in parts {
            let mut here = Vec::new();
            for spec in group.split(" & ") {
                let (id, text, shape) = node(spec);
                if id.is_empty() {
                    continue;
                }
                let entry = nodes.entry(id.clone()).or_insert_with(|| {
                    order.push(id.clone());
                    let mut t = Topic::new(text.clone());
                    t.key = Some(id.clone());
                    t
                });
                if spec.trim() != id {
                    entry.label = text;
                    entry.shape = shape;
                }
                for (from, label) in &prev {
                    edges.push((from.clone(), id.clone(), label.clone()));
                }
                here.push(id);
            }
            prev = match arrow {
                Some(label) => here.into_iter().map(|id| (id, label.clone())).collect(),
                None => Vec::new(),
            };
        }
    }
    if order.is_empty() {
        return Err(Error::Format("empty flowchart".into()));
    }
    // Unlabelled edges form the tree (first parent wins, no cycles); the rest stay links.
    let mut parent: HashMap<String, String> = HashMap::new();
    let mut links = Vec::new();
    for (a, b, label) in edges {
        let mut cur = Some(a.clone());
        let mut steps = 0;
        let mut cycle = false;
        while let Some(c) = cur {
            if c == b || steps > order.len() {
                cycle = true;
                break;
            }
            cur = parent.get(&c).cloned();
            steps += 1;
        }
        if label.is_empty() && !cycle && !parent.contains_key(&b) {
            parent.insert(b, a);
        } else {
            links.push(Link { from: a, to: b, label });
        }
    }
    let mut kids: HashMap<String, Vec<String>> = HashMap::new();
    for id in &order {
        if let Some(p) = parent.get(id) {
            kids.entry(p.clone()).or_default().push(id.clone());
        }
    }
    fn grow(id: &str, nodes: &mut HashMap<String, Topic>, kids: &HashMap<String, Vec<String>>, depth: usize) -> Topic {
        let mut t = nodes.remove(id).unwrap_or_default();
        if depth < 256 {
            t.children = kids.get(id).into_iter().flatten().map(|k| grow(k, nodes, kids, depth + 1)).collect();
        }
        t
    }
    let roots: Vec<Topic> =
        order.iter().filter(|id| !parent.contains_key(*id)).cloned().collect::<Vec<_>>().iter().map(|id| grow(id, &mut nodes, &kids, 0)).collect();
    let title = roots.first().map(|r| r.label.clone()).unwrap_or_default();
    Ok(build::build(&title, roots, &links))
}

pub fn import(text: &str) -> Result<ideacraft_core::Map> {
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty() && !l.trim_start().starts_with("%%")).collect();
    let (first, rest) = lines.split_first().ok_or_else(|| Error::Format("empty Mermaid file".into()))?;
    let first = first.trim();
    if first == "mindmap" {
        mindmap(rest)
    } else if first.starts_with("graph") || first.starts_with("flowchart") {
        flowchart(rest)
    } else {
        Err(Error::Format(format!("unsupported Mermaid diagram {:?}", first.chars().take(24).collect::<String>())))
    }
}

#[cfg(test)]
mod tests {
    use ideacraft_core::Shape;

    #[test]
    fn mindmap_shapes_and_indentation() {
        let m = super::import(
            "mindmap\n  root((Holiday))\n    Beach\n      id1[Sun cream]\n      ::icon(fa fa-sun)\n    Mountains\n      id2(\"Boots\")\n",
        )
        .unwrap();
        assert_eq!(m.title(), "Holiday");
        assert_eq!(m.node_count(), 5);
        assert!(m.nodes().any(|n| n.label == "Sun cream" && n.shape == Shape::Rect));
        assert!(m.nodes().any(|n| n.label == "Boots" && n.shape == Shape::Pill));
    }

    #[test]
    fn flowchart_edges_build_a_tree_and_labelled_links() {
        let m = super::import("flowchart TD\n  A[Idea] --> B(Plan)\n  B --> C{Build}\n  A -->|feeds| C\n  C -- loops back --> B\n  B --> D & E\n")
            .unwrap();
        let id = |l: &str| m.nodes().find(|n| n.label == l).unwrap().id;
        assert_eq!(m.node(id("Plan")).unwrap().parent, Some(id("Idea")));
        assert_eq!(m.node(id("Build")).unwrap().parent, Some(id("Plan")));
        assert_eq!(m.edge_count(), 2);
        assert!(m.edges().any(|e| e.label == "loops back"));
        assert!(super::import("sequenceDiagram\n A->>B: hi").is_err());
    }
}
