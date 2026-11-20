//! Interface languages. Labels are written in English in the code and looked up here; anything
//! without a translation shows in English. Messages with values use `{name}` placeholders.

mod it;

/// An interface language.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Language {
    #[default]
    En,
    It,
}

impl Language {
    pub const ALL: [Self; 2] = [Self::En, Self::It];

    /// The language's own name, shown in the switcher.
    pub const fn name(self) -> &'static str {
        match self {
            Self::En => "English",
            Self::It => "Italiano",
        }
    }

    /// Code saved in preferences and taken by `app.language.set`.
    pub const fn code(self) -> &'static str {
        match self {
            Self::En => "en",
            Self::It => "it",
        }
    }

    /// Reads a code or a locale tag (`it`, `it-IT`, `it_CH.UTF-8`).
    pub fn parse(tag: &str) -> Option<Self> {
        match tag.trim().split(['-', '_', '.']).next()?.to_ascii_lowercase().as_str() {
            "en" => Some(Self::En),
            "it" => Some(Self::It),
            _ => None,
        }
    }

    /// The system's language when supported, else English. The desktop app uses it on first run.
    pub fn system() -> Self {
        #[cfg(not(target_arch = "wasm32"))]
        {
            sys_locale::get_locale().and_then(|tag| Self::parse(&tag)).unwrap_or_default()
        }
        #[cfg(target_arch = "wasm32")]
        {
            Self::En
        }
    }

    /// The template language that matches.
    pub const fn templates(self) -> ideacraft_engine::formats::templates::Lang {
        match self {
            Self::En => ideacraft_engine::formats::templates::Lang::En,
            Self::It => ideacraft_engine::formats::templates::Lang::It,
        }
    }

    /// Translates an interface label.
    pub fn tr(self, text: &str) -> &str {
        match self {
            Self::En => text,
            Self::It => it::translate(text).unwrap_or(text),
        }
    }

    /// Translates a message and fills its `{name}` placeholders.
    pub fn fill(self, text: &str, values: &[(&str, &str)]) -> String {
        let mut out = self.tr(text).to_string();
        for (k, v) in values {
            out = out.replace(&format!("{{{k}}}"), v);
        }
        out
    }
}

/// Every label the interface translates (kept in step with the `it` table by a test).
pub const LABELS: &[&str] = &[
    "Map title",
    "New",
    "Templates…",
    "New map from a template",
    "Open…",
    "Open a map or import FreeMind, Freeplane, XMind, CmapTools, OPML, Markdown or Mermaid (Cmd+O)",
    "Save",
    "Save (Cmd+S)",
    "Export",
    "+ Idea",
    "+ Child",
    "+ Sibling",
    "Delete",
    "Collapse",
    "Link",
    "Drag from one idea to another to link them (or hold Shift while dragging)",
    "Tree",
    "Radial",
    "Fit",
    "Fit the map in the window (Cmd+0)",
    "Undo",
    "Redo",
    "Fill",
    "Shape",
    "Language",
    "rounded",
    "rect",
    "ellipse",
    "pill",
    "{ideas} ideas · {links} links · {selected} selected",
    "Double-click: new idea / edit · Tab: child · Enter: sibling · Shift+drag: link · Space: collapse",
    "New from template",
    "Pick a starting point. Everything stays editable.",
    "Markdown outline (.md)",
    "Opened {name}",
    "Imported {name} ({format})",
    "Couldn't open {name}: {error}",
    "file too large",
    "Exported {name}",
    "Couldn't export {name}: {error}",
    "Saved {name}",
    "Couldn't save {name}: {error}",
    "Untitled map",
    "Idea",
    "Maps and outlines",
    "All files",
    "IdeaCraft map",
    "Markdown / text outline",
    "select a node or give `id`",
    "File",
    "Open or import…",
    "Save as…",
    "Export as",
    "Unsaved changes",
    "Light theme",
    "Dark theme",
    "Templates",
    "Elements",
    "Layout",
    "Files",
    "Close",
    "Replace the current map? Unsaved changes will be lost.",
    "Replace",
    "Cancel",
    "Add",
    "New idea",
    "Child idea",
    "Same level",
    "Link ideas",
    "Select an idea to change its shape and colour.",
    "Colour",
    "Arrange the whole map",
    "Branches from left to right",
    "Ideas in a circle around the centre",
    "View",
    "Fit to window",
    "Collapse or expand branch",
    "Map",
    "Opens and imports",
    "Zoom out",
    "Zoom in",
    "Double-click anywhere to add an idea",
    "or start from a template",
    "Choose a template",
    "IdeaCraft (.ideacraft.json)",
    "Shapes",
    "Shape library",
    "Click a shape to add it. Drag it on the canvas, resize it from its corners, Shift+drag to connect.",
    "Search shapes",
    "Properties",
    "Size",
    "Auto",
    "Fit the text",
    "Remove",
    "name",
    "value",
    "Add property",
    "A `status` of up, warning or down shows a light on the shape.",
    "Align the selected shapes",
    "Align left",
    "Align centres",
    "Align right",
    "Align top",
    "Align middles",
    "Align bottom",
    "Distribute horizontally",
    "Distribute vertically",
    "Snap selection to grid",
    "Snap to grid while moving and resizing",
    "Snap to grid",
    "Bring to front",
    "Send to back",
    "Label",
    "Line",
    "Arrows",
    "Default colour",
    "Double-click to add a shape, or pick one from the library",
    "Mind map mode",
    "Diagram mode",
    "draw.io / diagrams.net (.drawio)",
    "SVG picture (.svg)",
    "diamond",
    "hexagon",
    "parallelogram",
    "cylinder",
    "document",
    "note",
    "cloud",
    "subprocess",
    "text",
    "container",
    "solid",
    "dashed",
    "dotted",
    "straight",
    "orthogonal",
    "curved",
    "none",
    "end",
    "start",
    "both",
    "Duplicate",
    "Delete Link",
    "Map / Diagram Mode",
    "Add Shape",
    "draw.io / diagrams.net",
    // Command labels, shown in tooltips.
    "New Map",
    "Add Idea",
    "Add Child Idea",
    "Add Sibling Idea",
    "Collapse/Expand Branch",
    "Tree Layout",
    "Radial Layout",
    // Layers, groups and the network check.
    "Layers",
    "Check",
    "Layers hold shapes. Hide a layer to declutter, lock it to keep its shapes out of the way.",
    "Default layer",
    "Select its shapes",
    "Move the selection to this layer",
    "Hide layer",
    "Show layer",
    "Unlock layer",
    "Lock layer",
    "Remove layer (its shapes go to the default layer)",
    "Rename layer",
    "New layer",
    "Layer name",
    "Add layer",
    "With shapes selected, the new layer takes them.",
    "Groups",
    "Grouped shapes select and move together.",
    "Checks the network shapes' properties: IP addresses and masks, gateways, VLANs and the interfaces on links.",
    "Check network",
    "Properties read: ip (10.0.1.5/24), mask, gateway, vlan; interfaces like Gi0/1=10.0.1.1/24 vlan 10; link labels like VLAN 10 or Gi0/1 - eth0.",
    "Result",
    "No problems found",
    "{errors} errors · {warnings} warnings · {infos} notes",
    "Network check: no problems found",
    "Network check: {errors} errors, {warnings} warnings, {infos} notes",
    "Select the shapes involved",
    "Invalid address",
    "Invalid mask",
    "Duplicate address",
    "Network address used as a host",
    "Broadcast address used as a host",
    "Subnet mismatch",
    "Gateway outside the subnet",
    "Invalid VLAN",
    "VLAN mismatch",
    "Unknown interface",
    "Interface used twice",
    "Duplicate interface",
    "No address",
    "Isolated device",
    "Note",
    "Group",
    "Ungroup",
    "Check Network",
    "Add Layer",
    "Remove Layer",
    "Rename Layer",
    "Show/Hide Layer",
    "Lock/Unlock Layer",
    "Move to Layer",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_label_has_an_italian_translation() {
        for (i, label) in LABELS.iter().enumerate() {
            assert!(LABELS.iter().take(i).all(|o| o != label), "duplicate {label}");
            assert!(it::translate(label).is_some_and(|t| !t.is_empty()), "missing Italian: {label}");
        }
        assert_eq!(Language::It.tr("Undo"), "Annulla");
        assert_eq!(Language::En.tr("Undo"), "Undo");
        assert_eq!(Language::It.tr("not a label"), "not a label");
        assert_eq!(Language::It.fill("Saved {name}", &[("name", "a.json")]), "Salvato a.json");
    }

    #[test]
    fn tags_reduce_to_a_language() {
        assert_eq!(Language::parse("it-IT"), Some(Language::It));
        assert_eq!(Language::parse("it_CH.UTF-8"), Some(Language::It));
        assert_eq!(Language::parse("en_US"), Some(Language::En));
        assert_eq!(Language::parse("de"), None);
        for l in Language::ALL {
            assert_eq!(Language::parse(l.code()), Some(l));
        }
    }
}
