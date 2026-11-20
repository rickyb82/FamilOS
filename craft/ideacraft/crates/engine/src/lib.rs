//! IdeaCraft's engine (layer L5): a [`Session`] owns the map, the selection and the undo history,
//! and everything a user can do is a command run through [`Session::execute`]. The UI, the CLI
//! and future automation (MCP, control channel) all reach the same commands.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod cmd;
mod params;

use std::panic::{AssertUnwindSafe, catch_unwind};

pub use cmd::{COMMANDS, CommandInfo, command};
pub use ideacraft_core as core;
use ideacraft_core::{Map, NodeId};
pub use ideacraft_formats as formats;
use serde_json::Value;

/// Most undo steps kept.
pub const UNDO_LIMIT: usize = 200;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Model(#[from] ideacraft_core::Error),
    #[error("unknown command {0:?}")]
    UnknownCommand(String),
    #[error("{0}")]
    Param(String),
    #[error(transparent)]
    Import(#[from] ideacraft_formats::Error),
    #[error("nothing to {0}")]
    Nothing(&'static str),
    #[error("internal error in {0}; the map was restored")]
    Internal(String),
}

pub type Result<T> = std::result::Result<T, Error>;

/// An open map and its editing state.
#[derive(Debug, Default)]
pub struct Session {
    map: Map,
    selection: Vec<NodeId>,
    undo: Vec<(Map, Vec<NodeId>)>,
    redo: Vec<(Map, Vec<NodeId>)>,
    dirty: bool,
}

impl Session {
    pub fn new(map: Map) -> Self {
        Self { map, ..Self::default() }
    }

    pub fn map(&self) -> &Map {
        &self.map
    }

    pub fn selection(&self) -> &[NodeId] {
        &self.selection
    }

    pub fn can_undo(&self) -> bool {
        !self.undo.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.redo.is_empty()
    }

    /// Changed since it was opened or last saved.
    pub fn is_dirty(&self) -> bool {
        self.dirty
    }

    pub fn mark_saved(&mut self) {
        self.dirty = false;
    }

    /// Marks the map as unsaved (an imported file that was never saved as a map).
    pub fn mark_dirty(&mut self) {
        self.dirty = true;
    }

    /// Runs a command. A command that fails leaves the map and selection as they were; a command
    /// that changes the map becomes one undo step. Panics are caught and the map restored (a
    /// safety net: commands must not panic in the first place).
    pub fn execute(&mut self, id: &str, params: &Value) -> Result<Value> {
        let info = command(id).ok_or_else(|| Error::UnknownCommand(id.chars().take(64).collect()))?;
        let before = (self.map.clone(), self.selection.clone());
        let outcome = catch_unwind(AssertUnwindSafe(|| cmd::run(self, info.id, params)));
        match outcome {
            Ok(Ok(value)) => {
                self.selection.retain(|n| self.map.node(*n).is_some());
                if info.undoable && self.map != before.0 {
                    self.undo.push(before);
                    if self.undo.len() > UNDO_LIMIT {
                        self.undo.remove(0);
                    }
                    self.redo.clear();
                    self.dirty = true;
                }
                Ok(value)
            }
            Ok(Err(e)) => {
                (self.map, self.selection) = before;
                Err(e)
            }
            Err(_) => {
                (self.map, self.selection) = before;
                Err(Error::Internal(info.id.to_string()))
            }
        }
    }

    fn undo_step(&mut self) -> Result<()> {
        let (map, sel) = self.undo.pop().ok_or(Error::Nothing("undo"))?;
        let current = (std::mem::replace(&mut self.map, map), std::mem::replace(&mut self.selection, sel));
        self.redo.push(current);
        self.dirty = true;
        Ok(())
    }

    fn redo_step(&mut self) -> Result<()> {
        let (map, sel) = self.redo.pop().ok_or(Error::Nothing("redo"))?;
        let current = (std::mem::replace(&mut self.map, map), std::mem::replace(&mut self.selection, sel));
        self.undo.push(current);
        self.dirty = true;
        Ok(())
    }

    /// Replaces the whole map (opening a file): history is cleared.
    pub fn replace_map(&mut self, map: Map) {
        *self = Self::new(map);
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn every_command_survives_empty_params() {
        for c in COMMANDS {
            let mut s = Session::new(Map::sample());
            let _ = s.execute(c.id, &json!({}));
            let _ = s.execute(c.id, &Value::Null);
            let _ = s.execute(c.id, &json!({"id": -1, "ids": "x", "x": "nan", "label": 7, "color": [], "parent": {}}));
            let mut empty = Session::default();
            let _ = empty.execute(c.id, &json!({}));
        }
    }

    #[test]
    fn commands_are_unique_and_documented() {
        for (i, c) in COMMANDS.iter().enumerate() {
            assert!(COMMANDS.iter().take(i).all(|o| o.id != c.id), "duplicate {}", c.id);
            assert!(!c.label.is_empty() && !c.menu.is_empty(), "{} needs a label and menu", c.id);
        }
    }

    #[test]
    fn undo_redo_and_failed_commands() {
        let mut s = Session::default();
        let id = s.execute("node.add", &json!({"label": "Root"})).unwrap()["id"].as_u64().unwrap();
        assert_eq!(s.selection(), [NodeId(id)]);
        s.execute("node.addChild", &json!({})).unwrap();
        assert_eq!(s.map().node_count(), 2);
        assert!(s.is_dirty());
        assert!(s.execute("node.setColor", &json!({"color": "#zzz"})).is_err());
        assert_eq!(s.map().node_count(), 2, "a failed command changes nothing");
        s.execute("edit.undo", &json!({})).unwrap();
        assert_eq!(s.map().node_count(), 1);
        s.execute("edit.redo", &json!({})).unwrap();
        assert_eq!(s.map().node_count(), 2);
        s.execute("selection.set", &json!({"ids": [id]})).unwrap();
        s.execute("node.delete", &json!({})).unwrap();
        assert_eq!(s.map().node_count(), 0, "deleting removes the branch");
        assert!(s.execute("edit.redo", &json!({})).is_err());
        assert!(s.execute("no.such", &json!({})).is_err());

        s.execute("map.newFromTemplate", &json!({"template": "swot"})).unwrap();
        assert_eq!(s.map().title(), "SWOT analysis");
        let md = s.execute("map.export", &json!({"format": "markdown"})).unwrap()["text"].as_str().unwrap().to_string();
        let r = s.execute("map.importText", &json!({"text": md})).unwrap();
        assert_eq!(r["format"], "Markdown / text outline");
        assert!(s.map().node_count() > 8);
        assert!(s.execute("map.newFromTemplate", &json!({"template": "x"})).is_err());
        s.execute("map.newFromTemplate", &json!({"template": "swot", "language": "it"})).unwrap();
        assert_eq!(s.map().title(), "Analisi SWOT");
        assert!(s.map().nodes().any(|n| n.label == "Punti di forza"));
        assert!(s.execute("map.newFromTemplate", &json!({"template": "swot", "language": "xx"})).is_err());

        // Diagram commands.
        s.execute("map.new", &json!({"title": "net"})).unwrap();
        let r = s
            .execute("node.addShape", &json!({"kind": "net.router", "label": "R1", "x": 0, "y": 0, "props": {"ip": "10.0.0.1", "status": "up"}}))
            .unwrap()["id"]
            .as_u64()
            .unwrap();
        assert_eq!(s.map().mode(), ideacraft_core::Mode::Diagram);
        assert_eq!(s.map().node(NodeId(r)).unwrap().props.get("ip").map(String::as_str), Some("10.0.0.1"));
        let sw = s.execute("node.addShape", &json!({"kind": "net.switch", "x": 300, "y": 0, "language": "it"})).unwrap()["id"].as_u64().unwrap();
        assert_eq!(s.map().node(NodeId(sw)).unwrap().label, "Switch");
        let pt = s.execute("node.addShape", &json!({"kind": "net.printer", "language": "it"})).unwrap()["id"].as_u64().unwrap();
        assert_eq!(s.map().node(NodeId(pt)).unwrap().label, "Stampante");
        s.execute("node.delete", &json!({"id": pt})).unwrap();
        let pc = s.execute("node.addShape", &json!({"kind": "net.pc", "x": 600, "y": 50})).unwrap()["id"].as_u64().unwrap();
        assert!(s.execute("node.addShape", &json!({"kind": "net.nope"})).is_err());
        let e = s.execute("edge.add", &json!({"from": r, "to": sw, "label": "1G"})).unwrap()["id"].as_u64().unwrap();
        s.execute("edge.setStyle", &json!({"id": e, "line": "dashed", "arrows": "both", "route": "orthogonal", "color": "#ff0000", "width": 3}))
            .unwrap();
        let st = s.map().edge(ideacraft_core::EdgeId(e)).unwrap().style;
        assert_eq!(
            (st.line, st.arrows, st.route, st.width),
            (ideacraft_core::LineStyle::Dashed, ideacraft_core::Arrows::Both, ideacraft_core::Route::Orthogonal, 3.0)
        );
        assert!(s.execute("edge.setStyle", &json!({"id": e, "route": "zigzag"})).is_err());
        s.execute("arrange.align", &json!({"ids": [r, sw, pc], "edge": "top"})).unwrap();
        assert!(s.map().nodes().all(|n| (n.pos.y - n.half_size().unwrap().1 - (0.0 - 46.0)).abs() < 0.01), "tops aligned to the highest");
        s.execute("arrange.distribute", &json!({"ids": [r, sw, pc], "axis": "x"})).unwrap();
        let xs: Vec<f32> = [r, sw, pc].iter().map(|id| s.map().node(NodeId(*id)).unwrap().pos.x).collect();
        assert!((xs[1] - xs[0] - (xs[2] - xs[1])).abs() < 0.01, "{xs:?}");
        s.execute("node.resize", &json!({"id": pc, "w": 200, "h": 120})).unwrap();
        assert_eq!(s.map().node(NodeId(pc)).unwrap().size, Some(ideacraft_core::Size::new(200.0, 120.0)));
        let copies = s.execute("edit.duplicate", &json!({"ids": [r]})).unwrap()["ids"].clone();
        assert_eq!(copies.as_array().unwrap().len(), 1);
        s.execute("node.setZ", &json!({"id": pc, "z": "front"})).unwrap();
        assert_eq!(s.map().nodes_in_draw_order().last().unwrap().id, NodeId(pc));
        s.execute("node.setKind", &json!({"id": pc, "kind": "flow.decision"})).unwrap();
        assert_eq!(s.map().node(NodeId(pc)).unwrap().shape, ideacraft_core::Shape::Diamond);
        s.execute("arrange.snap", &json!({"ids": [pc], "grid": 50})).unwrap();
        assert_eq!(s.map().node(NodeId(pc)).unwrap().pos.x % 50.0, 0.0);
        assert!(s.execute("map.stencils", &json!({"language": "it"})).unwrap().as_array().unwrap().len() > 30);
        s.execute("map.setMode", &json!({"mode": "map"})).unwrap();
        assert_eq!(s.map().mode(), ideacraft_core::Mode::Map);

        // Layers, groups and the network check.
        s.execute("node.setLayer", &json!({"ids": [r, sw], "layer": "Core"})).unwrap();
        assert_eq!(s.execute("map.layers", &json!({})).unwrap()[0]["nodes"], 2);
        s.execute("layer.setVisible", &json!({"name": "Core", "visible": false})).unwrap();
        assert!(!s.map().is_visible(NodeId(r)));
        assert!(s.execute("layer.setVisible", &json!({"name": "Core"})).is_err());
        s.execute("layer.rename", &json!({"name": "Core", "to": "Backbone"})).unwrap();
        s.execute("layer.setLocked", &json!({"name": "Backbone", "locked": true})).unwrap();
        assert!(s.map().is_locked(NodeId(r)));
        s.execute("layer.remove", &json!({"name": "Backbone"})).unwrap();
        assert!(s.map().layers().is_empty() && s.map().is_visible(NodeId(r)));
        let g = s.execute("group.create", &json!({"ids": [r, sw]})).unwrap()["group"].as_u64().unwrap();
        assert_eq!(s.map().group_members(NodeId(r)).len(), 2);
        assert!(s.execute("group.create", &json!({"ids": [r]})).is_err());
        s.execute("group.ungroup", &json!({"id": sw})).unwrap();
        assert_eq!(s.map().group_members(NodeId(r)).len(), 1);
        let _ = g;
        s.execute("node.setProps", &json!({"id": sw, "props": {"ip": "10.0.0.1", "mask": "255.255.255.0"}})).unwrap();
        let report = s.execute("net.validate", &json!({"language": "it"})).unwrap();
        assert_eq!(report["errors"], 1, "{report}");
        assert!(report["findings"][0]["message"].as_str().unwrap().contains("Indirizzo 10.0.0.1"));
    }
}
