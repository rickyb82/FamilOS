//! Turns a parsed outline (topics + links) into a styled, laid-out map.

use std::collections::HashMap;

use ideacraft_core::layout::{self, Layout};
use ideacraft_core::{Map, NodeId, Point, Rgb, Shape};

/// One topic of an imported tree.
#[derive(Debug, Default, Clone)]
pub struct Topic {
    pub label: String,
    /// The source format's id, for links.
    pub key: Option<String>,
    pub color: Option<Rgb>,
    pub shape: Option<Shape>,
    pub folded: bool,
    pub pos: Option<Point>,
    pub children: Vec<Topic>,
}

impl Topic {
    pub fn new(label: impl Into<String>) -> Self {
        Self { label: label.into(), ..Self::default() }
    }
}

/// A free link between two keyed topics (or, failing a key, two labels).
#[derive(Debug, Clone)]
pub struct Link {
    pub from: String,
    pub to: String,
    pub label: String,
}

/// Branch colours given to first-level branches when the source has none (from [`Rgb::PALETTE`]).
pub const BRANCH_COLORS: [Rgb; 7] = [
    Rgb(0xb3, 0xd4, 0xff),
    Rgb(0xb1, 0xec, 0xd8),
    Rgb(0xd4, 0xc4, 0xfb),
    Rgb(0xff, 0xc2, 0xa8),
    Rgb(0xf6, 0xb8, 0xd1),
    Rgb(0xd9, 0xf2, 0xa6),
    Rgb(0xff, 0xe0, 0x8a),
];

pub const ROOT_COLOR: Rgb = Rgb(0xff, 0xe0, 0x8a);

/// Builds a map. Without source positions the result gets a tree layout; without source colours
/// each first-level branch gets its own colour.
pub fn build(title: &str, roots: Vec<Topic>, links: &[Link]) -> Map {
    let mut map = Map::new(title);
    let mut keys: HashMap<String, NodeId> = HashMap::new();
    let mut labels: HashMap<String, NodeId> = HashMap::new();
    let any_pos = roots.iter().any(|r| r.pos.is_some());
    let any_color = has_color(&roots);
    let mut next_root_y = 0.0f32;
    // (topic, parent, depth, branch colour)
    let mut stack: Vec<(Topic, Option<NodeId>, usize, Option<Rgb>)> = roots.into_iter().rev().map(|t| (t, None, 0, None)).collect();
    let mut branch_index = 0usize;
    while let Some((mut topic, parent, depth, inherited)) = stack.pop() {
        let pos = topic.pos.unwrap_or_else(|| {
            if parent.is_none() {
                next_root_y += 400.0;
                Point::new(0.0, next_root_y - 400.0)
            } else {
                Point::default()
            }
        });
        let Ok(id) = map.add_node(topic.label.trim(), pos, parent) else { break };
        let color = topic.color.or(if any_color {
            None
        } else if depth == 0 {
            Some(ROOT_COLOR)
        } else if depth == 1 {
            branch_index += 1;
            BRANCH_COLORS.get((branch_index - 1) % BRANCH_COLORS.len()).copied()
        } else {
            inherited
        });
        let _ = map.set_color(id, color.unwrap_or(Rgb::WHITE));
        let shape = topic.shape.or(if depth == 0 && parent.is_none() && !any_pos { Some(Shape::Pill) } else { None });
        if let Some(s) = shape {
            let _ = map.set_shape(id, s);
        }
        if topic.folded && !topic.children.is_empty() {
            let _ = map.set_collapsed(id, true);
        }
        if let Some(k) = topic.key.take() {
            keys.insert(k, id);
        }
        labels.entry(topic.label.trim().to_string()).or_insert(id);
        // Children keep their order: give them increasing y so `children()` sorts them right.
        for (i, mut kid) in std::mem::take(&mut topic.children).into_iter().enumerate().rev() {
            if kid.pos.is_none() && any_pos {
                kid.pos = Some(Point::new(pos.x + 200.0, pos.y + i as f32 * 50.0));
            } else if kid.pos.is_none() {
                kid.pos = Some(Point::new(0.0, i as f32));
            }
            stack.push((kid, Some(id), depth + 1, color));
        }
    }
    for l in links {
        let find = |k: &str| keys.get(k).or_else(|| labels.get(k.trim())).copied();
        if let (Some(a), Some(b)) = (find(&l.from), find(&l.to)) {
            let _ = map.connect(a, b, &l.label);
        }
    }
    if !any_pos {
        layout::apply(&mut map, Layout::Tree);
    }
    map
}

fn has_color(roots: &[Topic]) -> bool {
    let mut stack: Vec<&Topic> = roots.iter().collect();
    while let Some(t) = stack.pop() {
        if t.color.is_some() {
            return true;
        }
        stack.extend(t.children.iter());
    }
    false
}

/// Parses `#rgb`, `#rrggbb`, `#rrggbbaa` and `r,g,b[,a]` colours.
pub fn color(text: &str) -> Option<Rgb> {
    let t = text.trim();
    if let Some(hex) = t.strip_prefix('#') {
        let hex = match hex.len() {
            3 => hex.chars().flat_map(|c| [c, c]).collect::<String>(),
            6 => hex.to_string(),
            8 => hex.get(..6)?.to_string(),
            _ => return None,
        };
        return Rgb::parse(&hex).ok();
    }
    let parts: Vec<u8> = t.split(',').filter_map(|p| p.trim().parse::<u8>().ok()).collect();
    match parts.as_slice() {
        [r, g, b] | [r, g, b, _] => Some(Rgb(*r, *g, *b)),
        _ => None,
    }
}
