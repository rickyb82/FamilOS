//! XMind `.xmind`: a zip holding `content.json` (XMind 2020 and later) or `content.xml` (older
//! versions). Only the first sheet is read.

use std::io::Read;

use serde_json::Value;

use crate::build::{self, Link, Topic};
use crate::xml::{self, Element};
use crate::{Error, MAX_FILE_BYTES, Result};

const MAX_DEPTH: usize = 256;

fn json_topic(v: &Value, depth: usize) -> Topic {
    let mut t = Topic::new(v.get("title").and_then(Value::as_str).unwrap_or_default());
    t.key = v.get("id").and_then(Value::as_str).map(str::to_string);
    t.folded = v.get("branch").and_then(Value::as_str) == Some("folded");
    t.color = v.pointer("/style/properties/svg:fill").and_then(Value::as_str).and_then(build::color);
    if depth < MAX_DEPTH {
        let kids = v.pointer("/children/attached").and_then(Value::as_array);
        t.children = kids.into_iter().flatten().map(|k| json_topic(k, depth + 1)).collect();
    }
    t
}

fn import_json(text: &str) -> Result<ideacraft_core::Map> {
    let v: Value = serde_json::from_str(text).map_err(|e| Error::Format(format!("XMind content.json: {e}")))?;
    let sheet = v.as_array().and_then(|a| a.first()).or(Some(&v)).filter(|s| s.get("rootTopic").is_some());
    let sheet = sheet.ok_or_else(|| Error::Format("XMind file without a sheet".into()))?;
    let root = sheet.get("rootTopic").map(|r| json_topic(r, 0)).unwrap_or_default();
    let mut roots = vec![root];
    let detached = sheet.pointer("/rootTopic/children/detached").and_then(Value::as_array);
    roots.extend(detached.into_iter().flatten().map(|d| json_topic(d, 1)));
    let links: Vec<Link> = sheet
        .get("relationships")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|r| {
            Some(Link {
                from: r.get("end1Id")?.as_str()?.to_string(),
                to: r.get("end2Id")?.as_str()?.to_string(),
                label: r.get("title").and_then(Value::as_str).unwrap_or_default().to_string(),
            })
        })
        .collect();
    let title = roots.first().map(|r| r.label.clone()).unwrap_or_default();
    Ok(build::build(&title, roots, &links))
}

fn xml_topic(e: &Element, depth: usize) -> Topic {
    let mut t = Topic::new(e.child("title").map(Element::all_text).unwrap_or_default());
    t.key = e.attr("id").map(str::to_string);
    t.folded = e.attr("branch") == Some("folded");
    if depth < MAX_DEPTH
        && let Some(children) = e.child("children")
    {
        let attached = children.children_named("topics").filter(|ts| ts.attr("type").is_none_or(|ty| ty == "attached"));
        t.children = attached.flat_map(|ts| ts.children_named("topic")).map(|k| xml_topic(k, depth + 1)).collect();
    }
    t
}

fn import_xml(text: &str) -> Result<ideacraft_core::Map> {
    let doc = xml::parse(text)?;
    let sheet = doc.child("sheet").ok_or_else(|| Error::Format("XMind content.xml without a sheet".into()))?;
    let root = sheet.child("topic").map(|t| xml_topic(t, 0)).unwrap_or_default();
    let links: Vec<Link> = sheet
        .child("relationships")
        .into_iter()
        .flat_map(|r| r.children_named("relationship"))
        .filter_map(|r| {
            Some(Link {
                from: r.attr("end1")?.to_string(),
                to: r.attr("end2")?.to_string(),
                label: r.child("title").map(Element::all_text).unwrap_or_default(),
            })
        })
        .collect();
    let title = root.label.clone();
    Ok(build::build(&title, vec![root], &links))
}

/// `content.json` or `content.xml` text.
pub fn import_text(text: &str) -> Result<ideacraft_core::Map> {
    if text.trim_start().starts_with('<') { import_xml(text) } else { import_json(text) }
}

fn read_entry(zip: &mut zip::ZipArchive<std::io::Cursor<&[u8]>>, name: &str) -> Option<String> {
    let entry = zip.by_name(name).ok()?;
    let mut text = String::new();
    entry.take(MAX_FILE_BYTES as u64).read_to_string(&mut text).ok()?;
    Some(text)
}

/// A whole `.xmind` file.
pub fn import_zip(bytes: &[u8]) -> Result<ideacraft_core::Map> {
    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(bytes)).map_err(|e| Error::Format(format!("XMind: {e}")))?;
    if let Some(json) = read_entry(&mut zip, "content.json") {
        return import_json(&json);
    }
    if let Some(xml) = read_entry(&mut zip, "content.xml") {
        return import_xml(&xml);
    }
    Err(Error::Format("XMind file without content.json or content.xml".into()))
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    #[test]
    fn reads_json_sheets_with_relationships_and_zip_files() {
        let json = r##"[{"id":"s","title":"Sheet 1","rootTopic":{"id":"r","title":"Launch","children":{"attached":[
            {"id":"a","title":"Marketing","branch":"folded","children":{"attached":[{"id":"a1","title":"Ads"}]}},
            {"id":"b","title":"Product","style":{"properties":{"svg:fill":"#B3D4FF"}}}]}},
            "relationships":[{"id":"x","end1Id":"a1","end2Id":"b","title":"promotes"}]}]"##;
        let m = super::import_text(json).unwrap();
        assert_eq!(m.node_count(), 4);
        assert_eq!(m.edge_count(), 1);
        assert!(m.nodes().any(|n| n.label == "Marketing" && n.collapsed));

        let mut buf = Vec::new();
        {
            let mut z = zip::ZipWriter::new(std::io::Cursor::new(&mut buf));
            z.start_file("content.json", zip::write::SimpleFileOptions::default()).unwrap();
            z.write_all(json.as_bytes()).unwrap();
            z.finish().unwrap();
        }
        let (m, f) = crate::import_bytes("launch.xmind", &buf).unwrap();
        assert_eq!((m.node_count(), f), (4, crate::Format::XMind));
    }

    #[test]
    fn reads_legacy_xml() {
        let xml = r#"<xmap-content><sheet><topic id="r"><title>Root</title><children><topics type="attached">
            <topic id="a"><title>A</title></topic><topic id="b"><title>B</title></topic></topics>
            <topics type="detached"><topic id="z"><title>Floating</title></topic></topics></children></topic>
            <relationships><relationship end1="a" end2="b"><title>then</title></relationship></relationships></sheet></xmap-content>"#;
        let m = super::import_text(xml).unwrap();
        assert_eq!(m.node_count(), 3);
        assert_eq!(m.edges().next().unwrap().label, "then");
    }
}
