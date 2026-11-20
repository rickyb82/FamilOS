//! SVG export: the visible map as a picture (ideas and shapes, stencil glyphs, branches, styled
//! links), for documents, the web and agents that want to look at what they built.

use ideacraft_core::route::{self, Aabb};
use ideacraft_core::{Arrows, LineStyle, Map, NodeId, Point, Route, Shape, Status, stencil};

use crate::xml::escape;

const FONT: f32 = 14.0;
const LINE_H: f32 = 18.0;
const PAD_X: f32 = 14.0;
const PAD_Y: f32 = 9.0;
const WRAP_CHARS: usize = 28;
/// Rough average glyph width for a proportional sans-serif at [`FONT`].
const CHAR_W: f32 = 7.4;
const LINK: &str = "#e06a2c";
const TEXT: &str = "#1f222b";

/// Greedy word wrap at about `width` characters, keeping explicit line breaks.
fn wrap(text: &str, width: usize) -> Vec<String> {
    let width = width.max(4);
    let mut lines = Vec::new();
    for para in text.split('\n') {
        let mut line = String::new();
        for word in para.split_whitespace() {
            if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > width {
                lines.push(std::mem::take(&mut line));
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(word);
        }
        lines.push(line);
    }
    if lines.iter().all(String::is_empty) {
        lines = vec![String::new()];
    }
    lines.truncate(12);
    lines
}

struct Box_ {
    id: NodeId,
    aabb: Aabb,
    lines: Vec<String>,
}

impl Box_ {
    fn x(&self) -> f32 {
        self.aabb.cx - self.aabb.hw
    }
    fn y(&self) -> f32 {
        self.aabb.cy - self.aabb.hh
    }
    fn w(&self) -> f32 {
        self.aabb.hw * 2.0
    }
    fn h(&self) -> f32 {
        self.aabb.hh * 2.0
    }
}

fn num(v: f32) -> String {
    format!("{:.1}", if v.is_finite() { v } else { 0.0 })
}

fn points(pts: &[Point]) -> String {
    pts.iter().map(|p| format!("{},{}", num(p.x), num(p.y))).collect::<Vec<_>>().join(" ")
}

/// A node's box: its explicit size, else a fit around its text (visible nodes only).
pub fn measure(map: &Map) -> Vec<(NodeId, Aabb)> {
    measure_nodes(map.nodes().filter(|n| map.is_visible(n.id)))
}

/// Boxes for every node, hidden ones included (for exports that keep hidden layers).
pub fn measure_all(map: &Map) -> Vec<(NodeId, Aabb)> {
    measure_nodes(map.nodes())
}

fn measure_nodes<'a>(nodes: impl Iterator<Item = &'a ideacraft_core::Node>) -> Vec<(NodeId, Aabb)> {
    nodes
        .map(|n| {
            let (hw, hh) = n.half_size().unwrap_or_else(|| {
                let lines = wrap(&n.label, WRAP_CHARS);
                let longest = lines.iter().map(|l| l.chars().count()).max().unwrap_or(0) as f32;
                let w = (longest * CHAR_W + PAD_X * 2.0).max(70.0) * if n.shape == Shape::Ellipse { 1.3 } else { 1.0 };
                (w / 2.0, (lines.len() as f32 * LINE_H + PAD_Y * 2.0) / 2.0)
            });
            (n.id, Aabb::new(n.pos.x, n.pos.y, hw, hh))
        })
        .collect()
}

/// The map as a standalone SVG document.
pub fn to_svg(map: &Map) -> String {
    let boxes: Vec<Box_> = measure(map)
        .into_iter()
        .map(|(id, aabb)| {
            let label = map.node(id).map(|n| n.label.as_str()).unwrap_or_default();
            let chars = ((aabb.hw * 2.0 - PAD_X) / CHAR_W).max(4.0) as usize;
            Box_ { id, aabb, lines: wrap(label, chars.min(WRAP_CHARS.max(chars))) }
        })
        .collect();
    let (mut x0, mut y0, mut x1, mut y1) = (f32::MAX, f32::MAX, f32::MIN, f32::MIN);
    for b in &boxes {
        x0 = x0.min(b.x());
        y0 = y0.min(b.y());
        x1 = x1.max(b.x() + b.w());
        y1 = y1.max(b.y() + b.h());
    }
    if boxes.is_empty() {
        (x0, y0, x1, y1) = (0.0, 0.0, 200.0, 100.0);
    }
    let margin = 40.0;
    let (vx, vy, vw, vh) = (x0 - margin, y0 - margin, x1 - x0 + 2.0 * margin, y1 - y0 + 2.0 * margin);
    let find = |id: NodeId| boxes.iter().find(|b| b.id == id);
    let mut out = format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"{} {} {} {}\" width=\"{}\" height=\"{}\" font-family=\"system-ui, -apple-system, 'Segoe UI', sans-serif\">\n<title>{}</title>\n<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" fill=\"#f3f4f8\"/>\n",
        num(vx),
        num(vy),
        num(vw),
        num(vh),
        num(vw),
        num(vh),
        escape(map.title()),
        num(vx),
        num(vy),
        num(vw),
        num(vh)
    );
    out.push_str("<defs>\n");
    // One arrowhead marker per link colour.
    let mut colors: Vec<String> = map.edges().map(|e| e.style.color.map_or(LINK.to_string(), |c| c.hex())).collect();
    colors.sort();
    colors.dedup();
    for c in &colors {
        out.push_str(&format!(
            "<marker id=\"arrow-{}\" viewBox=\"0 0 10 10\" refX=\"9\" refY=\"5\" markerWidth=\"8\" markerHeight=\"8\" orient=\"auto-start-reverse\"><path d=\"M0,0 L10,5 L0,10 z\" fill=\"{c}\"/></marker>\n",
            c.trim_start_matches('#')
        ));
    }
    out.push_str("</defs>\n");
    // Shapes in drawing order (containers first), then branches and links above them, then
    // the shapes that are not containers... simpler: containers, branches, links, other shapes.
    let ordered = map.nodes_in_draw_order();
    for n in ordered.iter().filter(|n| n.shape.is_container()) {
        if let Some(b) = find(n.id) {
            draw_node(&mut out, map, b, n);
        }
    }
    // Tree branches.
    for b in &boxes {
        let Some(p) = map.node(b.id).and_then(|n| n.parent).and_then(find) else { continue };
        let color = map.node(b.id).map(|n| n.color.hex()).unwrap_or_default();
        let right = b.aabb.cx >= p.aabb.cx;
        let (sx, ex) = if right { (p.x() + p.w(), b.x()) } else { (p.x(), b.x() + b.w()) };
        let (sy, ey) = (p.aabb.cy, b.aabb.cy);
        let bend = ((ex - sx).abs() * 0.5).max(12.0) * if right { 1.0 } else { -1.0 };
        out.push_str(&format!(
            "<path d=\"M{} {} C{} {} {} {} {} {}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"2.5\" stroke-opacity=\"0.9\" style=\"filter:brightness(0.8)\"/>\n",
            num(sx),
            num(sy),
            num(sx + bend),
            num(sy),
            num(ex - bend),
            num(ey),
            num(ex),
            num(ey)
        ));
    }
    // Links (their labels are drawn last, above the shapes).
    let mut labels = String::new();
    for e in map.edges() {
        let (Some(a), Some(b)) = (find(e.from), find(e.to)) else { continue };
        let path = route::route(a.aabb, b.aabb, e.style.route, 3.0);
        let color = e.style.color.map_or(LINK.to_string(), |c| c.hex());
        let width = if e.style.width > 0.0 { e.style.width } else { 1.6 };
        let dash = match e.style.line {
            LineStyle::Solid => String::new(),
            LineStyle::Dashed => format!(" stroke-dasharray=\"{} {}\"", num(width * 4.0 + 3.0), num(width * 2.0 + 3.0)),
            LineStyle::Dotted => format!(" stroke-dasharray=\"{} {}\" stroke-linecap=\"round\"", num(width * 0.1), num(width * 2.5)),
        };
        let marker_id = color.trim_start_matches('#');
        let (start, end) = match e.style.arrows {
            Arrows::None => ("", ""),
            Arrows::End => ("", " marker-end"),
            Arrows::Start => (" marker-start", ""),
            Arrows::Both => (" marker-start", " marker-end"),
        };
        let markers = format!(
            "{}{}",
            if start.is_empty() { String::new() } else { format!(" marker-start=\"url(#arrow-{marker_id})\"") },
            if end.is_empty() { String::new() } else { format!(" marker-end=\"url(#arrow-{marker_id})\"") }
        );
        let joins = if e.style.route == Route::Orthogonal { " stroke-linejoin=\"round\"" } else { "" };
        out.push_str(&format!(
            "<polyline points=\"{}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"{}\"{dash}{joins}{markers}/>\n",
            points(&path),
            num(width)
        ));
        if !e.label.is_empty() {
            let m = route::midpoint(&path);
            let w = e.label.chars().count() as f32 * 6.4 + 12.0;
            labels.push_str(&format!(
                "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"16\" rx=\"4\" fill=\"#ffffff\"/><text x=\"{}\" y=\"{}\" font-size=\"12\" fill=\"{color}\" text-anchor=\"middle\">{}</text>\n",
                num(m.x - w / 2.0),
                num(m.y - 8.0),
                num(w),
                num(m.x),
                num(m.y + 4.0),
                escape(&e.label)
            ));
        }
    }
    for n in ordered.iter().filter(|n| !n.shape.is_container()) {
        if let Some(b) = find(n.id) {
            draw_node(&mut out, map, b, n);
        }
    }
    out.push_str(&labels);
    out.push_str("</svg>\n");
    out
}

fn draw_node(out: &mut String, map: &Map, b: &Box_, n: &ideacraft_core::Node) {
    let fill = n.color.hex();
    let (cx, cy, w, h) = (b.aabb.cx, b.aabb.cy, b.w(), b.h());
    let stroke = if n.shape.is_container() {
        "stroke=\"#b4b9c6\" stroke-width=\"1\" stroke-dasharray=\"6 4\""
    } else {
        "stroke=\"#9aa0b0\" stroke-width=\"1\""
    };
    match n.shape {
        Shape::Text => {}
        Shape::Ellipse => out.push_str(&format!(
            "<ellipse cx=\"{}\" cy=\"{}\" rx=\"{}\" ry=\"{}\" fill=\"{fill}\" {stroke}/>\n",
            num(cx),
            num(cy),
            num(w / 2.0),
            num(h / 2.0)
        )),
        s => match s.polygon(w, h) {
            Some(poly) => {
                let pts: Vec<Point> = poly.into_iter().map(|p| Point::new(cx + p.x, cy + p.y)).collect();
                out.push_str(&format!("<polygon points=\"{}\" fill=\"{fill}\" {stroke}/>\n", points(&pts)));
            }
            None => {
                let r = match s {
                    Shape::Rect | Shape::Subprocess => 0.0,
                    Shape::Pill => h / 2.0,
                    Shape::Container => 12.0,
                    _ => 8.0,
                };
                let opacity = if s.is_container() { " fill-opacity=\"0.6\"" } else { "" };
                out.push_str(&format!(
                    "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" rx=\"{}\" fill=\"{fill}\"{opacity} {stroke}/>\n",
                    num(b.x()),
                    num(b.y()),
                    num(w),
                    num(h),
                    num(r)
                ));
            }
        },
    }
    for line in n.shape.inner_strokes(w, h) {
        let pts: Vec<Point> = line.into_iter().map(|p| Point::new(cx + p.x, cy + p.y)).collect();
        out.push_str(&format!("<polyline points=\"{}\" fill=\"none\" stroke=\"#9aa0b0\" stroke-width=\"1\"/>\n", points(&pts)));
    }
    let glyph = n.kind.as_deref().and_then(stencil::find).map(|s| s.glyph).filter(|g| !g.is_empty());
    let has_label = !n.label.trim().is_empty();
    if let Some(g) = glyph {
        let (gx, gy, gw, gh) = route::glyph_box(w, h, has_label);
        let at = |(x, y): &(f32, f32)| Point::new(cx + gx + x * gw, cy + gy + y * gh);
        let sw = (gw / 24.0).clamp(1.0, 2.5);
        for line in g.lines {
            let pts: Vec<Point> = line.iter().map(at).collect();
            out.push_str(&format!(
                "<polyline points=\"{}\" fill=\"none\" stroke=\"{TEXT}\" stroke-width=\"{}\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n",
                points(&pts),
                num(sw)
            ));
        }
        for (x, y, r) in g.circles {
            let c = at(&(*x, *y));
            out.push_str(&format!(
                "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" fill=\"none\" stroke=\"{TEXT}\" stroke-width=\"{}\"/>\n",
                num(c.x),
                num(c.y),
                num(r * gw),
                num(sw)
            ));
        }
        for poly in g.fills {
            let pts: Vec<Point> = poly.iter().map(at).collect();
            out.push_str(&format!("<polygon points=\"{}\" fill=\"{TEXT}\"/>\n", points(&pts)));
        }
    }
    if n.shape.is_container() {
        // Title in the top-left corner.
        out.push_str(&format!(
            "<text x=\"{}\" y=\"{}\" font-size=\"{FONT}\" font-weight=\"600\" fill=\"#6b7080\">{}</text>\n",
            num(b.x() + 12.0),
            num(b.y() + 20.0),
            escape(&n.label.replace('\n', " "))
        ));
    } else if has_label {
        // Label: centred, or in the lower part under a glyph.
        let center_y = if glyph.is_some() { cy + h * 0.5 - (b.lines.len() as f32 * LINE_H) / 2.0 - 4.0 } else { cy };
        let top = center_y - (b.lines.len() as f32 - 1.0) * LINE_H / 2.0 + FONT * 0.35;
        let size = if glyph.is_some() { FONT - 1.0 } else { FONT };
        for (i, line) in b.lines.iter().enumerate() {
            out.push_str(&format!(
                "<text x=\"{}\" y=\"{}\" font-size=\"{size}\" fill=\"{TEXT}\" text-anchor=\"middle\">{}</text>\n",
                num(cx),
                num(top + i as f32 * LINE_H),
                escape(line)
            ));
        }
    }
    if let Some(status) = n.status_light() {
        let color = match status {
            Status::Up => "#22a06b",
            Status::Warning => "#e3a008",
            Status::Down => "#e5484d",
        };
        out.push_str(&format!(
            "<circle cx=\"{}\" cy=\"{}\" r=\"5\" fill=\"{color}\" stroke=\"#ffffff\" stroke-width=\"1.5\"/>\n",
            num(b.x() + w - 8.0),
            num(b.y() + 8.0)
        ));
    }
    if n.collapsed {
        let hidden = map.descendants(n.id).len();
        out.push_str(&format!(
            "<circle cx=\"{}\" cy=\"{}\" r=\"10\" fill=\"#5b5bf0\"/><text x=\"{}\" y=\"{}\" font-size=\"11\" fill=\"#ffffff\" text-anchor=\"middle\">{hidden}</text>\n",
            num(b.x() + w + 12.0),
            num(cy),
            num(b.x() + w + 12.0),
            num(cy + 4.0)
        ));
    }
}

#[cfg(test)]
mod tests {
    use ideacraft_core::{Arrows, EdgeStyle, LineStyle, Point, Route};

    use super::*;

    #[test]
    fn svg_has_every_visible_idea_and_link() {
        let map = crate::templates::find("concept-map").map(|t| t.build()).unwrap();
        let svg = to_svg(&map);
        assert!(svg.starts_with("<svg") && svg.trim_end().ends_with("</svg>"));
        for n in map.nodes() {
            assert!(svg.contains(&escape(n.label.split_whitespace().next().unwrap())), "{}", n.label);
        }
        assert_eq!(svg.matches("marker-end=").count(), map.edge_count(), "one arrowhead per link");
        assert!(to_svg(&Map::default()).contains("<svg"), "an empty map is still a picture");
        assert_eq!(wrap("a b", 28), vec!["a b"]);
        assert!(wrap(&"word ".repeat(50), 28).iter().all(|l| l.chars().count() <= 28));
    }

    #[test]
    fn diagram_shapes_glyphs_and_link_styles_are_drawn() {
        let mut map = Map::new("net");
        let r = map.add_stencil(stencil::find("net.router").unwrap(), Some("R1"), Point::new(0.0, 0.0)).unwrap();
        map.set_prop(r, "status", "down").unwrap();
        let d = map.add_stencil(stencil::find("flow.decision").unwrap(), Some("OK?"), Point::new(300.0, 0.0)).unwrap();
        let z = map.add_stencil(stencil::find("basic.container").unwrap(), Some("DMZ"), Point::new(150.0, 0.0)).unwrap();
        let e = map.connect(r, d, "link").unwrap();
        map.set_edge_style(
            e,
            EdgeStyle {
                line: LineStyle::Dashed,
                arrows: Arrows::Both,
                route: Route::Orthogonal,
                color: Some(ideacraft_core::Rgb(0, 0, 255)),
                width: 2.0,
            },
        )
        .unwrap();
        let svg = to_svg(&map);
        assert!(svg.contains("<polygon"), "diamond");
        assert!(svg.contains("stroke-dasharray"), "dashed link");
        assert!(svg.contains("marker-start=\"url(#arrow-0000ff)\"") && svg.contains("marker-end=\"url(#arrow-0000ff)\""));
        assert!(svg.contains("#e5484d"), "status light");
        assert!(svg.contains("DMZ"));
        assert!(svg.find("DMZ").unwrap() < svg.find("R1").unwrap(), "containers are drawn first");
        assert!(svg.matches("<polyline").count() > 6, "the router glyph is drawn");
        let _ = z;
    }
}
