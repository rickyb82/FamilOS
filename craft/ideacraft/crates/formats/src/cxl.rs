//! CmapTools CXL: concepts and linking phrases joined by connections. A phrase between two
//! concepts ("plants —need→ water") becomes a labelled link; concepts keep their positions.

use std::collections::HashMap;

use ideacraft_core::{Point, Shape};

use crate::build::{self, Link, Topic};
use crate::xml::{self, Element};
use crate::{Error, Result};

fn list<'a>(map: &'a Element, list: &'a str, item: &'a str) -> impl Iterator<Item = &'a Element> + 'a {
    map.child(list).into_iter().flat_map(move |l| l.children_named(item))
}

pub fn import(text: &str) -> Result<ideacraft_core::Map> {
    let doc = xml::parse(text)?;
    if !doc.name.eq_ignore_ascii_case("cmap") {
        return Err(Error::Format("not a CmapTools CXL file".into()));
    }
    let map = doc.child("map").ok_or_else(|| Error::Format("CXL without <map>".into()))?;
    let title = doc.child("res-meta").and_then(|m| m.child("title")).map(Element::all_text).filter(|t| !t.is_empty());
    let mut looks: HashMap<&str, (Point, Option<ideacraft_core::Rgb>)> = HashMap::new();
    for a in list(map, "concept-appearance-list", "concept-appearance") {
        let num = |k: &str| a.attr(k).and_then(|v| v.trim().parse::<f32>().ok());
        if let (Some(id), Some(x), Some(y)) = (a.attr("id"), num("x"), num("y")) {
            let centre = Point::new(x + num("width").unwrap_or(0.0) / 2.0, y + num("height").unwrap_or(0.0) / 2.0);
            looks.insert(id, (centre, a.attr("background-color").and_then(build::color)));
        }
    }
    let mut concepts = Vec::new();
    for (i, c) in list(map, "concept-list", "concept").enumerate() {
        let Some(id) = c.attr("id") else { continue };
        let mut t = Topic::new(c.attr("label").unwrap_or_default().replace("\\n", "\n"));
        t.key = Some(id.to_string());
        t.shape = Some(Shape::Rounded);
        let (pos, color) = looks.get(id).copied().unwrap_or((Point::new((i % 6) as f32 * 220.0, (i / 6) as f32 * 120.0), None));
        t.pos = Some(pos);
        // Concepts without their own colour get a calm blue, as concept maps are usually one colour.
        t.color = color.or(Some(ideacraft_core::Rgb(0xdd, 0xe9, 0xff)));
        concepts.push(t);
    }
    let phrases: HashMap<&str, String> = list(map, "linking-phrase-list", "linking-phrase")
        .filter_map(|p| Some((p.attr("id")?, p.attr("label").unwrap_or_default().to_string())))
        .collect();
    let mut into: HashMap<&str, Vec<&str>> = HashMap::new();
    let mut out_of: HashMap<&str, Vec<&str>> = HashMap::new();
    let mut links = Vec::new();
    for c in list(map, "connection-list", "connection") {
        let (Some(from), Some(to)) = (c.attr("from-id"), c.attr("to-id")) else { continue };
        match (phrases.contains_key(from), phrases.contains_key(to)) {
            (false, true) => into.entry(to).or_default().push(from),
            (true, false) => out_of.entry(from).or_default().push(to),
            (false, false) => links.push(Link { from: from.into(), to: to.into(), label: String::new() }),
            (true, true) => {}
        }
    }
    for (phrase, label) in &phrases {
        for a in into.get(phrase).into_iter().flatten() {
            for b in out_of.get(phrase).into_iter().flatten() {
                links.push(Link { from: (*a).into(), to: (*b).into(), label: label.clone() });
            }
        }
    }
    links.sort_by(|a, b| (&a.from, &a.to).cmp(&(&b.from, &b.to)));
    let title = title.or_else(|| concepts.first().map(|c| c.label.clone())).unwrap_or_else(|| "Concept map".into());
    Ok(build::build(&title, concepts, &links))
}

#[cfg(test)]
mod tests {
    #[test]
    fn linking_phrases_become_labelled_links() {
        let cxl = r#"<?xml version="1.0"?><cmap xmlns="http://cmap.ihmc.us/xml/cmap/"><res-meta><title>Plants</title></res-meta><map>
            <concept-list><concept id="c1" label="Plants"/><concept id="c2" label="Water"/><concept id="c3" label="Light"/></concept-list>
            <linking-phrase-list><linking-phrase id="p1" label="need"/></linking-phrase-list>
            <connection-list><connection id="k1" from-id="c1" to-id="p1"/><connection id="k2" from-id="p1" to-id="c2"/>
              <connection id="k3" from-id="p1" to-id="c3"/></connection-list>
            <concept-appearance-list><concept-appearance id="c1" x="100" y="40" width="80" height="20" background-color="200,230,255,255"/></concept-appearance-list>
            </map></cmap>"#;
        let m = super::import(cxl).unwrap();
        assert_eq!(m.title(), "Plants");
        assert_eq!(m.node_count(), 3);
        assert_eq!(m.edge_count(), 2);
        assert!(m.edges().all(|e| e.label == "need"));
        let plants = m.nodes().find(|n| n.label == "Plants").unwrap();
        assert_eq!((plants.pos.x, plants.pos.y), (140.0, 50.0));
        assert_eq!(plants.color.hex(), "#c8e6ff");
    }
}
