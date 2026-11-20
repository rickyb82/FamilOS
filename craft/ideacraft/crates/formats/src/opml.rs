//! OPML outlines: `<opml><head><title/></head><body><outline text=…>` nested.

use crate::build::{self, Topic};
use crate::xml::{self, Element};
use crate::{Error, Result};

fn topic(e: &Element) -> Topic {
    let label = e.attr("text").or(e.attr("title")).unwrap_or_default();
    let mut t = Topic::new(label);
    t.children = e.children_named("outline").map(topic).collect();
    t
}

pub fn import(text: &str) -> Result<ideacraft_core::Map> {
    let root = xml::parse(text)?;
    if !root.name.eq_ignore_ascii_case("opml") {
        return Err(Error::Format("not an OPML document".into()));
    }
    let title = root.child("head").and_then(|h| h.child("title")).map(|t| t.text.trim().to_string()).filter(|t| !t.is_empty());
    let body = root.child("body").ok_or_else(|| Error::Format("OPML without <body>".into()))?;
    let mut tops: Vec<Topic> = body.children_named("outline").map(topic).collect();
    let roots = if tops.len() == 1 {
        tops
    } else {
        let mut root = Topic::new(title.clone().unwrap_or_else(|| "Outline".into()));
        root.children = std::mem::take(&mut tops);
        vec![root]
    };
    let title = title.or_else(|| roots.first().map(|r| r.label.clone())).unwrap_or_default();
    Ok(build::build(&title, roots, &[]))
}

#[cfg(test)]
mod tests {
    #[test]
    fn several_top_outlines_hang_under_the_title() {
        let m = super::import(
            r#"<opml version="2.0"><head><title>Trip</title></head><body>
            <outline text="Pack"><outline text="Tent"/></outline><outline title="Route"/></body></opml>"#,
        )
        .unwrap();
        assert_eq!(m.node_count(), 4);
        let root = m.roots()[0];
        assert_eq!(m.node(root).unwrap().label, "Trip");
        assert_eq!(m.children(root).len(), 2);
    }
}
