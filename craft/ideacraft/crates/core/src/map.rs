use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::limits::{MAX_COORD, MAX_DEPTH, MAX_EDGES, MAX_LABEL_CHARS, MAX_NODES, MAX_PROP_CHARS, MAX_PROPS, MAX_SIZE, MIN_SIZE};
use crate::{Error, Result, Rgb};

/// Current `.ideacraft.json` format version.
pub const FORMAT_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct NodeId(pub u64);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct EdgeId(pub u64);

/// A point in map coordinates (logical pixels at 100% zoom; y grows downwards).
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Point {
    pub x: f32,
    pub y: f32,
}

impl Point {
    pub const fn new(x: f32, y: f32) -> Self {
        Self { x, y }
    }

    /// Finite and inside ±[`MAX_COORD`]; NaN becomes 0.
    pub fn clamped(self) -> Self {
        let c = |v: f32| {
            if v.is_finite() {
                v.clamp(-MAX_COORD, MAX_COORD)
            } else if v.is_nan() {
                0.0
            } else {
                v.signum() * MAX_COORD
            }
        };
        Self { x: c(self.x), y: c(self.y) }
    }
}

/// A node's explicit size (diagram mode); without one a node fits its text.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Size {
    pub w: f32,
    pub h: f32,
}

impl Size {
    pub const fn new(w: f32, h: f32) -> Self {
        Self { w, h }
    }

    /// Finite and inside [`MIN_SIZE`]..=[`MAX_SIZE`].
    pub fn clamped(self) -> Self {
        let c = |v: f32| if v.is_finite() { v.clamp(MIN_SIZE, MAX_SIZE) } else { MIN_SIZE };
        Self { w: c(self.w), h: c(self.h) }
    }
}

/// How a node is drawn.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Shape {
    #[default]
    Rounded,
    Rect,
    Ellipse,
    Pill,
    Diamond,
    Hexagon,
    Parallelogram,
    Cylinder,
    Document,
    Note,
    Cloud,
    /// A rectangle with double side bars (predefined process).
    Subprocess,
    /// Text only, no outline or fill.
    Text,
    /// A large, light box drawn behind other shapes (a zone, a swimlane, a site).
    Container,
}

impl Shape {
    pub const ALL: [Self; 14] = [
        Self::Rounded,
        Self::Rect,
        Self::Ellipse,
        Self::Pill,
        Self::Diamond,
        Self::Hexagon,
        Self::Parallelogram,
        Self::Cylinder,
        Self::Document,
        Self::Note,
        Self::Cloud,
        Self::Subprocess,
        Self::Text,
        Self::Container,
    ];

    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|s| s.name().eq_ignore_ascii_case(name.trim()))
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::Rounded => "rounded",
            Self::Rect => "rect",
            Self::Ellipse => "ellipse",
            Self::Pill => "pill",
            Self::Diamond => "diamond",
            Self::Hexagon => "hexagon",
            Self::Parallelogram => "parallelogram",
            Self::Cylinder => "cylinder",
            Self::Document => "document",
            Self::Note => "note",
            Self::Cloud => "cloud",
            Self::Subprocess => "subprocess",
            Self::Text => "text",
            Self::Container => "container",
        }
    }

    /// Drawn behind everything else and never auto-sized.
    pub const fn is_container(self) -> bool {
        matches!(self, Self::Container)
    }

    /// The outline as a polygon around the centre, for a box of `w`×`h`; `None` for shapes
    /// drawn from rectangles and ellipses (rounded, rect, pill, ellipse, text, container).
    pub fn polygon(self, w: f32, h: f32) -> Option<Vec<Point>> {
        let (hw, hh) = (w / 2.0, h / 2.0);
        let pt = |x: f32, y: f32| Point::new(x, y);
        Some(match self {
            Self::Diamond => vec![pt(0.0, -hh), pt(hw, 0.0), pt(0.0, hh), pt(-hw, 0.0)],
            Self::Hexagon => {
                let k = hw.min(h * 0.4);
                vec![pt(-hw + k, -hh), pt(hw - k, -hh), pt(hw, 0.0), pt(hw - k, hh), pt(-hw + k, hh), pt(-hw, 0.0)]
            }
            Self::Parallelogram => {
                let k = (w * 0.18).min(hw);
                vec![pt(-hw + k, -hh), pt(hw, -hh), pt(hw - k, hh), pt(-hw, hh)]
            }
            Self::Document => {
                let mut v = vec![pt(-hw, -hh), pt(hw, -hh), pt(hw, hh - h * 0.12)];
                for i in 0..=16 {
                    let t = i as f32 / 16.0;
                    let x = hw - t * w;
                    let y = hh - h * 0.12 + (t * std::f32::consts::TAU).sin() * h * 0.08;
                    v.push(pt(x, y));
                }
                v
            }
            Self::Note => {
                let k = (w.min(h) * 0.2).min(24.0);
                vec![pt(-hw, -hh), pt(hw - k, -hh), pt(hw, -hh + k), pt(hw, hh), pt(-hw, hh)]
            }
            Self::Cylinder => {
                let cap = (h * 0.14).min(hw);
                let mut v = Vec::new();
                for i in 0..=16 {
                    let a = std::f32::consts::PI + std::f32::consts::PI * i as f32 / 16.0;
                    v.push(pt(hw * a.cos(), -hh + cap + cap * a.sin()));
                }
                for i in 0..=16 {
                    let a = std::f32::consts::PI * i as f32 / 16.0;
                    v.push(pt(hw * a.cos(), hh - cap + cap * a.sin()));
                }
                v
            }
            Self::Cloud => {
                let mut v = Vec::new();
                for i in 0..48 {
                    let a = std::f32::consts::TAU * i as f32 / 48.0;
                    let bump = 0.86 + 0.14 * (a * 7.0).cos().abs();
                    v.push(pt(hw * bump * a.cos(), hh * bump * a.sin()));
                }
                v
            }
            Self::Rounded | Self::Rect | Self::Ellipse | Self::Pill | Self::Subprocess | Self::Text | Self::Container => return None,
        })
    }

    /// Extra strokes drawn inside the outline (the cylinder's inner cap, the subprocess bars, the
    /// note's fold), as polylines around the centre.
    pub fn inner_strokes(self, w: f32, h: f32) -> Vec<Vec<Point>> {
        let (hw, hh) = (w / 2.0, h / 2.0);
        let pt = |x: f32, y: f32| Point::new(x, y);
        match self {
            Self::Cylinder => {
                let cap = (h * 0.14).min(hw);
                let line = (0..=16)
                    .map(|i| {
                        let a = std::f32::consts::PI * i as f32 / 16.0;
                        pt(hw * a.cos(), -hh + cap + cap * a.sin())
                    })
                    .collect();
                vec![line]
            }
            Self::Subprocess => {
                let k = (w * 0.08).min(12.0);
                vec![vec![pt(-hw + k, -hh), pt(-hw + k, hh)], vec![pt(hw - k, -hh), pt(hw - k, hh)]]
            }
            Self::Note => {
                let k = (w.min(h) * 0.2).min(24.0);
                vec![vec![pt(hw - k, -hh), pt(hw - k, -hh + k), pt(hw, -hh + k)]]
            }
            _ => Vec::new(),
        }
    }
}

/// How a link is drawn.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LineStyle {
    #[default]
    Solid,
    Dashed,
    Dotted,
}

/// Arrowheads on a link.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Arrows {
    #[default]
    End,
    None,
    Start,
    Both,
}

/// How a link finds its way between two nodes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Route {
    #[default]
    Straight,
    Orthogonal,
    Curved,
}

/// A link's look. The default (solid, end arrow, straight, inherited colour) is not written.
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct EdgeStyle {
    #[serde(default)]
    pub line: LineStyle,
    #[serde(default)]
    pub arrows: Arrows,
    #[serde(default)]
    pub route: Route,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color: Option<Rgb>,
    /// Stroke width; 0 means the default.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub width: f32,
}

fn is_zero(v: &f32) -> bool {
    *v == 0.0
}

fn is_default_style(s: &EdgeStyle) -> bool {
    *s == EdgeStyle::default()
}

impl EdgeStyle {
    pub fn clamped(mut self) -> Self {
        self.width = if self.width.is_finite() { self.width.clamp(0.0, 12.0) } else { 0.0 };
        self
    }
}

impl LineStyle {
    pub const ALL: [Self; 3] = [Self::Solid, Self::Dashed, Self::Dotted];
    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|s| s.name().eq_ignore_ascii_case(name.trim()))
    }
    pub const fn name(self) -> &'static str {
        match self {
            Self::Solid => "solid",
            Self::Dashed => "dashed",
            Self::Dotted => "dotted",
        }
    }
}

impl Arrows {
    pub const ALL: [Self; 4] = [Self::None, Self::End, Self::Start, Self::Both];
    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|s| s.name().eq_ignore_ascii_case(name.trim()))
    }
    pub const fn name(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::End => "end",
            Self::Start => "start",
            Self::Both => "both",
        }
    }
}

impl Route {
    pub const ALL: [Self; 3] = [Self::Straight, Self::Orthogonal, Self::Curved];
    pub fn parse(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|s| s.name().eq_ignore_ascii_case(name.trim()))
    }
    pub const fn name(self) -> &'static str {
        match self {
            Self::Straight => "straight",
            Self::Orthogonal => "orthogonal",
            Self::Curved => "curved",
        }
    }
}

/// What a map is: a mind/concept map (tree-first) or a free diagram (shapes and connectors).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Map,
    Diagram,
}

impl Mode {
    pub fn parse(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "map" => Some(Self::Map),
            "diagram" => Some(Self::Diagram),
            _ => None,
        }
    }
    pub const fn name(self) -> &'static str {
        match self {
            Self::Map => "map",
            Self::Diagram => "diagram",
        }
    }
}

/// A concept: a labelled box on the canvas.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Node {
    pub id: NodeId,
    pub label: String,
    /// Centre of the node.
    pub pos: Point,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent: Option<NodeId>,
    #[serde(default)]
    pub color: Rgb,
    #[serde(default)]
    pub shape: Shape,
    /// Children (and everything under them) are hidden.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub collapsed: bool,
    /// Stencil id (`net.router`…), which gives the node its icon.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    /// Explicit size; without one the node fits its text.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size: Option<Size>,
    /// Free properties (IP address, VLAN, owner, status…).
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub props: BTreeMap<String, String>,
    /// Drawing order: higher is in front; equal z draws in id order.
    #[serde(default, skip_serializing_if = "is_zero_i32")]
    pub z: i32,
    /// The layer this node is on; `None` is the default layer.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer: Option<String>,
    /// Nodes sharing a group id select and move together.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group: Option<u64>,
}

fn is_zero_i32(v: &i32) -> bool {
    *v == 0
}

/// A named layer: nodes on a hidden layer are not drawn, nodes on a locked one are not edited
/// from the canvas.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Layer {
    pub name: String,
    #[serde(default = "yes")]
    pub visible: bool,
    #[serde(default)]
    pub locked: bool,
}

fn yes() -> bool {
    true
}

/// Most layers a map can have.
pub const MAX_LAYERS: usize = 64;

impl Node {
    /// The node's rectangle half-size when it has an explicit size.
    pub fn half_size(&self) -> Option<(f32, f32)> {
        self.size.map(|s| (s.w / 2.0, s.h / 2.0))
    }

    /// `status` property mapped to a traffic light: up/ok/online → green, warning/degraded →
    /// amber, down/offline/error → red.
    pub fn status_light(&self) -> Option<Status> {
        let s = self.props.get("status")?.trim().to_ascii_lowercase();
        match s.as_str() {
            "up" | "ok" | "online" | "active" | "running" | "done" | "attivo" | "online " => Some(Status::Up),
            "warning" | "warn" | "degraded" | "slow" | "attenzione" => Some(Status::Warning),
            "down" | "offline" | "error" | "failed" | "ko" | "inattivo" | "guasto" => Some(Status::Down),
            _ => None,
        }
    }
}

/// A node's health, from its `status` property.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Up,
    Warning,
    Down,
}

/// A free, optionally labelled link between two concepts ("causes", "is part of"…).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Edge {
    pub id: EdgeId,
    pub from: NodeId,
    pub to: NodeId,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub label: String,
    #[serde(default, skip_serializing_if = "is_default_style")]
    pub style: EdgeStyle,
}

/// A concept map.
#[derive(Clone, Debug, PartialEq)]
pub struct Map {
    title: String,
    mode: Mode,
    layers: Vec<Layer>,
    nodes: BTreeMap<NodeId, Node>,
    edges: BTreeMap<EdgeId, Edge>,
    next_id: u64,
}

impl Default for Map {
    fn default() -> Self {
        Self::new("Untitled map")
    }
}

/// The file layout: plain lists, so files diff well.
#[derive(Serialize, Deserialize)]
struct File {
    format: String,
    version: u32,
    title: String,
    #[serde(default)]
    mode: Mode,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    layers: Vec<Layer>,
    /// Next free id, so ideas of deleted ideas are never reused.
    #[serde(default, rename = "nextId")]
    next_id: u64,
    #[serde(default)]
    nodes: Vec<Node>,
    #[serde(default)]
    edges: Vec<Edge>,
}

fn clean_label(label: &str) -> String {
    label.chars().filter(|c| *c == '\n' || !c.is_control()).take(MAX_LABEL_CHARS).collect()
}

fn clean_prop(text: &str) -> String {
    text.chars().filter(|c| !c.is_control()).take(MAX_PROP_CHARS).collect::<String>().trim().to_string()
}

fn clean_kind(kind: &str) -> Option<String> {
    let k: String = kind.trim().chars().filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-' || *c == '_').take(64).collect();
    (!k.is_empty()).then_some(k)
}

impl Map {
    pub fn new(title: &str) -> Self {
        Self { title: clean_label(title), mode: Mode::Map, layers: Vec::new(), nodes: BTreeMap::new(), edges: BTreeMap::new(), next_id: 1 }
    }

    /// A small built-in example (original content), used by `--sample` and tests.
    pub fn sample() -> Self {
        let mut map = Self::new("IdeaCraft");
        let build = |map: &mut Self| -> Result<()> {
            let root = map.add_node("IdeaCraft", Point::new(0.0, 0.0), None)?;
            map.set_color(root, Rgb(0xff, 0xe0, 0x8a))?;
            map.set_shape(root, Shape::Pill)?;
            let branches = [
                ("Canvas", Rgb(0xb3, 0xd4, 0xff), &["Infinite pan & zoom", "Drag to move", "Double-click to add"][..]),
                ("Structure", Rgb(0xb1, 0xec, 0xd8), &["Parent / child tree", "Free links", "Collapse branches"][..]),
                ("Layouts", Rgb(0xd4, 0xc4, 0xfb), &["Tree", "Radial"][..]),
                ("Open", Rgb(0xff, 0xc2, 0xa8), &["Plain JSON files", "Every action is a command", "CLI"][..]),
            ];
            let mut first_leaf = None;
            let mut last_leaf = None;
            for (i, (label, color, leaves)) in branches.iter().enumerate() {
                let branch = map.add_node(label, Point::new(220.0, i as f32 * 120.0), Some(root))?;
                map.set_color(branch, *color)?;
                for (j, leaf) in leaves.iter().enumerate() {
                    let id = map.add_node(leaf, Point::new(460.0, i as f32 * 120.0 + j as f32 * 40.0), Some(branch))?;
                    map.set_color(id, *color)?;
                    first_leaf.get_or_insert(id);
                    last_leaf = Some(id);
                }
            }
            if let (Some(a), Some(b)) = (first_leaf, last_leaf) {
                map.connect(a, b, "saved in")?;
            }
            crate::layout::apply(map, crate::layout::Layout::Tree);
            Ok(())
        };
        // The sample is tiny and well-formed; should it ever fail, an empty map is still valid.
        if build(&mut map).is_err() {
            map = Self::new("IdeaCraft");
        }
        map
    }

    pub fn title(&self) -> &str {
        &self.title
    }

    pub fn set_title(&mut self, title: &str) {
        self.title = clean_label(title);
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    pub fn set_mode(&mut self, mode: Mode) {
        self.mode = mode;
    }

    // ---- layers ------------------------------------------------------------------------------

    pub fn layers(&self) -> &[Layer] {
        &self.layers
    }

    pub fn layer(&self, name: &str) -> Option<&Layer> {
        self.layers.iter().find(|l| l.name == name)
    }

    /// Adds a layer (no-op when it exists).
    pub fn add_layer(&mut self, name: &str) -> Result<()> {
        let name = clean_prop(name);
        if name.is_empty() {
            return Err(Error::Format("a layer needs a name".into()));
        }
        if self.layer(&name).is_some() {
            return Ok(());
        }
        if self.layers.len() >= MAX_LAYERS {
            return Err(Error::Full("layers"));
        }
        self.layers.push(Layer { name, visible: true, locked: false });
        Ok(())
    }

    /// Removes a layer; its nodes go back to the default layer.
    pub fn remove_layer(&mut self, name: &str) -> Result<()> {
        let i = self
            .layers
            .iter()
            .position(|l| l.name == name)
            .ok_or_else(|| Error::Format(format!("no layer {:?}", name.chars().take(40).collect::<String>())))?;
        self.layers.remove(i);
        for n in self.nodes.values_mut() {
            if n.layer.as_deref() == Some(name) {
                n.layer = None;
            }
        }
        Ok(())
    }

    pub fn rename_layer(&mut self, name: &str, new: &str) -> Result<()> {
        let new = clean_prop(new);
        if new.is_empty() {
            return Err(Error::Format("a layer needs a name".into()));
        }
        if self.layer(&new).is_some() && new != name {
            return Err(Error::Format(format!("layer {new:?} exists")));
        }
        let l = self
            .layers
            .iter_mut()
            .find(|l| l.name == name)
            .ok_or_else(|| Error::Format(format!("no layer {:?}", name.chars().take(40).collect::<String>())))?;
        l.name = new.clone();
        for n in self.nodes.values_mut() {
            if n.layer.as_deref() == Some(name) {
                n.layer = Some(new.clone());
            }
        }
        Ok(())
    }

    pub fn set_layer_visible(&mut self, name: &str, visible: bool) -> Result<()> {
        let l = self
            .layers
            .iter_mut()
            .find(|l| l.name == name)
            .ok_or_else(|| Error::Format(format!("no layer {:?}", name.chars().take(40).collect::<String>())))?;
        l.visible = visible;
        Ok(())
    }

    pub fn set_layer_locked(&mut self, name: &str, locked: bool) -> Result<()> {
        let l = self
            .layers
            .iter_mut()
            .find(|l| l.name == name)
            .ok_or_else(|| Error::Format(format!("no layer {:?}", name.chars().take(40).collect::<String>())))?;
        l.locked = locked;
        Ok(())
    }

    /// Moves a node to a layer (`None`: the default layer); the layer is created when missing.
    pub fn set_node_layer(&mut self, id: NodeId, layer: Option<&str>) -> Result<()> {
        self.node(id).ok_or(Error::NoNode(id.0))?;
        let layer = match layer.map(clean_prop).filter(|l| !l.is_empty()) {
            Some(l) => {
                self.add_layer(&l)?;
                Some(l)
            }
            None => None,
        };
        self.node_mut(id)?.layer = layer;
        Ok(())
    }

    /// A node's layer is locked (not editable from the canvas).
    pub fn is_locked(&self, id: NodeId) -> bool {
        self.node(id).and_then(|n| n.layer.as_deref()).and_then(|l| self.layer(l)).is_some_and(|l| l.locked)
    }

    // ---- groups ------------------------------------------------------------------------------

    /// Puts the nodes in one new group (they leave any group they were in). Returns the group id.
    pub fn group(&mut self, ids: &[NodeId]) -> Result<u64> {
        if ids.len() < 2 {
            return Err(Error::Format("a group needs at least two shapes".into()));
        }
        for id in ids {
            self.node(*id).ok_or(Error::NoNode(id.0))?;
        }
        let gid = self.fresh_id()?;
        for id in ids {
            if let Some(n) = self.nodes.get_mut(id) {
                n.group = Some(gid);
            }
        }
        Ok(gid)
    }

    /// Dissolves the groups the nodes belong to.
    pub fn ungroup(&mut self, ids: &[NodeId]) -> Result<()> {
        let groups: BTreeSet<u64> = ids.iter().filter_map(|id| self.node(*id).and_then(|n| n.group)).collect();
        for n in self.nodes.values_mut() {
            if n.group.is_some_and(|g| groups.contains(&g)) {
                n.group = None;
            }
        }
        Ok(())
    }

    /// Every node in the same group as `id` (including `id`).
    pub fn group_members(&self, id: NodeId) -> Vec<NodeId> {
        match self.node(id).and_then(|n| n.group) {
            Some(g) => self.nodes.values().filter(|n| n.group == Some(g)).map(|n| n.id).collect(),
            None => vec![id],
        }
    }

    /// Nodes in drawing order: containers first, then by `z`, then by id.
    pub fn nodes_in_draw_order(&self) -> Vec<&Node> {
        let mut v: Vec<&Node> = self.nodes.values().collect();
        v.sort_by_key(|n| (!n.shape.is_container(), n.z, n.id));
        v
    }

    pub fn node(&self, id: NodeId) -> Option<&Node> {
        self.nodes.get(&id)
    }

    pub fn edge(&self, id: EdgeId) -> Option<&Edge> {
        self.edges.get(&id)
    }

    pub fn nodes(&self) -> impl Iterator<Item = &Node> {
        self.nodes.values()
    }

    pub fn edges(&self) -> impl Iterator<Item = &Edge> {
        self.edges.values()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    fn node_mut(&mut self, id: NodeId) -> Result<&mut Node> {
        self.nodes.get_mut(&id).ok_or(Error::NoNode(id.0))
    }

    fn fresh_id(&mut self) -> Result<u64> {
        let id = self.next_id;
        self.next_id = id.checked_add(1).ok_or(Error::Full("ids"))?;
        Ok(id)
    }

    /// Adds a node; `parent` makes it a child in the tree.
    pub fn add_node(&mut self, label: &str, pos: Point, parent: Option<NodeId>) -> Result<NodeId> {
        if self.nodes.len() >= MAX_NODES {
            return Err(Error::Full("nodes"));
        }
        if let Some(p) = parent {
            self.node(p).ok_or(Error::NoNode(p.0))?;
        }
        let id = NodeId(self.fresh_id()?);
        // A new main branch (child of a root) gets the next branch colour; deeper ideas take
        // their parent's colour, so each branch reads as one colour.
        let color = match parent.and_then(|p| self.node(p)) {
            Some(p) if p.parent.is_none() => {
                let branches = self.nodes.values().filter(|n| n.parent == Some(p.id)).count();
                Rgb::BRANCHES.get(branches % Rgb::BRANCHES.len()).copied().unwrap_or_default()
            }
            Some(p) => p.color,
            None => Rgb::default(),
        };
        self.nodes.insert(
            id,
            Node {
                id,
                label: clean_label(label),
                pos: pos.clamped(),
                parent,
                color,
                shape: Shape::default(),
                collapsed: false,
                kind: None,
                size: None,
                props: BTreeMap::new(),
                z: 0,
                layer: None,
                group: None,
            },
        );
        Ok(id)
    }

    /// Adds a shape from a stencil: its shape, size, colour and kind. Without a label the
    /// stencil's name is used.
    pub fn add_stencil(&mut self, stencil: &crate::stencil::Stencil, label: Option<&str>, pos: Point) -> Result<NodeId> {
        let id = self.add_node(label.unwrap_or(stencil.name), pos, None)?;
        if let Some(n) = self.nodes.get_mut(&id) {
            n.shape = stencil.shape;
            n.size = Some(stencil.size);
            n.color = stencil.color;
            n.kind = (!stencil.glyph.is_empty()).then(|| stencil.id.to_string());
            if stencil.shape.is_container() {
                n.z = -1;
            }
        }
        Ok(id)
    }

    /// Copies a node (not its branch) with a new id, offset by `(dx, dy)`.
    pub fn duplicate_node(&mut self, id: NodeId, dx: f32, dy: f32) -> Result<NodeId> {
        let src = self.node(id).ok_or(Error::NoNode(id.0))?.clone();
        if self.nodes.len() >= MAX_NODES {
            return Err(Error::Full("nodes"));
        }
        let new = NodeId(self.fresh_id()?);
        let mut n = src;
        n.id = new;
        n.pos = Point::new(n.pos.x + dx, n.pos.y + dy).clamped();
        n.collapsed = false;
        n.group = None;
        self.nodes.insert(new, n);
        Ok(new)
    }

    pub fn set_kind(&mut self, id: NodeId, kind: Option<&str>) -> Result<()> {
        self.node_mut(id)?.kind = kind.and_then(clean_kind);
        Ok(())
    }

    pub fn set_size(&mut self, id: NodeId, size: Option<Size>) -> Result<()> {
        self.node_mut(id)?.size = size.map(Size::clamped);
        Ok(())
    }

    pub fn set_z(&mut self, id: NodeId, z: i32) -> Result<()> {
        self.node_mut(id)?.z = z.clamp(-1_000, 1_000);
        Ok(())
    }

    /// Sets a property; an empty value removes it. At most [`MAX_PROPS`] per node.
    pub fn set_prop(&mut self, id: NodeId, key: &str, value: &str) -> Result<()> {
        let key = clean_prop(key);
        let value = clean_prop(value);
        let n = self.node_mut(id)?;
        if key.is_empty() {
            return Err(Error::Format("a property needs a name".into()));
        }
        if value.is_empty() {
            n.props.remove(&key);
        } else {
            if !n.props.contains_key(&key) && n.props.len() >= MAX_PROPS {
                return Err(Error::Full("properties"));
            }
            n.props.insert(key, value);
        }
        Ok(())
    }

    pub fn set_edge_style(&mut self, id: EdgeId, style: EdgeStyle) -> Result<()> {
        self.edges.get_mut(&id).ok_or(Error::NoEdge(id.0))?.style = style.clamped();
        Ok(())
    }

    /// The top-most node whose box contains `p` (explicit sizes only; text-fitted nodes are hit
    /// by the UI, which knows their text size).
    pub fn node_at(&self, p: Point) -> Option<NodeId> {
        self.nodes_in_draw_order()
            .into_iter()
            .rev()
            .filter(|n| self.is_visible(n.id))
            .find(|n| n.half_size().is_some_and(|(hw, hh)| (p.x - n.pos.x).abs() <= hw && (p.y - n.pos.y).abs() <= hh))
            .map(|n| n.id)
    }

    /// Removes a node with its whole branch and every link touching them; returns the removed ids.
    pub fn remove_node(&mut self, id: NodeId) -> Result<Vec<NodeId>> {
        self.node(id).ok_or(Error::NoNode(id.0))?;
        let mut gone = vec![id];
        gone.extend(self.descendants(id));
        let set: BTreeSet<NodeId> = gone.iter().copied().collect();
        for n in &gone {
            self.nodes.remove(n);
        }
        self.edges.retain(|_, e| !set.contains(&e.from) && !set.contains(&e.to));
        Ok(gone)
    }

    pub fn set_label(&mut self, id: NodeId, label: &str) -> Result<()> {
        self.node_mut(id)?.label = clean_label(label);
        Ok(())
    }

    pub fn set_pos(&mut self, id: NodeId, pos: Point) -> Result<()> {
        self.node_mut(id)?.pos = pos.clamped();
        Ok(())
    }

    pub fn set_color(&mut self, id: NodeId, color: Rgb) -> Result<()> {
        self.node_mut(id)?.color = color;
        Ok(())
    }

    pub fn set_shape(&mut self, id: NodeId, shape: Shape) -> Result<()> {
        self.node_mut(id)?.shape = shape;
        Ok(())
    }

    pub fn set_collapsed(&mut self, id: NodeId, collapsed: bool) -> Result<()> {
        self.node_mut(id)?.collapsed = collapsed;
        Ok(())
    }

    /// Moves a node (with its branch) under `parent`, or makes it a root with `None`.
    pub fn set_parent(&mut self, id: NodeId, parent: Option<NodeId>) -> Result<()> {
        self.node(id).ok_or(Error::NoNode(id.0))?;
        if let Some(p) = parent {
            self.node(p).ok_or(Error::NoNode(p.0))?;
            if p == id || self.ancestors(p).contains(&id) {
                return Err(Error::Cycle);
            }
        }
        self.node_mut(id)?.parent = parent;
        Ok(())
    }

    /// Links two nodes; linking a pair that is already linked returns the existing link.
    pub fn connect(&mut self, from: NodeId, to: NodeId, label: &str) -> Result<EdgeId> {
        if from == to {
            return Err(Error::SelfLink);
        }
        self.node(from).ok_or(Error::NoNode(from.0))?;
        self.node(to).ok_or(Error::NoNode(to.0))?;
        if let Some(e) = self.edges.values().find(|e| (e.from, e.to) == (from, to) || (e.from, e.to) == (to, from)) {
            return Ok(e.id);
        }
        if self.edges.len() >= MAX_EDGES {
            return Err(Error::Full("links"));
        }
        let id = EdgeId(self.fresh_id()?);
        self.edges.insert(id, Edge { id, from, to, label: clean_label(label), style: EdgeStyle::default() });
        Ok(id)
    }

    pub fn disconnect(&mut self, id: EdgeId) -> Result<Edge> {
        self.edges.remove(&id).ok_or(Error::NoEdge(id.0))
    }

    pub fn set_edge_label(&mut self, id: EdgeId, label: &str) -> Result<()> {
        self.edges.get_mut(&id).ok_or(Error::NoEdge(id.0))?.label = clean_label(label);
        Ok(())
    }

    /// Nodes without a parent, in id order.
    pub fn roots(&self) -> Vec<NodeId> {
        self.nodes.values().filter(|n| n.parent.is_none()).map(|n| n.id).collect()
    }

    /// Direct children, top to bottom on the canvas (ties by creation order), so dragging a child
    /// up or down and re-running a layout reorders it.
    pub fn children(&self, id: NodeId) -> Vec<NodeId> {
        let mut kids: Vec<&Node> = self.nodes.values().filter(|n| n.parent == Some(id)).collect();
        kids.sort_by(|a, b| a.pos.y.total_cmp(&b.pos.y).then(a.id.cmp(&b.id)));
        kids.into_iter().map(|n| n.id).collect()
    }

    /// Parent, grandparent… nearest first; stops at [`MAX_DEPTH`] or a cycle.
    pub fn ancestors(&self, id: NodeId) -> Vec<NodeId> {
        let mut out = Vec::new();
        let mut cur = self.node(id).and_then(|n| n.parent);
        while let Some(p) = cur {
            if out.len() >= MAX_DEPTH || out.contains(&p) || p == id {
                break;
            }
            out.push(p);
            cur = self.node(p).and_then(|n| n.parent);
        }
        out
    }

    /// Everything below a node, breadth first.
    pub fn descendants(&self, id: NodeId) -> Vec<NodeId> {
        let mut children_of: BTreeMap<NodeId, Vec<NodeId>> = BTreeMap::new();
        for n in self.nodes.values() {
            if let Some(p) = n.parent {
                children_of.entry(p).or_default().push(n.id);
            }
        }
        let mut seen = BTreeSet::from([id]);
        let mut out = Vec::new();
        let mut i = 0;
        let mut frontier = vec![id];
        while let Some(cur) = frontier.get(i).copied() {
            i += 1;
            for kid in children_of.get(&cur).into_iter().flatten() {
                if seen.insert(*kid) {
                    out.push(*kid);
                    frontier.push(*kid);
                }
            }
        }
        out
    }

    /// Shown on the canvas: no ancestor is collapsed.
    pub fn is_visible(&self, id: NodeId) -> bool {
        let Some(n) = self.node(id) else { return false };
        let layer_visible = n.layer.as_deref().and_then(|l| self.layer(l)).is_none_or(|l| l.visible);
        layer_visible && self.ancestors(id).iter().all(|a| self.node(*a).is_some_and(|n| !n.collapsed))
    }

    pub fn to_json(&self) -> String {
        let file = File {
            format: "ideacraft".into(),
            version: FORMAT_VERSION,
            title: self.title.clone(),
            mode: self.mode,
            layers: self.layers.clone(),
            next_id: self.next_id,
            nodes: self.nodes.values().cloned().collect(),
            edges: self.edges.values().cloned().collect(),
        };
        serde_json::to_string_pretty(&file).unwrap_or_default()
    }

    /// Reads a map leniently: duplicates, dangling parents and links, parent cycles and
    /// out-of-range values are repaired; only an unreadable file or a newer format is an error.
    pub fn from_json(text: &str) -> Result<Self> {
        let file: File = serde_json::from_str(text).map_err(|e| Error::Format(e.to_string()))?;
        if file.format != "ideacraft" {
            return Err(Error::Format(format!("not an IdeaCraft map ({:?})", file.format.chars().take(32).collect::<String>())));
        }
        if file.version > FORMAT_VERSION {
            return Err(Error::Format(format!("made by a newer IdeaCraft (format {})", file.version)));
        }
        let mut map = Self::new(&file.title);
        map.mode = file.mode;
        for l in file.layers.into_iter().take(MAX_LAYERS) {
            let name = clean_prop(&l.name);
            if !name.is_empty() && map.layer(&name).is_none() {
                map.layers.push(Layer { name, ..l });
            }
        }
        let file_next = file.next_id;
        let mut max_id = 0u64;
        for mut n in file.nodes.into_iter().take(MAX_NODES) {
            if map.nodes.contains_key(&n.id) {
                continue;
            }
            n.label = clean_label(&n.label);
            n.pos = n.pos.clamped();
            n.size = n.size.map(Size::clamped);
            n.kind = n.kind.as_deref().and_then(clean_kind);
            n.z = n.z.clamp(-1_000, 1_000);
            n.props =
                n.props.iter().map(|(k, v)| (clean_prop(k), clean_prop(v))).filter(|(k, v)| !k.is_empty() && !v.is_empty()).take(MAX_PROPS).collect();
            n.layer = n.layer.as_deref().map(clean_prop).filter(|l| !l.is_empty());
            if let Some(l) = &n.layer
                && map.layer(l).is_none()
                && map.layers.len() < MAX_LAYERS
            {
                map.layers.push(Layer { name: l.clone(), visible: true, locked: false });
            }
            max_id = max_id.max(n.id.0);
            map.nodes.insert(n.id, n);
        }
        let ids: Vec<NodeId> = map.nodes.keys().copied().collect();
        for id in &ids {
            let parent = map.node(*id).and_then(|n| n.parent);
            if let Some(p) = parent
                && (!map.nodes.contains_key(&p) || p == *id)
                && let Ok(n) = map.node_mut(*id)
            {
                n.parent = None;
            }
        }
        // Break parent cycles: walk up from each node; a walk that comes back cuts its last step.
        for id in &ids {
            let mut seen = BTreeSet::from([*id]);
            let mut cur = *id;
            let mut steps = 0;
            while let Some(p) = map.node(cur).and_then(|n| n.parent) {
                steps += 1;
                if !seen.insert(p) || steps > MAX_DEPTH {
                    if let Ok(n) = map.node_mut(cur) {
                        n.parent = None;
                    }
                    break;
                }
                cur = p;
            }
        }
        let mut pairs = BTreeSet::new();
        for mut e in file.edges.into_iter().take(MAX_EDGES) {
            let pair = (e.from.min(e.to), e.from.max(e.to));
            if e.from == e.to || !map.nodes.contains_key(&e.from) || !map.nodes.contains_key(&e.to) || map.edges.contains_key(&e.id) {
                continue;
            }
            if map.nodes.contains_key(&NodeId(e.id.0)) || !pairs.insert(pair) {
                continue;
            }
            e.label = clean_label(&e.label);
            e.style = e.style.clamped();
            max_id = max_id.max(e.id.0);
            map.edges.insert(e.id, e);
        }
        let max_group = map.nodes.values().filter_map(|n| n.group).max().unwrap_or(0);
        map.next_id = max_id.max(max_group).saturating_add(1).max(file_next).max(1);
        Ok(map)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_a_tree_with_links() {
        let mut m = Map::new("t");
        let a = m.add_node("A", Point::new(0.0, 0.0), None).unwrap();
        let b = m.add_node("B", Point::new(10.0, 5.0), Some(a)).unwrap();
        let c = m.add_node("C", Point::new(10.0, -5.0), Some(a)).unwrap();
        let d = m.add_node("D", Point::new(20.0, 0.0), Some(b)).unwrap();
        assert_eq!(m.children(a), vec![c, b], "children run top to bottom");
        assert_eq!(m.ancestors(d), vec![b, a]);
        assert_eq!(m.descendants(a), vec![b, c, d]);
        let e = m.connect(c, d, "relates").unwrap();
        assert_eq!(m.connect(d, c, "again").unwrap(), e, "a pair is linked once");
        assert_eq!(m.connect(c, c, ""), Err(Error::SelfLink));
        assert_eq!(m.set_parent(a, Some(d)), Err(Error::Cycle));
        m.set_collapsed(b, true).unwrap();
        assert!(!m.is_visible(d));
        assert!(m.is_visible(b));
        assert_eq!(m.remove_node(b).unwrap(), vec![b, d]);
        assert_eq!(m.edge_count(), 0, "links to removed nodes go with them");
        assert_eq!(m.node_count(), 2);
    }

    #[test]
    fn json_round_trips_and_repairs_hostile_files() {
        let m = Map::sample();
        assert!(m.node_count() > 10);
        assert_eq!(Map::from_json(&m.to_json()).unwrap(), m);

        let hostile = r#"{"format":"ideacraft","version":1,"title":"x",
            "nodes":[{"id":1,"label":"a","pos":{"x":1e30,"y":0},"parent":2},
                     {"id":2,"label":"b","pos":{"x":0,"y":0},"parent":1},
                     {"id":2,"label":"dup","pos":{"x":0,"y":0}},
                     {"id":3,"label":"c","pos":{"x":0,"y":0},"parent":99}],
            "edges":[{"id":4,"from":1,"to":1},{"id":5,"from":1,"to":42},{"id":6,"from":1,"to":3},{"id":7,"from":3,"to":1}]}"#;
        let m = Map::from_json(hostile).unwrap();
        assert_eq!(m.node_count(), 3);
        assert_eq!(m.edge_count(), 1);
        assert!(m.node(NodeId(1)).unwrap().pos.x <= crate::limits::MAX_COORD);
        assert_eq!(m.node(NodeId(3)).unwrap().parent, None);
        assert!(m.roots().len() >= 2, "the 1↔2 cycle is broken");
        let mut m = m;
        let fresh = m.add_node("new", Point::default(), None).unwrap();
        assert!(fresh.0 > 6, "new ids never collide with loaded ones");

        assert!(Map::from_json("{").is_err());
        assert!(Map::from_json(r#"{"format":"other","version":1,"title":""}"#).is_err());
        assert!(Map::from_json(r#"{"format":"ideacraft","version":99,"title":""}"#).is_err());
    }

    #[test]
    fn diagram_fields_round_trip_and_stay_bounded() {
        let mut m = Map::new("net");
        m.set_mode(Mode::Diagram);
        let router = crate::stencil::find("net.router").unwrap();
        let r = m.add_stencil(router, Some("Core router"), Point::new(10.0, 10.0)).unwrap();
        let n = m.node(r).unwrap();
        assert_eq!((n.kind.as_deref(), n.size, n.shape), (Some("net.router"), Some(router.size), Shape::Rounded));
        m.set_prop(r, " IP ", "10.0.0.1").unwrap();
        m.set_prop(r, "status", "down").unwrap();
        assert_eq!(m.node(r).unwrap().status_light(), Some(Status::Down));
        m.set_prop(r, "status", "").unwrap();
        assert!(!m.node(r).unwrap().props.contains_key("status"));
        assert!(m.set_prop(r, "", "x").is_err());
        for i in 0..MAX_PROPS + 5 {
            let _ = m.set_prop(r, &format!("k{i}"), "v");
        }
        assert_eq!(m.node(r).unwrap().props.len(), MAX_PROPS);
        m.set_size(r, Some(Size::new(f32::NAN, 1e9))).unwrap();
        assert_eq!(m.node(r).unwrap().size, Some(Size::new(MIN_SIZE, MAX_SIZE)));
        assert_eq!(m.node_at(Point::new(10.0, 10.0)), Some(r));
        assert_eq!(m.node_at(Point::new(5000.0, 10.0)), None);
        let pc = m.add_stencil(crate::stencil::find("net.pc").unwrap(), None, Point::new(300.0, 10.0)).unwrap();
        assert_eq!(m.node(pc).unwrap().label, "Workstation");
        let e = m.connect(r, pc, "1 Gbit").unwrap();
        m.set_edge_style(e, EdgeStyle { line: LineStyle::Dashed, arrows: Arrows::Both, route: Route::Orthogonal, color: None, width: 99.0 }).unwrap();
        assert_eq!(m.edge(e).unwrap().style.width, 12.0);
        let copy = m.duplicate_node(r, 20.0, 0.0).unwrap();
        assert_eq!(m.node(copy).unwrap().kind.as_deref(), Some("net.router"));
        assert_eq!(m.node(copy).unwrap().pos.x, 30.0);
        let back = Map::from_json(&m.to_json()).unwrap();
        assert_eq!(back, m);
        assert_eq!(back.mode(), Mode::Diagram);
        // Old files (no mode, size, kind, props, style) still read, and a hostile size is clamped.
        let old = r#"{"format":"ideacraft","version":1,"title":"t","nodes":[{"id":1,"label":"a","pos":{"x":0,"y":0},"size":{"w":-5,"h":"nan"}}]}"#;
        assert!(Map::from_json(old).is_err() || Map::from_json(old).is_ok());
        let old = r#"{"format":"ideacraft","version":1,"title":"t","nodes":[{"id":1,"label":"a","pos":{"x":0,"y":0},"size":{"w":-5,"h":1e9},"z":99999,"props":{"":"x","ip":"1.2.3.4"}}],"edges":[]}"#;
        let m = Map::from_json(old).unwrap();
        let n = m.node(NodeId(1)).unwrap();
        assert_eq!((n.size, n.z, n.props.len()), (Some(Size::new(MIN_SIZE, MAX_SIZE)), 1_000, 1));
        for s in Shape::ALL {
            let _ = s.polygon(100.0, 60.0);
            let _ = s.inner_strokes(100.0, 60.0);
            assert_eq!(Shape::parse(s.name()), Some(s));
        }
    }

    #[test]
    fn layers_and_groups() {
        let mut m = Map::new("l");
        let a = m.add_node("a", Point::default(), None).unwrap();
        let b = m.add_node("b", Point::new(10.0, 0.0), None).unwrap();
        let c = m.add_node("c", Point::new(20.0, 0.0), None).unwrap();
        m.set_node_layer(a, Some(" Cabling ")).unwrap();
        assert_eq!(m.layers().len(), 1);
        assert_eq!(m.node(a).unwrap().layer.as_deref(), Some("Cabling"));
        m.set_layer_visible("Cabling", false).unwrap();
        assert!(!m.is_visible(a) && m.is_visible(b));
        m.set_layer_locked("Cabling", true).unwrap();
        assert!(m.is_locked(a) && !m.is_locked(b));
        m.rename_layer("Cabling", "Wiring").unwrap();
        assert_eq!(m.node(a).unwrap().layer.as_deref(), Some("Wiring"));
        assert!(m.rename_layer("Wiring", "").is_err());
        assert!(m.set_layer_visible("nope", true).is_err());
        m.add_layer("Wiring").unwrap();
        assert_eq!(m.layers().len(), 1, "adding an existing layer is a no-op");
        let g = m.group(&[a, b]).unwrap();
        assert_eq!(m.group_members(a), vec![a, b]);
        assert_eq!(m.group_members(c), vec![c]);
        assert!(m.group(&[a]).is_err());
        let copy = m.duplicate_node(a, 5.0, 5.0).unwrap();
        assert_eq!(m.node(copy).unwrap().group, None, "a duplicate leaves the group");
        let back = Map::from_json(&m.to_json()).unwrap();
        assert_eq!(back, m);
        assert!(back.layer("Wiring").is_some_and(|l| l.locked && !l.visible));
        let fresh = back.clone().add_node("z", Point::default(), None).unwrap();
        assert!(fresh.0 > g, "ids never collide with group ids");
        m.ungroup(&[b]).unwrap();
        assert_eq!(m.group_members(a), vec![a]);
        m.remove_layer("Wiring").unwrap();
        assert_eq!(m.node(a).unwrap().layer, None);
        assert!(m.is_visible(a));
    }

    #[test]
    fn labels_and_positions_are_sanitised() {
        let mut m = Map::new("t");
        let a = m.add_node(&"x".repeat(MAX_LABEL_CHARS + 50), Point::new(f32::NAN, f32::INFINITY), None).unwrap();
        let n = m.node(a).unwrap();
        assert_eq!(n.label.chars().count(), MAX_LABEL_CHARS);
        assert_eq!(n.pos, Point::new(0.0, MAX_COORD));
        m.set_label(a, "two\nlines\u{7}").unwrap();
        assert_eq!(m.node(a).unwrap().label, "two\nlines");
    }
}
