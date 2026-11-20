//! IdeaCraft's MCP server (layer L6): the Model Context Protocol over stdio (newline-delimited
//! JSON-RPC 2.0), so AI agents can create, read, edit, lay out and export concept maps.
//!
//! Every edit goes through the engine's commands ([`Session::execute`]), so it is validated, can
//! be undone (`run_command` with `edit.undo`) and never leaves a half-applied map.
#![recursion_limit = "512"]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use std::io::{BufRead, Write};
use std::sync::Arc;

use ideacraft_engine::core::{Map, NodeId, stencil};
use ideacraft_engine::formats::{self, templates};
use ideacraft_engine::{COMMANDS, Session};
use roots::{Access, AutomationRoots};
use serde_json::{Value, json};

pub mod roots;

/// MCP protocol revision this server prefers.
pub const PROTOCOL_VERSION: &str = "2025-06-18";
/// Revisions it also speaks; a client asking for another gets [`PROTOCOL_VERSION`].
pub const SUPPORTED_VERSIONS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];
/// The open map as an outline with ids (`resources/read`).
pub const MAP_URI: &str = "ideacraft://map";
/// The open map as IdeaCraft JSON (`resources/read`).
pub const MAP_JSON_URI: &str = "ideacraft://map.json";
/// Longest request line read (bytes); longer lines are answered with an error.
const MAX_LINE: usize = 32 * 1024 * 1024;

const INSTRUCTIONS: &str = "NETWORK DIAGRAMS (schemi di rete): call network_guide first, then build_map with mode=diagram and net.* stencil kinds, then validate_network. Prompts: create_map (a map from a description), design_network (a checked network diagram), check_network. \
build_map creates a whole map in one call; validate_network checks ip/vlan/interfaces. IdeaCraft edits one map at a time: a mind/concept map (ideas in a tree plus labelled links) or, in diagram mode, a free diagram \
(network diagrams, flowcharts, architectures) made of stencil shapes (list_stencils: routers, switches, servers, clouds, flowchart symbols…) with sizes, \
properties (ip, vlan, status…) and styled connectors (style_link: orthogonal/dashed/arrows). Use add_shape for diagrams, add_idea/add_ideas for maps, \
align to tidy shapes, export drawio to hand the result to draw.io/Visio and svg to look at it. IdeaCraft edits one concept map at a time. Start with new_map (optionally from a template) or open_map, then add ideas \
with add_idea/add_ideas (children hang under a parent id), connect any two ideas with link_ideas (a labelled concept-map link), \
rearrange with layout, and read the result with get_map (an outline with ids) or export (svg to look at it). Save with save_map. \
run_command reaches every engine command (list_commands), including edit.undo and edit.redo.";

/// The server: one open map, the file it came from (path and the format it is saved in), and the
/// folders its tools may read and write ([`Server::confine`]; none: unconfined).
#[derive(Default)]
pub struct Server {
    session: Session,
    file: Option<(String, &'static str)>,
    roots: Option<Arc<AutomationRoots>>,
}

/// The format a file name asks for when saving: `ideacraft`, `markdown`, `opml`, `freemind` or `svg`.
fn format_for_path(path: &str) -> Option<&'static str> {
    let lower = path.to_ascii_lowercase();
    let by_ext = [
        (".ideacraft.json", "ideacraft"),
        (".json", "ideacraft"),
        (".md", "markdown"),
        (".markdown", "markdown"),
        (".txt", "markdown"),
        (".opml", "opml"),
        (".mm", "freemind"),
        (".svg", "svg"),
        (".drawio", "drawio"),
    ];
    by_ext.iter().find(|(e, _)| lower.ends_with(e)).map(|(_, f)| *f)
}

type ToolResult = Result<Value, String>;

fn tools() -> Value {
    let id = json!({"type": "integer", "minimum": 1});
    let color = json!({"type": "string", "description": "Fill colour, #rrggbb"});
    let shape = json!({"type": "string", "enum": ["rounded", "rect", "ellipse", "pill"]});
    let language = json!({"type": "string", "enum": ["en", "it"], "description": "Template language (default en)"});
    let template_ids: Vec<&str> = templates::ALL.iter().map(|t| t.id).collect();
    json!([
        {"name": "list_templates", "description": "List the built-in templates (id, name, category, description).",
         "inputSchema": {"type": "object", "properties": {"language": language}}},
        {"name": "new_map", "description": "Start a new map, empty or from a template. Replaces the open map.",
         "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "template": {"type": "string", "enum": template_ids}, "language": language}}},
        {"name": "open_map", "description": "Open a file: an IdeaCraft map or an import (FreeMind/Freeplane .mm, XMind .xmind, CmapTools .cxl, OPML, Markdown, Mermaid). .ideacraft.json, .mm, .opml and .md files are saved back in their own format. Only files inside the server's read root (--automation-read-root) can be opened.",
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "import_text", "description": "Replace the map with text in another format (Markdown outline, OPML, FreeMind XML, Mermaid mindmap/flowchart, CXL, XMind content.json). The format is detected when omitted.",
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "format": {"type": "string", "enum": ["md", "opml", "mm", "mermaid", "cxl", "xmind", "ideacraft"]}}, "required": ["text"]}},
        {"name": "save_map", "description": "Save the map. The format follows the extension (.ideacraft.json, .md, .opml, .mm, .drawio, .svg) unless given. Without path, saves back to the file it was opened from (a .mm stays FreeMind, an .opml stays OPML) or last saved to. Only inside the server's write root (--automation-write-root).",
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "format": {"type": "string", "enum": ["ideacraft", "markdown", "opml", "freemind", "drawio", "svg"]}}}},
        {"name": "get_map", "description": "Read the map: title, counts, an outline with each idea's [id], and the links. format=json returns the full IdeaCraft JSON (positions, colours).",
         "inputSchema": {"type": "object", "properties": {"format": {"type": "string", "enum": ["outline", "json"]}}}},
        {"name": "add_idea", "description": "Add one idea, optionally as a child of `parent`. Returns its id.",
         "inputSchema": {"type": "object", "properties": {"label": {"type": "string"}, "parent": id, "color": color, "shape": shape}, "required": ["label"]}},
        {"name": "add_ideas", "description": "Add several child ideas under `parent` (or as new roots without it). Returns their ids in order.",
         "inputSchema": {"type": "object", "properties": {"parent": id, "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 500}}, "required": ["labels"]}},
        {"name": "update_idea", "description": "Change an idea: text, colour, shape, collapsed, or move it under another parent (null makes it a root).",
         "inputSchema": {"type": "object", "properties": {"id": id, "label": {"type": "string"}, "color": color, "shape": shape, "collapsed": {"type": "boolean"}, "parent": {"type": ["integer", "null"]}}, "required": ["id"]}},
        {"name": "delete_idea", "description": "Delete an idea with its whole branch and the links touching it.",
         "inputSchema": {"type": "object", "properties": {"id": id}, "required": ["id"]}},
        {"name": "link_ideas", "description": "Link two ideas with an optional label (the linking phrase of a concept map, e.g. \"causes\"). Returns the link id.",
         "inputSchema": {"type": "object", "properties": {"from": id, "to": id, "label": {"type": "string"}}, "required": ["from", "to"]}},
        {"name": "unlink", "description": "Delete a link by its id.",
         "inputSchema": {"type": "object", "properties": {"id": id}, "required": ["id"]}},
        {"name": "layout", "description": "Arrange the whole map: tree (left to right) or radial (rings around each root).",
         "inputSchema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["tree", "radial"]}}, "required": ["kind"]}},
        {"name": "export", "description": "The map as text: markdown, opml, freemind (.mm XML), drawio (for draw.io / Visio), svg (a picture of the map) or ideacraft JSON.",
         "inputSchema": {"type": "object", "properties": {"format": {"type": "string", "enum": ["markdown", "opml", "freemind", "drawio", "svg", "ideacraft"]}}, "required": ["format"]}},
        {"name": "list_stencils", "description": "The shape library for diagrams: id, name, category (basic shapes, flowchart, network, cloud & servers, people & places), shape and default size.",
         "inputSchema": {"type": "object", "properties": {"language": language, "category": {"type": "string", "description": "Filter: basic, flowchart, network, cloud, people"}}}},
        {"name": "add_shape", "description": "Add a diagram shape from a stencil (see list_stencils), e.g. net.router, flow.decision, basic.container. Positions are map units (centre); shapes are about 110×90. Switches the map to diagram mode. Returns the id.",
         "inputSchema": {"type": "object", "properties": {"kind": {"type": "string"}, "label": {"type": "string"}, "language": language, "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"}, "h": {"type": "number"},
             "props": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Properties such as ip, vlan, model, owner, status (up|warning|down shows a light)"}}, "required": ["kind"]}},
        {"name": "style_link", "description": "Style a link: line solid|dashed|dotted, arrows none|end|start|both, route straight|orthogonal|curved, colour #rrggbb (null for default), width in px, and a new label.",
         "inputSchema": {"type": "object", "properties": {"id": id, "line": {"type": "string", "enum": ["solid", "dashed", "dotted"]}, "arrows": {"type": "string", "enum": ["none", "end", "start", "both"]},
             "route": {"type": "string", "enum": ["straight", "orthogonal", "curved"]}, "color": {"type": ["string", "null"]}, "width": {"type": "number"}, "label": {"type": "string"}}, "required": ["id"]}},
        {"name": "align", "description": "Tidy shapes: align several ids on an edge (left|right|top|bottom|centerX|centerY), distribute them along x or y, or snap them to the grid.",
         "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": id}, "edge": {"type": "string", "enum": ["left", "right", "top", "bottom", "centerX", "centerY"]},
             "distribute": {"type": "string", "enum": ["x", "y"]}, "snap": {"type": "boolean"}}, "required": ["ids"]}},
        {"name": "network_guide", "description": "How to draw a NETWORK DIAGRAM (schema di rete) or any Visio-style diagram from a plain-language request: the stencil kinds (net.router, net.switch, net.firewall, net.server, net.pc, net.wifi…), the properties that make it checkable (ip, gateway, vlan, interfaces), how to label links (VLAN 10, Gi0/1 - eth0), and a complete build_map example. Call it first when asked for a network, an infrastructure or an IT diagram.",
         "inputSchema": {"type": "object", "properties": {"language": language}}},
        {"name": "build_map", "description": "Build a whole mind map OR a diagram (NETWORK DIAGRAM / schema di rete, flowchart, architecture) in one call from a nested spec: {title?, mode?: map|diagram, layout?: tree|radial, nodes: [{label, kind?, color?, shape?, x?, y?, props?, children?: [...]}], links?: [{from, to, label?, route?, line?, arrows?}]}. `from`/`to` are node labels (or 'path' labels like 'Root/Branch') or ids returned later. For a network diagram use mode=diagram, a stencil `kind` per device (list_stencils / network_guide) and props like {\"ip\": \"192.168.10.5/24\", \"vlan\": \"10\"}; then call validate_network. Replaces the open map unless append=true. Returns the outline with ids.",
         "inputSchema": {"type": "object", "properties": {
             "title": {"type": "string"}, "mode": {"type": "string", "enum": ["map", "diagram"]}, "layout": {"type": "string", "enum": ["tree", "radial", "none"]}, "append": {"type": "boolean"},
             "nodes": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "kind": {"type": "string"}, "color": color, "shape": shape, "x": {"type": "number"}, "y": {"type": "number"},
                 "props": {"type": "object", "additionalProperties": {"type": "string"}}, "children": {"type": "array", "items": {"type": "object"}}}, "required": ["label"]}, "maxItems": 2000},
             "links": {"type": "array", "items": {"type": "object", "properties": {"from": {"type": ["string", "integer"]}, "to": {"type": ["string", "integer"]}, "label": {"type": "string"},
                 "route": {"type": "string", "enum": ["straight", "orthogonal", "curved"]}, "line": {"type": "string", "enum": ["solid", "dashed", "dotted"]}, "arrows": {"type": "string", "enum": ["none", "end", "start", "both"]}}, "required": ["from", "to"]}, "maxItems": 4000}},
             "required": ["nodes"]}},
        {"name": "validate_network", "description": "Check the diagram's network properties: IP syntax, duplicate addresses, network/broadcast addresses, hosts on one VLAN in different subnets, gateways outside their subnet, VLAN ids, interfaces named on links that the device does not declare, interfaces used twice, devices without an address, isolated devices. Returns findings with codes, messages and the ids involved.",
         "inputSchema": {"type": "object", "properties": {"language": language}}},
        {"name": "set_layer", "description": "Move shapes to a layer (created when missing; null for the default layer), and optionally show/hide or lock it. Hidden layers are not drawn; locked ones cannot be edited from the canvas.",
         "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": id}, "layer": {"type": ["string", "null"]}, "visible": {"type": "boolean"}, "locked": {"type": "boolean"}}, "required": ["layer"]}},
        {"name": "group", "description": "Group shapes so they select and move together (ungroup=true dissolves their groups).",
         "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": id, "minItems": 1}, "ungroup": {"type": "boolean"}}, "required": ["ids"]}},
        {"name": "set_mode", "description": "Switch the map between mind-map mode (tree layouts, concept links) and diagram mode (free shapes, snapping, styled connectors).",
         "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["map", "diagram"]}}, "required": ["mode"]}},
        {"name": "list_commands", "description": "Every engine command with its parameters, for run_command.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "run_command", "description": "Run any engine command by id (see list_commands), e.g. edit.undo, node.moveBy, selection.set.",
         "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "params": {"type": "object"}}, "required": ["command"]}}
    ])
}

/// Prompts: ready-made instructions that turn a request in natural language into tool calls.
fn prompts() -> Value {
    json!([
        {"name": "create_map", "title": "Create a map from a description",
         "description": "Describe a project, a topic or a process in plain language; the assistant builds the mind map, concept map or diagram with IdeaCraft's tools.",
         "arguments": [
            {"name": "description", "description": "What the map is about, in any language (e.g. 'piano di lancio di un prodotto', 'how the water cycle works')", "required": true},
            {"name": "kind", "description": "mindmap (default), concept-map, flowchart, network, architecture, org-chart", "required": false},
            {"name": "language", "description": "Language of the map's text: en or it (default: the language of the description)", "required": false},
            {"name": "save_as", "description": "A file path to save to (.ideacraft.json, .md, .mm, .opml, .drawio, .svg)", "required": false}]},
        {"name": "design_network", "title": "Design a network diagram",
         "description": "Describe a site, its devices, VLANs and addressing; the assistant draws the network with stencils, properties (ip, vlan, interfaces, status), labelled connectors and containers, then checks it.",
         "arguments": [
            {"name": "description", "description": "The site and what it needs (e.g. 'sede con 20 PC, 2 server, Wi-Fi ospiti, VLAN separate, firewall verso Internet')", "required": true},
            {"name": "addressing", "description": "Address plan or constraints (e.g. '192.168.10.0/24 server, 192.168.20.0/24 client, VLAN 10/20/30')", "required": false},
            {"name": "language", "description": "en or it", "required": false},
            {"name": "save_as", "description": "A file path to save to", "required": false}]},
        {"name": "check_network", "title": "Check the open network diagram",
         "description": "Validate IP addresses, subnets, VLANs, gateways and interfaces of the open diagram and explain the findings with fixes.",
         "arguments": [{"name": "language", "description": "en or it", "required": false}]}
    ])
}

fn prompt_text(name: &str, args: &Value) -> Option<(&'static str, String)> {
    let arg =
        |k: &str| args.get(k).and_then(Value::as_str).map(str::trim).filter(|v| !v.is_empty()).map(|v| v.chars().take(4000).collect::<String>());
    let lang = arg("language").unwrap_or_else(|| "the language of the description".into());
    let save = arg("save_as")
        .map(|p| format!("\n8. Save it with save_map to {p} (export too when the extension is not .ideacraft.json)."))
        .unwrap_or_else(|| "\n8. Do not save unless asked; report the outline.".into());
    match name {
        "create_map" => {
            let description = arg("description")?;
            let kind = arg("kind").unwrap_or_else(|| "mindmap".into());
            Some((
                "Build a map from a description",
                format!(
                    "Build an IdeaCraft map for this request (kind: {kind}; write every label in {lang}):\n\n{description}\n\n\
Work like this:\n\
1. Call list_templates; if a template fits, start from it with new_map {{template}} and edit, otherwise new_map with a short title.\n\
2. For a mind map or concept map: one root idea, 4–8 main branches, 2–5 sub-ideas each, short labels (2–6 words). Add the whole tree with build_map in one call. \
Add labelled links (link_ideas) for relations that cross branches (concept maps: a linking phrase such as 'causes', 'is part of').\n\
3. For a flowchart, architecture, network or org chart: set_mode diagram, then add_shape per element with the right stencil (list_stencils), real positions on a 20-px grid \
(left to right or top to bottom, about 200 px apart), link_ideas + style_link for connectors (orthogonal for flowcharts and networks, arrows where the flow has a direction, labels on decisions: Yes/No), \
basic.container for zones.\n\
4. Run layout (tree or radial) for maps; for diagrams use align to tidy rows and columns.\n\
5. Read it back with get_map and fix anything unclear or duplicated.\n\
6. If it is a network, run validate_network and fix every error.\n\
7. Finish with a short summary of the structure (3–6 lines) in {lang}.{save}"
                ),
            ))
        }
        "design_network" => {
            let description = arg("description")?;
            let addressing = arg("addressing").unwrap_or_else(|| {
                "choose a sensible private addressing plan (RFC 1918) with one subnet per VLAN and state it in the summary".into()
            });
            Some((
                "Design a network diagram",
                format!(
                    "Draw a network diagram in IdeaCraft for this site (labels in {lang}):\n\n{description}\n\nAddressing: {addressing}\n\n\
Work like this:\n\
1. new_map with the site's name, then set_mode diagram.\n\
2. One add_shape per device with the matching stencil (net.internet, net.firewall, net.router, net.switch, net.wifi, net.server, net.pc, net.laptop, net.phone, net.printer, net.storage, net.vpn, cloud.*). \
Give every device props: ip (with /prefix, e.g. 192.168.10.5/24), gateway, vlan; switches and routers an interfaces list such as \
'Gi0/1=192.168.10.1/24 vlan 10; Gi0/24 trunk 10,20'. Use status up/warning/down when known. Group devices of one room or zone in a basic.container.\n\
3. Lay devices on a 20-px grid: internet/firewall at the top, core switch below, server zone on one side, access switches and clients below (about 160 px apart).\n\
4. link_ideas between connected devices and style_link route orthogonal, arrows none; label each link with the VLAN and the interfaces at its ends, e.g. 'Gi0/2 ↔ eth0 VLAN 10'.\n\
5. Run validate_network; fix every error and warning (duplicate addresses, hosts on one VLAN in different subnets, gateways outside the subnet, unknown interfaces), then run it again.\n\
6. Read get_map and check the picture is tidy (align where needed).\n\
7. Summarise in {lang}: the addressing plan per VLAN, the devices per zone, and anything the request left open.{save}"
                ),
            ))
        }
        "check_network" => Some((
            "Check the open network diagram",
            format!(
                "Run validate_network on the open diagram (language {lang}). For each finding explain in one line what is wrong and the fix \
(the correct address, VLAN or interface). Apply fixes that are unambiguous with update_idea (props) or style_link (labels), then run validate_network again and report what remains."
            ),
        )),
        _ => None,
    }
}

/// The recipe for drawing a checkable network diagram, for models that only see tool descriptions.
fn network_guide(italian: bool) -> String {
    let example = r#"{"title": "Sede Cagliari", "mode": "diagram",
 "nodes": [
  {"label": "Internet", "kind": "net.internet", "x": 0, "y": 0},
  {"label": "Firewall", "kind": "net.firewall", "x": 0, "y": 160, "props": {"ip": "192.168.1.1/24", "interfaces": "wan=203.0.113.2/30; lan=192.168.1.1/24; vlan10=192.168.10.1/24; vlan20=192.168.20.1/24"}},
  {"label": "Switch core", "kind": "net.switch", "x": 0, "y": 320, "props": {"ip": "192.168.1.2/24", "gateway": "192.168.1.1", "vlans": "10,20", "interfaces": "Gi0/1 trunk 10,20; Gi0/2 vlan 10; Gi0/3 vlan 20"}},
  {"label": "Sala server", "kind": "basic.container", "x": 320, "y": 480, "w": 360, "h": 220},
  {"label": "File server", "kind": "net.server", "x": 250, "y": 500, "props": {"ip": "192.168.10.10/24", "gateway": "192.168.10.1", "vlan": "10", "status": "up"}},
  {"label": "Postazione 1", "kind": "net.pc", "x": -250, "y": 500, "props": {"ip": "192.168.20.21/24", "gateway": "192.168.20.1", "vlan": "20"}}
 ],
 "links": [
  {"from": "Internet", "to": "Firewall", "label": "WAN", "route": "orthogonal"},
  {"from": "Firewall", "to": "Switch core", "label": "lan - Gi0/1", "route": "orthogonal"},
  {"from": "Switch core", "to": "File server", "label": "VLAN 10 Gi0/2", "route": "orthogonal"},
  {"from": "Switch core", "to": "Postazione 1", "label": "VLAN 20 Gi0/3", "route": "orthogonal"}
 ]}"#;
    if italian {
        format!(
            "COME DISEGNARE UNO SCHEMA DI RETE CON IDEACRAFT

1. Usa build_map con mode=\"diagram\": un nodo per dispositivo con il `kind` dello stencil: net.internet, net.wan, net.router, net.firewall, net.switch, net.wifi (access point), net.server, net.storage (NAS), net.pc, net.laptop, net.phone, net.printer, net.vpn, net.loadbalancer, net.modem, net.rack, net.ups, net.iot, net.tablet; cloud.cloud, cloud.vm, cloud.database, cloud.queue…; basic.container per zone (sala server, uffici) con w/h. Posizioni x/y in una griglia da 160-200 unità, dall'alto (internet) verso il basso (postazioni).
2. Metti i dati nelle props: ip (10.0.1.5/24), gateway, vlan (10 oppure 10,20), interfaces (es. \"Gi0/1 trunk 10,20; Gi0/2 vlan 10; eth0=192.168.1.1/24\"), status (up|warning|down), e quello che serve (modello, owner).
3. Etichetta i collegamenti con la VLAN e le interfacce (\"VLAN 10 Gi0/2\", \"lan - Gi0/1\"); route orthogonal per i cavi, line dashed per VPN/Wi-Fi.
4. Dopo build_map chiama validate_network: segnala indirizzi non validi o duplicati, gateway fuori subnet, VLAN incoerenti, interfacce inesistenti o riusate, dispositivi isolati. Correggi con update_idea/run_command node.setProps e ripeti finché non ci sono errori.
5. Mostra la mappa con get_map; esporta con export drawio (per draw.io/Visio) o svg, oppure salva con save_map.

Esempio completo di build_map:
{example}"
        )
    } else {
        format!(
            "HOW TO DRAW A NETWORK DIAGRAM WITH IDEACRAFT

1. Call build_map with mode=\"diagram\": one node per device with the stencil `kind`: net.internet, net.wan, net.router, net.firewall, net.switch, net.wifi (access point), net.server, net.storage (NAS), net.pc, net.laptop, net.phone, net.printer, net.vpn, net.loadbalancer, net.modem, net.rack, net.ups, net.iot, net.tablet; cloud.cloud, cloud.vm, cloud.database, cloud.queue…; basic.container for zones (server room, offices) with w/h. Positions x/y on a 160-200 unit grid, top (internet) to bottom (workstations).
2. Put the data in props: ip (10.0.1.5/24), gateway, vlan (10 or 10,20), interfaces (e.g. \"Gi0/1 trunk 10,20; Gi0/2 vlan 10; eth0=192.168.1.1/24\"), status (up|warning|down), and whatever matters (model, owner).
3. Label links with the VLAN and the interfaces (\"VLAN 10 Gi0/2\", \"lan - Gi0/1\"); route orthogonal for cables, line dashed for VPN/Wi-Fi.
4. After build_map call validate_network: it reports invalid or duplicate addresses, gateways outside the subnet, VLAN mismatches, unknown or reused interfaces, isolated devices. Fix with update_idea/run_command node.setProps and repeat until there are no errors.
5. Show the map with get_map; export drawio (for draw.io/Visio) or svg, or save_map.

Complete build_map example:
{example}"
        )
    }
}

fn str_arg<'a>(args: &'a Value, name: &str) -> Option<&'a str> {
    args.get(name).and_then(Value::as_str)
}

fn id_arg(args: &Value, name: &str) -> Result<u64, String> {
    args.get(name).and_then(Value::as_u64).ok_or_else(|| format!("`{name}` must be an idea id (a positive integer)"))
}

/// Outline with ids, then links: what an agent needs to keep editing.
pub fn outline(map: &Map) -> String {
    let mut out = format!("# {}\n{} ideas, {} links\n\n", map.title(), map.node_count(), map.edge_count());
    let mut stack: Vec<(NodeId, usize)> = map.roots().into_iter().rev().map(|r| (r, 0)).collect();
    let mut seen = 0usize;
    while let Some((id, depth)) = stack.pop() {
        seen += 1;
        if seen > ideacraft_engine::core::limits::MAX_NODES {
            break;
        }
        let Some(n) = map.node(id) else { continue };
        let folded = if n.collapsed { " (collapsed)" } else { "" };
        let mut extra = Vec::new();
        if let Some(k) = &n.kind {
            extra.push(k.clone());
        }
        if n.shape != ideacraft_engine::core::Shape::Rounded {
            extra.push(n.shape.name().to_string());
        }
        if let Some(s) = n.size {
            extra.push(format!("{:.0}×{:.0} at ({:.0}, {:.0})", s.w, s.h, n.pos.x, n.pos.y));
        }
        for (k, v) in &n.props {
            extra.push(format!("{k}={v}"));
        }
        let extra = if extra.is_empty() { String::new() } else { format!(" {{{}}}", extra.join(", ")) };
        out.push_str(&format!("{}- [{}] {}{folded}{extra}\n", "  ".repeat(depth.min(64)), n.id.0, n.label.replace('\n', " ")));
        for kid in map.children(id).into_iter().rev() {
            stack.push((kid, depth + 1));
        }
    }
    if map.edge_count() > 0 {
        out.push_str("\nLinks:\n");
        for e in map.edges() {
            let label = if e.label.is_empty() { String::new() } else { format!(" \"{}\"", e.label) };
            let st = e.style;
            let style = if st == Default::default() {
                String::new()
            } else {
                format!(" ({}, {}, arrows {})", st.line.name(), st.route.name(), st.arrows.name())
            };
            out.push_str(&format!("- link {}: [{}] → [{}]{label}{style}\n", e.id.0, e.from.0, e.to.0));
        }
    }
    out
}

impl Server {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn map(&self) -> &Map {
        self.session.map()
    }

    fn exec(&mut self, command: &str, params: Value) -> ToolResult {
        self.session.execute(command, &params).map_err(|e| format!("{command}: {e}"))
    }

    fn render(&self, format: &str) -> Result<String, String> {
        let map = self.session.map();
        Ok(match format {
            "ideacraft" | "json" => map.to_json(),
            "markdown" | "md" => formats::to_markdown(map),
            "opml" => formats::to_opml(map),
            "freemind" | "mm" => formats::to_freemind(map),
            "svg" => formats::to_svg(map),
            "drawio" => formats::to_drawio(map),
            other => return Err(format!("unknown format {other:?}")),
        })
    }

    /// Runs one tool.
    pub fn call(&mut self, name: &str, args: &Value) -> ToolResult {
        match name {
            "list_templates" => {
                let language = str_arg(args, "language").unwrap_or("en");
                self.exec("map.templates", json!({"language": language}))
            }
            "new_map" => {
                if let Some(t) = str_arg(args, "template") {
                    self.exec("map.newFromTemplate", json!({"template": t, "language": str_arg(args, "language").unwrap_or("en")}))?;
                    if let Some(title) = str_arg(args, "title") {
                        self.exec("map.setTitle", json!({"title": title}))?;
                        self.session.mark_saved();
                    }
                } else {
                    self.exec("map.new", json!({"title": str_arg(args, "title").unwrap_or("Untitled map")}))?;
                }
                self.file = None;
                Ok(json!({"outline": outline(self.map())}))
            }
            "open_map" | "open" | "open_file" | "open_document" => {
                let path = str_arg(args, "path").or_else(|| str_arg(args, "file")).ok_or("`path` is required")?;
                self.check(path, Access::Read)?;
                let format = self.open_file(path)?;
                Ok(json!({"format": format, "path": path, "saves_back": self.file.is_some(), "outline": outline(self.map())}))
            }
            "import_text" => {
                let text = str_arg(args, "text").ok_or("`text` is required")?;
                let mut params = json!({"text": text});
                if let Some(f) = str_arg(args, "format") {
                    params["format"] = json!(f);
                }
                let r = self.exec("map.importText", params)?;
                self.file = None;
                Ok(json!({"format": r["format"], "outline": outline(self.map())}))
            }
            "save_map" | "save" | "save_file" | "save_document" => {
                let (path, source_format) = match str_arg(args, "path").or_else(|| str_arg(args, "file")) {
                    Some(p) => (p.to_string(), None),
                    None => self.file.clone().map(|(p, f)| (p, Some(f))).ok_or("`path` is required (the map has no file to save back to)")?,
                };
                let format = match str_arg(args, "format") {
                    Some(f) => f.to_string(),
                    None => source_format.or_else(|| format_for_path(&path)).unwrap_or("ideacraft").to_string(),
                };
                let text = self.render(&format)?;
                self.check(&path, Access::Write)?;
                std::fs::write(&path, text).map_err(|e| format!("{path}: {e}"))?;
                if format != "svg" {
                    self.session.mark_saved();
                    if let Some(f) = format_for_path(&path).filter(|f| *f == format.as_str() || (format == "json" && *f == "ideacraft")) {
                        self.file = Some((path.clone(), f));
                    }
                }
                Ok(json!({"saved": path, "format": format}))
            }
            "get_map" => match str_arg(args, "format").unwrap_or("outline") {
                "json" => Ok(json!({"json": self.map().to_json()})),
                _ => Ok(json!({"outline": outline(self.map())})),
            },
            "list_stencils" => {
                let italian = str_arg(args, "language") == Some("it");
                let category = str_arg(args, "category").map(|c| c.to_ascii_lowercase());
                Ok(Value::Array(
                    stencil::ALL
                        .iter()
                        .filter(|s| category.as_deref().is_none_or(|c| s.id.starts_with(c) || s.category.name().to_ascii_lowercase().starts_with(c)))
                        .map(|s| json!({"id": s.id, "name": s.name(italian), "category": if italian { s.category.name_it() } else { s.category.name() }, "shape": s.shape.name(), "w": s.size.w, "h": s.size.h}))
                        .collect(),
                ))
            }
            "add_shape" => {
                let mut params = json!({"kind": str_arg(args, "kind").ok_or("`kind` is required")?});
                for key in ["label", "x", "y", "props", "language"] {
                    if let Some(v) = args.get(key).filter(|v| !v.is_null()) {
                        params[key] = v.clone();
                    }
                }
                let id = self.exec("node.addShape", params)?["id"].as_u64().ok_or("no id returned")?;
                if let (Some(w), Some(h)) = (args.get("w").and_then(Value::as_f64), args.get("h").and_then(Value::as_f64)) {
                    self.exec("node.resize", json!({"id": id, "w": w, "h": h}))?;
                }
                Ok(json!({"id": id}))
            }
            "style_link" => {
                let id = id_arg(args, "id")?;
                let mut params = json!({"id": id});
                for key in ["line", "arrows", "route", "color", "width"] {
                    if let Some(v) = args.get(key) {
                        params[key] = v.clone();
                    }
                }
                if params.as_object().is_some_and(|o| o.len() > 1) {
                    self.exec("edge.setStyle", params)?;
                }
                if let Some(label) = str_arg(args, "label") {
                    self.exec("edge.setLabel", json!({"id": id, "label": label}))?;
                }
                Ok(json!({"id": id}))
            }
            "align" => {
                let ids = args.get("ids").cloned().ok_or("`ids` is required")?;
                if let Some(edge) = str_arg(args, "edge") {
                    self.exec("arrange.align", json!({"ids": ids, "edge": edge}))?;
                }
                if let Some(axis) = str_arg(args, "distribute") {
                    self.exec("arrange.distribute", json!({"ids": ids, "axis": axis}))?;
                }
                if args.get("snap").and_then(Value::as_bool) == Some(true) {
                    self.exec("arrange.snap", json!({"ids": ids}))?;
                }
                Ok(json!({"outline": outline(self.map())}))
            }
            "set_mode" => self.exec("map.setMode", json!({"mode": str_arg(args, "mode").unwrap_or_default()})),
            "validate_network" => self.exec("net.validate", json!({"language": str_arg(args, "language").unwrap_or("en")})),
            "set_layer" => {
                let layer = args.get("layer").cloned().unwrap_or(Value::Null);
                if let Some(ids) = args.get("ids").filter(|v| v.as_array().is_some_and(|a| !a.is_empty())) {
                    self.exec("node.setLayer", json!({"ids": ids, "layer": layer}))?;
                } else if let Some(name) = layer.as_str() {
                    self.exec("layer.add", json!({"name": name}))?;
                }
                if let (Some(name), Some(v)) = (layer.as_str(), args.get("visible").and_then(Value::as_bool)) {
                    self.exec("layer.setVisible", json!({"name": name, "visible": v}))?;
                }
                if let (Some(name), Some(l)) = (layer.as_str(), args.get("locked").and_then(Value::as_bool)) {
                    self.exec("layer.setLocked", json!({"name": name, "locked": l}))?;
                }
                self.exec("map.layers", json!({}))
            }
            "group" => {
                let ids = args.get("ids").cloned().ok_or("`ids` is required")?;
                if args.get("ungroup").and_then(Value::as_bool) == Some(true) {
                    self.exec("group.ungroup", json!({"ids": ids}))
                } else {
                    self.exec("group.create", json!({"ids": ids}))
                }
            }
            "network_guide" => {
                let italian = str_arg(args, "language").is_some_and(|l| l.eq_ignore_ascii_case("it"));
                Ok(json!({"guide": network_guide(italian)}))
            }
            "build_map" => {
                // Some clients wrap the whole spec in `spec`.
                let args = args.get("spec").filter(|s| s.is_object()).unwrap_or(args);
                let nodes = args.get("nodes").and_then(Value::as_array).ok_or("`nodes` must be a list")?;
                if args.get("append").and_then(Value::as_bool) != Some(true) {
                    self.exec("map.new", json!({"title": str_arg(args, "title").unwrap_or("Untitled map")}))?;
                    self.file = None;
                } else if let Some(t) = str_arg(args, "title") {
                    self.exec("map.setTitle", json!({"title": t}))?;
                }
                if let Some(m) = str_arg(args, "mode") {
                    self.exec("map.setMode", json!({"mode": m}))?;
                }
                let mut by_label: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
                let mut stack: Vec<(Value, Option<u64>, String, usize)> = nodes.iter().rev().map(|n| (n.clone(), None, String::new(), 0)).collect();
                let mut count = 0usize;
                while let Some((spec, parent, path, depth)) = stack.pop() {
                    count += 1;
                    if count > 2000 || depth > 64 {
                        break;
                    }
                    let label = spec.get("label").and_then(Value::as_str).unwrap_or("Idea");
                    let mut params = json!({"label": label});
                    if let Some(p) = parent {
                        params["parent"] = json!(p);
                    }
                    for key in ["x", "y", "kind"] {
                        if let Some(v) = spec.get(key).filter(|v| !v.is_null()) {
                            params[key] = v.clone();
                        }
                    }
                    let id = self.exec("node.add", params)?["id"].as_u64().ok_or("no id returned")?;
                    self.style(id, &spec)?;
                    if let (Some(w), Some(h)) = (spec.get("w").and_then(Value::as_f64), spec.get("h").and_then(Value::as_f64)) {
                        self.exec("node.resize", json!({"id": id, "w": w, "h": h}))?;
                    }
                    if let Some(props) = spec.get("props").filter(|p| p.is_object()) {
                        self.exec("node.setProps", json!({"id": id, "props": props}))?;
                    }
                    let full = if path.is_empty() { label.to_string() } else { format!("{path}/{label}") };
                    by_label.entry(label.to_string()).or_insert(id);
                    by_label.insert(full.clone(), id);
                    if let Some(kids) = spec.get("children").and_then(Value::as_array) {
                        for k in kids.iter().rev() {
                            stack.push((k.clone(), Some(id), full.clone(), depth + 1));
                        }
                    }
                }
                let mut missing = Vec::new();
                for l in args.get("links").and_then(Value::as_array).into_iter().flatten().take(4000) {
                    let find = |v: Option<&Value>| -> Option<u64> {
                        match v? {
                            Value::Number(n) => n.as_u64(),
                            Value::String(s) => by_label.get(s.trim()).copied(),
                            _ => None,
                        }
                    };
                    let (Some(a), Some(b)) = (find(l.get("from")), find(l.get("to"))) else {
                        missing.push(l.clone());
                        continue;
                    };
                    let e = self.exec("edge.add", json!({"from": a, "to": b, "label": l.get("label").and_then(Value::as_str).unwrap_or("")}))?["id"]
                        .clone();
                    let mut style = json!({"id": e});
                    for key in ["route", "line", "arrows", "color", "width"] {
                        if let Some(v) = l.get(key) {
                            style[key] = v.clone();
                        }
                    }
                    if style.as_object().is_some_and(|o| o.len() > 1) {
                        self.exec("edge.setStyle", style)?;
                    }
                }
                match str_arg(args, "layout") {
                    Some("none") => {}
                    Some("radial") => {
                        self.exec("layout.radial", json!({}))?;
                    }
                    _ if self.map().mode() == ideacraft_engine::core::Mode::Map => {
                        self.exec("layout.tree", json!({}))?;
                    }
                    _ => {}
                }
                Ok(json!({"outline": outline(self.map()), "unresolved_links": missing}))
            }
            "add_idea" => {
                let label = str_arg(args, "label").ok_or("`label` is required")?;
                let mut params = json!({"label": label});
                if args.get("parent").is_some_and(|p| !p.is_null()) {
                    params["parent"] = json!(id_arg(args, "parent")?);
                }
                let id = self.exec("node.add", params)?["id"].as_u64().ok_or("no id returned")?;
                self.style(id, args)?;
                Ok(json!({"id": id}))
            }
            "add_ideas" => {
                let labels = args.get("labels").and_then(Value::as_array).ok_or("`labels` must be a list of strings")?;
                let parent = if args.get("parent").is_some_and(|p| !p.is_null()) { Some(id_arg(args, "parent")?) } else { None };
                let mut ids = Vec::new();
                for label in labels.iter().take(500) {
                    let label = label.as_str().ok_or("`labels` must be a list of strings")?;
                    let mut params = json!({"label": label});
                    if let Some(p) = parent {
                        params["parent"] = json!(p);
                    }
                    ids.push(self.exec("node.add", params)?["id"].clone());
                }
                Ok(json!({"ids": ids}))
            }
            "update_idea" => {
                let id = id_arg(args, "id")?;
                if let Some(label) = str_arg(args, "label") {
                    self.exec("node.setLabel", json!({"id": id, "label": label}))?;
                }
                if let Some(c) = args.get("collapsed").and_then(Value::as_bool) {
                    self.exec("node.toggleCollapse", json!({"id": id, "collapsed": c}))?;
                }
                if let Some(parent) = args.get("parent") {
                    self.exec("node.setParent", json!({"id": id, "parent": parent}))?;
                }
                self.style(id, args)?;
                Ok(json!({"id": id}))
            }
            "delete_idea" => self.exec("node.delete", json!({"id": id_arg(args, "id")?})),
            "link_ideas" => self
                .exec("edge.add", json!({"from": id_arg(args, "from")?, "to": id_arg(args, "to")?, "label": str_arg(args, "label").unwrap_or("")})),
            "unlink" => self.exec("edge.delete", json!({"id": id_arg(args, "id")?})),
            "layout" => match str_arg(args, "kind") {
                Some("tree") => self.exec("layout.tree", json!({})),
                Some("radial") => self.exec("layout.radial", json!({})),
                _ => Err("`kind` must be tree or radial".into()),
            },
            "export" => {
                let format = str_arg(args, "format").ok_or("`format` is required")?;
                Ok(json!({"format": format, "text": self.render(format)?}))
            }
            "list_commands" => {
                Ok(Value::Array(COMMANDS.iter().map(|c| json!({"id": c.id, "label": c.label, "params": c.params, "undoable": c.undoable})).collect()))
            }
            "run_command" => {
                let command = str_arg(args, "command").ok_or("`command` is required")?;
                let params = args.get("params").cloned().unwrap_or_else(|| json!({}));
                self.exec(command, params)
            }
            other => Err(format!("unknown tool {other:?}")),
        }
    }

    /// From now on, `open_map` reads only inside `roots`' read root and `save_map` writes only
    /// inside its write root (with neither, no file by path). Files opened before (`--in`) are the
    /// operator's own.
    pub fn confine(&mut self, roots: Arc<AutomationRoots>) {
        self.roots = Some(roots);
    }

    /// May a tool read (or write) `path`? Always, when the server is not confined.
    fn check(&self, path: &str, access: Access) -> Result<(), String> {
        self.roots.as_ref().map_or(Ok(()), |r| r.check(path, access))
    }

    /// Opens a map or imports a file. Maps, FreeMind/Freeplane, OPML and Markdown files are then
    /// saved back to the same file in the same format; other imports need a path to save.
    pub fn open_file(&mut self, path: &str) -> Result<&'static str, String> {
        let len = std::fs::metadata(path).map_err(|e| format!("{path}: {e}"))?.len();
        if len > formats::MAX_FILE_BYTES as u64 {
            return Err(format!("{path}: larger than {} MB", formats::MAX_FILE_BYTES / (1024 * 1024)));
        }
        let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
        let (map, format) = formats::import_bytes(path, &bytes).map_err(|e| format!("{path}: {e}"))?;
        self.session.replace_map(map);
        let saves_as = match format {
            formats::Format::IdeaCraft => Some("ideacraft"),
            formats::Format::FreeMind => Some("freemind"),
            formats::Format::Opml => Some("opml"),
            formats::Format::Markdown => Some("markdown"),
            _ => None,
        };
        self.file = saves_as.map(|f| (path.to_string(), f));
        Ok(format.name())
    }

    fn style(&mut self, id: u64, args: &Value) -> Result<(), String> {
        if let Some(c) = str_arg(args, "color") {
            self.exec("node.setColor", json!({"id": id, "color": c}))?;
        }
        if let Some(s) = str_arg(args, "shape") {
            self.exec("node.setShape", json!({"id": id, "shape": s}))?;
        }
        Ok(())
    }

    /// Handles one JSON-RPC message; `None` for notifications.
    pub fn handle(&mut self, msg: &Value) -> Option<Value> {
        let id = msg.get("id").cloned()?;
        let method = msg.get("method").and_then(Value::as_str).unwrap_or_default();
        let params = msg.get("params").cloned().unwrap_or(Value::Null);
        let result = match method {
            "initialize" => Ok(json!({
                "protocolVersion": params.get("protocolVersion").and_then(Value::as_str).filter(|v| SUPPORTED_VERSIONS.contains(v)).unwrap_or(PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": false}, "resources": {"listChanged": false, "subscribe": false}, "prompts": {"listChanged": false}},
                "serverInfo": {"name": "ideacraft", "title": "IdeaCraft", "version": env!("CARGO_PKG_VERSION")},
                "instructions": INSTRUCTIONS,
            })),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({"tools": tools()})),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or_default();
                let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                Ok(match self.call(name, &args) {
                    Ok(v) => {
                        let text = match &v {
                            Value::Object(o) if o.len() == 1 && o.values().all(Value::is_string) => {
                                o.values().filter_map(Value::as_str).collect::<String>()
                            }
                            other => serde_json::to_string_pretty(other).unwrap_or_default(),
                        };
                        let structured = if v.is_object() { v } else { json!({"result": v}) };
                        json!({"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": false})
                    }
                    Err(e) => json!({"content": [{"type": "text", "text": e}], "isError": true}),
                })
            }
            "prompts/list" => Ok(json!({"prompts": prompts()})),
            "prompts/get" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or_default();
                let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
                match prompt_text(name, &args) {
                    Some((description, text)) => {
                        Ok(json!({"description": description, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}))
                    }
                    None => Err((-32602, format!("unknown prompt {:?}", name.chars().take(40).collect::<String>()))),
                }
            }
            "resources/list" => Ok(json!({"resources": [
                {"uri": MAP_URI, "name": "map", "title": "Open map (outline)", "description": "Title, ideas with their [id] and links", "mimeType": "text/markdown"},
                {"uri": MAP_JSON_URI, "name": "map.json", "title": "Open map (IdeaCraft JSON)", "description": "The full map: positions, colours, shapes, links", "mimeType": "application/json"},
            ]})),
            "resources/templates/list" => Ok(json!({"resourceTemplates": []})),
            "resources/read" => match params.get("uri").and_then(Value::as_str) {
                Some(MAP_URI) => Ok(json!({"contents": [{"uri": MAP_URI, "mimeType": "text/markdown", "text": outline(self.map())}]})),
                Some(MAP_JSON_URI) => Ok(json!({"contents": [{"uri": MAP_JSON_URI, "mimeType": "application/json", "text": self.map().to_json()}]})),
                _ => Err((-32002, "resource not found".to_string())),
            },
            _ => Err((-32601, format!("method not found: {}", method.chars().take(64).collect::<String>()))),
        };
        Some(match result {
            Ok(r) => json!({"jsonrpc": "2.0", "id": id, "result": r}),
            Err((code, message)) => json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),
        })
    }

    /// Serves until `input` ends: one JSON-RPC message per line in, one response per line out.
    pub fn serve(&mut self, input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
        for line in input.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let reply = if line.len() > MAX_LINE {
                Some(json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32600, "message": "request too large"}}))
            } else {
                match serde_json::from_str::<Value>(&line) {
                    Ok(Value::Array(batch)) => {
                        let replies: Vec<Value> = batch.iter().filter_map(|m| self.handle(m)).collect();
                        (!replies.is_empty()).then_some(Value::Array(replies))
                    }
                    Ok(msg) => self.handle(&msg),
                    Err(e) => Some(json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": format!("parse error: {e}")}})),
                }
            };
            if let Some(r) = reply {
                writeln!(output, "{r}")?;
                output.flush()?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn call(s: &mut Server, name: &str, args: Value) -> Value {
        let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
        assert_eq!(r["result"]["isError"], false, "{name}: {r}");
        r["result"]["structuredContent"].clone()
    }

    #[test]
    fn handshake_and_tool_list() {
        let mut s = Server::new();
        let init = s.handle(&json!({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})).unwrap();
        assert_eq!(init["result"]["serverInfo"]["name"], "ideacraft");
        assert!(s.handle(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).is_none(), "notifications get no reply");
        let list = s.handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})).unwrap();
        let tools = list["result"]["tools"].as_array().unwrap();
        assert!(tools.len() >= 25);
        let g = call(&mut s, "network_guide", json!({"language": "it"}));
        assert!(g["guide"].as_str().unwrap().contains("build_map"));
        for t in tools {
            assert_eq!(t["inputSchema"]["type"], "object", "{}", t["name"]);
            assert!(t["description"].as_str().is_some_and(|d| !d.is_empty()));
        }
        let missing = s.handle(&json!({"jsonrpc": "2.0", "id": 3, "method": "nope"})).unwrap();
        assert_eq!(missing["error"]["code"], -32601);
    }

    #[test]
    fn an_agent_builds_a_concept_map() {
        let mut s = Server::new();
        call(&mut s, "new_map", json!({"title": "Photosynthesis"}));
        let root = call(&mut s, "add_idea", json!({"label": "Photosynthesis", "shape": "pill", "color": "#ffe08a"}))["id"].as_u64().unwrap();
        let ids = call(&mut s, "add_ideas", json!({"parent": root, "labels": ["Light", "Water", "Carbon dioxide", "Glucose"]}))["ids"].clone();
        let (light, glucose) = (ids[0].as_u64().unwrap(), ids[3].as_u64().unwrap());
        call(&mut s, "link_ideas", json!({"from": light, "to": glucose, "label": "powers the making of"}));
        call(&mut s, "update_idea", json!({"id": glucose, "label": "Glucose (sugar)", "color": "#b1ecd8"}));
        call(&mut s, "layout", json!({"kind": "radial"}));
        let outline = call(&mut s, "get_map", json!({}))["outline"].as_str().unwrap().to_string();
        assert!(outline.contains(&format!("- [{root}] Photosynthesis")), "{outline}");
        assert!(outline.contains("  - ") && outline.contains("powers the making of"));
        let svg = call(&mut s, "export", json!({"format": "svg"}))["text"].as_str().unwrap().to_string();
        assert!(svg.contains("Glucose (sugar)"));
        call(&mut s, "run_command", json!({"command": "edit.undo"}));
        assert!(s.map().nodes().all(|n| n.label != "Glucose (sugar)") || s.map().node_count() == 5);

        let dir = std::env::temp_dir().join(format!("ideacraft-mcp-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("photo.ideacraft.json");
        call(&mut s, "save_map", json!({"path": file.to_str().unwrap()}));
        call(&mut s, "save_map", json!({"path": dir.join("photo.md").to_str().unwrap()}));
        let mut t = Server::new();
        let opened = call(&mut t, "open_map", json!({"path": dir.join("photo.md").to_str().unwrap()}));
        assert!(opened["outline"].as_str().unwrap().contains("Carbon dioxide"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn templates_imports_and_errors() {
        let mut s = Server::new();
        let list = call(&mut s, "list_templates", json!({"language": "it"}));
        assert!(list["result"].as_array().unwrap().iter().any(|t| t["name"] == "Analisi SWOT"));
        call(&mut s, "new_map", json!({"template": "swot", "language": "it"}));
        assert_eq!(s.map().title(), "Analisi SWOT");
        let r = call(&mut s, "import_text", json!({"text": "mindmap\n  root((Trip))\n    Pack\n    Route"}));
        assert_eq!(r["format"], "Mermaid");
        assert_eq!(s.map().node_count(), 3);
        for (name, args) in [
            ("add_idea", json!({})),
            ("delete_idea", json!({"id": 9999})),
            ("link_ideas", json!({"from": "x"})),
            ("layout", json!({"kind": "spiral"})),
            ("open_map", json!({"path": "/definitely/not/here.mm"})),
            ("save_map", json!({})),
            ("nope", json!({})),
        ] {
            let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
            assert_eq!(r["result"]["isError"], true, "{name} should fail cleanly: {r}");
        }
        assert_eq!(s.map().node_count(), 3, "failed tools change nothing");
    }

    #[test]
    fn an_agent_draws_a_network_diagram() {
        let mut s = Server::new();
        call(&mut s, "new_map", json!({"title": "Branch office"}));
        let stencils = call(&mut s, "list_stencils", json!({"category": "network", "language": "it"}));
        assert!(stencils["result"].as_array().unwrap().iter().all(|st| st["id"].as_str().unwrap().starts_with("net.")));
        let fw = call(
            &mut s,
            "add_shape",
            json!({"kind": "net.firewall", "label": "FW", "x": 0, "y": 0, "props": {"ip": "10.0.0.1", "status": "up"}}),
        )["id"]
            .as_u64()
            .unwrap();
        let sw = call(&mut s, "add_shape", json!({"kind": "net.switch", "label": "SW", "x": 300, "y": 0}))["id"].as_u64().unwrap();
        let pc = call(&mut s, "add_shape", json!({"kind": "net.pc", "x": 600, "y": 40, "w": 140, "h": 100}))["id"].as_u64().unwrap();
        let link = call(&mut s, "link_ideas", json!({"from": fw, "to": sw}))["id"].as_u64().unwrap();
        call(
            &mut s,
            "style_link",
            json!({"id": link, "route": "orthogonal", "line": "dashed", "arrows": "both", "color": "#ff0000", "width": 2, "label": "1 Gbit"}),
        );
        let out = call(&mut s, "align", json!({"ids": [fw, sw, pc], "edge": "top", "distribute": "x", "snap": true}))["outline"]
            .as_str()
            .unwrap()
            .to_string();
        assert!(out.contains("net.firewall") && out.contains("ip=10.0.0.1") && out.contains("dashed, orthogonal, arrows both"), "{out}");
        assert_eq!(s.map().mode(), ideacraft_engine::core::Mode::Diagram);
        let drawio = call(&mut s, "export", json!({"format": "drawio"}))["text"].as_str().unwrap().to_string();
        assert!(drawio.contains("<mxfile") && drawio.contains("orthogonalEdgeStyle"));
        let r = call(&mut s, "import_text", json!({"text": drawio}));
        assert_eq!(r["format"], "draw.io / diagrams.net");
        assert_eq!(s.map().node_count(), 3);
        call(&mut s, "set_mode", json!({"mode": "map"}));
        assert_eq!(s.map().mode(), ideacraft_engine::core::Mode::Map);
        for args in [json!({"kind": "net.nope"}), json!({})] {
            let r =
                s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "add_shape", "arguments": args}})).unwrap();
            assert_eq!(r["result"]["isError"], true);
        }
    }

    #[test]
    fn prompts_build_map_layers_groups_and_validation() {
        let mut s = Server::new();
        let list = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})).unwrap();
        assert_eq!(list["result"]["prompts"].as_array().unwrap().len(), 3);
        let p = s.handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "design_network", "arguments": {"description": "sede con 20 PC", "language": "it", "save_as": "/tmp/x.drawio"}}})).unwrap();
        let text = p["result"]["messages"][0]["content"]["text"].as_str().unwrap();
        assert!(text.contains("sede con 20 PC") && text.contains("validate_network") && text.contains("/tmp/x.drawio"));
        assert_eq!(
            s.handle(&json!({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "nope"}})).unwrap()["error"]["code"],
            -32602
        );
        assert_eq!(
            s.handle(&json!({"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "create_map", "arguments": {}}})).unwrap()["error"]
                ["code"],
            -32602
        );

        let out = call(
            &mut s,
            "build_map",
            json!({"title": "Launch", "nodes": [
            {"label": "Launch", "children": [
                {"label": "Marketing", "children": [{"label": "Ads"}, {"label": "Social"}]},
                {"label": "Product", "children": [{"label": "Beta"}]}]}],
            "links": [{"from": "Ads", "to": "Beta", "label": "promotes"}, {"from": "Launch/Product", "to": "Social"}, {"from": "Nope", "to": "Ads"}]}),
        );
        assert_eq!(s.map().node_count(), 6);
        assert_eq!(s.map().edge_count(), 2);
        assert_eq!(out["unresolved_links"].as_array().unwrap().len(), 1);
        assert!(out["outline"].as_str().unwrap().contains("promotes"));

        call(
            &mut s,
            "build_map",
            json!({"title": "Net", "mode": "diagram", "layout": "none", "nodes": [
            {"label": "R1", "kind": "net.router", "x": 0, "y": 0, "props": {"ip": "10.0.0.1/24", "interfaces": "eth0=10.0.0.1/24"}},
            {"label": "PC", "kind": "net.pc", "x": 300, "y": 0, "props": {"ip": "10.0.0.1/24", "gateway": "10.0.1.1"}}],
            "links": [{"from": "R1", "to": "PC", "label": "eth9 ↔ en0", "route": "orthogonal", "arrows": "none"}]}),
        );
        assert_eq!(s.map().mode(), ideacraft_engine::core::Mode::Diagram);
        let report = call(&mut s, "validate_network", json!({"language": "it"}));
        assert_eq!(report["errors"], 1, "{report}");
        let codes: Vec<&str> = report["findings"].as_array().unwrap().iter().map(|f| f["code"].as_str().unwrap()).collect();
        assert!(
            codes.contains(&"duplicate-address") && codes.contains(&"gateway-outside-subnet") && codes.contains(&"unknown-interface"),
            "{codes:?}"
        );
        let layers = call(&mut s, "set_layer", json!({"ids": [1], "layer": "Core", "locked": true}));
        assert_eq!(layers["result"][0]["locked"], true);
        assert_eq!(s.map().node(ideacraft_engine::core::NodeId(1)).unwrap().layer.as_deref(), Some("Core"));
        call(&mut s, "group", json!({"ids": [1, 2]}));
        assert_eq!(s.map().group_members(ideacraft_engine::core::NodeId(1)).len(), 2);
        call(&mut s, "group", json!({"ids": [1], "ungroup": true}));
        assert_eq!(s.map().group_members(ideacraft_engine::core::NodeId(1)).len(), 1);
    }

    #[test]
    fn opml_and_freemind_files_open_edit_and_save_back_in_their_format() {
        let dir = std::env::temp_dir().join(format!("ideacraft-mcp-files-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mm = dir.join("timeline.mm");
        std::fs::write(&mm, "<map version=\"1.0.1\">\n<node TEXT=\"Company 2007 → today\">\n<node TEXT=\"2007 · Start\" POSITION=\"right\">\n<node TEXT=\"Founded\"/>\n</node>\n<node TEXT=\"2008-2010\" POSITION=\"left\"/>\n</node>\n</map>\n").unwrap();
        let opml = dir.join("timeline.opml");
        std::fs::write(&opml, "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<opml version=\"2.0\"><head><title>Timeline</title></head><body>\n<outline text=\"Company\"><outline text=\"2007\"/></outline></body></opml>").unwrap();
        for (path, root_label) in [(&mm, "Company 2007 → today"), (&opml, "Company")] {
            let mut s = Server::new();
            let opened = call(&mut s, "open_file", json!({"path": path.to_str().unwrap()}));
            assert_eq!(opened["saves_back"], true);
            let root = s.map().roots()[0];
            assert_eq!(s.map().node(root).unwrap().label, root_label);
            call(&mut s, "add_idea", json!({"label": "Added by an agent", "parent": root.0}));
            call(&mut s, "save", json!({}));
            let mut t = Server::new();
            t.open_file(path.to_str().unwrap()).unwrap();
            assert!(t.map().nodes().any(|n| n.label == "Added by an agent"), "{path:?} saved back");
            let text = std::fs::read_to_string(path).unwrap();
            let expected = if path.extension().unwrap() == "mm" { "<map" } else { "<opml" };
            assert!(text.trim_start().starts_with(expected) || text.contains(expected), "{path:?} keeps its format");
        }
        let res = Server::new().handle(&json!({"jsonrpc": "2.0", "id": 9, "method": "resources/read", "params": {"uri": MAP_URI}})).unwrap();
        assert!(res["result"]["contents"][0]["text"].as_str().unwrap().starts_with("# "));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn serve_speaks_line_delimited_json_rpc() {
        let input = concat!(
            "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}\n",
            "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n",
            "not json\n",
            "\n",
            "[{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"ping\"}]\n",
        );
        let mut out = Vec::new();
        Server::new().serve(input.as_bytes(), &mut out).unwrap();
        let lines: Vec<Value> = String::from_utf8(out).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0]["id"], 1);
        assert_eq!(lines[1]["error"]["code"], -32700);
        assert_eq!(lines[2][0]["id"], 2);
    }

    /// `ideacraft-cli mcp --automation-read-root … --automation-write-root …`: a map the agent
    /// opens can't steer it into reading or writing files elsewhere (`..`, absolute paths, links
    /// out of the roots), and a refused save writes nothing.
    #[test]
    fn open_and_save_are_held_to_the_automation_roots() {
        let base = std::env::temp_dir().join(format!("ideacraft-mcp-roots-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        for d in ["in", "out", "outside"] {
            std::fs::create_dir_all(base.join(d)).unwrap();
        }
        std::fs::write(base.join("in/map.md"), "# Plan\n- Step").unwrap();
        std::fs::write(base.join("outside/secret.md"), "# Secret\n- top secret").unwrap();
        let p = |rel: &str| base.join(rel).to_string_lossy().into_owned();
        let sep = std::path::MAIN_SEPARATOR;
        let refused = |s: &mut Server, name: &str, args: Value| -> String {
            let r = s.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})).unwrap();
            assert_eq!(r["result"]["isError"], true, "{name} should fail: {r}");
            r["result"]["content"][0]["text"].as_str().unwrap_or_default().to_string()
        };
        let mut s = Server::new();
        s.confine(AutomationRoots::new(Some(&base.join("in")), Some(&base.join("out"))).unwrap());
        call(&mut s, "open_map", json!({"path": p("in/map.md")}));
        for path in [p("outside/secret.md"), format!("{}{sep}..{sep}outside{sep}secret.md", p("in")), "/etc/hostname".into()] {
            let e = refused(&mut s, "open_map", json!({"path": path}));
            assert!(e.contains("automation path rejected"), "{path}: {e}");
        }
        assert!(!call(&mut s, "get_map", json!({}))["outline"].to_string().contains("top secret"));
        // The map came from the read root: saving it back is a write there, refused.
        assert!(refused(&mut s, "save_map", json!({})).contains("outside the write root"));
        call(&mut s, "save_map", json!({"path": p("out/map.mm")}));
        for path in [p("outside/x.md"), format!("{}{sep}..{sep}outside{sep}x.svg", p("out")), "/tmp/../tmp/ideacraft-escape.md".into()] {
            let e = refused(&mut s, "save_map", json!({"path": path}));
            assert!(e.contains("automation path rejected"), "{path}: {e}");
        }
        if roots::tests::link(&base.join("outside/secret.md"), &base.join("in/leak.md"))
            && roots::tests::link(&base.join("outside"), &base.join("out/away"))
        {
            assert!(refused(&mut s, "open_map", json!({"path": p("in/leak.md")})).contains("outside the read root"));
            assert!(refused(&mut s, "save_map", json!({"path": p("out/away/x.md")})).contains("outside the write root"));
        }
        assert_eq!(std::fs::read_dir(base.join("outside")).unwrap().count(), 1, "nothing written outside");
        assert!(base.join("out/map.mm").is_file() && !std::path::Path::new("/tmp/ideacraft-escape.md").exists());
        // No roots at all (`ideacraft-cli mcp`'s default): no file by path.
        let mut t = Server::new();
        t.confine(AutomationRoots::new(None, None).unwrap());
        assert!(refused(&mut t, "open_map", json!({"path": p("in/map.md")})).contains("--automation-read-root"));
        assert!(refused(&mut t, "save_map", json!({"path": p("out/y.md")})).contains("--automation-write-root"));
        let _ = std::fs::remove_dir_all(base);
    }
}
