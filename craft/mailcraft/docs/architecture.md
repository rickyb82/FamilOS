# Architecture

> **Last updated:** 2026-10-13

| Crate | Layer | What it holds |
|---|---|---|
| `crates/core` (`mailcraft-core`) | L0 | Accounts, preferences and secrets (`account.rs`: validation, user-only files, platform paths); provider autoconfiguration (`autoconfig.rs`); mailboxes and roles (`mailbox.rs`); addresses, envelopes, messages, drafts and calendar helpers (`message.rs`); MIME parsing with `mail-parser` and building of outgoing messages (`mime.rs`: base64, RFC 2047, HTML→text, quoting); the backend trait and search queries (`backend.rs`); the in-memory mock backend with the demo mailbox (`mock.rs`); OAuth for Google and Microsoft 365 (`oauth.rs`: provider table, PKCE authorization URL, tokens, XOAUTH2 string, the broker trait and its mock); `limits`. No network. |
| `crates/net` (`mailcraft-net`) | L1 | `ImapSmtpBackend`: IMAP with the `imap` crate over rustls (LIST/STATUS, paged FETCH with header + partial text for snippets, UID STORE/MOVE/COPY/EXPUNGE, APPEND with APPENDUID, SEARCH), SMTP with `lettre` over rustls (`send_raw`, `test_connection`); XOAUTH2 on both for OAuth accounts; `HttpBroker` (the token endpoint over `ehttp`). Tested against tiny IMAP and SMTP servers started inside the tests (`fake.rs`). |
| `crates/engine` (`mailcraft-engine`) | L5 | `Session`: configuration, per-account cache (`store.rs`: mailboxes, envelopes newest first, conversations, opened messages and raw bytes, files on disk), the view (account or `*` for every account at once, mailbox or role, query, selection and open message with their account), the command table and dispatcher (`cmd.rs`), the worker thread (`worker.rs`: one backend per account, jobs → events, reply/forward composition, OAuth exchange and refresh) and the loopback listener that takes the browser back from an OAuth sign-in (`loopback.rs`). |
| `crates/ui-egui` (`mailcraft-ui-egui`) | L6 | `MailApp`: top bar, navigation, message list, reading pane, compose card, settings, toasts, keyboard shortcuts, i18n, themes, icons. The `snapshot` example renders it offscreen. |
| `crates/mcp` (`mailcraft-mcp`) | L6 | The MCP server: tools, prompts and resources over newline-delimited JSON-RPC on stdio, each tool an engine command. |
| `crates/remote` (`mailcraft-remote`) | L6 | `RemoteSession`: a `MailSession` that mirrors a `mailcraft-cli serve` server over HTTP (`ehttp`, native and wasm): `POST /api/cmd`, `GET /api/snapshot?since=N`. |
| `apps/mailcraft` | L7 | The desktop app (eframe, rfd for file dialogs). |
| `apps/mailcraft-cli` | L7 | The CLI, the `mcp` entry point and `serve` (the web app's server: HTTP API, static files, token). |
| `apps/mailcraft-web` | L7 | The web app (wasm32, trunk): the same `MailApp` on a `RemoteSession`; file picker and downloads for attachments. |
| `xtask` | — | `cargo xtask ci`, `cargo xtask layers`. |

## Commands and jobs

Every user-visible action is a `Command` in `crates/engine/src/cmd.rs`. Commands that only touch
local state (`account.*`, `prefs.*`, `selection.*`, `view.get`) answer at once. Commands that need
the server queue a `Job` on the worker and answer `{"job": id}` (or `{"jobs": [...]}` for
`mail.sync`); the UI calls `Session::pump` every frame to apply the `Event`s, the CLI and the MCP
server call `Session::run_blocking`, which pumps until the job completes. Flag changes and
moves are applied to the cache optimistically; a failure triggers a refresh of that mailbox.

The worker owns one `Box<dyn MailBackend>` per account (made by the `BackendFactory` the app
injects: `mailcraft_net::factory()` for IMAP/SMTP, `MockBackend` for tests and the demo), connects
lazily, drops the connection after a connect error and catches panics in the transport.

## Cache

`AccountState` keeps the mailbox list (with unread/total counts from STATUS), the envelopes of
each mailbox (newest first, merged page by page; the first page is authoritative for its uid
window), the opened messages and their raw bytes (for attachments and forwards), and writes
`mailboxes.json` plus one JSON file per mailbox under `cache/<account>/`. Conversations are
computed on demand from the envelopes' `References`/`In-Reply-To`/subject.

## Configuration

`config.json` holds accounts and preferences; `secrets.json` (0600) holds passwords by account
id. Both live in `MAILCRAFT_HOME` or the platform configuration directory. `account.add` fills
the servers from `autoconfig::suggest`; `account.probe` tries the suggested pairs until one
accepts the login and saves it; `account.test` logs in to IMAP (counting mailboxes) and SMTP.

## Web app

The interface is written against the `MailSession` trait. Locally it is the engine `Session`;
in the browser it is `RemoteSession`, which sends commands to `mailcraft-cli serve` and polls
`GET /api/snapshot?since=N` (every 2 s idle, 0.4 s while jobs run). The server answers
`{"unchanged": true}` until its revision moves; otherwise a `Snapshot`: the configuration
(never passwords: only which accounts have one), the view, every account's folders with counts,
the envelopes and the opened messages (as text) of the mailbox in view, and the jobs completed
since the client's revision, so toasts and form follow-ups (`account.suggest`, `account.add`,
`mail.attachment`) work the same way as locally. The server keeps one `Session` per user (one
in single-user mode), pumps them on a background thread, and refuses to bind beyond localhost
without a token. In multi-user mode (`--users-dir`) the framework's service token names the
user (`X-Mailcraft-User`) and mints per-user tokens (`POST /api/users/<user>/token`); the MCP
server can run for one user over the network (`mailcraft-cli mcp --server URL --token T`) through
`RemoteSession::run_blocking` (native only). See `docs/deploy.md`.

## Interface

The egui front end is one `MailApp` on top of the session. The list groups conversations when the
preference is on (and never in search results); the reading pane shows every member of the open
conversation, the newest expanded. The compose card builds `mail.send` parameters (with
`in_reply_to`/`references` from the open message) and keeps unsent text as a draft on close.
Host actions (attach files, save an attachment, open a link) are `Request`s served by the app.
