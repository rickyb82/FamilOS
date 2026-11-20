# IdeaCraft

**Design your own digital map.** IdeaCraft is an open-source, Rust-native canvas for dynamic
concept maps and mind maps — a free, fast alternative to closed whiteboard and mind-mapping apps.
It belongs to the *craft* family of open creative tools (GridCraft, PdfCraft, …) and shares their
conventions: everything is a command, nothing crashes, and every file stays readable.

> Status: **0.1, early preview.** The canvas, templates, import/export, the web app and the
> command engine work; see [ROADMAP.md](ROADMAP.md) for what comes next.

**Try it in the browser:** <https://rickyb82.github.io/ideacraft/> (add `?lang=it` for Italian,
`?template=swot` to start from a template).

## What it does

- **A canvas-tool interface** — a top bar with the File menu, title, undo/redo, language, theme
  and Export; a left rail with Templates, Elements, Layout and Files panels; a floating bar over
  the selected idea (colour, shape, add child or sibling, collapse, link, delete); zoom controls.
- **English and Italian** — the interface and the templates; the system or browser language is
  used on first run.
- **Desktop and web** — the same Rust code runs natively and in the browser (WebAssembly).
- **Diagram mode (Visio-style)** — switch any map to a free diagram: a shape library of
  74 original stencils in seven sets (basic shapes, flowchart, network devices, cloud & servers,
  people & places, UML, BPMN), resizable shapes with snap-to-grid, containers/zones, marquee
  selection, align and distribute, duplicate, front/back ordering, and styled connectors
  (straight, orthogonal or curved; solid, dashed or dotted; arrows at either end; colour and width).
- **Layers and groups** — put shapes on named layers, hide or lock a layer (locked shapes stay
  out of the way on the canvas), move the selection between layers; group shapes (Cmd+G) so they
  select and move together, ungroup with Cmd+Shift+G. Layers round-trip through draw.io files.
- **Interactive network diagrams** — every shape carries free properties (IP, VLAN, model,
  owner…) shown in a tooltip and editable in the Elements panel; a `status` of up / warning /
  down lights a green, amber or red indicator on the shape.
- **Network check** — the Check panel (and `ideacraft-cli check`, the `validate_network` MCP
  tool) validates what the diagram declares: IP addresses and masks (`ip 10.0.1.5/24`,
  `mask 255.255.255.0`), duplicate, network and broadcast addresses, gateways outside the subnet,
  VLAN ids and VLAN consistency between shapes and link labels (`VLAN 10`), interfaces declared on
  devices (`interfaces: Gi0/1=10.0.1.1/24 vlan 10; Gi0/24 trunk 10,20`) and used on links
  (`Gi0/1 - eth0`), hosts on one switch and VLAN in different subnets, devices without an
  address or without links. Each finding selects the shapes involved when clicked.
- **draw.io interchange** — open `.drawio` files (plain or compressed) and export to draw.io, so
  diagrams travel to and from diagrams.net, Visio and the tools that read it.

- **Infinite canvas** — pan, zoom around the pointer, fit to window, light and dark themes.
- **Ideas and structure** — a parent/child tree per map (Tab adds a child, Enter a sibling),
  free labelled links between any two ideas (Shift+drag, or the Link tool), collapsible branches.
- **Dynamic layouts** — tree and radial layouts, re-run any time; drag an idea up or down and
  re-layout to reorder it.
- **Styling** — node fills from an original palette and 14 shapes (rounded, rect, ellipse, pill,
  diamond, hexagon, parallelogram, cylinder, document, note, cloud, subprocess, text, container).
- **16 built-in templates** — 12 maps (Brainstorming, Concept map, Project plan, SWOT analysis,
  Decision making, Meeting notes, Book summary, Cause and effect, Weekly planner, Product roadmap,
  Customer journey, Lesson plan) and 4 diagrams (Office network, Flowchart, Cloud architecture,
  Organisation chart). All are original and defined in code.
- **Import** — FreeMind / Freeplane (`.mm`), XMind (`.xmind`, current and legacy), CmapTools
  (`.cxl`), OPML, Markdown / indented outlines, Mermaid (`mindmap`, simple `flowchart`), draw.io
  (`.drawio`). Open a file or drop it on the window.
- **Export** — Markdown outline, OPML, FreeMind `.mm`, draw.io, SVG, and IdeaCraft's own
  plain-JSON format (`.ideacraft.json`).
- **Undo/redo** for every change; the map is restored if a command ever fails.
- **MCP server for AI agents** — `ideacraft-cli mcp` lets Claude (or any MCP client) create,
  edit, lay out, export (including SVG) and save maps; see [docs/mcp.md](docs/mcp.md).
- **Headless CLI** for scripts: run any command, convert between formats, list templates.

## Download

Installers for Windows (MSI, or a portable zip) and macOS (universal DMG, plus a zip of
`ideacraft-cli`) are published on the
[GitHub releases page](https://github.com/rickyb82/ideacraft/releases); `SHA256SUMS.txt` lists
their checksums. They are **not code-signed yet**, so the first launch shows a warning:

- **Windows** — SmartScreen says "Windows protected your PC": click *More info*, then *Run anyway*.
- **macOS** — Gatekeeper says the app "cannot be opened": right-click (or Control-click)
  `IdeaCraft.app` and choose *Open*, then *Open* again; this is needed only the first time.

Linux users build from source (below) or use the web app. See [packaging/README.md](packaging/README.md)
to build the installers yourself.

## Run it

```sh
cargo run --release -p ideacraft                       # opens the template gallery
cargo run --release -p ideacraft -- --template swot    # start from a template
cargo run --release -p ideacraft -- notes.mm           # open or import a file
```

Linux needs the usual windowing libraries (X11 or Wayland, `libxkbcommon`).

### Web

```sh
cd apps/ideacraft-web && trunk serve --release    # http://127.0.0.1:8781
cd apps/ideacraft-web && trunk build --release    # → dist/web (host it on any static server)
```

You need [trunk](https://trunkrs.dev) and the `wasm32-unknown-unknown` target (installed by
`rust-toolchain.toml`). Open uses the browser's file picker (dropping files works too); Save and
Export download the file; the open map, language and theme are kept in the browser. Every push to
`main` publishes the web app to GitHub Pages (`.github/workflows/pages.yml`).

### Keyboard

| Key | Action |
|---|---|
| Double-click | new idea (empty canvas) / edit text (idea) |
| Tab / Enter | add child / add sibling, then type |
| F2 | edit text |
| Delete | delete the idea and its branch |
| Space | collapse / expand branch |
| Shift+drag | link two ideas |
| Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z | undo, redo |
| Cmd/Ctrl+L, Cmd/Ctrl+Shift+L | tree layout, radial layout |
| Cmd/Ctrl+0 | fit the map in the window |
| Cmd/Ctrl+O, S, Shift+S, N | open/import, save, save as, new |
| Cmd/Ctrl+D | duplicate |
| Cmd/Ctrl+] / Cmd/Ctrl+[ | bring to front / send to back |
| Arrow keys (Shift: one grid step) | nudge the selection |
| Drag on empty space (diagram mode) | marquee selection; corner handles resize |

### AI agents (MCP)

```sh
cargo install --path apps/ideacraft-cli
claude mcp add ideacraft -- ideacraft-cli mcp --automation-read-root ~/maps --automation-write-root ~/maps
```

The agent opens and saves files only inside those folders (none without them). Tools, examples
and the Claude Desktop configuration are in [docs/mcp.md](docs/mcp.md). The
server also offers **prompts**: `create_map` turns a plain-language brief ("a project plan for a
school garden", "the network of a branch office with two VLANs") into a complete map or diagram,
`design_network` draws an addressed network and checks it, and `check_network` reviews the open
diagram's addresses, VLANs and interfaces. The `build_map` tool creates a whole map in one call.

### Headless

```sh
cargo run -p ideacraft-cli -- templates
cargo run -p ideacraft-cli -- commands
cargo run -p ideacraft-cli -- convert plan.xmind plan.ideacraft.json
cargo run -p ideacraft-cli -- run --template brainstorm \
    --cmd 'node.addChild={"id":1,"label":"Ask a teacher"}' --cmd 'layout.radial' --out ideas.md
cargo run -p ideacraft-cli -- check office.drawio --lang it   # network findings; exit 1 on errors
```

### Look at the UI without a window

```sh
cargo run -p ideacraft-ui-egui --example snapshot -- --template swot out.png [--dark] [--lang it] [--panel elements|shapes|layout|layers|check|files] [--select 2]
```

## Develop

```sh
cargo xtask ci        # fmt, clippy -D warnings, tests, layer check, wasm build
```

Read [CLAUDE.md](CLAUDE.md) (rules for contributors and agents) and
[docs/architecture.md](docs/architecture.md) first.

## Licence

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option. Every
asset is original; see [ATTRIBUTION.md](ATTRIBUTION.md).
