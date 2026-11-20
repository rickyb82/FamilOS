//! The icon set, drawn in code (original artwork, no files): strokes on a 24×24 grid scaled into
//! the target rectangle.

use egui::{Color32, Painter, Pos2, Rect, Shape, Stroke, pos2, vec2};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Icon {
    Logo,
    Templates,
    Elements,
    Layout,
    Files,
    Undo,
    Redo,
    Plus,
    Child,
    Sibling,
    Trash,
    Link,
    Collapse,
    Tree,
    Radial,
    Fit,
    ZoomIn,
    ZoomOut,
    Download,
    Upload,
    Save,
    Moon,
    Sun,
    Globe,
    Close,
    Chevron,
    Palette,
    Shape,
    Doc,
    Shapes,
    AlignLeft,
    AlignCenterX,
    AlignRight,
    AlignTop,
    AlignCenterY,
    AlignBottom,
    DistributeX,
    DistributeY,
    Grid,
    Duplicate,
    ToFront,
    ToBack,
    LineStyle,
    Arrows,
    Props,
    Diagram,
    MindMap,
    Layers,
    Check,
    Group,
    Ungroup,
    Eye,
    EyeOff,
    Lock,
    Unlock,
    Edit,
}

/// Paints `icon` into `rect` with stroke colour `c`.
pub fn paint(p: &Painter, rect: Rect, icon: Icon, c: Color32) {
    let s = rect.width().min(rect.height()) / 24.0;
    let o = rect.center() - vec2(12.0, 12.0) * s;
    let at = |x: f32, y: f32| -> Pos2 { o + vec2(x, y) * s };
    let w = (1.7 * s).max(1.0);
    let st = Stroke::new(w, c);
    let line = |pts: &[(f32, f32)]| {
        p.add(Shape::line(pts.iter().map(|(x, y)| at(*x, *y)).collect(), st));
    };
    let rrect = |x0: f32, y0: f32, x1: f32, y1: f32, r: f32| {
        p.rect_stroke(Rect::from_min_max(at(x0, y0), at(x1, y1)), r * s, st, egui::StrokeKind::Middle);
    };
    let circle = |x: f32, y: f32, r: f32| {
        p.circle_stroke(at(x, y), r * s, st);
    };
    let dot = |x: f32, y: f32, r: f32| {
        p.circle_filled(at(x, y), r * s, c);
    };
    match icon {
        Icon::Logo => {
            // Three ideas joined: the IdeaCraft mark (colours are the brand's own).
            let (a, b, d) = (at(6.0, 12.0), at(18.0, 6.0), at(18.0, 18.0));
            p.line_segment([a, b], Stroke::new(w, c));
            p.line_segment([a, d], Stroke::new(w, c));
            p.circle_filled(a, 4.2 * s, Color32::from_rgb(0xff, 0xb4, 0x3d));
            p.circle_filled(b, 3.4 * s, Color32::from_rgb(0x6c, 0x8c, 0xff));
            p.circle_filled(d, 3.4 * s, Color32::from_rgb(0x3d, 0xc2, 0x9a));
        }
        Icon::Templates => {
            rrect(3.5, 3.5, 10.5, 10.5, 1.5);
            rrect(13.5, 3.5, 20.5, 10.5, 1.5);
            rrect(3.5, 13.5, 10.5, 20.5, 1.5);
            rrect(13.5, 13.5, 20.5, 20.5, 1.5);
        }
        Icon::Elements => {
            circle(7.5, 7.5, 4.0);
            rrect(13.5, 3.5, 20.5, 11.5, 1.5);
            line(&[(12.0, 13.5), (16.5, 20.5), (7.5, 20.5), (12.0, 13.5)]);
        }
        Icon::Layout | Icon::Tree => {
            rrect(2.5, 9.0, 8.5, 15.0, 1.5);
            rrect(15.5, 3.0, 21.5, 8.0, 1.5);
            rrect(15.5, 16.0, 21.5, 21.0, 1.5);
            line(&[(8.5, 12.0), (12.0, 12.0), (12.0, 5.5), (15.5, 5.5)]);
            line(&[(12.0, 12.0), (12.0, 18.5), (15.5, 18.5)]);
        }
        Icon::Radial => {
            circle(12.0, 12.0, 3.0);
            for (x, y) in [(12.0, 3.0), (20.0, 12.0), (12.0, 21.0), (4.0, 12.0)] {
                dot(x, y, 1.9);
                let (dx, dy) = ((x - 12.0) / 9.0, (y - 12.0) / 9.0);
                line(&[(12.0 + dx * 3.0, 12.0 + dy * 3.0), (x - dx * 2.0, y - dy * 2.0)]);
            }
        }
        Icon::Files | Icon::Doc => {
            line(&[(6.0, 3.0), (14.0, 3.0), (19.0, 8.0), (19.0, 21.0), (6.0, 21.0), (6.0, 3.0)]);
            line(&[(14.0, 3.0), (14.0, 8.0), (19.0, 8.0)]);
            if icon == Icon::Files {
                line(&[(9.0, 13.0), (16.0, 13.0)]);
                line(&[(9.0, 17.0), (14.0, 17.0)]);
            }
        }
        Icon::Undo => {
            line(&[(8.0, 5.0), (4.0, 9.0), (8.0, 13.0)]);
            p.add(Shape::line(arc(at(13.5, 14.0), 5.5 * s, -90.0, 90.0, 12), st));
            line(&[(4.0, 9.0), (13.5, 8.5)]);
            line(&[(13.5, 19.5), (8.0, 19.5)]);
        }
        Icon::Redo => {
            line(&[(16.0, 5.0), (20.0, 9.0), (16.0, 13.0)]);
            p.add(Shape::line(arc(at(10.5, 14.0), 5.5 * s, 90.0, 270.0, 12), st));
            line(&[(20.0, 9.0), (10.5, 8.5)]);
            line(&[(10.5, 19.5), (16.0, 19.5)]);
        }
        Icon::Plus | Icon::ZoomIn | Icon::ZoomOut => {
            if icon == Icon::Plus {
                line(&[(12.0, 5.0), (12.0, 19.0)]);
                line(&[(5.0, 12.0), (19.0, 12.0)]);
            } else {
                circle(10.5, 10.5, 6.0);
                line(&[(15.0, 15.0), (20.0, 20.0)]);
                line(&[(8.0, 10.5), (13.0, 10.5)]);
                if icon == Icon::ZoomIn {
                    line(&[(10.5, 8.0), (10.5, 13.0)]);
                }
            }
        }
        Icon::Child => {
            rrect(3.0, 4.0, 11.0, 10.0, 1.5);
            line(&[(7.0, 10.0), (7.0, 17.0), (12.0, 17.0)]);
            line(&[(17.0, 13.5), (17.0, 20.5)]);
            line(&[(13.5, 17.0), (20.5, 17.0)]);
        }
        Icon::Sibling => {
            rrect(3.0, 4.0, 13.0, 10.0, 1.5);
            rrect(3.0, 14.0, 13.0, 20.0, 1.5);
            line(&[(18.0, 13.5), (18.0, 20.5)]);
            line(&[(14.5, 17.0), (21.5, 17.0)]);
        }
        Icon::Trash => {
            line(&[(4.0, 6.5), (20.0, 6.5)]);
            line(&[(9.0, 6.5), (9.5, 3.5), (14.5, 3.5), (15.0, 6.5)]);
            line(&[(6.0, 6.5), (7.0, 20.5), (17.0, 20.5), (18.0, 6.5)]);
            line(&[(10.0, 10.0), (10.0, 17.0)]);
            line(&[(14.0, 10.0), (14.0, 17.0)]);
        }
        Icon::Link => {
            dot(5.0, 18.0, 2.2);
            dot(19.0, 6.0, 2.2);
            p.extend(Shape::dashed_line(&[at(7.0, 16.3), at(17.0, 7.7)], st, 2.5 * s, 2.0 * s));
            line(&[(13.5, 6.5), (17.5, 7.2), (16.8, 11.0)]);
        }
        Icon::Collapse => {
            rrect(3.5, 3.5, 20.5, 20.5, 3.0);
            line(&[(8.0, 12.0), (16.0, 12.0)]);
        }
        Icon::Fit => {
            line(&[(4.0, 9.0), (4.0, 4.0), (9.0, 4.0)]);
            line(&[(15.0, 4.0), (20.0, 4.0), (20.0, 9.0)]);
            line(&[(20.0, 15.0), (20.0, 20.0), (15.0, 20.0)]);
            line(&[(9.0, 20.0), (4.0, 20.0), (4.0, 15.0)]);
            rrect(8.5, 8.5, 15.5, 15.5, 1.5);
        }
        Icon::Download | Icon::Upload => {
            let (a, b) = if icon == Icon::Download { (4.0, 15.0) } else { (15.0, 4.0) };
            line(&[(12.0, a), (12.0, b)]);
            if icon == Icon::Download {
                line(&[(7.5, 10.5), (12.0, 15.0), (16.5, 10.5)]);
            } else {
                line(&[(7.5, 8.5), (12.0, 4.0), (16.5, 8.5)]);
            }
            line(&[(4.0, 16.0), (4.0, 20.0), (20.0, 20.0), (20.0, 16.0)]);
        }
        Icon::Save => {
            line(&[(4.0, 4.0), (16.5, 4.0), (20.0, 7.5), (20.0, 20.0), (4.0, 20.0), (4.0, 4.0)]);
            rrect(8.0, 4.0, 15.0, 9.0, 0.5);
            rrect(7.5, 13.0, 16.5, 20.0, 0.5);
        }
        Icon::Moon => {
            p.add(Shape::line(arc(at(12.0, 12.0), 8.0 * s, 40.0, 320.0, 24), st));
            p.add(Shape::line(arc(at(17.0, 9.0), 6.4 * s, 100.0, 232.0, 16), st));
        }
        Icon::Sun => {
            circle(12.0, 12.0, 4.0);
            for k in 0..8 {
                let a = k as f32 * std::f32::consts::FRAC_PI_4;
                let (cx, cy) = (a.cos(), a.sin());
                line(&[(12.0 + cx * 7.0, 12.0 + cy * 7.0), (12.0 + cx * 9.5, 12.0 + cy * 9.5)]);
            }
        }
        Icon::Globe => {
            circle(12.0, 12.0, 8.5);
            line(&[(3.5, 12.0), (20.5, 12.0)]);
            p.add(Shape::line(
                arc(at(12.0, 12.0), 8.5 * s, -90.0, 90.0, 16)
                    .into_iter()
                    .map(|q| pos2(at(12.0, 0.0).x + (q.x - at(12.0, 0.0).x) * 0.45, q.y))
                    .collect(),
                st,
            ));
            p.add(Shape::line(
                arc(at(12.0, 12.0), 8.5 * s, 90.0, 270.0, 16)
                    .into_iter()
                    .map(|q| pos2(at(12.0, 0.0).x + (q.x - at(12.0, 0.0).x) * 0.45, q.y))
                    .collect(),
                st,
            ));
        }
        Icon::Close => {
            line(&[(6.0, 6.0), (18.0, 18.0)]);
            line(&[(18.0, 6.0), (6.0, 18.0)]);
        }
        Icon::Chevron => line(&[(7.0, 10.0), (12.0, 15.0), (17.0, 10.0)]),
        Icon::Palette => {
            circle(12.0, 12.0, 8.5);
            dot(8.5, 9.0, 1.6);
            dot(12.5, 7.0, 1.6);
            dot(16.0, 10.0, 1.6);
            dot(9.0, 14.5, 1.6);
        }
        Icon::Shape => {
            rrect(3.5, 3.5, 13.0, 13.0, 1.0);
            circle(15.5, 15.5, 5.0);
        }
        Icon::Shapes => {
            rrect(3.0, 3.0, 11.0, 11.0, 1.0);
            circle(17.5, 7.0, 4.0);
            line(&[(7.0, 14.0), (11.5, 21.0), (2.5, 21.0), (7.0, 14.0)]);
            line(&[(13.5, 17.5), (17.5, 13.5), (21.5, 17.5), (17.5, 21.5), (13.5, 17.5)]);
        }
        Icon::AlignLeft => {
            line(&[(4.0, 3.0), (4.0, 21.0)]);
            rrect(7.0, 6.0, 19.0, 10.0, 0.5);
            rrect(7.0, 14.0, 14.0, 18.0, 0.5);
        }
        Icon::AlignRight => {
            line(&[(20.0, 3.0), (20.0, 21.0)]);
            rrect(5.0, 6.0, 17.0, 10.0, 0.5);
            rrect(10.0, 14.0, 17.0, 18.0, 0.5);
        }
        Icon::AlignCenterX => {
            line(&[(12.0, 3.0), (12.0, 21.0)]);
            rrect(5.0, 6.0, 19.0, 10.0, 0.5);
            rrect(8.0, 14.0, 16.0, 18.0, 0.5);
        }
        Icon::AlignTop => {
            line(&[(3.0, 4.0), (21.0, 4.0)]);
            rrect(6.0, 7.0, 10.0, 19.0, 0.5);
            rrect(14.0, 7.0, 18.0, 14.0, 0.5);
        }
        Icon::AlignBottom => {
            line(&[(3.0, 20.0), (21.0, 20.0)]);
            rrect(6.0, 5.0, 10.0, 17.0, 0.5);
            rrect(14.0, 10.0, 18.0, 17.0, 0.5);
        }
        Icon::AlignCenterY => {
            line(&[(3.0, 12.0), (21.0, 12.0)]);
            rrect(6.0, 5.0, 10.0, 19.0, 0.5);
            rrect(14.0, 8.0, 18.0, 16.0, 0.5);
        }
        Icon::DistributeX => {
            line(&[(3.0, 4.0), (3.0, 20.0)]);
            line(&[(21.0, 4.0), (21.0, 20.0)]);
            rrect(7.0, 8.0, 11.0, 16.0, 0.5);
            rrect(13.0, 8.0, 17.0, 16.0, 0.5);
        }
        Icon::DistributeY => {
            line(&[(4.0, 3.0), (20.0, 3.0)]);
            line(&[(4.0, 21.0), (20.0, 21.0)]);
            rrect(8.0, 7.0, 16.0, 11.0, 0.5);
            rrect(8.0, 13.0, 16.0, 17.0, 0.5);
        }
        Icon::Grid => {
            for k in [4.0, 12.0, 20.0] {
                for j in [4.0, 12.0, 20.0] {
                    dot(k, j, 1.6);
                }
            }
        }
        Icon::Duplicate => {
            rrect(3.5, 3.5, 14.5, 14.5, 1.5);
            line(&[(9.5, 18.5), (9.5, 20.5), (20.5, 20.5), (20.5, 9.5), (18.5, 9.5)]);
        }
        Icon::ToFront => {
            rrect(3.5, 9.5, 14.5, 20.5, 1.0);
            p.rect_filled(Rect::from_min_max(at(9.5, 3.5), at(20.5, 14.5)), 1.0 * s, c);
        }
        Icon::ToBack => {
            p.rect_filled(Rect::from_min_max(at(3.5, 9.5), at(14.5, 20.5)), 1.0 * s, c);
            rrect(9.5, 3.5, 20.5, 14.5, 1.0);
        }
        Icon::LineStyle => {
            line(&[(3.0, 6.0), (21.0, 6.0)]);
            line(&[(3.0, 12.0), (8.0, 12.0)]);
            line(&[(11.0, 12.0), (16.0, 12.0)]);
            line(&[(19.0, 12.0), (21.0, 12.0)]);
            dot(4.0, 18.0, 1.1);
            dot(9.0, 18.0, 1.1);
            dot(14.0, 18.0, 1.1);
            dot(19.0, 18.0, 1.1);
        }
        Icon::Arrows => {
            line(&[(3.0, 12.0), (21.0, 12.0)]);
            line(&[(16.0, 7.0), (21.0, 12.0), (16.0, 17.0)]);
            line(&[(8.0, 7.0), (3.0, 12.0), (8.0, 17.0)]);
        }
        Icon::Props => {
            rrect(3.5, 3.5, 20.5, 20.5, 2.0);
            line(&[(7.0, 9.0), (17.0, 9.0)]);
            line(&[(7.0, 13.0), (17.0, 13.0)]);
            line(&[(7.0, 17.0), (13.0, 17.0)]);
        }
        Icon::Diagram => {
            rrect(3.0, 3.0, 10.0, 9.0, 1.0);
            rrect(14.0, 3.0, 21.0, 9.0, 1.0);
            line(&[(8.0, 14.0), (12.0, 10.0), (16.0, 14.0), (12.0, 18.0), (8.0, 14.0)]);
            line(&[(6.5, 9.0), (6.5, 14.0), (8.0, 14.0)]);
            line(&[(17.5, 9.0), (17.5, 14.0), (16.0, 14.0)]);
            line(&[(12.0, 18.0), (12.0, 21.0)]);
        }
        Icon::MindMap => {
            circle(12.0, 12.0, 3.5);
            for (x, y) in [(4.0, 6.0), (20.0, 6.0), (4.0, 18.0), (20.0, 18.0)] {
                dot(x, y, 2.0);
                line(&[(12.0 + (x - 12.0) * 0.35, 12.0 + (y - 12.0) * 0.35), (x, y)]);
            }
        }
        Icon::Layers => {
            line(&[(12.0, 3.5), (20.5, 8.0), (12.0, 12.5), (3.5, 8.0), (12.0, 3.5)]);
            line(&[(3.5, 12.5), (12.0, 17.0), (20.5, 12.5)]);
            line(&[(3.5, 16.5), (12.0, 21.0), (20.5, 16.5)]);
        }
        Icon::Check => {
            circle(12.0, 12.0, 8.5);
            line(&[(7.5, 12.5), (10.5, 15.5), (16.5, 9.0)]);
        }
        Icon::Group => {
            rrect(6.0, 6.0, 12.5, 12.5, 1.0);
            rrect(11.5, 11.5, 18.0, 18.0, 1.0);
            line(&[(2.5, 7.0), (2.5, 2.5), (7.0, 2.5)]);
            line(&[(17.0, 2.5), (21.5, 2.5), (21.5, 7.0)]);
            line(&[(21.5, 17.0), (21.5, 21.5), (17.0, 21.5)]);
            line(&[(7.0, 21.5), (2.5, 21.5), (2.5, 17.0)]);
        }
        Icon::Ungroup => {
            rrect(2.5, 2.5, 10.5, 10.5, 1.0);
            rrect(13.5, 13.5, 21.5, 21.5, 1.0);
            line(&[(13.0, 3.0), (17.0, 3.0), (17.0, 7.0)]);
            line(&[(17.0, 3.0), (13.0, 7.0)]);
            line(&[(11.0, 21.0), (7.0, 21.0), (7.0, 17.0)]);
            line(&[(7.0, 21.0), (11.0, 17.0)]);
        }
        Icon::Eye | Icon::EyeOff => {
            line(&[(3.0, 12.0), (7.0, 7.5), (12.0, 6.0), (17.0, 7.5), (21.0, 12.0), (17.0, 16.5), (12.0, 18.0), (7.0, 16.5), (3.0, 12.0)]);
            circle(12.0, 12.0, 3.0);
            if icon == Icon::EyeOff {
                line(&[(4.5, 20.0), (19.5, 4.0)]);
            }
        }
        Icon::Lock => {
            rrect(5.0, 11.0, 19.0, 21.0, 1.5);
            p.add(Shape::line(arc(at(12.0, 11.0), 4.5 * s, 180.0, 360.0, 10), st));
            dot(12.0, 16.0, 1.5);
        }
        Icon::Unlock => {
            rrect(5.0, 11.0, 19.0, 21.0, 1.5);
            p.add(Shape::line(arc(at(15.0, 7.5), 4.5 * s, 180.0, 360.0, 10), st));
            line(&[(19.5, 7.5), (19.5, 11.0)]);
            line(&[(10.5, 7.5), (10.5, 9.0)]);
            dot(12.0, 16.0, 1.5);
        }
        Icon::Edit => {
            line(&[(4.0, 20.0), (4.5, 16.0), (16.0, 4.5), (19.5, 8.0), (8.0, 19.5), (4.0, 20.0)]);
            line(&[(13.5, 7.0), (17.0, 10.5)]);
        }
    }
}

/// Points on a circular arc, angles in degrees (0 = right, clockwise on screen).
fn arc(c: Pos2, r: f32, from: f32, to: f32, n: usize) -> Vec<Pos2> {
    (0..=n)
        .map(|i| {
            let a = (from + (to - from) * i as f32 / n as f32).to_radians();
            c + vec2(a.cos(), a.sin()) * r
        })
        .collect()
}
