//! Automatic layouts. Each tree keeps its root where the user put it; everything below the root
//! is placed again. Collapsed branches count as a single leaf.

use std::collections::BTreeMap;

use crate::limits::MAX_DEPTH;
use crate::{Map, NodeId, Point};

/// Horizontal distance between tree levels.
pub const LEVEL_GAP: f32 = 230.0;
/// Vertical space given to one leaf.
pub const LEAF_GAP: f32 = 56.0;
/// Distance between rings in the radial layout.
pub const RING_GAP: f32 = 210.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layout {
    /// Left-to-right tree (a classic mind map branch).
    Tree,
    /// Concentric rings around each root.
    Radial,
}

impl Layout {
    pub fn parse(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "tree" => Some(Self::Tree),
            "radial" => Some(Self::Radial),
            _ => None,
        }
    }
}

/// Visible children per node (collapsed nodes have none).
fn tree_of(map: &Map) -> BTreeMap<NodeId, Vec<NodeId>> {
    map.nodes().filter(|n| !n.collapsed).map(|n| (n.id, map.children(n.id))).collect()
}

fn leaves(tree: &BTreeMap<NodeId, Vec<NodeId>>, id: NodeId, depth: usize, memo: &mut BTreeMap<NodeId, f32>) -> f32 {
    if let Some(v) = memo.get(&id) {
        return *v;
    }
    let kids = tree.get(&id).map(Vec::as_slice).unwrap_or_default();
    let n = if kids.is_empty() || depth >= MAX_DEPTH { 1.0 } else { kids.iter().map(|k| leaves(tree, *k, depth + 1, memo)).sum::<f32>().max(1.0) };
    memo.insert(id, n);
    n
}

/// New positions for every node below a root.
pub fn positions(map: &Map, layout: Layout) -> Vec<(NodeId, Point)> {
    let tree = tree_of(map);
    let mut memo = BTreeMap::new();
    let mut out = Vec::new();
    for root in map.roots() {
        let Some(origin) = map.node(root).map(|n| n.pos) else { continue };
        // Iterative walk: (node, depth, start of its span, its span).
        let total = leaves(&tree, root, 0, &mut memo);
        let mut stack = vec![(root, 0usize, 0.0f32, total)];
        while let Some((id, depth, start, span)) = stack.pop() {
            if depth > 0 {
                let mid = start + span / 2.0;
                let pos = match layout {
                    Layout::Tree => Point::new(origin.x + depth as f32 * LEVEL_GAP, origin.y + (mid - total / 2.0) * LEAF_GAP),
                    Layout::Radial => {
                        let angle = mid / total * std::f32::consts::TAU;
                        let r = depth as f32 * RING_GAP;
                        Point::new(origin.x + r * angle.cos(), origin.y + r * angle.sin())
                    }
                };
                out.push((id, pos.clamped()));
            }
            if depth >= MAX_DEPTH {
                continue;
            }
            let mut cursor = start;
            for kid in tree.get(&id).into_iter().flatten() {
                let s = leaves(&tree, *kid, depth + 1, &mut memo);
                stack.push((*kid, depth + 1, cursor, s));
                cursor += s;
            }
        }
    }
    out
}

/// Applies a layout to the map.
pub fn apply(map: &mut Map, layout: Layout) {
    for (id, pos) in positions(map, layout) {
        // Every id comes from the map itself.
        let _ = map.set_pos(id, pos);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tree_layout_spreads_children_to_the_right_without_overlap() {
        let mut m = Map::new("t");
        let root = m.add_node("r", Point::new(100.0, 100.0), None).unwrap();
        let a = m.add_node("a", Point::default(), Some(root)).unwrap();
        let b = m.add_node("b", Point::new(0.0, 1.0), Some(root)).unwrap();
        let a1 = m.add_node("a1", Point::default(), Some(a)).unwrap();
        let a2 = m.add_node("a2", Point::new(0.0, 1.0), Some(a)).unwrap();
        apply(&mut m, Layout::Tree);
        let p = |id| m.node(id).unwrap().pos;
        assert_eq!(p(root), Point::new(100.0, 100.0), "the root stays put");
        assert_eq!(p(a).x, 100.0 + LEVEL_GAP);
        assert_eq!(p(a1).x, 100.0 + 2.0 * LEVEL_GAP);
        assert!(p(a1).y < p(a2).y && p(a2).y < p(b).y);
        assert!((p(a2).y - p(a1).y - LEAF_GAP).abs() < 0.01);
    }

    #[test]
    fn radial_layout_puts_levels_on_rings_and_skips_collapsed_branches() {
        let mut m = Map::new("t");
        let root = m.add_node("r", Point::default(), None).unwrap();
        let kids: Vec<_> = (0..4).map(|i| m.add_node("k", Point::new(0.0, i as f32), Some(root)).unwrap()).collect();
        let hidden = m.add_node("h", Point::new(5.0, 5.0), Some(kids[0])).unwrap();
        m.set_collapsed(kids[0], true).unwrap();
        apply(&mut m, Layout::Radial);
        for k in &kids {
            let p = m.node(*k).unwrap().pos;
            assert!(((p.x * p.x + p.y * p.y).sqrt() - RING_GAP).abs() < 0.1);
        }
        assert_eq!(m.node(hidden).unwrap().pos, Point::new(5.0, 5.0), "hidden nodes keep their place");
    }
}
