//! Connector routing shared by every renderer: where a link leaves one box, how it travels and
//! where it enters the other.

use crate::{Point, Route};

/// An axis-aligned box: centre and half extents.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Aabb {
    pub cx: f32,
    pub cy: f32,
    pub hw: f32,
    pub hh: f32,
}

impl Aabb {
    pub const fn new(cx: f32, cy: f32, hw: f32, hh: f32) -> Self {
        Self { cx, cy, hw, hh }
    }

    /// Where the ray from the centre towards `(tx, ty)` leaves the box, `pad` outside it.
    pub fn exit_towards(&self, tx: f32, ty: f32, pad: f32) -> Point {
        let (dx, dy) = (tx - self.cx, ty - self.cy);
        if dx.abs() < f32::EPSILON && dy.abs() < f32::EPSILON {
            return Point::new(self.cx, self.cy);
        }
        let sx = if dx.abs() > f32::EPSILON { (self.hw + pad) / dx.abs() } else { f32::MAX };
        let sy = if dy.abs() > f32::EPSILON { (self.hh + pad) / dy.abs() } else { f32::MAX };
        let k = sx.min(sy).min(1.0);
        Point::new(self.cx + dx * k, self.cy + dy * k)
    }

    pub fn contains(&self, p: Point) -> bool {
        (p.x - self.cx).abs() <= self.hw && (p.y - self.cy).abs() <= self.hh
    }
}

/// The polyline a link follows from `a` to `b`. Straight links join the two boundaries;
/// orthogonal links leave horizontally or vertically and bend once or twice; curved links are a
/// sampled cubic Bézier. The first and last points sit `pad` outside the boxes.
pub fn route(a: Aabb, b: Aabb, kind: Route, pad: f32) -> Vec<Point> {
    match kind {
        Route::Straight => vec![a.exit_towards(b.cx, b.cy, pad), b.exit_towards(a.cx, a.cy, pad)],
        Route::Orthogonal => orthogonal(a, b, pad),
        Route::Curved => curved(a, b, pad),
    }
}

fn orthogonal(a: Aabb, b: Aabb, pad: f32) -> Vec<Point> {
    let (dx, dy) = (b.cx - a.cx, b.cy - a.cy);
    let gap_x = dx.abs() - a.hw - b.hw;
    let gap_y = dy.abs() - a.hh - b.hh;
    if gap_x >= gap_y {
        // Side to side: horizontal, vertical, horizontal.
        let sx = a.cx + a.hw.copysign(dx) + pad.copysign(dx);
        let ex = b.cx - b.hw.copysign(dx) - pad.copysign(dx);
        let mid = (sx + ex) / 2.0;
        if (a.cy - b.cy).abs() < 0.5 {
            vec![Point::new(sx, a.cy), Point::new(ex, b.cy)]
        } else {
            vec![Point::new(sx, a.cy), Point::new(mid, a.cy), Point::new(mid, b.cy), Point::new(ex, b.cy)]
        }
    } else {
        // Top to bottom: vertical, horizontal, vertical.
        let sy = a.cy + a.hh.copysign(dy) + pad.copysign(dy);
        let ey = b.cy - b.hh.copysign(dy) - pad.copysign(dy);
        let mid = (sy + ey) / 2.0;
        if (a.cx - b.cx).abs() < 0.5 {
            vec![Point::new(a.cx, sy), Point::new(b.cx, ey)]
        } else {
            vec![Point::new(a.cx, sy), Point::new(a.cx, mid), Point::new(b.cx, mid), Point::new(b.cx, ey)]
        }
    }
}

fn curved(a: Aabb, b: Aabb, pad: f32) -> Vec<Point> {
    let s = a.exit_towards(b.cx, b.cy, pad);
    let e = b.exit_towards(a.cx, a.cy, pad);
    let (dx, dy) = (e.x - s.x, e.y - s.y);
    let horizontal = dx.abs() >= dy.abs();
    let bend = (if horizontal { dx.abs() } else { dy.abs() } * 0.5).max(16.0);
    let (c1, c2) = if horizontal {
        (Point::new(s.x + bend.copysign(dx), s.y), Point::new(e.x - bend.copysign(dx), e.y))
    } else {
        (Point::new(s.x, s.y + bend.copysign(dy)), Point::new(e.x, e.y - bend.copysign(dy)))
    };
    (0..=24)
        .map(|i| {
            let t = i as f32 / 24.0;
            let u = 1.0 - t;
            Point::new(
                u * u * u * s.x + 3.0 * u * u * t * c1.x + 3.0 * u * t * t * c2.x + t * t * t * e.x,
                u * u * u * s.y + 3.0 * u * u * t * c1.y + 3.0 * u * t * t * c2.y + t * t * t * e.y,
            )
        })
        .collect()
}

/// Distance from `p` to the polyline.
pub fn distance_to_polyline(path: &[Point], p: Point) -> f32 {
    let mut best = f32::MAX;
    for w in path.windows(2) {
        let (a, b) = (w[0], w[1]);
        let (vx, vy) = (b.x - a.x, b.y - a.y);
        let len2 = vx * vx + vy * vy;
        let t = if len2 > 0.0 { ((p.x - a.x) * vx + (p.y - a.y) * vy) / len2 } else { 0.0 }.clamp(0.0, 1.0);
        let (qx, qy) = (a.x + vx * t, a.y + vy * t);
        best = best.min(((p.x - qx).powi(2) + (p.y - qy).powi(2)).sqrt());
    }
    best
}

/// The polyline's midpoint by length, for a label.
pub fn midpoint(path: &[Point]) -> Point {
    let total: f32 = path.windows(2).map(|w| ((w[1].x - w[0].x).powi(2) + (w[1].y - w[0].y).powi(2)).sqrt()).sum();
    let mut left = total / 2.0;
    for w in path.windows(2) {
        let seg = ((w[1].x - w[0].x).powi(2) + (w[1].y - w[0].y).powi(2)).sqrt();
        if seg >= left && seg > 0.0 {
            let t = left / seg;
            return Point::new(w[0].x + (w[1].x - w[0].x) * t, w[0].y + (w[1].y - w[0].y) * t);
        }
        left -= seg;
    }
    path.first().copied().unwrap_or_default()
}

/// The box a stencil's glyph is drawn in, around the node centre: the upper part when there is a
/// label below it, the whole inset box otherwise. Returns (x, y, w, h) offsets from the centre.
pub fn glyph_box(w: f32, h: f32, has_label: bool) -> (f32, f32, f32, f32) {
    if has_label {
        let gh = (h * 0.56).min(w * 0.7);
        let gw = gh;
        (-gw / 2.0, -h / 2.0 + h * 0.07, gw, gh)
    } else {
        let g = w.min(h) * 0.7;
        (-g / 2.0, -g / 2.0, g, g)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_start_and_end_outside_their_boxes() {
        let a = Aabb::new(0.0, 0.0, 50.0, 25.0);
        let b = Aabb::new(300.0, 120.0, 50.0, 25.0);
        for kind in Route::ALL {
            let path = route(a, b, kind, 3.0);
            assert!(path.len() >= 2, "{kind:?}");
            let (s, e) = (path[0], path[path.len() - 1]);
            assert!(!a.contains(s) && !b.contains(e), "{kind:?}: {s:?} {e:?}");
            assert!((s.x - 53.0).abs() < 0.01 || kind == Route::Straight, "{kind:?} leaves the right side");
        }
        let orth = route(a, b, Route::Orthogonal, 0.0);
        assert_eq!(orth.len(), 4);
        assert!(orth.windows(2).all(|w| (w[0].x - w[1].x).abs() < 0.01 || (w[0].y - w[1].y).abs() < 0.01), "only axis-aligned segments");
        let below = route(a, Aabb::new(0.0, 200.0, 50.0, 25.0), Route::Orthogonal, 0.0);
        assert_eq!(below.len(), 2, "stacked boxes join with one vertical segment");
        assert!(distance_to_polyline(&orth, midpoint(&orth)) < 0.01);
        assert!(distance_to_polyline(&orth, Point::new(-500.0, -500.0)) > 400.0);
        assert_eq!(route(a, a, Route::Straight, 0.0).len(), 2, "a box to itself still gives two points");
    }
}
