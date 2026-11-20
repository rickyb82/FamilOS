//! draw.io / diagrams.net files (`.drawio`, also used by Visio-compatible tools): an `mxfile`
//! holding one or more `diagram`s whose `mxGraphModel` lists `mxCell` vertices and edges. The
//! model may be stored plain or compressed (URL-encoded, deflated, base64). Read from the public
//! format description and our own test files.

use std::collections::HashMap;
use std::io::Read;

use ideacraft_core::{Arrows, EdgeStyle, LineStyle, Map, Mode, NodeId, Point, Route, Shape, Size, stencil};

use crate::build;
use crate::xml::{self, Element, escape};
use crate::{Error, Result};

/// Style string entries: `key=value;key2=value2;flag;`.
fn style_map(style: &str) -> HashMap<String, String> {
    style
        .split(';')
        .filter(|s| !s.trim().is_empty())
        .map(|kv| match kv.split_once('=') {
            Some((k, v)) => (k.trim().to_string(), v.trim().to_string()),
            None => (kv.trim().to_string(), "1".to_string()),
        })
        .collect()
}

fn shape_of(style: &HashMap<String, String>) -> Shape {
    let shape = style.get("shape").map(String::as_str).unwrap_or_default();
    let head = style
        .keys()
        .find(|k| ["ellipse", "rhombus", "hexagon", "parallelogram", "cylinder", "note", "cloud", "text", "swimlane"].contains(&k.as_str()))
        .cloned()
        .unwrap_or_default();
    match (shape, head.as_str()) {
        ("ellipse", _) | (_, "ellipse") => Shape::Ellipse,
        ("rhombus", _) | (_, "rhombus") => Shape::Diamond,
        ("hexagon", _) | (_, "hexagon") => Shape::Hexagon,
        ("parallelogram", _) | (_, "parallelogram") => Shape::Parallelogram,
        ("cylinder", _) | ("cylinder3", _) | (_, "cylinder") => Shape::Cylinder,
        ("document", _) => Shape::Document,
        ("note", _) | (_, "note") => Shape::Note,
        ("cloud", _) | (_, "cloud") => Shape::Cloud,
        ("process", _) => Shape::Subprocess,
        ("swimlane", _) | (_, "swimlane") => Shape::Container,
        (_, "text") => Shape::Text,
        _ => {
            if style.get("rounded").is_some_and(|v| v == "1") {
                if style.get("arcSize").and_then(|v| v.parse::<f32>().ok()).is_some_and(|a| a >= 50.0) { Shape::Pill } else { Shape::Rounded }
            } else {
                Shape::Rect
            }
        }
    }
}

fn unescape_html(text: &str) -> String {
    // Labels may hold HTML (`<br>`, `&nbsp;`, bold…): keep the text, drop the tags.
    let mut out = String::new();
    let mut in_tag = false;
    let text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n").replace("</div>", "\n").replace("</p>", "\n");
    for c in text.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            c if !in_tag => out.push(c),
            _ => {}
        }
    }
    out.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .trim()
        .to_string()
}

/// Decodes a compressed `<diagram>` body: base64 → raw deflate → percent-encoded UTF-8.
fn inflate(text: &str) -> Result<String> {
    let compact: Vec<u8> = text.bytes().filter(|b| !b.is_ascii_whitespace()).collect();
    let bytes = base64_decode(&compact).ok_or_else(|| Error::Format("draw.io: bad base64".into()))?;
    let mut inflated = Vec::new();
    flate2::read::DeflateDecoder::new(&bytes[..])
        .take(crate::MAX_FILE_BYTES as u64)
        .read_to_end(&mut inflated)
        .map_err(|e| Error::Format(format!("draw.io: {e}")))?;
    let encoded = String::from_utf8_lossy(&inflated);
    Ok(percent_decode(&encoded))
}

fn base64_decode(input: &[u8]) -> Option<Vec<u8>> {
    let val = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => (c - b'A') as u32,
            b'a'..=b'z' => (c - b'a') as u32 + 26,
            b'0'..=b'9' => (c - b'0') as u32 + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            _ => return None,
        })
    };
    let mut out = Vec::with_capacity(input.len() * 3 / 4);
    let mut acc = 0u32;
    let mut bits = 0u32;
    for &c in input {
        if c == b'=' {
            break;
        }
        acc = (acc << 6) | val(c)?;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%'
            && let Some(hex) = text.get(i + 1..i + 3)
            && let Ok(v) = u8::from_str_radix(hex, 16)
        {
            out.push(v);
            i += 3;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Reads the first diagram of a draw.io file.
pub fn import(text: &str) -> Result<Map> {
    let doc = xml::parse(text)?;
    let title = doc.children_named("diagram").next().and_then(|d| d.attr("name")).filter(|n| !n.trim().is_empty()).map(str::to_string);
    let model: Element = if doc.name.eq_ignore_ascii_case("mxGraphModel") {
        doc
    } else if doc.name.eq_ignore_ascii_case("mxfile") {
        let diagram = doc.child("diagram").ok_or_else(|| Error::Format("draw.io file without a diagram".into()))?;
        match diagram.child("mxGraphModel") {
            Some(m) => m.clone(),
            None => {
                let body = diagram.text.trim();
                if body.is_empty() {
                    return Err(Error::Format("draw.io: empty diagram".into()));
                }
                let inner = inflate(body)?;
                xml::parse(&inner)?
            }
        }
    } else {
        return Err(Error::Format("not a draw.io file".into()));
    };
    let root = model.child("root").ok_or_else(|| Error::Format("draw.io: no root".into()))?;
    let mut map = Map::new("Diagram");
    map.set_mode(Mode::Diagram);
    let mut ids: HashMap<String, NodeId> = HashMap::new();
    let cells: Vec<&Element> = root.children_named("mxCell").collect();
    // Layers: cells under the root cell "0" that are neither vertices nor edges.
    let mut layer_names: HashMap<String, String> = HashMap::new();
    for cell in cells.iter().filter(|c| c.attr("parent") == Some("0") && c.attr("vertex") != Some("1") && c.attr("edge") != Some("1")) {
        let (Some(id), Some(name)) = (cell.attr("id"), cell.attr("value").map(unescape_html).filter(|v| !v.is_empty())) else { continue };
        if map.add_layer(&name).is_ok() {
            if cell.attr("visible") == Some("0") {
                let _ = map.set_layer_visible(&name, false);
            }
            if style_map(cell.attr("style").unwrap_or_default()).get("locked").is_some_and(|v| v == "1") {
                let _ = map.set_layer_locked(&name, true);
            }
            layer_names.insert(id.to_string(), name);
        }
    }
    // Vertices first (edges refer to them).
    for cell in cells.iter().filter(|c| c.attr("vertex") == Some("1")) {
        let Some(id) = cell.attr("id") else { continue };
        let style = style_map(cell.attr("style").unwrap_or_default());
        let geo = cell.child("mxGeometry");
        let num = |k: &str| geo.and_then(|g| g.attr(k)).and_then(|v| v.trim().parse::<f32>().ok()).unwrap_or(0.0);
        let (x, y, w, h) = (num("x"), num("y"), num("width").max(16.0), num("height").max(16.0));
        let label = unescape_html(cell.attr("value").unwrap_or_default());
        let Ok(node) = map.add_node(&label, Point::new(x + w / 2.0, y + h / 2.0), None) else { break };
        let shape = shape_of(&style);
        let _ = map.set_shape(node, shape);
        let _ = map.set_size(node, Some(Size::new(w, h)));
        if let Some(c) = style.get("fillColor").and_then(|c| build::color(c)) {
            let _ = map.set_color(node, c);
        } else if shape == Shape::Text {
            let _ = map.set_color(node, ideacraft_core::Rgb::WHITE);
        }
        if shape.is_container() {
            let _ = map.set_z(node, -1);
        }
        // Our own stencils round-trip through a custom style key.
        if let Some(k) = style.get("ideacraftKind").filter(|k| stencil::find(k).is_some()) {
            let _ = map.set_kind(node, Some(k));
        }
        if let Some(layer) = cell.attr("parent").and_then(|p| layer_names.get(p)) {
            let _ = map.set_node_layer(node, Some(layer));
        }
        ids.insert(id.to_string(), node);
    }
    for cell in cells.iter().filter(|c| c.attr("edge") == Some("1")) {
        let (Some(src), Some(dst)) = (cell.attr("source").and_then(|s| ids.get(s)), cell.attr("target").and_then(|t| ids.get(t))) else { continue };
        let style = style_map(cell.attr("style").unwrap_or_default());
        let label = unescape_html(cell.attr("value").unwrap_or_default());
        let Ok(edge) = map.connect(*src, *dst, &label) else { continue };
        let line = if style.get("dashed").is_some_and(|v| v == "1") {
            if style.get("dashPattern").is_some_and(|p| p.starts_with("1 ")) { LineStyle::Dotted } else { LineStyle::Dashed }
        } else {
            LineStyle::Solid
        };
        let end = style.get("endArrow").is_none_or(|a| a != "none");
        let start = style.get("startArrow").is_some_and(|a| a != "none");
        let arrows = match (start, end) {
            (true, true) => Arrows::Both,
            (true, false) => Arrows::Start,
            (false, true) => Arrows::End,
            (false, false) => Arrows::None,
        };
        let route = match style.get("edgeStyle").map(String::as_str) {
            Some("orthogonalEdgeStyle") | Some("elbowEdgeStyle") | Some("entityRelationEdgeStyle") => Route::Orthogonal,
            _ if style.get("curved").is_some_and(|v| v == "1") => Route::Curved,
            _ => Route::Straight,
        };
        let color = style.get("strokeColor").and_then(|c| build::color(c));
        let width = style.get("strokeWidth").and_then(|w| w.parse::<f32>().ok()).unwrap_or(0.0);
        let _ = map.set_edge_style(edge, EdgeStyle { line, arrows, route, color, width });
    }
    if let Some(name) = title {
        map.set_title(&name);
    }
    Ok(map)
}

fn style_of(n: &ideacraft_core::Node) -> String {
    let mut s = match n.shape {
        Shape::Rounded => "rounded=1;whiteSpace=wrap;html=1;".to_string(),
        Shape::Rect => "rounded=0;whiteSpace=wrap;html=1;".to_string(),
        Shape::Pill => "rounded=1;arcSize=50;whiteSpace=wrap;html=1;".to_string(),
        Shape::Ellipse => "ellipse;whiteSpace=wrap;html=1;".to_string(),
        Shape::Diamond => "rhombus;whiteSpace=wrap;html=1;".to_string(),
        Shape::Hexagon => "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;".to_string(),
        Shape::Parallelogram => "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;".to_string(),
        Shape::Cylinder => "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;".to_string(),
        Shape::Document => "shape=document;whiteSpace=wrap;html=1;".to_string(),
        Shape::Note => "shape=note;whiteSpace=wrap;html=1;size=20;".to_string(),
        Shape::Cloud => "ellipse;shape=cloud;whiteSpace=wrap;html=1;".to_string(),
        Shape::Subprocess => "shape=process;whiteSpace=wrap;html=1;".to_string(),
        Shape::Text => "text;html=1;align=center;verticalAlign=middle;".to_string(),
        Shape::Container => "swimlane;whiteSpace=wrap;html=1;startSize=28;".to_string(),
    };
    s.push_str(&format!("fillColor={};strokeColor=#9aa0b0;", n.color.hex()));
    if n.kind.is_some() {
        s.push_str("verticalLabelPosition=bottom;verticalAlign=top;");
    }
    if let Some(k) = &n.kind {
        s.push_str(&format!("ideacraftKind={k};"));
    }
    s
}

/// The map as an uncompressed draw.io file. Stencil icons are not draw.io shapes, so they travel
/// as labelled rounded boxes (with their kind kept for a round trip into IdeaCraft).
pub fn export(map: &Map) -> String {
    let measured: HashMap<NodeId, ideacraft_core::route::Aabb> = crate::svg::measure_all(map).into_iter().collect();
    let mut out = String::new();
    out.push_str(&format!(
        "<mxfile host=\"IdeaCraft\" version=\"{}\" type=\"device\">\n<diagram id=\"d1\" name=\"{}\">\n<mxGraphModel grid=\"1\" gridSize=\"20\" page=\"1\"><root>\n<mxCell id=\"0\"/>\n<mxCell id=\"1\" parent=\"0\"/>\n",
        env!("CARGO_PKG_VERSION"),
        escape(map.title())
    ));
    for (i, l) in map.layers().iter().enumerate() {
        let visible = if l.visible { "" } else { " visible=\"0\"" };
        let locked = if l.locked { " style=\"locked=1;\"" } else { "" };
        out.push_str(&format!("<mxCell id=\"L{i}\" value=\"{}\" parent=\"0\"{visible}{locked}/>\n", escape(&l.name)));
    }
    let parent_of = |n: &ideacraft_core::Node| match n.layer.as_deref().and_then(|l| map.layers().iter().position(|x| x.name == l)) {
        Some(i) => format!("L{i}"),
        None => "1".to_string(),
    };
    // Hidden layers are exported too (draw.io keeps them hidden), so walk every node.
    for n in map.nodes_in_draw_order() {
        let hidden_by_fold = !map.is_visible(n.id) && n.layer.as_deref().and_then(|l| map.layer(l)).is_none_or(|l| l.visible);
        if hidden_by_fold {
            continue;
        }
        let Some(b) = measured.get(&n.id) else { continue };
        let value = escape(&n.label).replace('\n', "&lt;br&gt;");
        out.push_str(&format!(
            "<mxCell id=\"n{}\" value=\"{}\" style=\"{}\" vertex=\"1\" parent=\"{}\"><mxGeometry x=\"{:.1}\" y=\"{:.1}\" width=\"{:.1}\" height=\"{:.1}\" as=\"geometry\"/></mxCell>\n",
            n.id.0,
            value,
            escape(&style_of(n)),
            parent_of(n),
            b.cx - b.hw,
            b.cy - b.hh,
            b.hw * 2.0,
            b.hh * 2.0
        ));
    }
    // Tree branches become plain connectors; links keep their style.
    for n in map.nodes() {
        if let Some(p) = n.parent.filter(|_| map.is_visible(n.id)) {
            out.push_str(&format!(
                "<mxCell id=\"b{}\" style=\"edgeStyle=none;curved=1;endArrow=none;strokeColor={};strokeWidth=2;\" edge=\"1\" parent=\"1\" source=\"n{}\" target=\"n{}\"><mxGeometry relative=\"1\" as=\"geometry\"/></mxCell>\n",
                n.id.0,
                n.color.hex(),
                p.0,
                n.id.0
            ));
        }
    }
    for e in map.edges() {
        if !(map.is_visible(e.from) && map.is_visible(e.to)) {
            continue;
        }
        let st = e.style;
        let mut style = match st.route {
            Route::Orthogonal => "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;".to_string(),
            Route::Curved => "edgeStyle=none;curved=1;html=1;".to_string(),
            Route::Straight => "edgeStyle=none;html=1;".to_string(),
        };
        match st.line {
            LineStyle::Solid => {}
            LineStyle::Dashed => style.push_str("dashed=1;"),
            LineStyle::Dotted => style.push_str("dashed=1;dashPattern=1 3;"),
        }
        let (start, end) = match st.arrows {
            Arrows::None => ("none", "none"),
            Arrows::End => ("none", "classic"),
            Arrows::Start => ("classic", "none"),
            Arrows::Both => ("classic", "classic"),
        };
        style.push_str(&format!("startArrow={start};endArrow={end};strokeColor={};", st.color.map_or("#e06a2c".to_string(), |c| c.hex())));
        if st.width > 0.0 {
            style.push_str(&format!("strokeWidth={:.1};", st.width));
        }
        out.push_str(&format!(
            "<mxCell id=\"e{}\" value=\"{}\" style=\"{}\" edge=\"1\" parent=\"1\" source=\"n{}\" target=\"n{}\"><mxGeometry relative=\"1\" as=\"geometry\"/></mxCell>\n",
            e.id.0,
            escape(&e.label),
            escape(&style),
            e.from.0,
            e.to.0
        ));
    }
    out.push_str("</root></mxGraphModel>\n</diagram>\n</mxfile>\n");
    out
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    const PLAIN: &str = r##"<mxfile host="test"><diagram name="Office" id="x"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="a" value="Router&lt;br&gt;core" style="rounded=1;fillColor=#dde9ff;" vertex="1" parent="1"><mxGeometry x="100" y="50" width="120" height="60" as="geometry"/></mxCell>
<mxCell id="b" value="OK?" style="rhombus;whiteSpace=wrap;" vertex="1" parent="1"><mxGeometry x="400" y="40" width="100" height="80" as="geometry"/></mxCell>
<mxCell id="c" value="DB" style="shape=cylinder3;" vertex="1" parent="1"><mxGeometry x="400" y="300" width="80" height="80" as="geometry"/></mxCell>
<mxCell id="e1" value="1 Gbit" style="edgeStyle=orthogonalEdgeStyle;dashed=1;startArrow=classic;endArrow=classic;strokeColor=#ff0000;strokeWidth=3;" edge="1" parent="1" source="a" target="b"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e2" style="endArrow=none;" edge="1" parent="1" source="b" target="c"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>"##;

    #[test]
    fn reads_plain_files() {
        let m = import(PLAIN).unwrap();
        assert_eq!(m.title(), "Office");
        assert_eq!(m.mode(), Mode::Diagram);
        assert_eq!(m.node_count(), 3);
        let router = m.nodes().find(|n| n.label.starts_with("Router")).unwrap();
        assert_eq!(router.label, "Router\ncore");
        assert_eq!((router.pos.x, router.pos.y), (160.0, 80.0));
        assert_eq!(router.size, Some(Size::new(120.0, 60.0)));
        assert_eq!(router.shape, Shape::Rounded);
        assert_eq!(router.color.hex(), "#dde9ff");
        assert!(m.nodes().any(|n| n.shape == Shape::Diamond) && m.nodes().any(|n| n.shape == Shape::Cylinder));
        let e = m.edges().find(|e| e.label == "1 Gbit").unwrap();
        assert_eq!((e.style.route, e.style.line, e.style.arrows, e.style.width), (Route::Orthogonal, LineStyle::Dashed, Arrows::Both, 3.0));
        assert_eq!(e.style.color.map(|c| c.hex()), Some("#ff0000".to_string()));
        assert_eq!(m.edges().find(|e| e.label.is_empty()).unwrap().style.arrows, Arrows::None);
    }

    #[test]
    fn reads_compressed_files_and_round_trips_exports() {
        let model = r#"<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="a" value="Caf&#233; &amp; co" style="ellipse;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell></root></mxGraphModel>"#;
        // URL-encode, deflate, base64 — the way draw.io stores diagrams.
        let encoded: String = model.bytes().map(|b| if b.is_ascii_alphanumeric() { (b as char).to_string() } else { format!("%{b:02X}") }).collect();
        let mut enc = flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(encoded.as_bytes()).unwrap();
        let deflated = enc.finish().unwrap();
        let b64 = {
            const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
            let mut s = String::new();
            for chunk in deflated.chunks(3) {
                let n = chunk.iter().fold(0u32, |acc, b| (acc << 8) | *b as u32) << (8 * (3 - chunk.len()));
                for i in 0..4 {
                    if i <= chunk.len() {
                        s.push(T[((n >> (18 - 6 * i)) & 63) as usize] as char);
                    } else {
                        s.push('=');
                    }
                }
            }
            s
        };
        let file = format!("<mxfile><diagram name=\"Z\" id=\"1\">{b64}</diagram></mxfile>");
        let m = import(&file).unwrap();
        assert_eq!(m.node_count(), 1);
        assert_eq!(m.nodes().next().unwrap().label, "Café & co");
        assert_eq!(m.nodes().next().unwrap().shape, Shape::Ellipse);

        let m = import(PLAIN).unwrap();
        let text = export(&m);
        assert!(text.starts_with("<mxfile"));
        let back = import(&text).unwrap();
        assert_eq!(back.node_count(), m.node_count());
        assert_eq!(back.edge_count(), m.edge_count());
        let e = back.edges().find(|e| e.label == "1 Gbit").unwrap();
        assert_eq!((e.style.route, e.style.line, e.style.arrows), (Route::Orthogonal, LineStyle::Dashed, Arrows::Both));
        // A stencil kind and layers survive the round trip.
        let mut m = Map::new("k");
        let fw = m.add_stencil(stencil::find("net.firewall").unwrap(), None, Point::new(0.0, 0.0)).unwrap();
        m.set_node_layer(fw, Some("Security")).unwrap();
        m.set_layer_locked("Security", true).unwrap();
        let back = import(&export(&m)).unwrap();
        let n = back.nodes().next().unwrap();
        assert_eq!(n.kind.as_deref(), Some("net.firewall"));
        assert_eq!(n.layer.as_deref(), Some("Security"));
        assert!(back.layer("Security").is_some_and(|l| l.locked));
        assert!(import("<mxfile><diagram id='1'>%%%garbage</diagram></mxfile>").is_err());
        assert!(import("<svg/>").is_err());
    }
}
