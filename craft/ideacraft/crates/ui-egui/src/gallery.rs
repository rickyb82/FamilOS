//! Template previews: a miniature of a map.

use egui::{Color32, Pos2, Rect, Stroke, StrokeKind, pos2, vec2};
use ideacraft_engine::core::Map;

use crate::theme::Tokens;

/// Draws a miniature of `map` inside `rect`.
pub fn thumbnail(painter: &egui::Painter, map: &Map, rect: Rect, t: &Tokens) {
    painter.rect_filled(rect, 6.0, t.canvas);
    let nodes: Vec<_> = map.nodes().filter(|n| map.is_visible(n.id)).collect();
    if nodes.is_empty() {
        return;
    }
    let (mut lo, mut hi) = (pos2(f32::MAX, f32::MAX), pos2(f32::MIN, f32::MIN));
    for n in &nodes {
        lo = lo.min(pos2(n.pos.x, n.pos.y));
        hi = hi.max(pos2(n.pos.x, n.pos.y));
    }
    let bounds = Rect::from_min_max(lo, hi).expand2(vec2(80.0, 30.0));
    let inner = rect.shrink(8.0);
    let scale = (inner.width() / bounds.width()).min(inner.height() / bounds.height());
    if !scale.is_finite() {
        return;
    }
    let at = |x: f32, y: f32| -> Pos2 { inner.center() + (pos2(x, y) - bounds.center()) * scale };
    let node_at = |id| map.node(id).map(|n| at(n.pos.x, n.pos.y));
    for n in &nodes {
        if let (Some(a), Some(b)) = (n.parent.and_then(node_at), node_at(n.id)) {
            painter.line_segment([a, b], Stroke::new(1.0, t.branch));
        }
    }
    for e in map.edges() {
        if let (Some(a), Some(b)) = (node_at(e.from), node_at(e.to)) {
            painter.extend(egui::Shape::dashed_line(&[a, b], Stroke::new(1.0, t.link), 3.0, 2.0));
        }
    }
    let size = vec2(110.0, 30.0) * scale;
    for n in &nodes {
        let r = Rect::from_center_size(at(n.pos.x, n.pos.y), size.max(vec2(10.0, 4.0)));
        painter.rect_filled(r, r.height() / 3.0, Color32::from_rgb(n.color.0, n.color.1, n.color.2));
        painter.rect_stroke(r, r.height() / 3.0, Stroke::new(0.5, t.node_border), StrokeKind::Inside);
    }
}
