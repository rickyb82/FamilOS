//! The command catalog. Ids follow the app's menus (`node.addChild`, `layout.tree`) plus
//! primitives (`node.move`, `selection.set`).

use ideacraft_core::layout::{self, LEAF_GAP, LEVEL_GAP, Layout};
use ideacraft_core::{Arrows, LineStyle, Map, Mode, NodeId, Point, Rgb, Route, Shape, Size, stencil};
use ideacraft_formats::templates::Lang;
use serde_json::{Value, json};

use crate::params as p;
use crate::{Error, Result, Session};

/// A command's metadata: what menus, palettes, the CLI and `--help` show.
#[derive(Clone, Copy, Debug)]
pub struct CommandInfo {
    pub id: &'static str,
    pub label: &'static str,
    /// Menu path, `Menu › Item`.
    pub menu: &'static str,
    /// Default keyboard shortcut, empty when none (`Cmd` is ⌘ on macOS, Ctrl elsewhere).
    pub shortcut: &'static str,
    /// Parameters, documented for the CLI and automation.
    pub params: &'static str,
    /// Changes the map, so it is an undo step.
    pub undoable: bool,
}

const fn c(id: &'static str, label: &'static str, menu: &'static str, shortcut: &'static str, params: &'static str, undoable: bool) -> CommandInfo {
    CommandInfo { id, label, menu, shortcut, params, undoable }
}

pub const COMMANDS: &[CommandInfo] = &[
    c("map.new", "New Map", "File › New", "Cmd+N", "{title?}", false),
    c(
        "map.newFromTemplate",
        "New from Template",
        "File › New from Template",
        "",
        "{template: brainstorm|concept-map|project-plan|swot|decision|meeting|book|fishbone|weekly|roadmap|journey|lesson|network|flowchart|cloud|org-chart, language?: en|it}",
        false,
    ),
    c("map.templates", "Templates", "File › New from Template", "", "{language?: en|it} → [{id, name, category, description}]", false),
    c(
        "map.importText",
        "Import",
        "File › Import",
        "",
        "{text, format?: mm|xmind|cxl|opml|md|mermaid|drawio|ideacraft} (detected when absent) → {format}",
        false,
    ),
    c("map.export", "Export", "File › Export", "", "{format: markdown|opml|freemind|svg|drawio|ideacraft} → {text}", false),
    c("map.setTitle", "Rename Map", "File › Rename", "", "{title}", true),
    c("map.info", "Map Info", "File › Info", "", "{} → {title, nodes, links, selection}", false),
    c("map.setMode", "Map / Diagram Mode", "View › Mode", "", "{mode: map|diagram}", true),
    c("map.stencils", "Shape Library", "Insert › Shape", "", "{language?: en|it} → [{id, name, category, shape, w, h}]", false),
    c("node.add", "Add Idea", "Insert › Idea", "", "{label?, x?, y?, parent?, kind?: stencil id, w?, h?} → {id}", true),
    c(
        "node.addShape",
        "Add Shape",
        "Insert › Shape",
        "",
        "{kind: stencil id (map.stencils), label?, x?, y?, props?: {k: v}, language?: en|it (default label)} → {id}",
        true,
    ),
    c("node.addChild", "Add Child Idea", "Insert › Child", "Tab", "{id?, label?} (default: the selection) → {id}", true),
    c("node.addSibling", "Add Sibling Idea", "Insert › Sibling", "Enter", "{id?, label?} → {id}", true),
    c("node.delete", "Delete", "Edit › Delete", "Delete", "{id? | ids?} (removes the whole branch)", true),
    c("node.setLabel", "Edit Text", "Edit › Edit Text", "F2", "{id?, label}", true),
    c("node.move", "Move", "Arrange › Move", "", "{id?, x, y}", true),
    c("node.moveBy", "Nudge", "Arrange › Nudge", "", "{id? | ids?, dx, dy}", true),
    c("node.setColor", "Fill Colour", "Format › Fill Colour", "", "{id? | ids?, color: \"#rrggbb\"}", true),
    c(
        "node.setShape",
        "Shape",
        "Format › Shape",
        "",
        "{id? | ids?, shape: rounded|rect|ellipse|pill|diamond|hexagon|parallelogram|cylinder|document|note|cloud|subprocess|text|container}",
        true,
    ),
    c(
        "node.setKind",
        "Stencil",
        "Format › Stencil",
        "",
        "{id? | ids?, kind: stencil id | null} (also sets the stencil's shape, size and colour)",
        true,
    ),
    c("node.resize", "Resize", "Format › Size", "", "{id? | ids?, w, h, x?, y?} (w/h absent: auto size; x/y: new centre)", true),
    c("node.setProps", "Properties", "Format › Properties", "", "{id? | ids?, props: {name: value}} (empty value removes; merges)", true),
    c("node.setZ", "Bring to Front / Send to Back", "Arrange › Order", "", "{id? | ids?, z: int | \"front\" | \"back\"}", true),
    c("edit.duplicate", "Duplicate", "Edit › Duplicate", "Cmd+D", "{id? | ids?, dx?, dy?} → {ids}", true),
    c("group.create", "Group", "Arrange › Group", "Cmd+G", "{ids?} (selection by default; at least two) → {group}", true),
    c("group.ungroup", "Ungroup", "Arrange › Ungroup", "Cmd+Shift+G", "{id? | ids?}", true),
    c("map.layers", "Layers", "View › Layers", "", "{} → [{name, visible, locked, nodes}]", false),
    c("layer.add", "Add Layer", "View › Layers › Add", "", "{name}", true),
    c("layer.remove", "Remove Layer", "View › Layers › Remove", "", "{name} (its shapes go to the default layer)", true),
    c("layer.rename", "Rename Layer", "View › Layers › Rename", "", "{name, to}", true),
    c("layer.setVisible", "Show/Hide Layer", "View › Layers › Visible", "", "{name, visible: bool}", true),
    c("layer.setLocked", "Lock/Unlock Layer", "View › Layers › Locked", "", "{name, locked: bool}", true),
    c("node.setLayer", "Move to Layer", "Arrange › Move to Layer", "", "{id? | ids?, layer: name | null} (the layer is created when missing)", true),
    c(
        "net.validate",
        "Check Network",
        "Review › Check Network",
        "",
        "{language?: en|it} → {errors, warnings, infos, findings: [{code, severity, message, nodes, edges, detail}]}",
        false,
    ),
    c("arrange.align", "Align", "Arrange › Align", "", "{ids?, edge: left|right|top|bottom|centerX|centerY} (selection by default)", true),
    c("arrange.distribute", "Distribute", "Arrange › Distribute", "", "{ids?, axis: x|y}", true),
    c("arrange.snap", "Snap to Grid", "Arrange › Snap to Grid", "", "{ids?, grid?: number (default 20)}", true),
    c("node.toggleCollapse", "Collapse/Expand Branch", "View › Collapse/Expand", "Space", "{id? | ids?, collapsed?: bool}", true),
    c("node.setParent", "Attach To", "Arrange › Attach To", "", "{id?, parent: id | null}", true),
    c("edge.add", "Link Ideas", "Insert › Link", "", "{from, to, label?} → {id}", true),
    c("edge.delete", "Delete Link", "Edit › Delete Link", "", "{id}", true),
    c("edge.setLabel", "Link Text", "Edit › Link Text", "", "{id, label}", true),
    c(
        "edge.setStyle",
        "Link Style",
        "Format › Link Style",
        "",
        "{id, line?: solid|dashed|dotted, arrows?: none|end|start|both, route?: straight|orthogonal|curved, color?: \"#rrggbb\"|null, width?}",
        true,
    ),
    c("layout.tree", "Tree Layout", "Arrange › Tree Layout", "Cmd+L", "{}", true),
    c("layout.radial", "Radial Layout", "Arrange › Radial Layout", "Cmd+Shift+L", "{}", true),
    c("selection.set", "Select", "Edit › Select", "", "{ids: [id…]}", false),
    c("selection.all", "Select All", "Edit › Select All", "Cmd+A", "{}", false),
    c("selection.clear", "Select None", "Edit › Select None", "Escape", "{}", false),
    c("edit.undo", "Undo", "Edit › Undo", "Cmd+Z", "{}", false),
    c("edit.redo", "Redo", "Edit › Redo", "Cmd+Shift+Z", "{}", false),
];

pub fn command(id: &str) -> Option<&'static CommandInfo> {
    COMMANDS.iter().find(|c| c.id == id)
}

fn node_pos(map: &Map, id: NodeId) -> Result<Point> {
    map.node(id).map(|n| n.pos).ok_or(Error::Model(ideacraft_core::Error::NoNode(id.0)))
}

/// Where a new child of `parent` goes: right of it, under its lowest child.
fn child_slot(map: &Map, parent: NodeId) -> Result<Point> {
    let at = node_pos(map, parent)?;
    let lowest = map.children(parent).iter().filter_map(|k| map.node(*k)).map(|n| n.pos).max_by(|a, b| a.y.total_cmp(&b.y));
    Ok(match lowest {
        Some(l) => Point::new(l.x, l.y + LEAF_GAP),
        None => Point::new(at.x + LEVEL_GAP, at.y),
    })
}

/// The optional `language` parameter of template commands.
fn language(prm: &Value) -> Result<Lang> {
    match p::opt_str(prm, "language")? {
        None => Ok(Lang::En),
        Some(code) => Lang::parse(code).ok_or_else(|| Error::Param("`language` must be en or it".into())),
    }
}

/// A node's centre and half-size; text-fitted nodes count as 60×30.
fn bounds(map: &Map, id: NodeId) -> Result<(NodeId, Point, f32, f32)> {
    let n = map.node(id).ok_or(ideacraft_core::Error::NoNode(id.0))?;
    let (hw, hh) = n.half_size().unwrap_or((60.0, 20.0));
    Ok((id, n.pos, hw, hh))
}

/// Applies `props: {name: value}` from the params (empty values remove).
fn set_props(s: &mut Session, id: NodeId, prm: &Value) -> Result<()> {
    let Some(props) = prm.get("props").filter(|v| !v.is_null()) else { return Ok(()) };
    let obj = props.as_object().ok_or_else(|| Error::Param("`props` must be an object of name: value".into()))?;
    for (k, v) in obj.iter().take(ideacraft_core::limits::MAX_PROPS) {
        let value = match v {
            Value::Null => String::new(),
            Value::String(t) => t.clone(),
            other => other.to_string(),
        };
        s.map.set_prop(id, k, &value)?;
    }
    Ok(())
}

fn added(s: &mut Session, id: NodeId) -> Value {
    s.selection = vec![id];
    json!({ "id": id.0 })
}

pub(crate) fn run(s: &mut Session, id: &str, prm: &Value) -> Result<Value> {
    match id {
        "map.new" => {
            let title = p::opt_str(prm, "title")?.unwrap_or("Untitled map");
            s.replace_map(Map::new(title));
            Ok(json!({}))
        }
        "map.newFromTemplate" => {
            let id = p::str(prm, "template")?;
            let t = ideacraft_formats::templates::find(id)
                .ok_or_else(|| Error::Param(format!("no template {:?}", id.chars().take(40).collect::<String>())))?;
            s.replace_map(t.build_in(language(prm)?));
            Ok(json!({}))
        }
        "map.templates" => {
            let lang = language(prm)?;
            Ok(Value::Array(
                ideacraft_formats::templates::ALL
                    .iter()
                    .map(|t| json!({"id": t.id, "name": t.name(lang), "category": t.category(lang), "description": t.description(lang)}))
                    .collect(),
            ))
        }
        "map.importText" => {
            let text = p::str(prm, "text")?;
            let format = match p::opt_str(prm, "format")? {
                Some(f) => ideacraft_formats::Format::parse(f)
                    .ok_or_else(|| Error::Param(format!("unknown format {:?}", f.chars().take(24).collect::<String>())))?,
                None => ideacraft_formats::sniff(text),
            };
            let map = ideacraft_formats::import_text(text, format)?;
            s.replace_map(map);
            Ok(json!({"format": format.name()}))
        }
        "map.export" => {
            let text = match p::str(prm, "format")?.to_ascii_lowercase().as_str() {
                "markdown" | "md" => ideacraft_formats::to_markdown(&s.map),
                "opml" => ideacraft_formats::to_opml(&s.map),
                "freemind" | "mm" => ideacraft_formats::to_freemind(&s.map),
                "ideacraft" | "json" => s.map.to_json(),
                "svg" => ideacraft_formats::to_svg(&s.map),
                "drawio" => ideacraft_formats::to_drawio(&s.map),
                _ => return Err(Error::Param("`format` must be markdown, opml, freemind, svg, drawio or ideacraft".into())),
            };
            Ok(json!({"text": text}))
        }
        "map.setTitle" => {
            s.map.set_title(p::str(prm, "title")?);
            Ok(json!({}))
        }
        "map.info" => Ok(json!({
            "title": s.map.title(),
            "nodes": s.map.node_count(),
            "links": s.map.edge_count(),
            "selection": s.selection.iter().map(|n| n.0).collect::<Vec<_>>(),
        })),
        "map.setMode" => {
            let mode = Mode::parse(p::str(prm, "mode")?).ok_or_else(|| Error::Param("`mode` must be map or diagram".into()))?;
            s.map.set_mode(mode);
            Ok(json!({}))
        }
        "map.stencils" => {
            let italian = language(prm)? == Lang::It;
            Ok(Value::Array(
                stencil::ALL
                    .iter()
                    .map(|st| {
                        let category = if italian { st.category.name_it() } else { st.category.name() };
                        json!({"id": st.id, "name": st.name(italian), "category": category, "shape": st.shape.name(), "w": st.size.w, "h": st.size.h})
                    })
                    .collect(),
            ))
        }
        "node.add" => {
            let parent = p::opt_node(prm, "parent")?;
            let pos = match (p::opt_point(prm)?, parent) {
                (Some(at), _) => at,
                (None, Some(par)) => child_slot(&s.map, par)?,
                (None, None) => Point::new(0.0, s.map.nodes().map(|n| n.pos.y + 100.0).fold(0.0, f32::max)),
            };
            let label = p::opt_str(prm, "label")?.unwrap_or("Idea");
            let new = match p::opt_str(prm, "kind")? {
                Some(kind) => {
                    let st = stencil::find(kind)
                        .ok_or_else(|| Error::Param(format!("unknown stencil {:?} (see map.stencils)", kind.chars().take(40).collect::<String>())))?;
                    let id = s.map.add_stencil(st, Some(label), pos)?;
                    if let Some(par) = parent {
                        s.map.set_parent(id, Some(par))?;
                    }
                    id
                }
                None => s.map.add_node(label, pos, parent)?,
            };
            if let (Some(w), Some(h)) = (p::opt_f32(prm, "w")?, p::opt_f32(prm, "h")?) {
                s.map.set_size(new, Some(Size::new(w, h)))?;
            }
            Ok(added(s, new))
        }
        "node.addShape" => {
            let kind = p::str(prm, "kind")?;
            let st = stencil::find(kind)
                .ok_or_else(|| Error::Param(format!("unknown stencil {:?} (see map.stencils)", kind.chars().take(40).collect::<String>())))?;
            let pos = p::opt_point(prm)?.unwrap_or_else(|| {
                // Below the lowest shape, so new shapes never land on top of each other.
                let low = s.map.nodes().map(|n| n.pos.y + n.size.map_or(60.0, |sz| sz.h / 2.0)).fold(0.0, f32::max);
                Point::new(0.0, if s.map.node_count() == 0 { 0.0 } else { low + st.size.h / 2.0 + 40.0 })
            });
            let default_label = st.name(language(prm)? == Lang::It);
            let new = s.map.add_stencil(st, Some(p::opt_str(prm, "label")?.unwrap_or(default_label)), pos)?;
            set_props(s, new, prm)?;
            s.map.set_mode(Mode::Diagram);
            Ok(added(s, new))
        }
        "node.setKind" => {
            let kind =
                match prm.get("kind") {
                    None => return Err(Error::Param("`kind` must be a stencil id or null".into())),
                    Some(Value::Null) => None,
                    Some(_) => {
                        let k = p::str(prm, "kind")?;
                        Some(stencil::find(k).ok_or_else(|| {
                            Error::Param(format!("unknown stencil {:?} (see map.stencils)", k.chars().take(40).collect::<String>()))
                        })?)
                    }
                };
            for t in p::targets(prm, &s.selection)? {
                match kind {
                    Some(st) => {
                        s.map.set_kind(t, (!st.glyph.is_empty()).then_some(st.id))?;
                        s.map.set_shape(t, st.shape)?;
                        s.map.set_size(t, Some(st.size))?;
                        s.map.set_color(t, st.color)?;
                    }
                    None => s.map.set_kind(t, None)?,
                }
            }
            Ok(json!({}))
        }
        "node.resize" => {
            let size = match (p::opt_f32(prm, "w")?, p::opt_f32(prm, "h")?) {
                (Some(w), Some(h)) => Some(Size::new(w, h)),
                (None, None) => None,
                _ => return Err(Error::Param("give both `w` and `h`, or neither for auto size".into())),
            };
            let at = p::opt_point(prm)?;
            for t in p::targets(prm, &s.selection)? {
                s.map.set_size(t, size)?;
                if let Some(at) = at {
                    s.map.set_pos(t, at)?;
                }
            }
            Ok(json!({}))
        }
        "node.setProps" => {
            for t in p::targets(prm, &s.selection)? {
                set_props(s, t, prm)?;
            }
            Ok(json!({}))
        }
        "node.setZ" => {
            let (front, back) = (s.map.nodes().map(|n| n.z).max().unwrap_or(0), s.map.nodes().map(|n| n.z).min().unwrap_or(0));
            let z = match prm.get("z") {
                Some(Value::String(w)) if w == "front" => front.saturating_add(1),
                Some(Value::String(w)) if w == "back" => back.saturating_sub(1),
                Some(v) if v.is_i64() => v.as_i64().unwrap_or(0).clamp(-1_000, 1_000) as i32,
                _ => return Err(Error::Param("`z` must be an integer, \"front\" or \"back\"".into())),
            };
            for t in p::targets(prm, &s.selection)? {
                s.map.set_z(t, z)?;
            }
            Ok(json!({}))
        }
        "edit.duplicate" => {
            let dx = p::opt_f32(prm, "dx")?.unwrap_or(30.0);
            let dy = p::opt_f32(prm, "dy")?.unwrap_or(30.0);
            let mut ids = Vec::new();
            for t in p::targets(prm, &s.selection)? {
                ids.push(s.map.duplicate_node(t, dx, dy)?);
            }
            s.selection = ids.clone();
            Ok(json!({"ids": ids.iter().map(|n| n.0).collect::<Vec<_>>()}))
        }
        "group.create" => {
            let ids = p::targets(prm, &s.selection)?;
            let g = s.map.group(&ids)?;
            Ok(json!({"group": g}))
        }
        "group.ungroup" => {
            let ids = p::targets(prm, &s.selection)?;
            s.map.ungroup(&ids)?;
            Ok(json!({}))
        }
        "map.layers" => Ok(Value::Array(
            s.map
                .layers()
                .iter()
                .map(|l| json!({"name": l.name, "visible": l.visible, "locked": l.locked, "nodes": s.map.nodes().filter(|n| n.layer.as_deref() == Some(l.name.as_str())).count()}))
                .collect(),
        )),
        "layer.add" => {
            s.map.add_layer(p::str(prm, "name")?)?;
            Ok(json!({}))
        }
        "layer.remove" => {
            s.map.remove_layer(p::str(prm, "name")?)?;
            Ok(json!({}))
        }
        "layer.rename" => {
            s.map.rename_layer(p::str(prm, "name")?, p::str(prm, "to")?)?;
            Ok(json!({}))
        }
        "layer.setVisible" => {
            let visible = prm.get("visible").and_then(Value::as_bool).ok_or_else(|| Error::Param("`visible` must be true or false".into()))?;
            s.map.set_layer_visible(p::str(prm, "name")?, visible)?;
            let map = &s.map;
            s.selection.retain(|n| map.is_visible(*n));
            Ok(json!({}))
        }
        "layer.setLocked" => {
            let locked = prm.get("locked").and_then(Value::as_bool).ok_or_else(|| Error::Param("`locked` must be true or false".into()))?;
            s.map.set_layer_locked(p::str(prm, "name")?, locked)?;
            Ok(json!({}))
        }
        "node.setLayer" => {
            let layer = match prm.get("layer") {
                None => return Err(Error::Param("`layer` must be a name or null".into())),
                Some(Value::Null) => None,
                Some(_) => Some(p::str(prm, "layer")?),
            };
            for t in p::targets(prm, &s.selection)? {
                s.map.set_node_layer(t, layer)?;
            }
            Ok(json!({}))
        }
        "net.validate" => {
            let italian = language(prm)? == Lang::It;
            let findings = ideacraft_core::netcheck::validate(&s.map);
            let count = |sev| findings.iter().filter(|f| f.severity == sev).count();
            Ok(json!({
                "errors": count(ideacraft_core::netcheck::Severity::Error),
                "warnings": count(ideacraft_core::netcheck::Severity::Warning),
                "infos": count(ideacraft_core::netcheck::Severity::Info),
                "findings": findings.iter().map(|f| json!({
                    "code": f.code.name(),
                    "severity": match f.severity { ideacraft_core::netcheck::Severity::Error => "error", ideacraft_core::netcheck::Severity::Warning => "warning", ideacraft_core::netcheck::Severity::Info => "info" },
                    "message": f.message(italian),
                    "nodes": f.nodes.iter().map(|n| n.0).collect::<Vec<_>>(),
                    "edges": f.edges.iter().map(|e| e.0).collect::<Vec<_>>(),
                    "detail": f.detail,
                })).collect::<Vec<_>>(),
            }))
        }
        "arrange.align" => {
            let ids = p::targets(prm, &s.selection)?;
            if ids.len() < 2 {
                return Err(Error::Param("select at least two shapes to align".into()));
            }
            let edge = p::str(prm, "edge")?;
            let boxes: Vec<(NodeId, Point, f32, f32)> = ids.iter().map(|id| bounds(&s.map, *id)).collect::<Result<_>>()?;
            let (min_x, max_x) = boxes.iter().fold((f32::MAX, f32::MIN), |(lo, hi), (_, c, hw, _)| (lo.min(c.x - hw), hi.max(c.x + hw)));
            let (min_y, max_y) = boxes.iter().fold((f32::MAX, f32::MIN), |(lo, hi), (_, c, _, hh)| (lo.min(c.y - hh), hi.max(c.y + hh)));
            for (id, c, hw, hh) in boxes {
                let pos = match edge {
                    "left" => Point::new(min_x + hw, c.y),
                    "right" => Point::new(max_x - hw, c.y),
                    "top" => Point::new(c.x, min_y + hh),
                    "bottom" => Point::new(c.x, max_y - hh),
                    "centerX" | "centerx" | "center" => Point::new((min_x + max_x) / 2.0, c.y),
                    "centerY" | "centery" | "middle" => Point::new(c.x, (min_y + max_y) / 2.0),
                    _ => return Err(Error::Param("`edge` must be left, right, top, bottom, centerX or centerY".into())),
                };
                s.map.set_pos(id, pos)?;
            }
            Ok(json!({}))
        }
        "arrange.distribute" => {
            let ids = p::targets(prm, &s.selection)?;
            if ids.len() < 3 {
                return Err(Error::Param("select at least three shapes to distribute".into()));
            }
            let horizontal = match p::str(prm, "axis")? {
                "x" | "horizontal" => true,
                "y" | "vertical" => false,
                _ => return Err(Error::Param("`axis` must be x or y".into())),
            };
            let mut boxes: Vec<(NodeId, Point, f32, f32)> = ids.iter().map(|id| bounds(&s.map, *id)).collect::<Result<_>>()?;
            boxes.sort_by(|a, b| if horizontal { a.1.x.total_cmp(&b.1.x) } else { a.1.y.total_cmp(&b.1.y) });
            let (Some(first), Some(last)) = (boxes.first(), boxes.last()) else { return Ok(json!({})) };
            let span = if horizontal { (last.1.x - last.2) - (first.1.x + first.2) } else { (last.1.y - last.3) - (first.1.y + first.3) };
            let inner: f32 = boxes.iter().skip(1).take(boxes.len().saturating_sub(2)).map(|b| if horizontal { b.2 * 2.0 } else { b.3 * 2.0 }).sum();
            let gap = (span - inner) / (boxes.len() - 1) as f32;
            let mut cursor = if horizontal { first.1.x + first.2 } else { first.1.y + first.3 };
            let n = boxes.len();
            for (i, (id, c, hw, hh)) in boxes.into_iter().enumerate() {
                if i == 0 || i == n - 1 {
                    continue;
                }
                cursor += gap;
                let pos = if horizontal { Point::new(cursor + hw, c.y) } else { Point::new(c.x, cursor + hh) };
                cursor += if horizontal { hw * 2.0 } else { hh * 2.0 };
                s.map.set_pos(id, pos)?;
            }
            Ok(json!({}))
        }
        "arrange.snap" => {
            let grid = p::opt_f32(prm, "grid")?.unwrap_or(20.0).clamp(1.0, 1000.0);
            for t in p::targets(prm, &s.selection)? {
                let at = node_pos(&s.map, t)?;
                s.map.set_pos(t, Point::new((at.x / grid).round() * grid, (at.y / grid).round() * grid))?;
            }
            Ok(json!({}))
        }
        "node.addChild" => {
            let parent = p::target(prm, &s.selection)?;
            let pos = child_slot(&s.map, parent)?;
            s.map.set_collapsed(parent, false)?;
            let new = s.map.add_node(p::opt_str(prm, "label")?.unwrap_or("Idea"), pos, Some(parent))?;
            Ok(added(s, new))
        }
        "node.addSibling" => {
            let of = p::target(prm, &s.selection)?;
            let label = p::opt_str(prm, "label")?.unwrap_or("Idea");
            let at = node_pos(&s.map, of)?;
            let parent = s.map.node(of).and_then(|n| n.parent);
            // Just below `of`, pushing later siblings down so the order stays as seen.
            let pos = Point::new(at.x, at.y + LEAF_GAP / 2.0);
            if let Some(par) = parent {
                for sib in s.map.children(par) {
                    let sp = node_pos(&s.map, sib)?;
                    if sib != of && sp.y > at.y {
                        s.map.set_pos(sib, Point::new(sp.x, sp.y + LEAF_GAP))?;
                    }
                }
            }
            let pos = if parent.is_some() { pos } else { Point::new(at.x, at.y + 100.0) };
            let new = s.map.add_node(label, pos, parent)?;
            Ok(added(s, new))
        }
        "node.delete" => {
            let mut removed = 0;
            for t in p::targets(prm, &s.selection)? {
                if s.map.node(t).is_some() {
                    removed += s.map.remove_node(t)?.len();
                } else if prm.get("ids").is_some() || prm.get("id").is_some() {
                    return Err(ideacraft_core::Error::NoNode(t.0).into());
                }
            }
            Ok(json!({ "removed": removed }))
        }
        "node.setLabel" => {
            let t = p::target(prm, &s.selection)?;
            s.map.set_label(t, p::str(prm, "label")?)?;
            Ok(json!({}))
        }
        "node.move" => {
            let t = p::target(prm, &s.selection)?;
            let at = p::opt_point(prm)?.ok_or_else(|| Error::Param("give `x` and `y`".into()))?;
            s.map.set_pos(t, at)?;
            Ok(json!({}))
        }
        "node.moveBy" => {
            let dx = p::opt_f32(prm, "dx")?.unwrap_or(0.0);
            let dy = p::opt_f32(prm, "dy")?.unwrap_or(0.0);
            for t in p::targets(prm, &s.selection)? {
                let at = node_pos(&s.map, t)?;
                s.map.set_pos(t, Point::new(at.x + dx, at.y + dy))?;
            }
            Ok(json!({}))
        }
        "node.setColor" => {
            let color = Rgb::parse(p::str(prm, "color")?)?;
            for t in p::targets(prm, &s.selection)? {
                s.map.set_color(t, color)?;
            }
            Ok(json!({}))
        }
        "node.setShape" => {
            let name = p::str(prm, "shape")?;
            let shape = Shape::parse(name).ok_or_else(|| Error::Param("`shape` must be rounded, rect, ellipse or pill".into()))?;
            for t in p::targets(prm, &s.selection)? {
                s.map.set_shape(t, shape)?;
            }
            Ok(json!({}))
        }
        "node.toggleCollapse" => {
            let force = prm.get("collapsed").and_then(Value::as_bool);
            for t in p::targets(prm, &s.selection)? {
                let now = s.map.node(t).map(|n| n.collapsed).ok_or(ideacraft_core::Error::NoNode(t.0))?;
                s.map.set_collapsed(t, force.unwrap_or(!now))?;
            }
            // Selected nodes that just disappeared fall back to the collapsed branch's root.
            let map = &s.map;
            s.selection.retain(|n| map.is_visible(*n));
            Ok(json!({}))
        }
        "node.setParent" => {
            let t = p::target(prm, &s.selection)?;
            let parent = match prm.get("parent") {
                Some(Value::Null) => None,
                Some(_) => Some(p::node(prm, "parent")?),
                None => return Err(Error::Param("`parent` must be a node id or null".into())),
            };
            s.map.set_parent(t, parent)?;
            Ok(json!({}))
        }
        "edge.add" => {
            let e = s.map.connect(p::node(prm, "from")?, p::node(prm, "to")?, p::opt_str(prm, "label")?.unwrap_or(""))?;
            Ok(json!({ "id": e.0 }))
        }
        "edge.delete" => {
            s.map.disconnect(p::edge(prm, "id")?)?;
            Ok(json!({}))
        }
        "edge.setLabel" => {
            s.map.set_edge_label(p::edge(prm, "id")?, p::str(prm, "label")?)?;
            Ok(json!({}))
        }
        "edge.setStyle" => {
            let id = p::edge(prm, "id")?;
            let mut style = s.map.edge(id).map(|e| e.style).ok_or(ideacraft_core::Error::NoEdge(id.0))?;
            if let Some(v) = p::opt_str(prm, "line")? {
                style.line = LineStyle::parse(v).ok_or_else(|| Error::Param("`line` must be solid, dashed or dotted".into()))?;
            }
            if let Some(v) = p::opt_str(prm, "arrows")? {
                style.arrows = Arrows::parse(v).ok_or_else(|| Error::Param("`arrows` must be none, end, start or both".into()))?;
            }
            if let Some(v) = p::opt_str(prm, "route")? {
                style.route = Route::parse(v).ok_or_else(|| Error::Param("`route` must be straight, orthogonal or curved".into()))?;
            }
            match prm.get("color") {
                None => {}
                Some(Value::Null) => style.color = None,
                Some(_) => style.color = Some(Rgb::parse(p::str(prm, "color")?)?),
            }
            if let Some(w) = p::opt_f32(prm, "width")? {
                style.width = w;
            }
            s.map.set_edge_style(id, style)?;
            Ok(json!({}))
        }
        "layout.tree" => {
            layout::apply(&mut s.map, Layout::Tree);
            Ok(json!({}))
        }
        "layout.radial" => {
            layout::apply(&mut s.map, Layout::Radial);
            Ok(json!({}))
        }
        "selection.set" => {
            let ids = p::targets(prm, &[])?;
            if let Some(bad) = ids.iter().find(|n| s.map.node(**n).is_none()) {
                return Err(ideacraft_core::Error::NoNode(bad.0).into());
            }
            s.selection = ids;
            Ok(json!({}))
        }
        "selection.all" => {
            let map = &s.map;
            s.selection = map.nodes().map(|n| n.id).filter(|n| map.is_visible(*n)).collect();
            Ok(json!({}))
        }
        "selection.clear" => {
            s.selection.clear();
            Ok(json!({}))
        }
        "edit.undo" => s.undo_step().map(|()| json!({})),
        "edit.redo" => s.redo_step().map(|()| json!({})),
        other => Err(Error::UnknownCommand(other.to_string())),
    }
}
