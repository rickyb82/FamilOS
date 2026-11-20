//! Reading and writing other concept-map and mind-map formats, and IdeaCraft's built-in templates
//! (layer L2: depends only on `ideacraft-core`).
//!
//! Import: FreeMind / Freeplane (`.mm`), XMind (`.xmind`, both the current JSON and the legacy XML
//! layout), CmapTools (`.cxl`), OPML (`.opml`), Markdown / indented outlines (`.md`, `.txt`),
//! Mermaid (`mindmap` and simple `flowchart`/`graph`) and draw.io / diagrams.net (`.drawio`). Formats are read from their public
//! descriptions and our own test files; no third-party code is used.
//!
//! Export: Markdown outline, OPML, FreeMind `.mm`, draw.io and SVG, so maps travel to other tools.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

mod build;
mod cxl;
pub mod diagrams;
mod drawio;
mod export;
mod freemind;
mod markdown;
mod mermaid;
mod opml;
mod svg;
pub mod templates;
mod xmind;
mod xml;

pub use drawio::{export as to_drawio, import as from_drawio};
pub use export::{to_freemind, to_markdown, to_opml};
use ideacraft_core::Map;
pub use svg::{measure, measure_all, to_svg};

/// Largest file read, in bytes.
pub const MAX_FILE_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum Error {
    #[error("{0}")]
    Format(String),
    #[error("unsupported file: {0}")]
    Unsupported(String),
}

pub type Result<T> = std::result::Result<T, Error>;

/// The formats IdeaCraft reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    IdeaCraft,
    FreeMind,
    XMind,
    Cxl,
    Opml,
    Markdown,
    Mermaid,
    DrawIo,
}

impl Format {
    pub const ALL: [Self; 8] = [Self::IdeaCraft, Self::FreeMind, Self::XMind, Self::Cxl, Self::Opml, Self::Markdown, Self::Mermaid, Self::DrawIo];

    pub const fn name(self) -> &'static str {
        match self {
            Self::IdeaCraft => "IdeaCraft",
            Self::FreeMind => "FreeMind / Freeplane",
            Self::XMind => "XMind",
            Self::Cxl => "CmapTools (CXL)",
            Self::Opml => "OPML",
            Self::Markdown => "Markdown / text outline",
            Self::Mermaid => "Mermaid",
            Self::DrawIo => "draw.io / diagrams.net",
        }
    }

    /// File extensions, without the dot.
    pub const fn extensions(self) -> &'static [&'static str] {
        match self {
            Self::IdeaCraft => &["ideacraft", "json"],
            Self::FreeMind => &["mm"],
            Self::XMind => &["xmind"],
            Self::Cxl => &["cxl"],
            Self::Opml => &["opml"],
            Self::Markdown => &["md", "markdown", "txt"],
            Self::Mermaid => &["mmd", "mermaid"],
            Self::DrawIo => &["drawio", "dio"],
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        let n = name.trim().trim_start_matches('.').to_ascii_lowercase();
        Self::ALL.into_iter().find(|f| f.extensions().contains(&n.as_str()) || f.name().to_ascii_lowercase().starts_with(&n))
    }

    fn from_path(name: &str) -> Option<Self> {
        let lower = name.to_ascii_lowercase();
        if lower.ends_with(".ideacraft.json") {
            return Some(Self::IdeaCraft);
        }
        let ext = lower.rsplit_once('.').map(|(_, e)| e)?;
        Self::ALL.into_iter().find(|f| f.extensions().contains(&ext))
    }
}

/// Every extension IdeaCraft can open (for file dialogs).
pub fn open_extensions() -> Vec<&'static str> {
    Format::ALL.iter().flat_map(|f| f.extensions().iter().copied()).collect()
}

fn text_of(bytes: &[u8]) -> String {
    let bytes = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes);
    String::from_utf8_lossy(bytes).into_owned()
}

/// Guesses the format of a text document.
pub fn sniff(text: &str) -> Format {
    let t = text.trim_start();
    if t.starts_with('{') && t.contains("\"ideacraft\"") {
        return Format::IdeaCraft;
    }
    if t.starts_with('[') || t.starts_with('{') {
        return Format::XMind;
    }
    if t.starts_with('<') {
        let head: String = t.chars().take(4096).collect::<String>().to_ascii_lowercase();
        if head.contains("<opml") {
            return Format::Opml;
        }
        if head.contains("<mxfile") || head.contains("<mxgraphmodel") {
            return Format::DrawIo;
        }
        if head.contains("<cmap") {
            return Format::Cxl;
        }
        if head.contains("xmap-content") {
            return Format::XMind;
        }
        return Format::FreeMind;
    }
    let first = t.lines().find(|l| !l.trim().is_empty() && !l.trim_start().starts_with("%%")).unwrap_or_default().trim();
    if first == "mindmap" || first.starts_with("graph") || first.starts_with("flowchart") {
        return Format::Mermaid;
    }
    Format::Markdown
}

/// Reads a text document in `format`.
pub fn import_text(text: &str, format: Format) -> Result<Map> {
    match format {
        Format::IdeaCraft => Map::from_json(text).map_err(|e| Error::Format(e.to_string())),
        Format::FreeMind => freemind::import(text),
        Format::XMind => xmind::import_text(text),
        Format::Cxl => cxl::import(text),
        Format::Opml => opml::import(text),
        Format::Markdown => markdown::import(text),
        Format::Mermaid => mermaid::import(text),
        Format::DrawIo => drawio::import(text),
    }
}

/// Reads a file: the name's extension picks the format, the content decides when it is unknown.
pub fn import_bytes(name: &str, bytes: &[u8]) -> Result<(Map, Format)> {
    if bytes.len() > MAX_FILE_BYTES {
        return Err(Error::Unsupported(format!("larger than {} MB", MAX_FILE_BYTES / (1024 * 1024))));
    }
    if bytes.starts_with(b"PK") {
        return xmind::import_zip(bytes).map(|m| (m, Format::XMind));
    }
    let text = text_of(bytes);
    let format = Format::from_path(name).filter(|f| *f != Format::XMind).unwrap_or_else(|| sniff(&text));
    import_text(&text, format).map(|m| (m, format))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sniffs_and_picks_formats() {
        assert_eq!(sniff("<?xml version='1.0'?><opml version='2.0'>"), Format::Opml);
        assert_eq!(sniff("<map version=\"1.0.1\"><node TEXT=\"a\"/></map>"), Format::FreeMind);
        assert_eq!(sniff("<cmap xmlns=\"http://cmap.ihmc.us/xml/cmap/\">"), Format::Cxl);
        assert_eq!(sniff("mindmap\n  root((x))"), Format::Mermaid);
        assert_eq!(sniff("<mxfile host=\"x\"><diagram/></mxfile>"), Format::DrawIo);
        assert_eq!(Format::parse("drawio"), Some(Format::DrawIo));
        assert_eq!(sniff("# Title\n- a"), Format::Markdown);
        assert_eq!(sniff("[{\"rootTopic\":{}}]"), Format::XMind);
        assert_eq!(Format::parse("mm"), Some(Format::FreeMind));
        assert_eq!(Format::parse("opml"), Some(Format::Opml));
        let (m, f) = import_bytes("notes.txt", b"\xEF\xBB\xBFTopic\n  A\n  B").unwrap();
        assert_eq!(f, Format::Markdown);
        assert_eq!(m.node_count(), 3);
        assert!(import_bytes("x.xmind", b"PK\x03\x04garbage").is_err());
    }

    #[test]
    fn hostile_input_never_panics() {
        let samples: [&[u8]; 9] = [b"", b"<", b"<map><node", b"[", b"{", b"mindmap\n\t\t\t((", b"PK", b"\xff\xfe\x00", b"graph TD\n-->|x|"];
        for bytes in samples {
            for name in ["a.mm", "a.xmind", "a.cxl", "a.opml", "a.md", "a.mmd", "a.json", "a.drawio", "a"] {
                let _ = import_bytes(name, bytes);
            }
        }
    }
}
