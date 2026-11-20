# IdeaCraft — instructions for agents

IdeaCraft is a clean-room, open-source, Rust-native canvas for dynamic concept maps and mind maps,
part of the *craft* family (`gridcraft`, `pdfcraft`, …) and following the same conventions.

## Assets — absolute rule
No vendor iconography, images, fonts, templates, themes or sample files — ever (XMind, MindManager,
Miro, Canva, Lucid, Microsoft Visio, Cisco icon sets, draw.io's own shape libraries…). Stencils are
original strokes in `crates/core/src/stencil.rs`; generic device symbols are fine, copied artwork is not. Templates, palettes and the sample map are original and defined in
code (`crates/formats/src/templates.rs`, `crates/core/src/color.rs`). Any asset file needs a row in
`ATTRIBUTION.md`. Other tools' *formats* are read from public descriptions and our own test files;
never copy code from GPL/AGPL/LGPL projects (FreeMind, Freeplane, …).

## Never crash
- No `unwrap()`, `expect()`, `panic!`, `unreachable!`, `todo!`, `unimplemented!` or `unsafe` in
  non-test code (crates carry `#![deny(clippy::unwrap_used, …)]`, `unsafe_code = "forbid"`).
- Errors are `Result` through each crate's error type.
- Input is hostile (files, commands, CLI params): bounded parsing (`crates/core/src/lib.rs`
  `limits`, `crates/formats/src/xml.rs`), `get()` over indexing, clamp coordinates.
- `Session::execute` restores the map on a failed or panicking command — a safety net only.
- Every crash fix lands with a regression test. `every_command_survives_empty_params` and
  `crates/engine/tests/command_sequence_fuzz.rs` (soak: `IDEACRAFT_FUZZ_SEEDS`,
  `IDEACRAFT_FUZZ_STEPS`; replay: `IDEACRAFT_FUZZ_SEED`) guard the engine; importers have
  hostile-input tests.

## Architecture
- Layers (checked by `cargo xtask layers`): `core` (L0) → `formats` (L2) → `engine` (L5) →
  `ui-egui` / `mcp` (L6) → `apps/*` (L7). Nothing below L6 depends on egui; only apps use `rfd`/`eframe`.
- **Everything is a command** (`crates/engine/src/cmd.rs`: id, label, menu, shortcut, params,
  undoable). The UI, the CLI and the MCP server (`crates/mcp`, `ideacraft-cli mcp`) call the same
  commands; a new user-visible feature is a command first, then a tool or button.
- **The UI is thin**: it reads `Session` state and acts through `IdeaApp::run`; colours come
  from `theme::Tokens`, icons from `icons.rs` (drawn in code), stencil glyphs and connector
  routing from `core` (`stencil.rs`, `route.rs`) so the canvas and the SVG export agree. Host-only actions (file dialogs,
  downloads) go through `IdeaApp::requests`, served by `apps/ideacraft` (rfd) and
  `apps/ideacraft-web` (browser picker, downloads).
- Programmatic calls never open dialogs.

## Working
- Quality gate before every commit: `cargo xtask ci` (fmt, clippy -D warnings, tests, layers,
  wasm). **Never break wasm**: `apps/ideacraft-web` must build for `wasm32-unknown-unknown`.
- Rust is pinned in `rust-toolchain.toml` so local clippy and CI agree.
- Interface strings are English in code and translated in `crates/ui-egui/src/i18n/` (add every
  new label to `i18n::LABELS` and the Italian table); templates carry their Italian text.
- For UI work, look at the result:
  `cargo run -p ideacraft-ui-egui --example snapshot -- --template swot out.png`.
- Update `ROADMAP.md` (progress log) and the docs in `docs/` when work lands.
