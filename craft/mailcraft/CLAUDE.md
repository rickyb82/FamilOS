# MailCraft — instructions for agents

MailCraft is an open-source, Rust-native IMAP/SMTP mail client with a Gmail-style interface and
an MCP server, part of the *craft* family (`gridcraft`, `ideacraft`, `pdfcraft`, …) and following
the same conventions.

## Assets — absolute rule
No vendor iconography, images, fonts, templates, themes or sample files — ever (Google/Gmail,
Microsoft/Outlook, Apple, Mozilla/Thunderbird…). Icons are strokes in `crates/ui-egui/src/icons.rs`,
themes in `theme.rs`, the demo mailbox in `crates/core/src/mock.rs`, all original. Any asset file
needs a row in `ATTRIBUTION.md`. Provider settings in `crates/core/src/autoconfig.rs` come from
the providers' public documentation (host names and ports are facts, not assets). Never copy
code from GPL/AGPL/LGPL projects (Thunderbird, Evolution, KMail, …).

## Never crash
- No `unwrap()`, `expect()`, `panic!`, `unreachable!`, `todo!`, `unimplemented!` or `unsafe` in
  non-test code (crates carry `#![deny(clippy::unwrap_used, …)]`, `unsafe_code = "forbid"`).
- Errors are `Result` through each crate's error type (`core::Error`, `engine::Error`).
- Input is hostile: servers, messages, files, command and MCP parameters. Bounded parsing
  (`core::limits`), `get()` over indexing, checked arithmetic, char-boundary clipping (`core::clip`).
- The worker thread catches a panicking transport and reports it as an error; the session never
  leaves the cache half-changed (optimistic flag/move changes trigger a refresh on failure).
- Every crash fix lands with a regression test. `every_command_survives_empty_params` guards
  the engine; the transport is tested against the in-process fake IMAP/SMTP servers
  (`crates/net/src/fake.rs`); MIME parsing has hostile-input tests.

## Security and privacy
- Passwords live only in `secrets.json` (0600) via `core::Secrets`; never log them, never return
  them from a command, a tool or a resource (`account_json` has `has_password`, nothing else).
- TLS is rustls with system roots; `trust_invalid_certs` is per account and off by default.
- The MCP server runs with the user's rights on the user's machine: tools that send or delete
  mail are described so an agent asks first; keep it that way.

## Architecture
- Layers (checked by `cargo xtask layers`): `core` (L0) → `net` (L1) → `engine` (L5) →
  `ui-egui` / `mcp` / `remote` (L6) → `apps/*` (L7). Nothing below L6 depends on egui; `engine`
  depends on `core` only (the transport is injected as a `BackendFactory`); only apps use
  `rfd`/`eframe`. The UI talks to a `MailSession` (the local `Session` or `RemoteSession`), so it
  must never read `Session`-only state; results of commands arrive through `pump` on the web.
- **Everything is a command** (`crates/engine/src/cmd.rs`: id, label, menu, shortcut, params,
  network). The UI, the CLI and the MCP server call the same commands; a new user-visible
  feature is a command first, then a tool or button. Network commands return `{"job": id}`;
  `Session::pump` applies results, `Session::run_blocking` waits for them.
- **The UI is thin**: it reads `Session` state and acts through `MailApp::run`; colours from
  `theme::Tokens`, icons from `icons.rs`. Host-only actions (file dialogs, links) go through
  `MailApp::requests`, served by `apps/mailcraft`.
- Programmatic calls never open dialogs.

## Working
- Quality gate before every commit: `cargo xtask ci` (fmt, clippy -D warnings, tests, layers,
  wasm). **Never break wasm**: `apps/mailcraft-web` must build for `wasm32-unknown-unknown`;
  use `web_time` instead of `std::time::{Instant, SystemTime}` (they panic in the browser).
- Rust is pinned in `rust-toolchain.toml` so local clippy and CI agree.
- Interface strings are English in code and translated in `crates/ui-egui/src/i18n/` (add every
  new label to `i18n::LABELS` and the Italian table); engine messages are translated by prefix
  in `i18n/it.rs` `MESSAGES`.
- For UI work, look at the result:
  `cargo run -p mailcraft-ui-egui --example snapshot -- out.png --lang it --open 0`.
- No real mail server is reachable from CI: test against `MockBackend` (engine, UI, MCP) and
  the fake servers (net). A real-server check is `mailcraft-cli add you@… --password …`.
- Update `ROADMAP.md` (progress log) and the docs in `docs/` when work lands.
