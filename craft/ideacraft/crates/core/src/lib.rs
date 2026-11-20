//! IdeaCraft's document model (layer L0): a concept map of nodes joined by links.
//!
//! Every node may have one *parent* (the tree that layouts, collapsing and keyboard navigation
//! follow) and any number of free *links* to other nodes (labelled cross-connections, the
//! "concept" part of a concept map). Nothing here depends on the engine or a UI.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod color;
mod error;
pub mod layout;
mod map;
pub mod netcheck;
pub mod route;
pub mod stencil;

pub use color::Rgb;
pub use error::{Error, Result};
pub use map::{Arrows, Edge, EdgeId, EdgeStyle, Layer, LineStyle, MAX_LAYERS, Map, Mode, Node, NodeId, Point, Route, Shape, Size, Status};

/// Limits applied to every document, whoever built it: files, commands and the CLI are hostile.
pub mod limits {
    /// Most nodes a map can hold.
    pub const MAX_NODES: usize = 20_000;
    /// Most links a map can hold.
    pub const MAX_EDGES: usize = 40_000;
    /// Longest node or link label, in characters.
    pub const MAX_LABEL_CHARS: usize = 2_000;
    /// Deepest parent chain walked (cycles and absurd depths stop here).
    pub const MAX_DEPTH: usize = 512;
    /// Largest coordinate magnitude; positions are clamped into ±this.
    pub const MAX_COORD: f32 = 1.0e6;
    /// Explicit node sizes are clamped into this range.
    pub const MIN_SIZE: f32 = 16.0;
    pub const MAX_SIZE: f32 = 4_000.0;
    /// Most properties on one node.
    pub const MAX_PROPS: usize = 64;
    /// Longest property name or value, in characters.
    pub const MAX_PROP_CHARS: usize = 500;
}
