//! The infinite canvas: draws ideas, shapes and links, and turns pointer and keyboard input into
//! commands. Map mode and diagram mode share it: diagram mode adds sized shapes with stencil
//! glyphs, resize handles, marquee selection, snapping and selectable, styled connectors.

use std::sync::Arc;

use egui::epaint::{CubicBezierShape, Mesh, Vertex, WHITE_UV};
use egui::{Align2, Color32, FontId, Galley, Key, Modifiers, Pos2, Rect, Sense, Shape as EShape, Stroke, StrokeKind, Vec2, pos2, vec2};
use ideacraft_engine::core::route::{self, Aabb};
use ideacraft_engine::core::{Arrows, Edge, EdgeId, LineStyle, Map, Mode, Node, NodeId, Point, Route, Shape, Status, stencil};
use serde_json::json;

use crate::IdeaApp;
use crate::theme::Tokens;

const MIN_ZOOM: f32 = 0.1;
const MAX_ZOOM: f32 = 4.0;
const PAD: Vec2 = vec2(14.0, 9.0);
const MIN_WIDTH: f32 = 70.0;
const WRAP: f32 = 220.0;
const FONT: f32 = 15.0;
/// Snap grid in diagram mode (map units).
pub const GRID: f32 = 20.0;
const HANDLE: f32 = 9.0;
const EDGE_HIT: f32 = 7.0;

/// Camera: map point (0,0) is drawn at the canvas centre plus `pan`.
#[derive(Clone, Debug)]
pub struct View {
    pub pan: Vec2,
    pub zoom: f32,
    /// Fit the whole map in the window on the next frame.
    pub fit_pending: bool,
}

impl Default for View {
    fn default() -> Self {
        Self { pan: Vec2::ZERO, zoom: 1.0, fit_pending: true }
    }
}

#[derive(Debug)]
pub(crate) struct CanvasState {
    drag: Option<Drag>,
    /// The idea under the pointer.
    pub hover: Option<NodeId>,
    /// The canvas rectangle of the last frame (screen space).
    pub last_canvas: Rect,
}

impl Default for CanvasState {
    fn default() -> Self {
        Self { drag: None, hover: None, last_canvas: Rect::NOTHING }
    }
}

#[derive(Debug)]
enum Drag {
    Pan,
    /// Moving ideas: preview offset in map units, applied as one command on release.
    Move {
        ids: Vec<NodeId>,
        offset: Vec2,
    },
    Link {
        from: NodeId,
        to: Pos2,
    },
    /// Resizing from a corner (0 = top-left, 1 = top-right, 2 = bottom-right, 3 = bottom-left).
    Resize {
        id: NodeId,
        corner: u8,
        start: Rect,
        preview: Rect,
    },
    /// Rubber-band selection, in screen space.
    Marquee {
        from: Pos2,
        to: Pos2,
    },
}

struct Frame {
    origin: Pos2,
    zoom: f32,
}

impl Frame {
    fn to_screen(&self, p: Point) -> Pos2 {
        self.origin + vec2(p.x, p.y) * self.zoom
    }
    fn to_map(&self, p: Pos2) -> Point {
        let v = (p - self.origin) / self.zoom;
        Point::new(v.x, v.y)
    }
    fn rect_to_map(&self, r: Rect) -> Rect {
        let a = self.to_map(r.min);
        let b = self.to_map(r.max);
        Rect::from_min_max(pos2(a.x, a.y), pos2(b.x, b.y))
    }
}

struct Laid {
    id: NodeId,
    rect: Rect,
    galley: Arc<Galley>,
    /// Where the label goes (below the glyph on stencil shapes, the top-left on containers).
    label_pos: Pos2,
}

fn fill(n: &Node) -> Color32 {
    Color32::from_rgb(n.color.0, n.color.1, n.color.2)
}

fn glyph_of(n: &Node) -> Option<stencil::Glyph> {
    n.kind.as_deref().and_then(stencil::find).map(|s| s.glyph).filter(|g| !g.is_empty())
}

/// Lays out every visible node in screen space, honouring drag previews.
fn lay_out(ui: &egui::Ui, map: &Map, f: &Frame, drag: Option<&Drag>, text: Color32) -> Vec<Laid> {
    let font = FontId::proportional(FONT * f.zoom);
    let offset = |id: NodeId| match drag {
        Some(Drag::Move { ids, offset }) if ids.contains(&id) => *offset,
        _ => Vec2::ZERO,
    };
    map.nodes_in_draw_order()
        .into_iter()
        .filter(|n| map.is_visible(n.id))
        .map(|n| {
            let o = offset(n.id);
            let centre = f.to_screen(Point::new(n.pos.x + o.x, n.pos.y + o.y));
            let label = if n.label.is_empty() { " ".to_string() } else { n.label.clone() };
            let glyph = glyph_of(n).is_some();
            let container = n.shape.is_container();
            if let Some(Drag::Resize { id, preview, .. }) = drag
                && *id == n.id
            {
                let rect =
                    Rect::from_min_max(f.to_screen(Point::new(preview.min.x, preview.min.y)), f.to_screen(Point::new(preview.max.x, preview.max.y)));
                return laid_sized(ui, n, rect, &font, text, glyph, container, label);
            }
            match n.size {
                Some(size) => {
                    let rect = Rect::from_center_size(centre, vec2(size.w, size.h) * f.zoom);
                    laid_sized(ui, n, rect, &font, text, glyph, container, label)
                }
                None => {
                    let galley = ui.painter().layout(label, font.clone(), text, WRAP * f.zoom);
                    let pad = PAD * f.zoom * if n.shape == Shape::Ellipse { 1.6 } else { 1.0 };
                    let size = (galley.size() + pad * 2.0).max(vec2(MIN_WIDTH * f.zoom, 0.0));
                    let rect = Rect::from_center_size(centre, size);
                    Laid { id: n.id, label_pos: rect.center() - galley.size() / 2.0, rect, galley }
                }
            }
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn laid_sized(ui: &egui::Ui, n: &Node, rect: Rect, font: &FontId, text: Color32, glyph: bool, container: bool, label: String) -> Laid {
    let zoom = font.size / FONT;
    let (font, wrap, color) = if container {
        (FontId::proportional((FONT - 1.0) * zoom), rect.width() - 24.0 * zoom, text.gamma_multiply(0.75))
    } else if glyph {
        (FontId::proportional((FONT - 1.5) * zoom), rect.width() - 10.0 * zoom, text)
    } else {
        (font.clone(), (rect.width() - PAD.x * zoom).max(20.0), text)
    };
    let galley = ui.painter().layout(label, font, color, wrap.max(20.0));
    let label_pos = if container {
        rect.min + vec2(12.0, 8.0) * zoom
    } else if glyph {
        pos2(rect.center().x - galley.size().x / 2.0, rect.bottom() - galley.size().y - 6.0 * zoom)
    } else {
        rect.center() - galley.size() / 2.0
    };
    Laid { id: n.id, rect, galley, label_pos }
}

/// The topmost shape under `p`; shapes on a locked layer are not picked.
fn hit(map: &Map, laid: &[Laid], p: Pos2) -> Option<NodeId> {
    laid.iter().rev().find(|l| l.rect.contains(p) && !map.is_locked(l.id)).map(|l| l.id)
}

/// `ids` with every member of their groups (minus locked shapes), in a stable order.
fn with_groups(map: &Map, ids: impl IntoIterator<Item = NodeId>) -> Vec<u64> {
    let mut out: Vec<u64> = Vec::new();
    for id in ids {
        for m in map.group_members(id) {
            if !map.is_locked(m) && !out.contains(&m.0) {
                out.push(m.0);
            }
        }
    }
    out
}

/// The resize handle under `p` on the selected node's rectangle.
fn handle_hit(rect: Rect, p: Pos2) -> Option<u8> {
    let r = HANDLE / 2.0 + 3.0;
    [rect.left_top(), rect.right_top(), rect.right_bottom(), rect.left_bottom()].iter().position(|c| (p - *c).abs().max_elem() <= r).map(|i| i as u8)
}

fn aabb(r: Rect) -> Aabb {
    Aabb::new(r.center().x, r.center().y, r.width() / 2.0, r.height() / 2.0)
}

/// A link's polyline in screen space, and whether it uses the concept-map default look.
fn edge_path(e: &Edge, laid: &[Laid], zoom: f32) -> Option<Vec<Pos2>> {
    let a = laid.iter().find(|l| l.id == e.from)?;
    let b = laid.iter().find(|l| l.id == e.to)?;
    let path = route::route(aabb(a.rect), aabb(b.rect), e.style.route, 3.0 * zoom);
    Some(path.into_iter().map(|p| pos2(p.x, p.y)).collect())
}

fn edge_hit(map: &Map, laid: &[Laid], zoom: f32, p: Pos2) -> Option<EdgeId> {
    map.edges()
        .filter_map(|e| edge_path(e, laid, zoom).map(|path| (e.id, path)))
        .map(|(id, path)| (id, route::distance_to_polyline(&path.iter().map(|q| Point::new(q.x, q.y)).collect::<Vec<_>>(), Point::new(p.x, p.y))))
        .filter(|(_, d)| *d <= EDGE_HIT)
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .map(|(id, _)| id)
}

fn fit(view: &mut View, map: &Map, canvas: Rect) {
    view.fit_pending = false;
    let (mut lo, mut hi) = (pos2(f32::MAX, f32::MAX), pos2(f32::MIN, f32::MIN));
    let mut any = false;
    for n in map.nodes().filter(|n| map.is_visible(n.id)) {
        let (hw, hh) = n.half_size().unwrap_or((70.0, 20.0));
        lo = lo.min(pos2(n.pos.x - hw, n.pos.y - hh));
        hi = hi.max(pos2(n.pos.x + hw, n.pos.y + hh));
        any = true;
    }
    if !any {
        view.pan = Vec2::ZERO;
        view.zoom = 1.0;
        return;
    }
    let bounds = Rect::from_min_max(lo, hi).expand2(vec2(60.0, 40.0));
    let zoom = (canvas.width() / bounds.width()).min(canvas.height() / bounds.height()).clamp(MIN_ZOOM, 1.5);
    view.zoom = if zoom.is_finite() { zoom } else { 1.0 };
    view.pan = -bounds.center().to_vec2() * view.zoom;
}

/// A branch curve from parent `a` to child `b`: it leaves and enters horizontally when the child
/// sits beside its parent, vertically when it sits (nearly) above or below it (radial layouts).
fn edge_points(a: Rect, b: Rect) -> [Pos2; 4] {
    let d = b.center() - a.center();
    let gap_x = d.x.abs() - (a.width() + b.width()) / 2.0;
    if gap_x > 0.0 && d.x.abs() >= 0.4 * d.y.abs() {
        let right = d.x >= 0.0;
        let (s, e) = if right { (a.right_center(), b.left_center()) } else { (a.left_center(), b.right_center()) };
        let bend = ((e.x - s.x).abs() * 0.5).max(12.0) * if right { 1.0 } else { -1.0 };
        [s, s + vec2(bend, 0.0), e - vec2(bend, 0.0), e]
    } else {
        let down = d.y >= 0.0;
        let (s, e) = if down { (a.center_bottom(), b.center_top()) } else { (a.center_top(), b.center_bottom()) };
        let bend = ((e.y - s.y).abs() * 0.5).max(12.0) * if down { 1.0 } else { -1.0 };
        [s, s + vec2(0.0, bend), e - vec2(0.0, bend), e]
    }
}

/// Fills a simple polygon (convex or not) by ear clipping.
fn fill_polygon(painter: &egui::Painter, pts: &[Pos2], color: Color32) {
    if pts.len() < 3 {
        return;
    }
    let area: f32 =
        pts.windows(2).map(|w| w[0].x * w[1].y - w[1].x * w[0].y).sum::<f32>() + pts[pts.len() - 1].x * pts[0].y - pts[0].x * pts[pts.len() - 1].y;
    let ccw = area > 0.0;
    let cross = |a: Pos2, b: Pos2, c: Pos2| (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    let inside = |p: Pos2, a: Pos2, b: Pos2, c: Pos2| {
        let (d1, d2, d3) = (cross(a, b, p), cross(b, c, p), cross(c, a, p));
        !((d1 < 0.0 || d2 < 0.0 || d3 < 0.0) && (d1 > 0.0 || d2 > 0.0 || d3 > 0.0))
    };
    let mut idx: Vec<usize> = (0..pts.len()).collect();
    let mut mesh = Mesh::default();
    for p in pts {
        mesh.vertices.push(Vertex { pos: *p, uv: WHITE_UV, color });
    }
    let mut guard = 0;
    while idx.len() > 3 && guard < 4 * pts.len() {
        guard += 1;
        let n = idx.len();
        let mut clipped = false;
        for i in 0..n {
            let (ia, ib, ic) = (idx[(i + n - 1) % n], idx[i], idx[(i + 1) % n]);
            let (a, b, c) = (pts[ia], pts[ib], pts[ic]);
            let convex = if ccw { cross(a, b, c) > 0.0 } else { cross(a, b, c) < 0.0 };
            if !convex {
                continue;
            }
            if idx.iter().any(|&j| j != ia && j != ib && j != ic && inside(pts[j], a, b, c)) {
                continue;
            }
            mesh.indices.extend_from_slice(&[ia as u32, ib as u32, ic as u32]);
            idx.remove(i);
            clipped = true;
            break;
        }
        if !clipped {
            break;
        }
    }
    if idx.len() == 3 {
        mesh.indices.extend_from_slice(&[idx[0] as u32, idx[1] as u32, idx[2] as u32]);
    }
    if mesh.indices.is_empty() {
        painter.add(EShape::convex_polygon(pts.to_vec(), color, Stroke::NONE));
    } else {
        painter.add(EShape::mesh(mesh));
    }
}

fn dashed_rect(painter: &egui::Painter, r: Rect, stroke: Stroke, dash: f32) {
    let corners = [r.left_top(), r.right_top(), r.right_bottom(), r.left_bottom(), r.left_top()];
    painter.extend(EShape::dashed_line(&corners, stroke, dash, dash * 0.7));
}

fn status_color(s: Status) -> Color32 {
    match s {
        Status::Up => Color32::from_rgb(0x22, 0xa0, 0x6b),
        Status::Warning => Color32::from_rgb(0xe3, 0xa0, 0x08),
        Status::Down => Color32::from_rgb(0xe5, 0x48, 0x4d),
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_node(painter: &egui::Painter, map: &Map, n: &Node, l: &Laid, f: &Frame, t: &Tokens, selected: bool, editing: bool) {
    let r = l.rect;
    let (w, h) = (r.width(), r.height());
    let border = if selected { Stroke::new(2.5, t.selection) } else { Stroke::new(1.0, t.node_border) };
    let shadow = |painter: &egui::Painter, rr: f32| {
        painter.rect_filled(r.translate(vec2(0.0, 3.0 * f.zoom)).expand(1.0), rr, Color32::from_black_alpha(14));
        painter.rect_filled(r.translate(vec2(0.0, 1.0 * f.zoom)), rr, Color32::from_black_alpha(16));
    };
    match n.shape {
        Shape::Text => {
            if selected {
                painter.rect_stroke(r, 4.0, Stroke::new(1.0, t.selection), StrokeKind::Outside);
            }
        }
        Shape::Container => {
            painter.rect_filled(r, 12.0 * f.zoom, fill(n).gamma_multiply(if t.dark { 0.25 } else { 0.55 }));
            dashed_rect(painter, r, Stroke::new(if selected { 2.0 } else { 1.0 }, if selected { t.selection } else { t.node_border }), 8.0 * f.zoom);
        }
        Shape::Ellipse => {
            if selected {
                painter.add(EShape::ellipse_filled(r.center(), r.size() / 2.0 + vec2(5.0, 5.0), t.selection.gamma_multiply(0.18)));
            }
            painter.add(EShape::ellipse_filled(r.center() + vec2(0.0, 2.0 * f.zoom), r.size() / 2.0, Color32::from_black_alpha(16)));
            painter.add(EShape::ellipse_filled(r.center(), r.size() / 2.0, fill(n)));
            painter.add(EShape::ellipse_stroke(r.center(), r.size() / 2.0, border));
        }
        s => match s.polygon(w, h) {
            Some(poly) => {
                let pts: Vec<Pos2> = poly.iter().map(|p| r.center() + vec2(p.x, p.y)).collect();
                let shadow_pts: Vec<Pos2> = pts.iter().map(|p| *p + vec2(0.0, 2.0 * f.zoom)).collect();
                fill_polygon(painter, &shadow_pts, Color32::from_black_alpha(16));
                fill_polygon(painter, &pts, fill(n));
                painter.add(EShape::closed_line(pts, border));
            }
            None => {
                let rr = match s {
                    Shape::Rect | Shape::Subprocess => 0.0,
                    Shape::Pill => h / 2.0,
                    _ => 8.0 * f.zoom,
                };
                if selected {
                    painter.rect_filled(r.expand(5.0), rr + 5.0, t.selection.gamma_multiply(0.18));
                }
                shadow(painter, rr);
                painter.rect_filled(r, rr, fill(n));
                painter.rect_stroke(r, rr, border, StrokeKind::Inside);
            }
        },
    }
    for line in n.shape.inner_strokes(w, h) {
        let pts: Vec<Pos2> = line.iter().map(|p| r.center() + vec2(p.x, p.y)).collect();
        painter.add(EShape::line(pts, Stroke::new(1.0, t.node_border)));
    }
    if let Some(g) = glyph_of(n) {
        let (gx, gy, gw, gh) = route::glyph_box(w, h, !n.label.trim().is_empty());
        let at = |(x, y): &(f32, f32)| r.center() + vec2(gx + x * gw, gy + y * gh);
        let stroke = Stroke::new((gw / 24.0).clamp(1.0, 2.5), t.text);
        for line in g.lines {
            painter.add(EShape::line(line.iter().map(at).collect(), stroke));
        }
        for (x, y, rad) in g.circles {
            painter.circle_stroke(at(&(*x, *y)), rad * gw, stroke);
        }
        for poly in g.fills {
            painter.add(EShape::convex_polygon(poly.iter().map(at).collect(), t.text, Stroke::NONE));
        }
    }
    if !editing && n.shape.is_container() {
        painter.galley(l.label_pos, l.galley.clone(), t.muted);
    } else if !editing {
        painter.galley(l.label_pos, l.galley.clone(), t.text);
    }
    if let Some(status) = n.status_light() {
        let c = r.right_top() + vec2(-9.0, 9.0) * f.zoom.max(0.6);
        painter.circle_filled(c, 5.0 * f.zoom.max(0.6), status_color(status));
        painter.circle_stroke(c, 5.0 * f.zoom.max(0.6), Stroke::new(1.5, Color32::WHITE));
    }
    let hidden = if n.collapsed { map.descendants(n.id).len() } else { 0 };
    if hidden > 0 {
        let badge = r.right_center() + vec2(12.0 * f.zoom, 0.0);
        painter.circle_filled(badge, 10.0 * f.zoom, t.selection);
        painter.text(badge, Align2::CENTER_CENTER, format!("{hidden}"), FontId::proportional(11.0 * f.zoom), Color32::WHITE);
    }
}

fn draw_handles(painter: &egui::Painter, r: Rect, t: &Tokens) {
    for c in [r.left_top(), r.right_top(), r.right_bottom(), r.left_bottom()] {
        let h = Rect::from_center_size(c, vec2(HANDLE, HANDLE));
        painter.rect_filled(h, 2.0, Color32::WHITE);
        painter.rect_stroke(h, 2.0, Stroke::new(1.5, t.selection), StrokeKind::Inside);
    }
}

fn arrowhead(painter: &egui::Painter, tip: Pos2, from: Pos2, size: f32, color: Color32) {
    let dir = (tip - from).normalized();
    if !dir.x.is_finite() || !dir.y.is_finite() {
        return;
    }
    let side = vec2(-dir.y, dir.x) * size * 0.5;
    let back = tip - dir * size;
    painter.add(EShape::convex_polygon(vec![tip, back + side, back - side], color, Stroke::NONE));
}

fn draw_edge(painter: &egui::Painter, e: &Edge, path: &[Pos2], f: &Frame, t: &Tokens, concept_look: bool, selected: bool) {
    if path.len() < 2 {
        return;
    }
    let color = e.style.color.map_or(t.link, |c| Color32::from_rgb(c.0, c.1, c.2));
    let width = (if e.style.width > 0.0 { e.style.width } else { 1.6 } * f.zoom).max(1.0);
    let stroke = Stroke::new(width, color);
    let (line, arrows) = if concept_look { (LineStyle::Dashed, Arrows::End) } else { (e.style.line, e.style.arrows) };
    if selected {
        painter.add(EShape::line(path.to_vec(), Stroke::new(width + 6.0, t.selection.gamma_multiply(0.25))));
    }
    match line {
        LineStyle::Solid => {
            painter.add(EShape::line(path.to_vec(), stroke));
        }
        LineStyle::Dashed => painter.extend(EShape::dashed_line(path, stroke, (width * 4.0 + 3.0).max(4.0), (width * 2.0 + 3.0).max(3.0))),
        LineStyle::Dotted => painter.extend(EShape::dashed_line(path, Stroke::new(width * 1.4, color), width * 0.4, (width * 2.2).max(3.0))),
    }
    let size = (10.0 * f.zoom).max(6.0) + width;
    if matches!(arrows, Arrows::End | Arrows::Both) {
        arrowhead(painter, path[path.len() - 1], path[path.len() - 2], size, color);
    }
    if matches!(arrows, Arrows::Start | Arrows::Both) {
        arrowhead(painter, path[0], path[1], size, color);
    }
}

fn draw(app: &IdeaApp, painter: &egui::Painter, f: &Frame, laid: &[Laid], t: &Tokens, canvas: Rect) {
    let map = app.session.map();
    let diagram = map.mode() == Mode::Diagram;
    // Dot grid: the snap grid in diagram mode.
    let step = if diagram { GRID } else { 32.0 } * f.zoom;
    if step > 7.0 {
        let start = f.origin + vec2(((canvas.left() - f.origin.x) / step).floor() * step, ((canvas.top() - f.origin.y) / step).floor() * step);
        let mut y = start.y;
        while y < canvas.bottom() {
            let mut x = start.x;
            while x < canvas.right() {
                painter.circle_filled(pos2(x, y), 1.2, t.grid_dot);
                x += step;
            }
            y += step;
        }
    }
    let selected = app.session.selection();
    let find = |id: NodeId| laid.iter().find(|l| l.id == id);
    // Containers first, under everything.
    for l in laid {
        if let Some(n) = map.node(l.id).filter(|n| n.shape.is_container()) {
            let editing = app.editing.as_ref().is_some_and(|(id, _)| *id == n.id);
            draw_node(painter, map, n, l, f, t, selected.contains(&n.id), editing);
        }
    }
    // Tree branches.
    for l in laid {
        let Some(n) = map.node(l.id) else { continue };
        if let Some(p) = n.parent.and_then(find) {
            let color = fill(n);
            let stroke = Stroke::new((2.5 * f.zoom).max(1.0), color.lerp_to_gamma(t.branch, 0.45));
            painter.add(CubicBezierShape::from_points_stroke(edge_points(p.rect, l.rect), false, Color32::TRANSPARENT, stroke));
        }
    }
    // Links.
    let link_font = FontId::proportional(12.0 * f.zoom);
    let mut labels: Vec<(Pos2, String, Color32)> = Vec::new();
    for e in map.edges() {
        let Some(path) = edge_path(e, laid, f.zoom) else { continue };
        let concept_look = !diagram && e.style == Default::default();
        draw_edge(painter, e, &path, f, t, concept_look, app.edge_selection == Some(e.id));
        if !e.label.is_empty() {
            let m = route::midpoint(&path.iter().map(|q| Point::new(q.x, q.y)).collect::<Vec<_>>());
            labels.push((pos2(m.x, m.y), e.label.clone(), e.style.color.map_or(t.link, |c| Color32::from_rgb(c.0, c.1, c.2))));
        }
    }
    // Shapes and ideas.
    for l in laid {
        let Some(n) = map.node(l.id).filter(|n| !n.shape.is_container()) else { continue };
        let editing = app.editing.as_ref().is_some_and(|(id, _)| *id == n.id);
        draw_node(painter, map, n, l, f, t, selected.contains(&n.id), editing);
    }
    for (mid, text, color) in labels {
        let g = painter.layout_no_wrap(text, link_font.clone(), color);
        let r = Rect::from_center_size(mid, g.size() + vec2(8.0, 4.0) * f.zoom);
        painter.rect_filled(r, 4.0 * f.zoom, t.link_label_bg);
        painter.galley(r.min + vec2(4.0, 2.0) * f.zoom, g, color);
    }
    // A dashed frame around each group that has a selected member.
    let mut groups: Vec<u64> = selected.iter().filter_map(|id| map.node(*id).and_then(|n| n.group)).collect();
    groups.sort_unstable();
    groups.dedup();
    for g in groups {
        let members = laid.iter().filter(|l| map.node(l.id).is_some_and(|n| n.group == Some(g))).map(|l| l.rect);
        if let Some(r) = members.reduce(|a, b| a.union(b)) {
            dashed_rect(painter, r.expand(6.0 * f.zoom), Stroke::new(1.0, t.selection), 6.0);
        }
    }
    // Resize handles on a single selected shape.
    if let [only] = selected
        && app.editing.is_none()
        && let Some(l) = find(*only)
        && (diagram || map.node(*only).is_some_and(|n| n.size.is_some()))
    {
        draw_handles(painter, l.rect, t);
    }
    match &app.canvas.drag {
        Some(Drag::Link { from, to }) => {
            if let Some(a) = find(*from) {
                painter.line_segment([a.rect.center(), *to], Stroke::new(2.0, t.link));
            }
        }
        Some(Drag::Marquee { from, to }) => {
            let r = Rect::from_two_pos(*from, *to);
            painter.rect_filled(r, 2.0, t.selection.gamma_multiply(0.12));
            painter.rect_stroke(r, 2.0, Stroke::new(1.0, t.selection), StrokeKind::Inside);
        }
        _ => {}
    }
}

/// Canvas shortcuts. Runs before any widget so Tab adds a child instead of moving focus.
pub(crate) fn keyboard(app: &mut IdeaApp, ui: &egui::Ui) {
    if app.editing.is_some() || ui.ctx().memory(|m| m.focused().is_some()) {
        return;
    }
    let pressed = |m: Modifiers, k: Key| ui.ctx().input_mut(|i| i.consume_key(m, k));
    let shift_cmd = Modifiers::COMMAND | Modifiers::SHIFT;
    let has_sel = !app.session.selection().is_empty();
    if pressed(shift_cmd, Key::Z) || pressed(Modifiers::COMMAND, Key::Y) {
        app.run("edit.redo", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::Z) {
        app.run("edit.undo", json!({}));
    } else if pressed(shift_cmd, Key::L) {
        app.run("layout.radial", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::L) {
        app.run("layout.tree", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::A) {
        app.run("selection.all", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::D) {
        app.run("edit.duplicate", json!({}));
    } else if pressed(shift_cmd, Key::G) {
        app.run("group.ungroup", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::G) {
        app.run("group.create", json!({}));
    } else if pressed(Modifiers::COMMAND, Key::CloseBracket) {
        app.run("node.setZ", json!({"z": "front"}));
    } else if pressed(Modifiers::COMMAND, Key::OpenBracket) {
        app.run("node.setZ", json!({"z": "back"}));
    } else if pressed(Modifiers::COMMAND, Key::Num0) {
        app.view.fit_pending = true;
    } else if pressed(Modifiers::COMMAND, Key::O) {
        app.requests.push(crate::Request::Open);
    } else if pressed(shift_cmd, Key::S) {
        app.requests.push(crate::Request::SaveAs);
    } else if pressed(Modifiers::COMMAND, Key::S) {
        app.save_or_ask();
    } else if pressed(Modifiers::COMMAND, Key::N) {
        app.new_map();
    } else if let Some(edge) = app.edge_selection.filter(|_| !has_sel) {
        if pressed(Modifiers::NONE, Key::Delete) || pressed(Modifiers::NONE, Key::Backspace) {
            app.run("edge.delete", json!({"id": edge.0}));
            app.edge_selection = None;
        } else if pressed(Modifiers::NONE, Key::Escape) {
            app.edge_selection = None;
        }
    } else if !has_sel {
        let _ = pressed(Modifiers::NONE, Key::Tab);
        if pressed(Modifiers::NONE, Key::Escape) {
            app.link_mode = false;
        }
    } else if pressed(Modifiers::NONE, Key::Tab) {
        app.run_and_edit("node.addChild", json!({}));
    } else if pressed(Modifiers::NONE, Key::Enter) {
        app.run_and_edit("node.addSibling", json!({}));
    } else if pressed(Modifiers::NONE, Key::Delete) || pressed(Modifiers::NONE, Key::Backspace) {
        app.run("node.delete", json!({}));
    } else if pressed(Modifiers::NONE, Key::Space) {
        app.run("node.toggleCollapse", json!({}));
    } else if pressed(Modifiers::NONE, Key::F2) {
        if let Some(id) = app.session.selection().first().copied() {
            app.begin_edit(id);
        }
    } else if pressed(Modifiers::NONE, Key::Escape) {
        app.run("selection.clear", json!({}));
    } else {
        let step = if ui.input(|i| i.modifiers.shift) { GRID } else { 1.0 };
        let nudge = [(Key::ArrowLeft, -step, 0.0), (Key::ArrowRight, step, 0.0), (Key::ArrowUp, 0.0, -step), (Key::ArrowDown, 0.0, step)];
        for (key, dx, dy) in nudge {
            if pressed(Modifiers::NONE, key) || pressed(Modifiers::SHIFT, key) {
                app.run("node.moveBy", json!({"dx": dx, "dy": dy}));
                break;
            }
        }
    }
}

/// Selects a shape, with the rest of its group. With `additive` (Cmd/Shift) the group is toggled
/// in or out of the current selection.
fn select(app: &mut IdeaApp, id: NodeId, additive: bool) {
    app.edge_selection = None;
    let members = with_groups(app.session.map(), [id]);
    let mut ids: Vec<u64> = if additive { app.session.selection().iter().map(|n| n.0).collect() } else { Vec::new() };
    if ids.contains(&id.0) {
        ids.retain(|n| !members.contains(n));
    } else {
        for m in members {
            if !ids.contains(&m) {
                ids.push(m);
            }
        }
    }
    app.run("selection.set", json!({"ids": ids}));
}

fn text_editor(app: &mut IdeaApp, ui: &egui::Ui, laid: &[Laid], f: &Frame) {
    let Some((id, mut draft)) = app.editing.clone() else { return };
    let Some(rect) = laid.iter().find(|l| l.id == id).map(|l| l.rect) else {
        app.editing = None;
        return;
    };
    let width = rect.width().max(160.0 * f.zoom);
    let mut done = false;
    let mut cancel = false;
    egui::Area::new(egui::Id::new("idea-editor")).fixed_pos(pos2(rect.center().x - width / 2.0, rect.top())).order(egui::Order::Foreground).show(
        ui.ctx(),
        |ui| {
            let enter = ui.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter));
            cancel = ui.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Escape));
            let edit = egui::TextEdit::multiline(&mut draft)
                .desired_width(width)
                .desired_rows(1)
                .font(FontId::proportional(FONT * f.zoom))
                .horizontal_align(egui::Align::Center)
                .hint_text(app.tr("Idea"));
            let resp = ui.add(edit);
            if !resp.has_focus() && !resp.lost_focus() {
                resp.request_focus();
            }
            done = enter || resp.lost_focus();
        },
    );
    if cancel {
        app.editing = None;
    } else {
        app.editing = Some((id, draft));
        if done {
            app.commit_edit();
        }
    }
}

/// Rounds a map position to the grid when snapping is on.
fn snapped(app: &IdeaApp, p: Point) -> Point {
    if app.snap && app.session.map().mode() == Mode::Diagram { Point::new((p.x / GRID).round() * GRID, (p.y / GRID).round() * GRID) } else { p }
}

pub fn show(app: &mut IdeaApp, ui: &mut egui::Ui, t: &Tokens) {
    let (resp, painter) = ui.allocate_painter(ui.available_size(), Sense::click_and_drag());
    let canvas = resp.rect;
    app.canvas.last_canvas = canvas;
    if app.view.fit_pending {
        fit(&mut app.view, app.session.map(), canvas);
    }
    // Zoom (pinch or Cmd+wheel) around the pointer; plain wheel pans.
    if resp.hovered() {
        let (zoom_delta, scroll) = ui.input(|i| (i.zoom_delta(), i.smooth_scroll_delta));
        if (zoom_delta - 1.0).abs() > f32::EPSILON {
            let pointer = resp.hover_pos().unwrap_or(canvas.center());
            let new_zoom = (app.view.zoom * zoom_delta).clamp(MIN_ZOOM, MAX_ZOOM);
            let anchor = pointer - canvas.center() - app.view.pan;
            app.view.pan -= anchor * (new_zoom / app.view.zoom - 1.0);
            app.view.zoom = new_zoom;
        } else {
            app.view.pan += scroll;
        }
    }
    let f = Frame { origin: canvas.center() + app.view.pan, zoom: app.view.zoom };
    let diagram = app.session.map().mode() == Mode::Diagram;
    let mut laid = lay_out(ui, app.session.map(), &f, app.canvas.drag.as_ref(), t.text);
    let pointer = resp.interact_pointer_pos().or(resp.hover_pos());
    let shift = ui.input(|i| i.modifiers.shift);
    let additive = ui.input(|i| i.modifiers.command || i.modifiers.shift);
    let single_rect = match app.session.selection() {
        [only] => laid.iter().find(|l| l.id == *only).map(|l| l.rect),
        _ => None,
    };
    let handles_on = single_rect
        .is_some_and(|_| diagram || app.session.selection().first().and_then(|id| app.session.map().node(*id)).is_some_and(|n| n.size.is_some()));

    app.canvas.hover = pointer.filter(|_| resp.hovered()).and_then(|p| hit(app.session.map(), &laid, p));

    if resp.drag_started() {
        app.commit_edit();
        // Where the button went down, not where the pointer is now that the drag is recognised.
        let p = ui.input(|i| i.pointer.press_origin()).or(pointer).unwrap_or_default();
        let handle = single_rect.filter(|_| handles_on).and_then(|r| handle_hit(r, p).map(|c| (r, c)));
        let on = hit(app.session.map(), &laid, p);
        app.canvas.drag = match (handle, on) {
            (Some((r, corner)), _) => {
                let id = app.session.selection()[0];
                let start = f.rect_to_map(r);
                Some(Drag::Resize { id, corner, start, preview: start })
            }
            (None, Some(id)) if shift || app.link_mode => Some(Drag::Link { from: id, to: p }),
            (None, Some(id)) => {
                if !app.session.selection().contains(&id) {
                    select(app, id, false);
                }
                Some(Drag::Move { ids: app.session.selection().to_vec(), offset: Vec2::ZERO })
            }
            (None, None) if diagram || shift => Some(Drag::Marquee { from: p, to: p }),
            (None, None) => Some(Drag::Pan),
        };
    }
    if resp.dragged() {
        let delta = resp.drag_delta();
        match &mut app.canvas.drag {
            Some(Drag::Pan) => app.view.pan += delta,
            Some(Drag::Move { offset, .. }) => *offset += delta / app.view.zoom,
            Some(Drag::Link { to, .. }) | Some(Drag::Marquee { to, .. }) => *to = pointer.unwrap_or(*to),
            Some(Drag::Resize { corner, start, preview, .. }) => {
                if let Some(p) = pointer {
                    let m = f.to_map(p);
                    let (min, max) = (start.min, start.max);
                    let mut r = match corner {
                        0 => Rect::from_min_max(pos2(m.x, m.y), max),
                        1 => Rect::from_min_max(pos2(min.x, m.y), pos2(m.x, max.y)),
                        2 => Rect::from_min_max(min, pos2(m.x, m.y)),
                        _ => Rect::from_min_max(pos2(m.x, min.y), pos2(max.x, m.y)),
                    };
                    if r.width() < 24.0 {
                        r.max.x = r.min.x + 24.0;
                    }
                    if r.height() < 16.0 {
                        r.max.y = r.min.y + 16.0;
                    }
                    *preview = r;
                }
            }
            None => {}
        }
    }
    if resp.drag_stopped() {
        match app.canvas.drag.take() {
            Some(Drag::Move { ids, offset }) if offset != Vec2::ZERO => {
                // Snap the first dragged shape and move the rest by the same amount.
                let (dx, dy) = match ids.first().and_then(|id| app.session.map().node(*id)) {
                    Some(n) => {
                        let target = snapped(app, Point::new(n.pos.x + offset.x, n.pos.y + offset.y));
                        (target.x - n.pos.x, target.y - n.pos.y)
                    }
                    None => (offset.x, offset.y),
                };
                app.run("node.moveBy", json!({"ids": ids.iter().map(|n| n.0).collect::<Vec<_>>(), "dx": dx, "dy": dy}));
            }
            Some(Drag::Link { from, to }) => match hit(app.session.map(), &laid, to) {
                Some(target) if target != from => {
                    if let Some(id) = app.run("edge.add", json!({"from": from.0, "to": target.0})).and_then(|v| v["id"].as_u64()) {
                        app.run("selection.clear", json!({}));
                        app.edge_selection = Some(EdgeId(id));
                    }
                }
                _ => {}
            },
            Some(Drag::Resize { id, preview, .. }) => {
                let c = snapped(app, Point::new(preview.center().x, preview.center().y));
                let (w, h) = if app.snap && diagram {
                    ((preview.width() / GRID).round().max(1.0) * GRID, (preview.height() / GRID).round().max(1.0) * GRID)
                } else {
                    (preview.width(), preview.height())
                };
                app.run("node.resize", json!({"id": id.0, "w": w, "h": h, "x": c.x, "y": c.y}));
            }
            Some(Drag::Marquee { from, to }) => {
                let r = Rect::from_two_pos(from, to);
                let inside = laid.iter().filter(|l| {
                    r.intersects(l.rect) && !app.session.map().node(l.id).is_some_and(|n| n.shape.is_container() && !r.contains_rect(l.rect))
                });
                let ids = with_groups(app.session.map(), inside.map(|l| l.id));
                app.edge_selection = None;
                app.run("selection.set", json!({"ids": ids}));
            }
            _ => {}
        }
        laid = lay_out(ui, app.session.map(), &f, None, t.text);
    }
    if resp.double_clicked() {
        match pointer.and_then(|p| hit(app.session.map(), &laid, p)) {
            Some(id) => app.begin_edit(id),
            None => {
                if let Some(edge) = pointer.and_then(|p| edge_hit(app.session.map(), &laid, f.zoom, p)) {
                    app.run("selection.clear", json!({}));
                    app.edge_selection = Some(edge);
                    app.focus_edge_label = true;
                } else {
                    let at = snapped(app, f.to_map(pointer.unwrap_or(canvas.center())));
                    app.run_and_edit("node.add", json!({"x": at.x, "y": at.y}));
                }
            }
        }
        laid = lay_out(ui, app.session.map(), &f, None, t.text);
    } else if resp.clicked() {
        app.commit_edit();
        match pointer.and_then(|p| hit(app.session.map(), &laid, p)) {
            Some(id) => select(app, id, additive),
            None => match pointer.and_then(|p| edge_hit(app.session.map(), &laid, f.zoom, p)) {
                Some(edge) => {
                    app.run("selection.clear", json!({}));
                    app.edge_selection = Some(edge);
                }
                None => {
                    app.edge_selection = None;
                    app.run("selection.clear", json!({}));
                }
            },
        }
    }
    if app.edge_selection.is_some_and(|e| app.session.map().edge(e).is_none()) {
        app.edge_selection = None;
    }
    // A command may have added or removed ideas.
    let laid = if app.editing.is_some() || resp.changed() { lay_out(ui, app.session.map(), &f, app.canvas.drag.as_ref(), t.text) } else { laid };
    draw(app, &painter, &f, &laid, t, canvas);
    // Properties tooltip.
    if app.canvas.drag.is_none()
        && let Some(n) = app.canvas.hover.and_then(|id| app.session.map().node(id)).filter(|n| !n.props.is_empty()).cloned()
    {
        resp.clone().on_hover_ui_at_pointer(|ui| {
            ui.set_max_width(260.0);
            ui.label(egui::RichText::new(&n.label).strong());
            if let Some(kind) = n.kind.as_deref().and_then(stencil::find) {
                ui.label(egui::RichText::new(kind.name(app.language == crate::Language::It)).small().color(t.muted));
            }
            egui::Grid::new("props-tip").num_columns(2).spacing(vec2(10.0, 2.0)).show(ui, |ui| {
                for (k, v) in &n.props {
                    ui.label(egui::RichText::new(k).small().color(t.muted));
                    ui.label(egui::RichText::new(v).small());
                    ui.end_row();
                }
            });
        });
    }
    text_editor(app, ui, &laid, &f);
    overlays(app, ui, canvas, &laid, t);
}

fn card_frame(t: &Tokens) -> egui::Frame {
    egui::Frame::NONE
        .fill(t.card)
        .stroke(Stroke::new(1.0, t.border))
        .corner_radius(12)
        .inner_margin(egui::Margin::symmetric(6, 4))
        .shadow(egui::epaint::Shadow { offset: [0, 4], blur: 16, spread: 0, color: Color32::from_black_alpha(if t.dark { 90 } else { 28 }) })
}

/// Places a floating bar near `r`, inside the canvas.
fn bar_at(ctx: &egui::Context, id: &str, canvas: Rect, r: Rect, add: impl FnOnce(&mut egui::Ui)) {
    let above = r.top() - 58.0 > canvas.top() + 8.0;
    let y = if above { r.top() - 12.0 } else { r.bottom() + 12.0 };
    let x = r.center().x.clamp(canvas.left() + 220.0, (canvas.right() - 220.0).max(canvas.left() + 220.0));
    let pivot = if above { Align2::CENTER_BOTTOM } else { Align2::CENTER_TOP };
    egui::Area::new(egui::Id::new(id))
        .order(egui::Order::Foreground)
        .pivot(pivot)
        .fixed_pos(pos2(x, y.clamp(canvas.top() + 8.0, canvas.bottom() - 8.0)))
        .show(ctx, add);
}

/// Floating controls over the canvas: the selection bars, zoom, and the empty-map invitation.
fn overlays(app: &mut IdeaApp, ui: &egui::Ui, canvas: Rect, laid: &[Laid], t: &Tokens) {
    use crate::icons::Icon;
    use crate::widgets::{divider, icon_button, primary_button};
    let ctx = ui.ctx().clone();
    let diagram = app.session.map().mode() == Mode::Diagram;
    // Node selection bar, above the first selected idea.
    let anchor = app.session.selection().first().and_then(|id| laid.iter().find(|l| l.id == *id)).map(|l| l.rect);
    if let (Some(r), None, None) = (anchor, &app.editing, &app.canvas.drag) {
        let many = app.session.selection().len() > 1;
        bar_at(&ctx, "selection-bar", canvas, r, |ui| {
            card_frame(t).show(ui, |ui| {
                ui.horizontal(|ui| {
                    let palette = icon_button(ui, t, Icon::Palette, app.tr("Colour"), true, false);
                    egui::Popup::menu(&palette).show(|ui| {
                        ui.set_max_width(170.0);
                        crate::sidebar::swatches(app, ui, t, true, 28.0);
                    });
                    let shape = icon_button(ui, t, Icon::Shape, app.tr("Shape"), true, false);
                    egui::Popup::menu(&shape).show(|ui| {
                        for s in Shape::ALL {
                            if ui.button(app.tr(s.name())).clicked() {
                                app.run("node.setShape", json!({"shape": s.name()}));
                                ui.close();
                            }
                        }
                    });
                    divider(ui, t);
                    if !diagram {
                        if icon_button(ui, t, Icon::Child, &app.tip("node.addChild"), true, false).clicked() {
                            app.run_and_edit("node.addChild", json!({}));
                        }
                        if icon_button(ui, t, Icon::Sibling, &app.tip("node.addSibling"), true, false).clicked() {
                            app.run_and_edit("node.addSibling", json!({}));
                        }
                        if icon_button(ui, t, Icon::Collapse, &app.tip("node.toggleCollapse"), true, false).clicked() {
                            app.run("node.toggleCollapse", json!({}));
                        }
                    } else {
                        if icon_button(ui, t, Icon::Duplicate, &app.tip("edit.duplicate"), true, false).clicked() {
                            app.run("edit.duplicate", json!({}));
                        }
                        if icon_button(ui, t, Icon::ToFront, app.tr("Bring to front"), true, false).clicked() {
                            app.run("node.setZ", json!({"z": "front"}));
                        }
                        if icon_button(ui, t, Icon::ToBack, app.tr("Send to back"), true, false).clicked() {
                            app.run("node.setZ", json!({"z": "back"}));
                        }
                        if icon_button(ui, t, Icon::Props, app.tr("Properties"), true, app.sidebar == Some(crate::SidePanel::Elements)).clicked() {
                            app.sidebar = Some(crate::SidePanel::Elements);
                        }
                    }
                    let link_tip = app.tr("Drag from one idea to another to link them (or hold Shift while dragging)").to_string();
                    if icon_button(ui, t, Icon::Link, &link_tip, true, app.link_mode).clicked() {
                        app.link_mode = !app.link_mode;
                    }
                    let grouped = app.session.selection().iter().any(|id| app.session.map().node(*id).is_some_and(|n| n.group.is_some()));
                    if many || grouped {
                        divider(ui, t);
                        if many && icon_button(ui, t, Icon::Group, &app.tip("group.create"), true, false).clicked() {
                            app.run("group.create", json!({}));
                        }
                        if grouped && icon_button(ui, t, Icon::Ungroup, &app.tip("group.ungroup"), true, false).clicked() {
                            app.run("group.ungroup", json!({}));
                        }
                    }
                    if many {
                        for (icon, edge, tip) in [
                            (Icon::AlignLeft, "left", "Align left"),
                            (Icon::AlignCenterX, "centerX", "Align centres"),
                            (Icon::AlignTop, "top", "Align top"),
                        ] {
                            if icon_button(ui, t, icon, app.tr(tip), true, false).clicked() {
                                app.run("arrange.align", json!({"edge": edge}));
                            }
                        }
                    }
                    divider(ui, t);
                    if icon_button(ui, t, Icon::Trash, &app.tip("node.delete"), true, false).clicked() {
                        app.run("node.delete", json!({}));
                    }
                });
            });
        });
    }
    // Link bar, at the selected link's middle.
    if let (Some(edge), None) = (app.edge_selection.and_then(|id| app.session.map().edge(id).cloned()), &app.canvas.drag)
        && app.session.selection().is_empty()
        && let Some(path) = edge_path(&edge, laid, app.view.zoom)
    {
        let m = route::midpoint(&path.iter().map(|q| Point::new(q.x, q.y)).collect::<Vec<_>>());
        let r = Rect::from_center_size(pos2(m.x, m.y), vec2(10.0, 28.0));
        let focus = std::mem::take(&mut app.focus_edge_label);
        bar_at(&ctx, "link-bar", canvas, r, |ui| {
            card_frame(t).show(ui, |ui| {
                ui.horizontal(|ui| {
                    let mut label = app.edge_label_draft.clone().unwrap_or_else(|| edge.label.clone());
                    let te = ui.add(egui::TextEdit::singleline(&mut label).desired_width(120.0).hint_text(app.tr("Label")));
                    if focus {
                        te.request_focus();
                    }
                    if te.changed() {
                        app.edge_label_draft = Some(label.clone());
                    }
                    if te.lost_focus()
                        && let Some(d) = app.edge_label_draft.take().filter(|d| *d != edge.label)
                    {
                        app.run("edge.setLabel", json!({"id": edge.id.0, "label": d}));
                    }
                    divider(ui, t);
                    let line = icon_button(ui, t, Icon::LineStyle, app.tr("Line"), true, false);
                    egui::Popup::menu(&line).show(|ui| {
                        for s in LineStyle::ALL {
                            if ui.selectable_label(edge.style.line == s, app.tr(s.name())).clicked() {
                                app.run("edge.setStyle", json!({"id": edge.id.0, "line": s.name()}));
                                ui.close();
                            }
                        }
                        ui.separator();
                        for r in Route::ALL {
                            if ui.selectable_label(edge.style.route == r, app.tr(r.name())).clicked() {
                                app.run("edge.setStyle", json!({"id": edge.id.0, "route": r.name()}));
                                ui.close();
                            }
                        }
                        ui.separator();
                        for w in [1.0, 2.0, 3.0, 5.0] {
                            if ui.selectable_label((edge.style.width - w).abs() < 0.01, format!("{w:.0} px")).clicked() {
                                app.run("edge.setStyle", json!({"id": edge.id.0, "width": w}));
                                ui.close();
                            }
                        }
                    });
                    let arrows = icon_button(ui, t, Icon::Arrows, app.tr("Arrows"), true, false);
                    egui::Popup::menu(&arrows).show(|ui| {
                        for a in Arrows::ALL {
                            if ui.selectable_label(edge.style.arrows == a, app.tr(a.name())).clicked() {
                                app.run("edge.setStyle", json!({"id": edge.id.0, "arrows": a.name()}));
                                ui.close();
                            }
                        }
                    });
                    let palette = icon_button(ui, t, Icon::Palette, app.tr("Colour"), true, false);
                    egui::Popup::menu(&palette).show(|ui| {
                        ui.set_max_width(200.0);
                        ui.horizontal_wrapped(|ui| {
                            for c in crate::sidebar::LINK_COLORS {
                                let (rect, resp) = ui.allocate_exact_size(vec2(24.0, 24.0), Sense::click());
                                ui.painter().circle_filled(rect.center(), 10.0, Color32::from_rgb(c.0, c.1, c.2));
                                if resp.clicked() {
                                    app.run("edge.setStyle", json!({"id": edge.id.0, "color": c.hex()}));
                                    ui.close();
                                }
                            }
                        });
                        if ui.button(app.tr("Default colour")).clicked() {
                            app.run("edge.setStyle", json!({"id": edge.id.0, "color": null}));
                            ui.close();
                        }
                    });
                    divider(ui, t);
                    if icon_button(ui, t, Icon::Trash, &app.tip("edge.delete"), true, false).clicked() {
                        app.run("edge.delete", json!({"id": edge.id.0}));
                        app.edge_selection = None;
                    }
                });
            });
        });
    }
    // Zoom and snapping.
    egui::Area::new(egui::Id::new("zoom-bar"))
        .order(egui::Order::Foreground)
        .pivot(Align2::RIGHT_BOTTOM)
        .fixed_pos(canvas.right_bottom() - vec2(14.0, 14.0))
        .show(&ctx, |ui| {
            card_frame(t).show(ui, |ui| {
                ui.horizontal(|ui| {
                    if diagram {
                        if icon_button(ui, t, Icon::Grid, app.tr("Snap to grid"), true, app.snap).clicked() {
                            app.snap = !app.snap;
                        }
                        divider(ui, t);
                    }
                    let zoom = |app: &mut IdeaApp, k: f32| app.view.zoom = (app.view.zoom * k).clamp(MIN_ZOOM, MAX_ZOOM);
                    if icon_button(ui, t, Icon::ZoomOut, app.tr("Zoom out"), true, false).clicked() {
                        zoom(app, 1.0 / 1.2);
                    }
                    ui.label(egui::RichText::new(format!("{:.0}%", app.view.zoom * 100.0)).color(t.ui_text).small());
                    if icon_button(ui, t, Icon::ZoomIn, app.tr("Zoom in"), true, false).clicked() {
                        zoom(app, 1.2);
                    }
                    if icon_button(ui, t, Icon::Fit, app.tr("Fit to window"), true, false).clicked() {
                        app.view.fit_pending = true;
                    }
                });
            });
        });
    // Empty map.
    if app.session.map().node_count() == 0 && app.editing.is_none() {
        egui::Area::new(egui::Id::new("empty-map")).order(egui::Order::Middle).pivot(Align2::CENTER_CENTER).fixed_pos(canvas.center()).show(
            &ctx,
            |ui| {
                ui.vertical_centered(|ui| {
                    let headline =
                        if diagram { "Double-click to add a shape, or pick one from the library" } else { "Double-click anywhere to add an idea" };
                    ui.add(egui::Label::new(egui::RichText::new(app.tr(headline)).size(18.0).color(t.muted)).selectable(false));
                    ui.add_space(6.0);
                    ui.add(egui::Label::new(egui::RichText::new(app.tr("or start from a template")).color(t.muted)).selectable(false));
                    ui.add_space(10.0);
                    ui.horizontal(|ui| {
                        if primary_button(ui, t, Some(Icon::Templates), app.tr("Choose a template")).clicked() {
                            app.sidebar = Some(crate::SidePanel::Templates);
                        }
                        if diagram && primary_button(ui, t, Some(Icon::Shapes), app.tr("Shape library")).clicked() {
                            app.sidebar = Some(crate::SidePanel::Shapes);
                        }
                    });
                });
            },
        );
    }
}
