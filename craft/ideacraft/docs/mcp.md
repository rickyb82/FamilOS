# MCP server

> **Last updated:** 2026-10-26

IdeaCraft speaks the [Model Context Protocol](https://modelcontextprotocol.io) so AI agents
(Claude Code, Claude Desktop and any other MCP client) can build and edit concept maps directly:
`ideacraft-cli mcp` serves MCP over stdio (newline-delimited JSON-RPC 2.0, protocol
`2025-06-18`). The server is `crates/mcp` (`ideacraft-mcp`); every edit goes through the engine's
commands, so it is validated and undoable, and a failed call never changes the map.

## Install and connect

```sh
cargo install --path apps/ideacraft-cli          # puts ideacraft-cli on your PATH
claude mcp add ideacraft -- ideacraft-cli mcp --automation-read-root ~/maps --automation-write-root ~/maps   # Claude Code
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "ideacraft": { "command": "ideacraft-cli",
  "args": ["mcp", "--automation-read-root", "/Users/me/maps", "--automation-write-root", "/Users/me/maps"] } } }
```

### Files the agent may read and write

A map the agent opens can carry instructions aimed at it (prompt injection). So the server
reaches the file system only inside two folders given at start: `open_map` reads only inside
`--automation-read-root`, `save_map` writes only inside `--automation-write-root`. Paths are made
absolute against the working directory and their links are followed before the comparison: `../`
out of a root, an absolute path elsewhere and a link that leads out are refused
(`automation path rejected: …`), and nothing is written. The two roots are separate (a write root
is not readable unless it is also inside the read root).

**Without the flags the server reads and writes no file by path** (`automation file access
refused: no read root …`); maps still come and go as text through `import_text` and `export`.
To give an agent your whole disk, say so: `--automation-read-root / --automation-write-root /`.
`--in FILE` is yours, not the agent's: it is opened before the roots apply (saving it back still
needs it inside the write root).

Start with a file, as with the other craft apps (`gridcraft-cli mcp --in …`):

```sh
ideacraft-cli mcp --in timeline.mm        # or an .opml, .md, .ideacraft.json, .xmind, .cxl …
```

`.ideacraft.json`, FreeMind/Freeplane `.mm`, `.opml` and `.md` files are **saved back in their
own format**: `save_map` without a path rewrites the file that was opened. XMind, CmapTools and
Mermaid files are read only; give `save_map` a path to save them.

Then ask, for example: *"Make a concept map of the water cycle in Italian, with labelled links
between the stages, lay it out radially and save it as ciclo.ideacraft.json and ciclo.svg."*

## Tools

| Tool | What it does |
|---|---|
| `list_templates` | The built-in templates (`language`: en, it) |
| `new_map` | New empty map, or from a `template` (with `language`, optional `title`) |
| `open_map` (also `open`, `open_file`) | Open an IdeaCraft map or import FreeMind/Freeplane, XMind, CmapTools CXL, OPML, Markdown, Mermaid |
| `import_text` | Replace the map with text in any importable format (detected when `format` is omitted) |
| `save_map` (also `save`, `save_file`) | Save; without a path, back to the opened file in its format; otherwise the extension decides (`.ideacraft.json`, `.md`, `.opml`, `.mm`, `.svg`) |
| `get_map` | Outline with each idea's `[id]` and the links, or the full JSON |
| `add_idea` / `add_ideas` | Add one idea (with colour, shape) or several children under a parent |
| `update_idea` | Text, colour, shape, collapsed, or move under another parent |
| `delete_idea` | Remove an idea and its branch |
| `link_ideas` / `unlink` | Labelled concept-map links between any two ideas |
| `layout` | `tree` or `radial` |
| `export` | `markdown`, `opml`, `freemind`, `drawio` (for draw.io / Visio), `svg` (a picture of the map) or `ideacraft` JSON |
| `list_stencils` | The shape library for diagrams (`category`: basic, flowchart, network, cloud, people, uml, bpmn; `language`) |
| `network_guide` | The recipe for a checkable network diagram (stencil kinds, properties, link labels, a complete `build_map` example), in English or Italian; for clients that do not pass the server instructions to the model |
| `build_map` | A whole map or diagram from one nested spec: `title`, `mode`, `layout`, `nodes` (label, kind, colour, shape, x/y, props, children…) and `links` (from/to by label, path or id, with label and style); `append` adds to the open map. Returns the outline and any unresolved links |
| `validate_network` | Checks the diagram's addresses, masks, gateways, VLANs and interfaces (`language`): counts and findings with codes, messages and the shapes/links involved |
| `set_layer` | Move shapes to a layer (`layer`, `null` for the default; created when missing) and/or show, hide, lock or unlock it (`visible`, `locked`); returns the layers |
| `group` | Group the given shapes so they select and move together, or `ungroup` them |
| `add_shape` | A diagram shape from a stencil (`kind`, e.g. `net.router`, `flow.decision`, `basic.container`) with label, position, size and `props` (ip, vlan, status…); switches to diagram mode |
| `style_link` | Line (solid/dashed/dotted), arrows (none/end/start/both), route (straight/orthogonal/curved), colour, width, label of a link |
| `align` | Align several shapes on an edge, distribute them along x or y, snap them to the grid |
| `set_mode` | `map` (mind map) or `diagram` |
| `list_commands` / `run_command` | Every engine command, e.g. `edit.undo`, `node.moveBy` |

Resources: `ideacraft://map` (the outline with ids, Markdown) and `ideacraft://map.json` (the
full map). Protocol revisions 2025-06-18, 2025-03-26 and 2024-11-05 are accepted.

## Prompts (natural language → map)

`prompts/list` offers three prompts an MCP client can run (Claude Desktop shows them as slash
commands); each one is a short procedure the model follows with the tools above.

| Prompt | Arguments | What it does |
|---|---|---|
| `create_map` | `description` (required), `kind` (`auto`, `map`, `diagram`), `language`, `save_as` | From a plain-language brief, decides between a mind map and a diagram, picks a template when one fits, builds the whole map with `build_map`, lays it out, shows the outline and optionally saves it |
| `design_network` | `description` (required), `addressing` (optional sites/subnets/VLANs), `language`, `save_as` | Draws a network: layer-3 devices, switches, zones as containers, hosts with `ip`, `gateway`, `vlan`, `status`, interfaces on the network devices, VLAN-labelled links; then runs `validate_network` and fixes what it finds |
| `check_network` | `language` | Runs the check on the open diagram and explains every finding with a concrete fix |

Example: *"/create_map a concept map of photosynthesis for a 14-year-old, in Italian"* or
*"/design_network a branch office: firewall, core switch, server VLAN 10 (192.168.10.0/24),
office VLAN 20 (192.168.20.0/24), Wi-Fi VLAN 30, twenty workstations"*.

### What the check understands

Properties on shapes: `ip` / `ips` / `address` (`10.0.1.5`, `10.0.1.5/24`, `10.0.1.5 255.255.255.0`,
several separated by commas), `mask` / `netmask` / `subnet`, `gateway` / `gw`, `vlan` / `vlans`
(`10`, `10,20`, `trunk`), `interfaces` / `ports` (`Gi0/1=10.0.1.1/24 vlan 10; Gi0/24 trunk 10,20;
eth0 192.168.1.1/24`). Link labels: `VLAN 10`, `VLAN 10/20`, interface names on either end
(`Gi0/1 - eth0`, `eth1 ↔ Gi0/1`). Findings (`code`): `bad-address`, `bad-mask`,
`duplicate-address`, `network-address`, `broadcast-address`, `subnet-mismatch`,
`gateway-outside-subnet`, `bad-vlan`, `vlan-mismatch`, `unknown-interface`, `interface-reused`,
`duplicate-interface`, `no-address`, `isolated`. Routers, firewalls, internet/WAN/VPN clouds
route between subnets; switches, access points, racks and UPSes forward without an address.

For diagrams, ask for example: *"Draw the network of a branch office: internet, firewall, a core
switch, two servers in a 'Server room' container and six workstations, with IP addresses and
status, orthogonal connectors labelled with the VLAN, then export it as branch.drawio."*
`get_map` shows each shape's stencil, size, position and properties, and each link's style.

Ids are positive integers shown as `[id]` by `get_map`. Main branches get their own colour
automatically; deeper ideas take their branch's colour.

## Scope

The server edits its own map in memory and reads or writes files on the machine where it runs,
with the permissions of the user who started it. It does not drive a running desktop or web
app yet (a live control channel, as in GridCraft, is on the roadmap).
