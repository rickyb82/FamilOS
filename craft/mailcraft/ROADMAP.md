# MailCraft roadmap

> **Stage:** 0.1 — a working web app, desktop client and MCP server, tested against in-process
> IMAP/SMTP servers; the first real-mailbox round trips are the next thing to prove.

## Now (0.1 — done)

- Workspace in the craft layout: `core` → `net` → `engine` → `ui-egui` / `mcp` → apps.
- Accounts with provider autoconfiguration (24 providers documented, conventional guesses and
  hosting providers probed for unknown domains), validation, user-only secrets file.
- IMAP over rustls: folders with roles and counts, paged lists with snippets, full fetch,
  flags, move (MOVE or copy+expunge), delete, append (drafts, sent copies), search (server-side
  for ASCII, local otherwise), create folder. SMTP over rustls with lettre.
- Engine: commands for accounts, preferences and mail; worker thread; optimistic flag/move with
  refresh on failure; cache on disk; conversations; reply/forward composition.
- Gmail-style egui interface: navigation, list with hover and bulk actions, reading pane or full
  view, conversations, compose card, settings (accounts form with suggest/probe/test,
  preferences, assistant, shortcuts), light/dark, English/Italian, keyboard shortcuts.
- MCP server: 29 tools (configuration and mail), 4 prompts (`setup_account`, `triage_inbox`,
  `write_email`, `configure`), 2 resources; CLI with `mcp`, `add`, `test`, `list`, `read`,
  `search`, `send`, `set`, `run`.
- Web app (WebAssembly, GitHub Pages) on top of `mailcraft-cli serve`: the engine's state is
  mirrored by revision over a tiny HTTP API, commands run on the server, attachments go through
  the browser's picker and downloads; token-protected when bound beyond localhost.
- Multi-user server (`--users-dir`): a session and a data directory per user, a service token
  for the framework (`X-Mailcraft-User`), per-user tokens minted by the framework for the page
  and the user's MCP process (`mailcraft-cli mcp --server URL --token T`), Docker image.
- Several accounts per user: the Gmail-style switcher under the avatar and the unified view
  (`account: "*"`): every account's inbox, starred, sent, drafts… merged newest first with an
  account chip per row, cross-account search, actions routed to the message's account, a
  *From* selector in the compose window.
- Folders: IMAP subscriptions (LSUB/SUBSCRIBE, `mailbox.subscribe`), special-folder choice and
  new folders in the account form; sent mail copied to a Sent folder created when missing;
  `mail.close`.
- OAuth sign-in for Google and Microsoft 365 (XOAUTH2, PKCE): client configured by the person
  or the operator, consent in the browser, loopback redirect on the desktop and
  `/api/oauth/callback` on the server, automatic token refresh, CLI `oauth`/`login`, MCP
  `configure_oauth`/`sign_in`.

## Next (0.2)

- Real-mailbox smoke tests on the main Italian providers (Aruba, Libero, PEC) and Gmail.
- HTML rendering of messages (today HTML is reduced to text); inline images.
- IMAP IDLE for push notifications; background sync in the desktop app.
- Undo for archive/trash; snooze; labels/keywords (`$label`); PGP later.
- Address book from sent mail with completion in the compose window.
- Web app: HTTPS built into `serve`, push updates instead of polling, PWA install.

## Later

- Calendar invitations (iCalendar), contacts (CardDAV).
- Rules/filters, signatures per identity, aliases.

## Progress log
- 2026-10-26: Second security pass: without a token `serve` answers only to this machine (`Host` local on its port against DNS rebinding, CORS only for local or `--allow-origin` origins, `Access-Control-Allow-Private-Network` only on an allowed preflight), tokens of at least 32 characters, the user only from `X-Mailcraft-User`, `--open` and the web app carry the token in the fragment (kept in memory inside a frame); links open without a shell (`core::link::openable`, no `cmd /C start`); attachment types cannot add header lines; MCP `send_mail` takes `{name, data}` and `save_attachment` returns the file as an embedded resource (up to 25 MB); IMAP TLS done in `mailcraft-net` on rustls 0.23 with the system roots (the `imap` crate's TLS pinned rustls 0.22), RustSec: 0 vulnerabilities
- 2026-10-26: Security review fixes: sessions served over HTTP refuse file paths (`Session::allow_host_paths`, `mail.send`/`draft.save`/`mail.attachment` take and give base64 only), the printable page is built from the message's text with a Content-Security-Policy (no handlers, `javascript:` links, frames or remote images), the service token is compared in constant time and read from the `Authorization` header only and never printed, the snapshot drops the OAuth client secrets
- 2026-10-15: adding a Gmail/Microsoft account without the OAuth client no longer shows a greyed-out sign-in button: the form explains the client, has its fields right there, and offers the app-password route.

| Date | Change |
|---|---|
| 2026-10-13 | 0.1: model, autoconfig, IMAP/SMTP transport with in-process test servers, engine with commands and cache, Gmail-style egui UI, MCP server with configuration prompts, CLI |
| 2026-10-13 | Web app: `MailSession` abstraction, snapshots by revision, `mailcraft-remote`, `mailcraft-cli serve` (HTTP API, static files, token), `apps/mailcraft-web` on GitHub Pages |
| 2026-10-13 | Compose toolbar fixes found in the browser: the body's selection is remembered across the focus loss a toolbar click causes (egui collapses it), the focus returns to the body on the next quiet frame (a click frame takes it straight back), `c` with a message open brings it back instead of replacing it |
| 2026-10-13 | Compose shows the styles instead of the marks: a layouter (`ui-egui/src/richtext.rs`) draws bold (faux, egui has no bold face), italic, underline, sizes, colours, lists, indent, centred/right lines and links in the text box, with the marks laid out as zero-width transparent slivers so cursor and edits keep working on the plain markup |
| 2026-10-13 | Gmail-like compose toolbars: formatting row (font, size, B/I/U, text colour, alignment, lists, indent, undo/redo; markup spans `{color=}` `{size=}` `{font=}` `{align=}` `{indent}` rendered in the HTML) toggled by "Aa", action row (Send with menu, attach, link, emoji, image, signature, print, more, discard) |
| 2026-10-13 | Multi-user server for frameworks: `--users-dir`, service and per-user tokens, `/api/users`, MCP over the server (`mcp --server`), Dockerfile and compose, deploy guide |
| 2026-10-13 | Time zones: dates shown in the device's (or the browser's) zone, preference `time_offset` (auto, UTC, +02:00) in Settings, the CLI and the MCP tools |
| 2026-10-13 | Assistant integration for frameworks: `--assist-url` announced in the snapshot, reply-suggestion chips under the open message and "Write with the assistant" in the compose window (web app, same-origin `POST`), preferences `assist_suggestions`/`assist_cloud` |
| 2026-10-13 | Compose formatting bar (markup → HTML: bold, italic, underline, lists, links, inline images as `cid`), print for messages and drafts (self-printing page handed to the host), draggable compose window |
| 2026-10-13 | HTML signatures (text + HTML messages, `data:` logos as inline `cid` images, replies and forwards too), recipient completion from an address book built from headers and sent mail (`contacts.search`, MCP `search_contacts`), `html` body for `mail.send` |
| 2026-10-13 | OAuth sign-in for Gmail and Microsoft 365: `core::oauth` (providers, PKCE, tokens, broker), XOAUTH2 on IMAP and SMTP, loopback listener, server callback, Settings section and account sign-in choice (client fields inside the form, servers filled from the address), built-in client at build time, CLI `oauth`/`login`, MCP tools, `docs/oauth.md` |
| 2026-10-13 | Folder subscriptions and special-folder choice in the account form, Sent folder created when the server lacks one (copy failures reported), `mail.close` (the reader's X works on the web too), settings layout fixes (visible fields, no overlap) |
| 2026-10-13 | Account switcher under the avatar; unified inbox across accounts (`account: "*"` in `mail.select`/`mail.list`/`mail.search`, account-tagged envelopes, *All accounts* in the navigation and the switcher, account chips in the list, *From* selector when composing) |
