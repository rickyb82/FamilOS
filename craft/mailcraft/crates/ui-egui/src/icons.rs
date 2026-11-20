//! The icon set, drawn in code (strokes on a 24-unit grid): no bitmap or vendor artwork.

use egui::{Color32, Painter, Pos2, Rect, Shape, Stroke, pos2, vec2};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Icon {
    Logo,
    Compose,
    Inbox,
    Star,
    StarFilled,
    Sent,
    Drafts,
    Archive,
    Spam,
    Trash,
    Folder,
    Search,
    Sync,
    Settings,
    Moon,
    Sun,
    Globe,
    Close,
    Chevron,
    ChevronRight,
    Reply,
    ReplyAll,
    Forward,
    Attachment,
    MailOpen,
    Mail,
    Move,
    Back,
    Plus,
    Check,
    Account,
    Send,
    Save,
    More,
    Download,
    Warning,
    Delete,
    Keyboard,
    Print,
    Image,
    Bold,
    Italic,
    Underline,
    TextColor,
    TextSize,
    AlignLeft,
    AlignCenter,
    AlignRight,
    ListBullet,
    ListNumber,
    Indent,
    Undo,
    Redo,
    Link,
    Emoji,
    Signature,
    TextFormat,
    Robot,
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
    let star = |filled: bool| {
        let pts: Vec<Pos2> = (0..10)
            .map(|i| {
                let a = -std::f32::consts::FRAC_PI_2 + i as f32 * std::f32::consts::PI / 5.0;
                let r = if i % 2 == 0 { 9.0 } else { 4.2 };
                at(12.0 + a.cos() * r, 12.5 + a.sin() * r)
            })
            .collect();
        if filled {
            p.add(Shape::convex_polygon(pts.clone(), c, Stroke::NONE));
        }
        p.add(Shape::closed_line(pts, st));
    };
    let envelope = |open: bool| {
        if open {
            line(&[(3.5, 10.0), (12.0, 4.0), (20.5, 10.0), (20.5, 20.5), (3.5, 20.5), (3.5, 10.0)]);
            line(&[(3.5, 10.5), (12.0, 15.5), (20.5, 10.5)]);
        } else {
            rrect(3.5, 6.0, 20.5, 18.5, 1.5);
            line(&[(3.5, 7.0), (12.0, 13.0), (20.5, 7.0)]);
        }
    };
    match icon {
        Icon::Logo => {
            // The MailCraft mark: an envelope with the craft-family three-dot spark (brand colours).
            p.rect_filled(Rect::from_min_max(at(2.5, 5.0), at(21.5, 19.5)), 3.0 * s, Color32::from_rgb(0x4a, 0x6c, 0xf7));
            p.add(Shape::line(vec![at(2.5, 7.0), at(12.0, 13.5), at(21.5, 7.0)], Stroke::new(w * 1.1, Color32::WHITE)));
            p.circle_filled(at(19.0, 5.0), 3.2 * s, Color32::from_rgb(0xff, 0xb4, 0x3d));
        }
        Icon::Compose => {
            line(&[(4.0, 20.0), (4.5, 16.0), (16.0, 4.5), (19.5, 8.0), (8.0, 19.5), (4.0, 20.0)]);
            line(&[(13.5, 7.0), (17.0, 10.5)]);
        }
        Icon::Inbox => {
            line(&[(4.0, 4.5), (20.0, 4.5), (20.0, 19.5), (4.0, 19.5), (4.0, 4.5)]);
            line(&[(4.0, 13.0), (8.5, 13.0), (10.0, 16.0), (14.0, 16.0), (15.5, 13.0), (20.0, 13.0)]);
        }
        Icon::Star => star(false),
        Icon::StarFilled => star(true),
        Icon::Sent | Icon::Send => {
            line(&[(3.5, 11.0), (20.5, 4.0), (14.0, 20.5), (11.5, 13.0), (3.5, 11.0)]);
            line(&[(11.5, 13.0), (20.5, 4.0)]);
        }
        Icon::Drafts => {
            line(&[(6.0, 3.0), (14.0, 3.0), (19.0, 8.0), (19.0, 21.0), (6.0, 21.0), (6.0, 3.0)]);
            line(&[(14.0, 3.0), (14.0, 8.0), (19.0, 8.0)]);
            line(&[(9.0, 13.0), (16.0, 13.0)]);
            line(&[(9.0, 17.0), (14.0, 17.0)]);
        }
        Icon::Archive => {
            rrect(3.5, 4.0, 20.5, 8.5, 1.0);
            line(&[(5.0, 8.5), (5.0, 20.0), (19.0, 20.0), (19.0, 8.5)]);
            line(&[(10.0, 13.0), (14.0, 13.0)]);
        }
        Icon::Spam | Icon::Warning => {
            line(&[(12.0, 3.5), (21.0, 20.0), (3.0, 20.0), (12.0, 3.5)]);
            line(&[(12.0, 9.5), (12.0, 14.5)]);
            dot(12.0, 17.2, 1.1);
        }
        Icon::Trash | Icon::Delete => {
            line(&[(4.0, 6.5), (20.0, 6.5)]);
            line(&[(9.0, 6.5), (9.5, 3.5), (14.5, 3.5), (15.0, 6.5)]);
            line(&[(6.0, 6.5), (7.0, 20.5), (17.0, 20.5), (18.0, 6.5)]);
            line(&[(10.0, 10.0), (10.0, 17.0)]);
            line(&[(14.0, 10.0), (14.0, 17.0)]);
        }
        Icon::Folder | Icon::Move => {
            line(&[(3.0, 6.0), (9.5, 6.0), (11.5, 8.5), (21.0, 8.5), (21.0, 19.5), (3.0, 19.5), (3.0, 6.0)]);
            if icon == Icon::Move {
                line(&[(9.0, 14.0), (15.0, 14.0)]);
                line(&[(12.5, 11.5), (15.0, 14.0), (12.5, 16.5)]);
            }
        }
        Icon::Search => {
            circle(10.5, 10.5, 6.0);
            line(&[(15.0, 15.0), (20.5, 20.5)]);
        }
        Icon::Sync => {
            p.add(Shape::line(arc(at(12.0, 12.0), 7.5 * s, 210.0, 330.0, 14), st));
            p.add(Shape::line(arc(at(12.0, 12.0), 7.5 * s, 30.0, 150.0, 14), st));
            line(&[(15.5, 5.0), (18.5, 8.2), (14.8, 9.0)]);
            line(&[(8.5, 19.0), (5.5, 15.8), (9.2, 15.0)]);
        }
        Icon::Settings => {
            circle(12.0, 12.0, 3.2);
            for k in 0..8 {
                let a = k as f32 * std::f32::consts::FRAC_PI_4;
                let (cx, cy) = (a.cos(), a.sin());
                line(&[(12.0 + cx * 6.0, 12.0 + cy * 6.0), (12.0 + cx * 9.0, 12.0 + cy * 9.0)]);
            }
            circle(12.0, 12.0, 6.5);
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
        Icon::ChevronRight => line(&[(10.0, 7.0), (15.0, 12.0), (10.0, 17.0)]),
        Icon::Reply | Icon::ReplyAll => {
            let dx = if icon == Icon::ReplyAll { 3.0 } else { 0.0 };
            line(&[(9.0 + dx, 6.0), (3.5 + dx, 11.0), (9.0 + dx, 16.0)]);
            line(&[(3.5 + dx, 11.0), (14.0, 11.0), (18.0, 12.5), (20.5, 16.5), (20.5, 19.0)]);
            if icon == Icon::ReplyAll {
                line(&[(6.5, 6.5), (2.0, 11.0), (6.5, 15.5)]);
            }
        }
        Icon::Forward => {
            line(&[(15.0, 6.0), (20.5, 11.0), (15.0, 16.0)]);
            line(&[(20.5, 11.0), (10.0, 11.0), (6.0, 12.5), (3.5, 16.5), (3.5, 19.0)]);
        }
        Icon::Attachment => {
            p.add(Shape::line(arc(at(14.5, 7.5), 3.5 * s, 180.0, 360.0, 10), st));
            line(&[(11.0, 7.5), (11.0, 16.5)]);
            p.add(Shape::line(arc(at(13.5, 16.5), 2.5 * s, 0.0, 180.0, 8), st));
            line(&[(16.0, 16.5), (16.0, 8.0)]);
            line(&[(18.0, 7.5), (18.0, 15.5)]);
            p.add(Shape::line(arc(at(13.5, 15.5), 4.5 * s, 0.0, 180.0, 10), st));
            line(&[(9.0, 15.5), (9.0, 9.0)]);
        }
        Icon::MailOpen => envelope(true),
        Icon::Mail => envelope(false),
        Icon::Back => {
            line(&[(10.0, 6.0), (4.0, 12.0), (10.0, 18.0)]);
            line(&[(4.0, 12.0), (20.0, 12.0)]);
        }
        Icon::Plus => {
            line(&[(12.0, 5.0), (12.0, 19.0)]);
            line(&[(5.0, 12.0), (19.0, 12.0)]);
        }
        Icon::Check => {
            circle(12.0, 12.0, 8.5);
            line(&[(7.5, 12.5), (10.5, 15.5), (16.5, 9.0)]);
        }
        Icon::Account => {
            circle(12.0, 8.5, 4.5);
            p.add(Shape::line(arc(at(12.0, 22.0), 8.0 * s, 200.0, 340.0, 14), st));
        }
        Icon::Save => {
            line(&[(4.0, 4.0), (16.5, 4.0), (20.0, 7.5), (20.0, 20.0), (4.0, 20.0), (4.0, 4.0)]);
            rrect(8.0, 4.0, 15.0, 9.0, 0.5);
            rrect(7.5, 13.0, 16.5, 20.0, 0.5);
        }
        Icon::More => {
            dot(6.0, 12.0, 1.7);
            dot(12.0, 12.0, 1.7);
            dot(18.0, 12.0, 1.7);
        }
        Icon::Download => {
            line(&[(12.0, 4.0), (12.0, 15.0)]);
            line(&[(7.5, 10.5), (12.0, 15.0), (16.5, 10.5)]);
            line(&[(4.5, 19.5), (19.5, 19.5)]);
        }
        Icon::Keyboard => {
            rrect(3.0, 6.5, 21.0, 17.5, 1.5);
            for x in [6.5, 10.0, 13.5, 17.0] {
                dot(x, 10.0, 0.9);
            }
            line(&[(7.5, 14.0), (16.5, 14.0)]);
        }
        Icon::Print => {
            rrect(4.0, 9.5, 20.0, 17.5, 1.5);
            line(&[(7.5, 9.5), (7.5, 4.5), (16.5, 4.5), (16.5, 9.5)]);
            line(&[(7.5, 14.5), (7.5, 20.0), (16.5, 20.0), (16.5, 14.5)]);
            dot(17.0, 12.0, 0.9);
        }
        Icon::Image => {
            rrect(3.5, 5.0, 20.5, 19.0, 1.5);
            line(&[(3.5, 16.0), (9.0, 11.0), (13.0, 15.0), (16.0, 12.5), (20.5, 16.5)]);
            dot(15.5, 8.5, 1.4);
        }
        Icon::Bold => {
            line(&[(7.0, 4.5), (7.0, 19.5), (14.0, 19.5), (17.0, 17.5), (17.5, 14.0), (15.0, 12.0), (7.0, 12.0)]);
            line(&[(7.0, 4.5), (13.5, 4.5), (16.0, 6.5), (16.0, 10.0), (13.5, 12.0)]);
        }
        Icon::Italic => {
            line(&[(10.0, 4.5), (18.0, 4.5)]);
            line(&[(6.0, 19.5), (14.0, 19.5)]);
            line(&[(14.0, 4.5), (10.0, 19.5)]);
        }
        Icon::Underline => {
            line(&[(7.0, 4.5), (7.0, 12.0), (8.5, 15.5), (12.0, 17.0), (15.5, 15.5), (17.0, 12.0), (17.0, 4.5)]);
            line(&[(5.5, 20.5), (18.5, 20.5)]);
        }
        Icon::TextColor => {
            line(&[(6.5, 16.5), (12.0, 3.5), (17.5, 16.5)]);
            line(&[(8.5, 12.0), (15.5, 12.0)]);
            p.rect_filled(Rect::from_min_max(at(4.5, 19.0), at(19.5, 21.5)), 1.0 * s, c);
        }
        Icon::TextSize => {
            line(&[(3.5, 10.0), (9.5, 10.0)]);
            line(&[(6.5, 10.0), (6.5, 19.5)]);
            line(&[(11.0, 4.5), (21.0, 4.5)]);
            line(&[(16.0, 4.5), (16.0, 19.5)]);
        }
        Icon::AlignLeft | Icon::AlignCenter | Icon::AlignRight => {
            for (i, len) in [(5.0, 17.0), (9.0, 11.0), (13.0, 17.0), (17.0, 11.0)] {
                let (x0, x1) = match icon {
                    Icon::AlignLeft => (3.5, 3.5 + len),
                    Icon::AlignRight => (20.5 - len, 20.5),
                    _ => (12.0 - len / 2.0, 12.0 + len / 2.0),
                };
                line(&[(x0, i), (x1, i)]);
            }
        }
        Icon::ListBullet => {
            for y in [6.5, 12.0, 17.5] {
                dot(5.5, y, 1.4);
                line(&[(9.5, y), (20.5, y)]);
            }
        }
        Icon::ListNumber => {
            line(&[(4.5, 5.5), (6.0, 4.5), (6.0, 8.5)]);
            line(&[(4.5, 11.5), (6.5, 11.0), (6.5, 13.0), (4.5, 15.0), (7.0, 15.0)]);
            line(&[(4.5, 17.5), (7.0, 17.5), (6.0, 19.0), (7.0, 20.5), (4.5, 20.5)]);
            for y in [6.5, 12.5, 18.5] {
                line(&[(10.0, y), (20.5, y)]);
            }
        }
        Icon::Indent => {
            line(&[(3.5, 5.0), (20.5, 5.0)]);
            line(&[(10.5, 9.5), (20.5, 9.5)]);
            line(&[(10.5, 14.0), (20.5, 14.0)]);
            line(&[(3.5, 19.0), (20.5, 19.0)]);
            line(&[(3.5, 9.5), (7.0, 11.75), (3.5, 14.0)]);
        }
        Icon::Undo | Icon::Redo => {
            let flip = |x: f32| if matches!(icon, Icon::Redo) { 24.0 - x } else { x };
            line(&[(flip(4.0), 10.0), (flip(8.5), 5.5)]);
            line(&[(flip(4.0), 10.0), (flip(8.5), 14.5)]);
            p.add(Shape::line(
                (0..=8)
                    .map(|i| {
                        let a = std::f32::consts::PI * (0.5 - i as f32 / 8.0 * 0.5);
                        at(flip(4.0 + 9.5 * (1.0 - a.cos()) + 1.0), 10.0 + 9.0 * a.sin() * 0.95 - 8.5 * (1.0 - a.sin()) * 0.0)
                    })
                    .map(|pt| Pos2::new(pt.x, pt.y))
                    .collect(),
                st,
            ));
            line(&[(flip(4.0), 10.0), (flip(13.0), 10.0)]);
            line(&[(flip(13.0), 10.0), (flip(17.0), 11.5), (flip(18.5), 15.0), (flip(17.0), 18.5), (flip(13.0), 20.0), (flip(7.5), 20.0)]);
        }
        Icon::Link => {
            line(&[(10.0, 7.5), (13.5, 4.0), (18.5, 4.0), (20.5, 6.0), (20.5, 11.0), (16.5, 15.0)]);
            line(&[(14.0, 16.5), (10.5, 20.0), (5.5, 20.0), (3.5, 18.0), (3.5, 13.0), (7.5, 9.0)]);
            line(&[(9.0, 15.0), (15.0, 9.0)]);
        }
        Icon::Emoji => {
            circle(12.0, 12.0, 8.5);
            dot(9.0, 10.0, 1.1);
            dot(15.0, 10.0, 1.1);
            p.add(Shape::line(arc(at(12.0, 12.5), 4.5 * s, 20.0, 160.0, 10), st));
        }
        Icon::Signature => {
            line(&[(4.0, 19.5), (20.0, 19.5)]);
            line(&[(5.0, 15.5), (8.0, 11.0), (10.5, 15.0), (12.5, 9.0), (14.0, 14.5), (16.0, 11.5), (17.0, 13.5), (19.0, 12.0)]);
        }
        Icon::TextFormat => {
            line(&[(3.5, 18.0), (8.5, 5.0), (13.5, 18.0)]);
            line(&[(5.5, 13.0), (11.5, 13.0)]);
            line(&[(14.5, 18.0), (17.5, 10.0), (20.5, 18.0)]);
            line(&[(15.5, 15.5), (19.5, 15.5)]);
        }
        Icon::Robot => {
            rrect(5.0, 8.0, 19.0, 19.5, 2.0);
            line(&[(12.0, 8.0), (12.0, 4.5)]);
            dot(12.0, 4.0, 1.3);
            dot(9.0, 13.0, 1.4);
            dot(15.0, 13.0, 1.4);
            line(&[(9.0, 16.5), (15.0, 16.5)]);
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
