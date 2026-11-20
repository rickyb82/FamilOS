# IdeaCraft roadmap

> **Last updated:** 2026-10-26 · **Stage:** 0.1 preview

IdeaCraft aims to be the open, fast, scriptable place to think in maps: concept maps, mind maps
and idea boards, with the polish of commercial canvas tools and none of the lock-in.

## Now (0.1 — done)

- Workspace in the craft layout: `core` → `formats` → `engine` → `ui-egui` → apps.
- Canvas: ideas, tree branches, labelled links, collapse, pan/zoom/fit, light/dark.
- Tree and radial layouts; node colours and shapes.
- 12 original templates with a preview gallery.
- Import FreeMind/Freeplane, XMind, CmapTools CXL, OPML, Markdown, Mermaid; export Markdown,
  OPML, FreeMind; native `.ideacraft.json`.
- Command engine with undo/redo, panic guard, command-sequence fuzzing; headless CLI;
  offscreen snapshot renderer.
- Canvas-tool interface (top bar, left rail with panels, floating selection bar, zoom), icons
  drawn in code, light/dark themes.
- Web app (WebAssembly, trunk) published to GitHub Pages; browser open/save/export, state kept
  in the browser.
- English and Italian interface and templates.
- MCP server (`ideacraft-cli mcp`) for AI agents; SVG export.
- Diagram mode: 74 stencils in seven sets (basic, flowchart, network, cloud, people, UML, BPMN),
  sized shapes, snap grid, containers, marquee, align/distribute, z-order, styled connectors
  (route, line, arrows, colour, width), properties with status lights, draw.io import/export,
  four diagram templates, MCP diagram tools.
- Layers (show/hide/lock, draw.io round-trip) and groups (Cmd+G); Layers panel.
- Network check (addresses, masks, gateways, VLANs, interfaces) in the Check panel, the CLI and
  MCP; the office network template is fully addressed and passes it.
- MCP prompts: `create_map` and `design_network` build a whole map from a plain-language brief
  (`build_map` tool), `check_network` reviews a diagram.

## Next (0.2)

- Keyboard navigation between ideas; connector waypoints and edge-to-edge attachment points.
- Swimlane layouts; more stencil sets (electrical, floor plans); group resize as one.
- Balanced mind-map layout (branches on both sides), org-chart and timeline layouts.
- Text formatting in ideas (bold, lists), notes, links to URLs, icons/emoji, images.
- Free shapes and sticky notes outside the tree (board mode).
- Themes: whole-map colour schemes, fonts from `storytold/craft-fonts`.
- More interface languages.

## Later

- Real-time collaboration (CRDT), comments.
- Presentation mode (walk through branches), PNG/SVG/PDF export.
- A live control channel so agents can drive a running desktop or web app (the MCP server works
  on its own map today).
- More importers: MindManager-style XML, GraphML, Obsidian canvas.

## Progress log

| Date | Change |
|---|---|
| 2026-10-12 | 0.1: skeleton, canvas, layouts, 12 templates, 6 import formats, 3 export formats, CLI |
| 2026-10-12 | Italian interface and templates; new canvas-tool interface; web app on GitHub Pages |
| 2026-10-12 | MCP server for AI agents (16 tools); SVG export; automatic branch colours |
| 2026-10-12 | MCP: `--in FILE`, saving back to .mm/.opml/.md files in their format, resources, tool aliases |
| 2026-10-12 | Diagram mode (Visio-style): stencils, styled connectors, properties with status lights, align/distribute, draw.io import/export, diagram templates, MCP diagram tools |
| 2026-10-12 | Layers and groups, UML/BPMN/network stencils (74), network check (IP/VLAN/interfaces) in UI, CLI and MCP, MCP prompts and `build_map` for natural-language maps |
| 2026-10-23 | Release pipeline: original app icon (`cargo xtask icons`), Windows MSI + portable zip, macOS universal DMG + CLI zip, draft GitHub releases (`packaging/`, `release.yml`) |
| 2026-10-26 | Security: `ideacraft-cli mcp` opens files only inside `--automation-read-root` and saves only inside `--automation-write-root` (links followed, `..` and absolute paths outside refused; no file by path without them); zip 9 (RUSTSEC-2026-0367/0368/0369), tokio 1.54.1 |
