//! Stencils: the shape library of the diagram mode. Every stencil is original artwork defined
//! here as strokes on a unit square, so the canvas and the SVG export draw the same thing.

use crate::{Rgb, Shape, Size};

/// A stencil's category, for the shape library.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Category {
    Basic,
    Flowchart,
    Network,
    Cloud,
    People,
    Uml,
    Bpmn,
}

impl Category {
    pub const ALL: [Self; 7] = [Self::Basic, Self::Flowchart, Self::Network, Self::Cloud, Self::People, Self::Uml, Self::Bpmn];

    pub const fn name(self) -> &'static str {
        match self {
            Self::Basic => "Basic shapes",
            Self::Flowchart => "Flowchart",
            Self::Network => "Network",
            Self::Cloud => "Cloud & servers",
            Self::People => "People & places",
            Self::Uml => "UML",
            Self::Bpmn => "BPMN",
        }
    }

    pub const fn name_it(self) -> &'static str {
        match self {
            Self::Basic => "Forme base",
            Self::Flowchart => "Diagramma di flusso",
            Self::Network => "Rete",
            Self::Cloud => "Cloud e server",
            Self::People => "Persone e luoghi",
            Self::Uml => "UML",
            Self::Bpmn => "BPMN",
        }
    }

    /// The id prefix of the category's stencils (`net.`, `uml.`…).
    pub const fn prefix(self) -> &'static str {
        match self {
            Self::Basic => "basic.",
            Self::Flowchart => "flow.",
            Self::Network => "net.",
            Self::Cloud => "cloud.",
            Self::People => "people.",
            Self::Uml => "uml.",
            Self::Bpmn => "bpmn.",
        }
    }
}

/// Strokes on a unit square (0,0 top-left … 1,1 bottom-right): polylines, circles and filled
/// polygons, drawn with the node's text colour.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Glyph {
    pub lines: &'static [&'static [(f32, f32)]],
    /// (cx, cy, r)
    pub circles: &'static [(f32, f32, f32)],
    pub fills: &'static [&'static [(f32, f32)]],
}

impl Glyph {
    pub const NONE: Self = Self { lines: &[], circles: &[], fills: &[] };

    pub const fn is_empty(&self) -> bool {
        self.lines.is_empty() && self.circles.is_empty() && self.fills.is_empty()
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Stencil {
    /// Stable id saved in files, e.g. `net.router`.
    pub id: &'static str,
    pub name: &'static str,
    pub name_it: &'static str,
    pub category: Category,
    pub shape: Shape,
    pub size: Size,
    pub color: Rgb,
    /// Drawn inside the shape above the label; empty for plain shapes.
    pub glyph: Glyph,
}

impl Stencil {
    pub fn name(&self, italian: bool) -> &'static str {
        if italian { self.name_it } else { self.name }
    }
}

const fn rgb(hex: u32) -> Rgb {
    Rgb((hex >> 16) as u8, (hex >> 8) as u8, hex as u8)
}

const WHITE: Rgb = rgb(0xffffff);
const BLUE: Rgb = rgb(0xdde9ff);
const GREEN: Rgb = rgb(0xd8f3e6);
const PEACH: Rgb = rgb(0xffe3d1);
const LILAC: Rgb = rgb(0xe6dcfb);
const YELLOW: Rgb = rgb(0xfff1b8);
const GREY: Rgb = rgb(0xeceef3);

#[allow(clippy::too_many_arguments)]
const fn plain(id: &'static str, name: &'static str, name_it: &'static str, category: Category, shape: Shape, w: f32, h: f32, color: Rgb) -> Stencil {
    Stencil { id, name, name_it, category, shape, size: Size { w, h }, color, glyph: Glyph::NONE }
}

const fn icon(id: &'static str, name: &'static str, name_it: &'static str, category: Category, color: Rgb, glyph: Glyph) -> Stencil {
    Stencil { id, name, name_it, category, shape: Shape::Rounded, size: Size { w: 112.0, h: 92.0 }, color, glyph }
}

const fn glyph(lines: &'static [&'static [(f32, f32)]], circles: &'static [(f32, f32, f32)]) -> Glyph {
    Glyph { lines, circles, fills: &[] }
}

// ---- network glyphs ---------------------------------------------------------------------------

const ROUTER: Glyph = Glyph {
    lines: &[
        &[(0.5, 0.2), (0.5, 0.8)],
        &[(0.2, 0.5), (0.8, 0.5)],
        &[(0.4, 0.28), (0.5, 0.18), (0.6, 0.28)],
        &[(0.4, 0.72), (0.5, 0.82), (0.6, 0.72)],
        &[(0.28, 0.4), (0.18, 0.5), (0.28, 0.6)],
        &[(0.72, 0.4), (0.82, 0.5), (0.72, 0.6)],
    ],
    circles: &[(0.5, 0.5, 0.46)],
    fills: &[],
};

const SWITCH: Glyph = glyph(
    &[
        &[(0.06, 0.3), (0.94, 0.3), (0.94, 0.7), (0.06, 0.7), (0.06, 0.3)],
        &[(0.2, 0.42), (0.5, 0.42)],
        &[(0.42, 0.36), (0.5, 0.42), (0.42, 0.48)],
        &[(0.5, 0.58), (0.8, 0.58)],
        &[(0.58, 0.52), (0.5, 0.58), (0.58, 0.64)],
    ],
    &[],
);

const FIREWALL: Glyph = glyph(
    &[
        &[(0.08, 0.18), (0.92, 0.18), (0.92, 0.82), (0.08, 0.82), (0.08, 0.18)],
        &[(0.08, 0.39), (0.92, 0.39)],
        &[(0.08, 0.61), (0.92, 0.61)],
        &[(0.36, 0.18), (0.36, 0.39)],
        &[(0.64, 0.18), (0.64, 0.39)],
        &[(0.22, 0.39), (0.22, 0.61)],
        &[(0.5, 0.39), (0.5, 0.61)],
        &[(0.78, 0.39), (0.78, 0.61)],
        &[(0.36, 0.61), (0.36, 0.82)],
        &[(0.64, 0.61), (0.64, 0.82)],
    ],
    &[],
);

const SERVER: Glyph = Glyph {
    lines: &[
        &[(0.22, 0.08), (0.78, 0.08), (0.78, 0.92), (0.22, 0.92), (0.22, 0.08)],
        &[(0.22, 0.36), (0.78, 0.36)],
        &[(0.22, 0.64), (0.78, 0.64)],
        &[(0.32, 0.22), (0.5, 0.22)],
        &[(0.32, 0.5), (0.5, 0.5)],
        &[(0.32, 0.78), (0.5, 0.78)],
    ],
    circles: &[(0.66, 0.22, 0.04), (0.66, 0.5, 0.04), (0.66, 0.78, 0.04)],
    fills: &[],
};

const PC: Glyph =
    glyph(&[&[(0.08, 0.14), (0.92, 0.14), (0.92, 0.66), (0.08, 0.66), (0.08, 0.14)], &[(0.5, 0.66), (0.5, 0.8)], &[(0.3, 0.84), (0.7, 0.84)]], &[]);

const LAPTOP: Glyph = glyph(
    &[&[(0.16, 0.2), (0.84, 0.2), (0.84, 0.66), (0.16, 0.66), (0.16, 0.2)], &[(0.04, 0.78), (0.96, 0.78), (0.9, 0.66), (0.1, 0.66), (0.04, 0.78)]],
    &[],
);

const PRINTER: Glyph = glyph(
    &[
        &[(0.28, 0.1), (0.72, 0.1), (0.72, 0.36), (0.28, 0.36), (0.28, 0.1)],
        &[(0.1, 0.36), (0.9, 0.36), (0.9, 0.72), (0.1, 0.72), (0.1, 0.36)],
        &[(0.28, 0.6), (0.72, 0.6), (0.72, 0.9), (0.28, 0.9), (0.28, 0.6)],
    ],
    &[(0.78, 0.46, 0.04)],
);

const DATABASE: Glyph = glyph(
    &[
        &[(0.2, 0.2), (0.2, 0.8)],
        &[(0.8, 0.2), (0.8, 0.8)],
        &[(0.2, 0.8), (0.26, 0.86), (0.4, 0.9), (0.6, 0.9), (0.74, 0.86), (0.8, 0.8)],
        &[(0.2, 0.2), (0.26, 0.26), (0.4, 0.3), (0.6, 0.3), (0.74, 0.26), (0.8, 0.2), (0.74, 0.14), (0.6, 0.1), (0.4, 0.1), (0.26, 0.14), (0.2, 0.2)],
        &[(0.2, 0.5), (0.26, 0.56), (0.4, 0.6), (0.6, 0.6), (0.74, 0.56), (0.8, 0.5)],
    ],
    &[],
);

const WIFI: Glyph = Glyph {
    lines: &[
        &[(0.14, 0.42), (0.26, 0.3), (0.4, 0.24), (0.6, 0.24), (0.74, 0.3), (0.86, 0.42)],
        &[(0.26, 0.56), (0.36, 0.47), (0.5, 0.43), (0.64, 0.47), (0.74, 0.56)],
        &[(0.38, 0.7), (0.5, 0.63), (0.62, 0.7)],
    ],
    circles: &[(0.5, 0.82, 0.05)],
    fills: &[&[(0.45, 0.82), (0.5, 0.77), (0.55, 0.82), (0.5, 0.87)]],
};

const INTERNET: Glyph = glyph(
    &[
        &[(0.06, 0.5), (0.94, 0.5)],
        &[(0.5, 0.06), (0.5, 0.94)],
        &[(0.5, 0.06), (0.34, 0.24), (0.3, 0.5), (0.34, 0.76), (0.5, 0.94)],
        &[(0.5, 0.06), (0.66, 0.24), (0.7, 0.5), (0.66, 0.76), (0.5, 0.94)],
        &[(0.14, 0.28), (0.86, 0.28)],
        &[(0.14, 0.72), (0.86, 0.72)],
    ],
    &[(0.5, 0.5, 0.44)],
);

const PHONE: Glyph = Glyph {
    lines: &[&[(0.3, 0.06), (0.7, 0.06), (0.7, 0.94), (0.3, 0.94), (0.3, 0.06)], &[(0.3, 0.2), (0.7, 0.2)], &[(0.3, 0.78), (0.7, 0.78)]],
    circles: &[(0.5, 0.86, 0.035)],
    fills: &[],
};

const STORAGE: Glyph = glyph(
    &[
        &[(0.1, 0.14), (0.9, 0.14), (0.9, 0.86), (0.1, 0.86), (0.1, 0.14)],
        &[(0.1, 0.38), (0.9, 0.38)],
        &[(0.1, 0.62), (0.9, 0.62)],
        &[(0.2, 0.26), (0.6, 0.26)],
        &[(0.2, 0.5), (0.6, 0.5)],
        &[(0.2, 0.74), (0.6, 0.74)],
    ],
    &[(0.76, 0.26, 0.04), (0.76, 0.5, 0.04), (0.76, 0.74, 0.04)],
);

const LOCK: Glyph = glyph(
    &[
        &[(0.2, 0.46), (0.8, 0.46), (0.8, 0.92), (0.2, 0.92), (0.2, 0.46)],
        &[(0.32, 0.46), (0.32, 0.28), (0.38, 0.14), (0.5, 0.08), (0.62, 0.14), (0.68, 0.28), (0.68, 0.46)],
        &[(0.5, 0.62), (0.5, 0.76)],
    ],
    &[],
);

const LOAD_BALANCER: Glyph = glyph(
    &[
        &[(0.5, 0.12), (0.5, 0.4)],
        &[(0.5, 0.4), (0.2, 0.4), (0.2, 0.66)],
        &[(0.5, 0.4), (0.5, 0.66)],
        &[(0.5, 0.4), (0.8, 0.4), (0.8, 0.66)],
        &[(0.14, 0.72), (0.26, 0.72)],
        &[(0.44, 0.72), (0.56, 0.72)],
        &[(0.74, 0.72), (0.86, 0.72)],
        &[(0.14, 0.66), (0.14, 0.88), (0.26, 0.88), (0.26, 0.66)],
        &[(0.44, 0.66), (0.44, 0.88), (0.56, 0.88), (0.56, 0.66)],
        &[(0.74, 0.66), (0.74, 0.88), (0.86, 0.88), (0.86, 0.66)],
    ],
    &[(0.5, 0.12, 0.07)],
);

const CLOUD_GLYPH: Glyph = glyph(
    &[&[
        (0.22, 0.76),
        (0.12, 0.7),
        (0.08, 0.58),
        (0.14, 0.46),
        (0.26, 0.42),
        (0.3, 0.3),
        (0.42, 0.22),
        (0.56, 0.24),
        (0.66, 0.34),
        (0.8, 0.36),
        (0.9, 0.46),
        (0.92, 0.6),
        (0.86, 0.72),
        (0.76, 0.76),
        (0.22, 0.76),
    ]],
    &[],
);

const VIRTUAL_MACHINE: Glyph = glyph(
    &[
        &[(0.1, 0.14), (0.9, 0.14), (0.9, 0.86), (0.1, 0.86), (0.1, 0.14)],
        &[(0.22, 0.3), (0.5, 0.3), (0.5, 0.56), (0.22, 0.56), (0.22, 0.3)],
        &[(0.5, 0.44), (0.78, 0.44), (0.78, 0.7), (0.5, 0.7), (0.5, 0.56)],
    ],
    &[],
);

const CONTAINER: Glyph = glyph(
    &[
        &[(0.1, 0.4), (0.5, 0.2), (0.9, 0.4), (0.9, 0.78), (0.5, 0.96), (0.1, 0.78), (0.1, 0.4)],
        &[(0.1, 0.4), (0.5, 0.58), (0.9, 0.4)],
        &[(0.5, 0.58), (0.5, 0.96)],
    ],
    &[],
);

const FUNCTION: Glyph =
    glyph(&[&[(0.34, 0.1), (0.2, 0.3), (0.34, 0.5), (0.2, 0.7), (0.34, 0.9)], &[(0.66, 0.1), (0.8, 0.3), (0.66, 0.5), (0.8, 0.7), (0.66, 0.9)]], &[]);

const QUEUE: Glyph = glyph(
    &[
        &[(0.06, 0.34), (0.94, 0.34), (0.94, 0.66), (0.06, 0.66), (0.06, 0.34)],
        &[(0.28, 0.34), (0.28, 0.66)],
        &[(0.5, 0.34), (0.5, 0.66)],
        &[(0.72, 0.34), (0.72, 0.66)],
    ],
    &[],
);

const USER: Glyph =
    Glyph { lines: &[&[(0.16, 0.92), (0.2, 0.72), (0.34, 0.6), (0.66, 0.6), (0.8, 0.72), (0.84, 0.92)]], circles: &[(0.5, 0.32, 0.2)], fills: &[] };

const TEAM: Glyph = Glyph {
    lines: &[
        &[(0.04, 0.9), (0.1, 0.72), (0.22, 0.64), (0.42, 0.64), (0.5, 0.72)],
        &[(0.5, 0.72), (0.58, 0.64), (0.78, 0.64), (0.9, 0.72), (0.96, 0.9)],
        &[(0.3, 0.92), (0.36, 0.8), (0.5, 0.74), (0.64, 0.8), (0.7, 0.92)],
    ],
    circles: &[(0.3, 0.42, 0.14), (0.7, 0.42, 0.14), (0.5, 0.56, 0.12)],
    fills: &[],
};

const BUILDING: Glyph = glyph(
    &[
        &[(0.18, 0.92), (0.18, 0.16), (0.82, 0.16), (0.82, 0.92)],
        &[(0.08, 0.92), (0.92, 0.92)],
        &[(0.3, 0.3), (0.42, 0.3)],
        &[(0.58, 0.3), (0.7, 0.3)],
        &[(0.3, 0.46), (0.42, 0.46)],
        &[(0.58, 0.46), (0.7, 0.46)],
        &[(0.3, 0.62), (0.42, 0.62)],
        &[(0.58, 0.62), (0.7, 0.62)],
        &[(0.42, 0.92), (0.42, 0.76), (0.58, 0.76), (0.58, 0.92)],
    ],
    &[],
);

const HOME: Glyph = glyph(
    &[
        &[(0.1, 0.5), (0.5, 0.14), (0.9, 0.5)],
        &[(0.22, 0.42), (0.22, 0.9), (0.78, 0.9), (0.78, 0.42)],
        &[(0.42, 0.9), (0.42, 0.64), (0.58, 0.64), (0.58, 0.9)],
    ],
    &[],
);

const CAMERA: Glyph = Glyph {
    lines: &[&[(0.06, 0.34), (0.66, 0.34), (0.66, 0.7), (0.06, 0.7), (0.06, 0.34)], &[(0.66, 0.46), (0.94, 0.34), (0.94, 0.7), (0.66, 0.58)]],
    circles: &[(0.36, 0.52, 0.09)],
    fills: &[],
};

#[allow(clippy::too_many_arguments)]
const fn sized(
    id: &'static str,
    name: &'static str,
    name_it: &'static str,
    category: Category,
    shape: Shape,
    w: f32,
    h: f32,
    color: Rgb,
    glyph: Glyph,
) -> Stencil {
    Stencil { id, name, name_it, category, shape, size: Size { w, h }, color, glyph }
}

// ---- network extras ------------------------------------------------------------------------------

const MODEM: Glyph = Glyph {
    lines: &[&[(0.06, 0.4), (0.94, 0.4), (0.94, 0.7), (0.06, 0.7), (0.06, 0.4)], &[(0.78, 0.4), (0.78, 0.18)], &[(0.86, 0.4), (0.86, 0.12)]],
    circles: &[(0.2, 0.55, 0.035), (0.32, 0.55, 0.035), (0.44, 0.55, 0.035)],
    fills: &[],
};

const TABLET: Glyph = Glyph {
    lines: &[
        &[(0.16, 0.08), (0.84, 0.08), (0.84, 0.92), (0.16, 0.92), (0.16, 0.08)],
        &[(0.24, 0.18), (0.76, 0.18), (0.76, 0.78), (0.24, 0.78), (0.24, 0.18)],
    ],
    circles: &[(0.5, 0.86, 0.03)],
    fills: &[],
};

const IOT: Glyph = Glyph {
    lines: &[
        &[(0.36, 0.36), (0.64, 0.36), (0.64, 0.64), (0.36, 0.64), (0.36, 0.36)],
        &[(0.5, 0.36), (0.5, 0.14)],
        &[(0.5, 0.64), (0.5, 0.86)],
        &[(0.36, 0.5), (0.14, 0.5)],
        &[(0.64, 0.5), (0.86, 0.5)],
    ],
    circles: &[(0.5, 0.14, 0.05), (0.5, 0.86, 0.05), (0.14, 0.5, 0.05), (0.86, 0.5, 0.05)],
    fills: &[],
};

const WAN: Glyph = glyph(
    &[
        &[
            (0.2, 0.7),
            (0.1, 0.62),
            (0.1, 0.5),
            (0.2, 0.42),
            (0.3, 0.3),
            (0.46, 0.26),
            (0.6, 0.3),
            (0.72, 0.4),
            (0.86, 0.44),
            (0.92, 0.56),
            (0.86, 0.68),
            (0.76, 0.72),
            (0.2, 0.72),
        ],
        &[(0.3, 0.84), (0.7, 0.84)],
        &[(0.34, 0.92), (0.66, 0.92)],
    ],
    &[],
);

const RACK: Glyph = glyph(
    &[
        &[(0.16, 0.06), (0.84, 0.06), (0.84, 0.94), (0.16, 0.94), (0.16, 0.06)],
        &[(0.16, 0.28), (0.84, 0.28)],
        &[(0.16, 0.5), (0.84, 0.5)],
        &[(0.16, 0.72), (0.84, 0.72)],
        &[(0.26, 0.17), (0.6, 0.17)],
        &[(0.26, 0.39), (0.6, 0.39)],
        &[(0.26, 0.61), (0.6, 0.61)],
        &[(0.26, 0.83), (0.6, 0.83)],
    ],
    &[(0.72, 0.17, 0.03), (0.72, 0.39, 0.03), (0.72, 0.61, 0.03), (0.72, 0.83, 0.03)],
);

const UPS: Glyph = Glyph {
    lines: &[
        &[(0.14, 0.14), (0.86, 0.14), (0.86, 0.86), (0.14, 0.86), (0.14, 0.14)],
        &[(0.56, 0.24), (0.38, 0.52), (0.52, 0.52), (0.44, 0.76), (0.64, 0.46), (0.5, 0.46), (0.56, 0.24)],
    ],
    circles: &[],
    fills: &[],
};

// ---- UML -----------------------------------------------------------------------------------------

const ACTOR: Glyph = Glyph {
    lines: &[&[(0.5, 0.3), (0.5, 0.62)], &[(0.22, 0.4), (0.78, 0.4)], &[(0.26, 0.92), (0.5, 0.62), (0.74, 0.92)]],
    circles: &[(0.5, 0.18, 0.12)],
    fills: &[],
};

const CLASS: Glyph = glyph(
    &[
        &[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9), (0.1, 0.1)],
        &[(0.1, 0.36), (0.9, 0.36)],
        &[(0.1, 0.64), (0.9, 0.64)],
        &[(0.2, 0.5), (0.6, 0.5)],
        &[(0.2, 0.77), (0.5, 0.77)],
    ],
    &[],
);

const INTERFACE: Glyph = Glyph { lines: &[&[(0.7, 0.5), (0.9, 0.5)]], circles: &[(0.5, 0.5, 0.2)], fills: &[] };

const COMPONENT: Glyph = glyph(
    &[
        &[(0.26, 0.14), (0.9, 0.14), (0.9, 0.86), (0.26, 0.86), (0.26, 0.14)],
        &[(0.1, 0.32), (0.4, 0.32), (0.4, 0.44), (0.1, 0.44), (0.1, 0.32)],
        &[(0.1, 0.56), (0.4, 0.56), (0.4, 0.68), (0.1, 0.68), (0.1, 0.56)],
    ],
    &[],
);

const PACKAGE: Glyph =
    glyph(&[&[(0.1, 0.3), (0.1, 0.9), (0.9, 0.9), (0.9, 0.3), (0.1, 0.3)], &[(0.1, 0.3), (0.1, 0.14), (0.44, 0.14), (0.5, 0.3)]], &[]);

const USE_CASE: Glyph = Glyph { lines: &[], circles: &[], fills: &[] };

const DEVICE_NODE: Glyph = glyph(
    &[
        &[(0.1, 0.3), (0.7, 0.3), (0.7, 0.9), (0.1, 0.9), (0.1, 0.3)],
        &[(0.1, 0.3), (0.3, 0.1), (0.9, 0.1), (0.9, 0.7), (0.7, 0.9)],
        &[(0.7, 0.3), (0.9, 0.1)],
    ],
    &[],
);

// ---- BPMN ----------------------------------------------------------------------------------------

const GATEWAY_X: Glyph = glyph(&[&[(0.34, 0.34), (0.66, 0.66)], &[(0.66, 0.34), (0.34, 0.66)]], &[]);
const GATEWAY_PLUS: Glyph = glyph(&[&[(0.5, 0.28), (0.5, 0.72)], &[(0.28, 0.5), (0.72, 0.5)]], &[]);
const GATEWAY_O: Glyph = Glyph { lines: &[], circles: &[(0.5, 0.5, 0.2)], fills: &[] };
const EVENT_TIMER: Glyph = Glyph { lines: &[&[(0.5, 0.5), (0.5, 0.28)], &[(0.5, 0.5), (0.66, 0.58)]], circles: &[(0.5, 0.5, 0.34)], fills: &[] };
const EVENT_MESSAGE: Glyph = glyph(&[&[(0.2, 0.3), (0.8, 0.3), (0.8, 0.7), (0.2, 0.7), (0.2, 0.3)], &[(0.2, 0.3), (0.5, 0.52), (0.8, 0.3)]], &[]);
const USER_TASK: Glyph =
    Glyph { lines: &[&[(0.3, 0.8), (0.34, 0.66), (0.5, 0.6), (0.66, 0.66), (0.7, 0.8)]], circles: &[(0.5, 0.42, 0.12)], fills: &[] };
const SERVICE_TASK: Glyph = Glyph {
    lines: &[&[(0.5, 0.24), (0.5, 0.34)], &[(0.5, 0.66), (0.5, 0.76)], &[(0.24, 0.5), (0.34, 0.5)], &[(0.66, 0.5), (0.76, 0.5)]],
    circles: &[(0.5, 0.5, 0.18), (0.5, 0.5, 0.07)],
    fills: &[],
};
const DATA_STORE: Glyph = glyph(
    &[
        &[(0.2, 0.26), (0.2, 0.76)],
        &[(0.8, 0.26), (0.8, 0.76)],
        &[(0.2, 0.76), (0.3, 0.86), (0.7, 0.86), (0.8, 0.76)],
        &[(0.2, 0.26), (0.3, 0.16), (0.7, 0.16), (0.8, 0.26), (0.7, 0.36), (0.3, 0.36), (0.2, 0.26)],
    ],
    &[],
);

/// Every stencil, by category.
pub const ALL: &[Stencil] = &[
    // Basic
    plain("basic.rect", "Rectangle", "Rettangolo", Category::Basic, Shape::Rect, 120.0, 60.0, WHITE),
    plain("basic.rounded", "Rounded rectangle", "Rettangolo arrotondato", Category::Basic, Shape::Rounded, 120.0, 60.0, WHITE),
    plain("basic.ellipse", "Ellipse", "Ellisse", Category::Basic, Shape::Ellipse, 120.0, 70.0, WHITE),
    plain("basic.circle", "Circle", "Cerchio", Category::Basic, Shape::Ellipse, 80.0, 80.0, WHITE),
    plain("basic.diamond", "Diamond", "Rombo", Category::Basic, Shape::Diamond, 120.0, 80.0, WHITE),
    plain("basic.hexagon", "Hexagon", "Esagono", Category::Basic, Shape::Hexagon, 120.0, 70.0, WHITE),
    plain("basic.parallelogram", "Parallelogram", "Parallelogramma", Category::Basic, Shape::Parallelogram, 130.0, 60.0, WHITE),
    plain("basic.cloud", "Cloud", "Nuvola", Category::Basic, Shape::Cloud, 140.0, 90.0, WHITE),
    plain("basic.note", "Sticky note", "Post-it", Category::Basic, Shape::Note, 120.0, 100.0, YELLOW),
    plain("basic.text", "Text", "Testo", Category::Basic, Shape::Text, 120.0, 40.0, WHITE),
    plain("basic.container", "Container", "Contenitore", Category::Basic, Shape::Container, 320.0, 220.0, GREY),
    // Flowchart
    plain("flow.start", "Start / End", "Inizio / Fine", Category::Flowchart, Shape::Pill, 120.0, 50.0, GREEN),
    plain("flow.process", "Process", "Processo", Category::Flowchart, Shape::Rect, 130.0, 60.0, BLUE),
    plain("flow.decision", "Decision", "Decisione", Category::Flowchart, Shape::Diamond, 140.0, 90.0, YELLOW),
    plain("flow.data", "Input / Output", "Input / Output", Category::Flowchart, Shape::Parallelogram, 140.0, 60.0, LILAC),
    plain("flow.document", "Document", "Documento", Category::Flowchart, Shape::Document, 130.0, 70.0, WHITE),
    plain("flow.database", "Database", "Database", Category::Flowchart, Shape::Cylinder, 110.0, 80.0, PEACH),
    plain("flow.subprocess", "Subprocess", "Sottoprocesso", Category::Flowchart, Shape::Subprocess, 140.0, 60.0, BLUE),
    plain("flow.connector", "Connector", "Connettore", Category::Flowchart, Shape::Ellipse, 44.0, 44.0, GREY),
    plain("flow.note", "Note", "Nota", Category::Flowchart, Shape::Note, 120.0, 80.0, YELLOW),
    // Network
    icon("net.router", "Router", "Router", Category::Network, BLUE, ROUTER),
    icon("net.switch", "Switch", "Switch", Category::Network, BLUE, SWITCH),
    icon("net.firewall", "Firewall", "Firewall", Category::Network, PEACH, FIREWALL),
    icon("net.server", "Server", "Server", Category::Network, GREY, SERVER),
    icon("net.pc", "Workstation", "Postazione", Category::Network, WHITE, PC),
    icon("net.laptop", "Laptop", "Portatile", Category::Network, WHITE, LAPTOP),
    icon("net.phone", "Phone", "Telefono", Category::Network, WHITE, PHONE),
    icon("net.printer", "Printer", "Stampante", Category::Network, WHITE, PRINTER),
    icon("net.wifi", "Access point", "Access point", Category::Network, GREEN, WIFI),
    icon("net.internet", "Internet", "Internet", Category::Network, LILAC, INTERNET),
    icon("net.storage", "Storage / NAS", "Storage / NAS", Category::Network, GREY, STORAGE),
    icon("net.vpn", "VPN / Security", "VPN / Sicurezza", Category::Network, PEACH, LOCK),
    icon("net.loadbalancer", "Load balancer", "Load balancer", Category::Network, GREEN, LOAD_BALANCER),
    icon("net.camera", "IP camera", "Telecamera IP", Category::Network, WHITE, CAMERA),
    // Cloud & servers
    icon("cloud.cloud", "Cloud service", "Servizio cloud", Category::Cloud, LILAC, CLOUD_GLYPH),
    icon("cloud.vm", "Virtual machine", "Macchina virtuale", Category::Cloud, BLUE, VIRTUAL_MACHINE),
    icon("cloud.container", "Container", "Container", Category::Cloud, BLUE, CONTAINER),
    icon("cloud.function", "Function", "Funzione", Category::Cloud, GREEN, FUNCTION),
    icon("cloud.database", "Database", "Database", Category::Cloud, PEACH, DATABASE),
    icon("cloud.queue", "Queue", "Coda", Category::Cloud, YELLOW, QUEUE),
    icon("cloud.storage", "Object storage", "Storage a oggetti", Category::Cloud, GREY, STORAGE),
    // People & places
    icon("people.user", "User", "Utente", Category::People, WHITE, USER),
    icon("people.team", "Team", "Team", Category::People, WHITE, TEAM),
    icon("people.office", "Office / Site", "Sede / Ufficio", Category::People, GREY, BUILDING),
    icon("people.home", "Home", "Casa", Category::People, WHITE, HOME),
    icon("net.modem", "Modem / ONT", "Modem / ONT", Category::Network, WHITE, MODEM),
    icon("net.wan", "WAN / MPLS", "WAN / MPLS", Category::Network, LILAC, WAN),
    icon("net.rack", "Rack", "Rack", Category::Network, GREY, RACK),
    icon("net.ups", "UPS", "UPS", Category::Network, YELLOW, UPS),
    icon("net.tablet", "Tablet", "Tablet", Category::Network, WHITE, TABLET),
    icon("net.iot", "IoT device", "Dispositivo IoT", Category::Network, GREEN, IOT),
    // UML
    sized("uml.actor", "Actor", "Attore", Category::Uml, Shape::Text, 80.0, 100.0, WHITE, ACTOR),
    sized("uml.usecase", "Use case", "Caso d'uso", Category::Uml, Shape::Ellipse, 150.0, 70.0, BLUE, USE_CASE),
    sized("uml.class", "Class", "Classe", Category::Uml, Shape::Rect, 140.0, 100.0, WHITE, CLASS),
    sized("uml.interface", "Interface", "Interfaccia", Category::Uml, Shape::Rounded, 120.0, 80.0, GREEN, INTERFACE),
    sized("uml.component", "Component", "Componente", Category::Uml, Shape::Rect, 130.0, 90.0, BLUE, COMPONENT),
    sized("uml.package", "Package", "Package", Category::Uml, Shape::Rect, 130.0, 90.0, YELLOW, PACKAGE),
    sized("uml.node", "Node / Device", "Nodo / Dispositivo", Category::Uml, Shape::Rect, 130.0, 90.0, GREY, DEVICE_NODE),
    plain("uml.state", "State", "Stato", Category::Uml, Shape::Rounded, 130.0, 60.0, LILAC),
    plain("uml.note", "Note", "Nota", Category::Uml, Shape::Note, 130.0, 80.0, YELLOW),
    // BPMN
    plain("bpmn.start", "Start event", "Evento di inizio", Category::Bpmn, Shape::Ellipse, 48.0, 48.0, GREEN),
    plain("bpmn.end", "End event", "Evento di fine", Category::Bpmn, Shape::Ellipse, 48.0, 48.0, PEACH),
    sized("bpmn.timer", "Timer event", "Evento temporale", Category::Bpmn, Shape::Ellipse, 48.0, 48.0, YELLOW, EVENT_TIMER),
    sized("bpmn.message", "Message event", "Evento messaggio", Category::Bpmn, Shape::Ellipse, 48.0, 48.0, YELLOW, EVENT_MESSAGE),
    plain("bpmn.task", "Task", "Attività", Category::Bpmn, Shape::Rounded, 130.0, 70.0, BLUE),
    sized("bpmn.usertask", "User task", "Attività utente", Category::Bpmn, Shape::Rounded, 130.0, 80.0, BLUE, USER_TASK),
    sized("bpmn.servicetask", "Service task", "Attività di servizio", Category::Bpmn, Shape::Rounded, 130.0, 80.0, BLUE, SERVICE_TASK),
    plain("bpmn.subprocess", "Subprocess", "Sottoprocesso", Category::Bpmn, Shape::Subprocess, 150.0, 80.0, BLUE),
    sized("bpmn.gateway", "Exclusive gateway", "Gateway esclusivo", Category::Bpmn, Shape::Diamond, 60.0, 60.0, YELLOW, GATEWAY_X),
    sized("bpmn.parallel", "Parallel gateway", "Gateway parallelo", Category::Bpmn, Shape::Diamond, 60.0, 60.0, YELLOW, GATEWAY_PLUS),
    sized("bpmn.inclusive", "Inclusive gateway", "Gateway inclusivo", Category::Bpmn, Shape::Diamond, 60.0, 60.0, YELLOW, GATEWAY_O),
    plain("bpmn.data", "Data object", "Oggetto dati", Category::Bpmn, Shape::Document, 90.0, 70.0, WHITE),
    sized("bpmn.store", "Data store", "Archivio dati", Category::Bpmn, Shape::Rounded, 90.0, 80.0, GREY, DATA_STORE),
    plain("bpmn.pool", "Pool / Lane", "Pool / Corsia", Category::Bpmn, Shape::Container, 520.0, 160.0, GREY),
];

pub fn find(id: &str) -> Option<&'static Stencil> {
    ALL.iter().find(|s| s.id.eq_ignore_ascii_case(id.trim()))
}

pub fn in_category(category: Category) -> impl Iterator<Item = &'static Stencil> {
    ALL.iter().filter(move |s| s.category == category)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stencils_are_unique_and_glyphs_stay_in_the_unit_square() {
        for (i, s) in ALL.iter().enumerate() {
            assert!(ALL.iter().take(i).all(|o| o.id != s.id), "duplicate {}", s.id);
            assert!(s.size.w >= 20.0 && s.size.h >= 20.0);
            for line in s.glyph.lines.iter().chain(s.glyph.fills) {
                assert!(line.len() >= 2 || line.is_empty(), "{}", s.id);
                for (x, y) in *line {
                    assert!((0.0..=1.0).contains(x) && (0.0..=1.0).contains(y), "{}: ({x}, {y})", s.id);
                }
            }
            for (cx, cy, r) in s.glyph.circles {
                assert!(cx - r >= -0.01 && cx + r <= 1.01 && cy - r >= -0.01 && cy + r <= 1.01, "{}", s.id);
            }
        }
        assert_eq!(find("NET.ROUTER").map(|s| s.name), Some("Router"));
        assert!(Category::ALL.iter().all(|c| in_category(*c).count() >= 4));
        assert!(ALL.iter().all(|s| s.id.starts_with(s.category.prefix())), "ids carry their category prefix");
    }
}
