//! FreeMind / Freeplane `.mm`: `<map><node TEXT=… ID=… FOLDED=… BACKGROUND_COLOR=…>` nested,
//! with `<arrowlink DESTINATION=…>` for cross links and `<richcontent TYPE="NODE">` for HTML text.

use crate::build::{self, Link, Topic};
use crate::xml::{self, Element};
use crate::{Error, Result};

fn topic(e: &Element, links: &mut Vec<Link>) -> Topic {
    let rich = e.children_named("richcontent").find(|r| r.attr("TYPE").is_none_or(|t| t.eq_ignore_ascii_case("NODE"))).map(Element::all_text);
    let label = e.attr("TEXT").map(str::to_string).or(rich).unwrap_or_default();
    let mut t = Topic::new(label);
    t.key = e.attr("ID").map(str::to_string);
    t.color = e.attr("BACKGROUND_COLOR").and_then(build::color);
    t.folded = e.attr("FOLDED").is_some_and(|f| f.eq_ignore_ascii_case("true"));
    if let Some(from) = &t.key {
        for a in e.children_named("arrowlink") {
            if let Some(to) = a.attr("DESTINATION") {
                let label = a.attr("MIDDLE_LABEL").or(a.attr("SOURCE_LABEL")).unwrap_or_default();
                links.push(Link { from: from.clone(), to: to.to_string(), label: label.to_string() });
            }
        }
    }
    t.children = e.children_named("node").map(|c| topic(c, links)).collect();
    t
}

pub fn import(text: &str) -> Result<ideacraft_core::Map> {
    let root = xml::parse(text)?;
    if !root.name.eq_ignore_ascii_case("map") {
        return Err(Error::Format("not a FreeMind/Freeplane map (no <map>)".into()));
    }
    let mut links = Vec::new();
    let roots: Vec<Topic> = root.children_named("node").map(|n| topic(n, &mut links)).collect();
    let title = roots.first().map(|r| r.label.clone()).unwrap_or_else(|| "Imported map".into());
    Ok(build::build(&title, roots, &links))
}

#[cfg(test)]
mod tests {
    #[test]
    fn reads_nodes_folding_colours_rich_text_and_arrow_links() {
        let mm = r##"<map version="freeplane 1.9.0">
<node TEXT="Garden" ID="ID_1" BACKGROUND_COLOR="#ccffcc">
  <node TEXT="Vegetables" ID="ID_2" POSITION="right" FOLDED="true">
    <node TEXT="Tomato" ID="ID_3"><arrowlink DESTINATION="ID_5" MIDDLE_LABEL="needs"/></node>
  </node>
  <node ID="ID_4" POSITION="left"><richcontent TYPE="NODE"><html><body><p>Herbs &amp; <b>spices</b></p></body></html></richcontent></node>
  <node TEXT="Water" ID="ID_5"/>
</node></map>"##;
        let m = super::import(mm).unwrap();
        assert_eq!(m.title(), "Garden");
        assert_eq!(m.node_count(), 5);
        let label = |l: &str| m.nodes().find(|n| n.label == l).unwrap();
        assert!(label("Vegetables").collapsed);
        assert_eq!(label("Garden").color.hex(), "#ccffcc");
        assert!(m.nodes().any(|n| n.label == "Herbs & spices"));
        let e = m.edges().next().unwrap();
        assert_eq!((e.from, e.to, e.label.as_str()), (label("Tomato").id, label("Water").id, "needs"));
        assert!(super::import("<opml/>").is_err());
    }
}
