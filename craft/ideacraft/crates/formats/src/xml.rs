//! A tiny, bounded XML tree on top of quick-xml: enough for the map formats we read.

use quick_xml::Reader;
use quick_xml::events::{BytesStart, Event};

use crate::{Error, Result};

const MAX_ELEMENTS: usize = 400_000;
const MAX_DEPTH: usize = 256;

#[derive(Debug, Default, Clone)]
pub struct Element {
    /// Local name, without a namespace prefix.
    pub name: String,
    pub attrs: Vec<(String, String)>,
    pub children: Vec<Element>,
    /// Text directly inside this element.
    pub text: String,
}

impl Element {
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }

    pub fn child(&self, name: &str) -> Option<&Element> {
        self.children.iter().find(|c| c.name.eq_ignore_ascii_case(name))
    }

    pub fn children_named<'a>(&'a self, name: &'a str) -> impl Iterator<Item = &'a Element> + 'a {
        self.children.iter().filter(move |c| c.name.eq_ignore_ascii_case(name))
    }

    /// All text inside, depth first, whitespace collapsed.
    pub fn all_text(&self) -> String {
        let mut out = String::new();
        let mut stack = vec![self];
        while let Some(e) = stack.pop() {
            out.push(' ');
            out.push_str(&e.text);
            stack.extend(e.children.iter().rev());
        }
        out.split_whitespace().collect::<Vec<_>>().join(" ")
    }
}

fn local(name: &[u8]) -> String {
    let n = String::from_utf8_lossy(name);
    n.rsplit(':').next().unwrap_or_default().to_string()
}

fn element(start: &BytesStart) -> Element {
    let attrs = start
        .attributes()
        .flatten()
        .map(|a| {
            (
                local(a.key.as_ref()),
                a.normalized_value(quick_xml::XmlVersion::default())
                    .map(|v| v.into_owned())
                    .unwrap_or_else(|_| String::from_utf8_lossy(&a.value).into_owned()),
            )
        })
        .collect();
    Element { name: local(start.name().as_ref()), attrs, ..Element::default() }
}

/// Parses a document into its root element.
pub fn parse(text: &str) -> Result<Element> {
    let mut reader = Reader::from_str(text);
    reader.config_mut().trim_text(false);
    let mut stack: Vec<Element> = vec![Element::default()];
    let mut count = 0usize;
    loop {
        let event = reader.read_event().map_err(|e| Error::Format(format!("XML: {e}")))?;
        match event {
            Event::Start(s) => {
                count += 1;
                if count > MAX_ELEMENTS || stack.len() > MAX_DEPTH {
                    return Err(Error::Format("XML too large or too deep".into()));
                }
                stack.push(element(&s));
            }
            Event::Empty(s) => {
                count += 1;
                if count > MAX_ELEMENTS {
                    return Err(Error::Format("XML too large".into()));
                }
                if let Some(top) = stack.last_mut() {
                    top.children.push(element(&s));
                }
            }
            Event::End(_) => {
                if stack.len() > 1
                    && let Some(done) = stack.pop()
                    && let Some(top) = stack.last_mut()
                {
                    top.children.push(done);
                }
            }
            Event::Text(t) => {
                if let (Some(top), Ok(s)) = (stack.last_mut(), t.decode()) {
                    top.text.push_str(&s);
                }
            }
            Event::CData(t) => {
                if let Some(top) = stack.last_mut() {
                    top.text.push_str(&String::from_utf8_lossy(&t));
                }
            }
            Event::GeneralRef(r) => {
                let resolved = if r.is_char_ref() {
                    r.resolve_char_ref().ok().flatten().map(String::from)
                } else {
                    r.decode().ok().and_then(|n| match n.as_ref() {
                        "amp" => Some("&".to_string()),
                        "lt" => Some("<".to_string()),
                        "gt" => Some(">".to_string()),
                        "quot" => Some("\"".to_string()),
                        "apos" => Some("'".to_string()),
                        "nbsp" => Some(" ".to_string()),
                        _ => None,
                    })
                };
                if let (Some(top), Some(s)) = (stack.last_mut(), resolved) {
                    top.text.push_str(&s);
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    // Unclosed elements: fold them up so a truncated file still yields what it has.
    while stack.len() > 1 {
        if let Some(done) = stack.pop()
            && let Some(top) = stack.last_mut()
        {
            top.children.push(done);
        }
    }
    stack.pop().and_then(|doc| doc.children.into_iter().next()).ok_or_else(|| Error::Format("empty XML document".into()))
}

/// Escapes text for an attribute or element.
pub fn escape(text: &str) -> String {
    quick_xml::escape::escape(text).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_entities_namespaces_and_truncation() {
        let root = parse(r#"<?xml version="1.0"?><a:map x="1 &amp; 2"><node TEXT="caf&#233;">one &lt;two&gt;</node><open>"#).unwrap();
        assert_eq!(root.name, "map");
        assert_eq!(root.attr("X"), Some("1 & 2"));
        let node = root.child("node").unwrap();
        assert_eq!(node.attr("text"), Some("café"));
        assert_eq!(node.text, "one <two>");
        assert!(root.child("open").is_some());
        assert!(parse("").is_err());
        assert!(parse("<a>").is_ok());
        let deep = "<a>".repeat(MAX_DEPTH + 5);
        assert!(parse(&deep).is_err());
    }
}
