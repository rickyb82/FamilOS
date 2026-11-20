//! Reading command parameters. Every value is hostile: wrong types and out-of-range numbers are
//! errors, never panics.

use ideacraft_core::{EdgeId, NodeId, Point};
use serde_json::Value;

use crate::{Error, Result};

fn param_err(name: &str, want: &str) -> Error {
    Error::Param(format!("`{name}` must be {want}"))
}

pub fn opt_str<'a>(p: &'a Value, name: &str) -> Result<Option<&'a str>> {
    match p.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(_) => Err(param_err(name, "a string")),
    }
}

pub fn str<'a>(p: &'a Value, name: &str) -> Result<&'a str> {
    opt_str(p, name)?.ok_or_else(|| param_err(name, "a string"))
}

pub fn opt_f32(p: &Value, name: &str) -> Result<Option<f32>> {
    match p.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v.as_f64().filter(|f| f.is_finite()).map(|f| Some(f as f32)).ok_or_else(|| param_err(name, "a number")),
    }
}

pub fn opt_point(p: &Value) -> Result<Option<Point>> {
    match (opt_f32(p, "x")?, opt_f32(p, "y")?) {
        (Some(x), Some(y)) => Ok(Some(Point::new(x, y).clamped())),
        (None, None) => Ok(None),
        _ => Err(Error::Param("give both `x` and `y`".into())),
    }
}

fn as_id(v: &Value, name: &str) -> Result<u64> {
    v.as_u64().ok_or_else(|| param_err(name, "a node or link id"))
}

pub fn opt_node(p: &Value, name: &str) -> Result<Option<NodeId>> {
    match p.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => as_id(v, name).map(|id| Some(NodeId(id))),
    }
}

pub fn node(p: &Value, name: &str) -> Result<NodeId> {
    opt_node(p, name)?.ok_or_else(|| param_err(name, "a node id"))
}

pub fn edge(p: &Value, name: &str) -> Result<EdgeId> {
    p.get(name).ok_or_else(|| param_err(name, "a link id")).and_then(|v| as_id(v, name)).map(EdgeId)
}

/// `ids: [..]` when given, else `id`, else the selection.
pub fn targets(p: &Value, selection: &[NodeId]) -> Result<Vec<NodeId>> {
    if let Some(v) = p.get("ids").filter(|v| !v.is_null()) {
        let list = v.as_array().ok_or_else(|| param_err("ids", "a list of node ids"))?;
        return list.iter().take(ideacraft_core::limits::MAX_NODES).map(|v| as_id(v, "ids").map(NodeId)).collect();
    }
    if let Some(id) = opt_node(p, "id")? {
        return Ok(vec![id]);
    }
    if selection.is_empty() {
        return Err(Error::Param("select a node or give `id`".into()));
    }
    Ok(selection.to_vec())
}

/// The single target: `id`, else the first selected node.
pub fn target(p: &Value, selection: &[NodeId]) -> Result<NodeId> {
    targets(p, selection)?.first().copied().ok_or_else(|| Error::Param("select a node or give `id`".into()))
}
