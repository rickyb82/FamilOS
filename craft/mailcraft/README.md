# MailCraft

An open-source, Gmail-style e-mail client for classic **IMAP and SMTP** mailboxes, written in
Rust, with an **MCP server** so an AI assistant can configure it in plain language and read,
triage and answer your mail. Part of the *craft* family (`gridcraft`, `ideacraft`, `pdfcraft`…).

```
┌──────────────┬─────────────────────────────────────┬──────────────────────────┐
│ ✎ Compose    │ ☐ ★ Anna Rossi   Riunione di lunedì  │ Riunione di lunedì       │
│ ▸ Inbox    3 │ ☐ ★ Monitoraggio 2 Re: Backup…  📎   │ Anna Rossi <anna@…>      │
│   Starred    │ ☐ ★ Fatturazione Fattura n° 2026…    │ A: Utente Esempio              │
│   Sent       │ ☐ ★ Luca Bianchi Re: Preventivo…     │                          │
│   Drafts     │ …                                    │ Ciao Utente Esempio, …         │
│   Archive    │                                      │                          │
│   Trash      │                                      │ [Reply] [Reply all] [Fwd]│
└──────────────┴─────────────────────────────────────┴──────────────────────────┘
```

## What it does

- **Web app and desktop app** — the same Rust interface runs in the browser (WebAssembly, served
  from GitHub Pages or by `mailcraft-cli serve`) and natively; the web app talks to a small
  server that holds the accounts, so the browser never sees IMAP or passwords.
- **Classic mail, modern client** — IMAP (TLS, STARTTLS) and SMTP (TLS, STARTTLS, plain for local
  bridges) over rustls. Folders with roles (inbox, sent, drafts, trash, spam, archive) detected
  from SPECIAL-USE attributes and English/Italian names; unread counts; conversations grouped by
  `References`/`In-Reply-To`; search with Gmail operators (`from:`, `subject:`, `is:unread`,
  `is:starred`, `has:attachment`, `newer_than:7d`); attachments; replies with quotes; forwards
  with the original attachments; drafts saved to the server; a local cache so the client opens
  instantly and works offline.
- **Gmail-style interface** — left navigation with the special folders and the accounts, the
  message list with stars, hover actions and bulk actions, a reading pane (or full view), a
  compose card docked at the bottom right, light and dark themes, English and Italian, keyboard
  shortcuts (`c` compose, `/` search, `j`/`k`, `e` archive, `#` trash, `s` star, `r` reply…).
- **Gmail and Microsoft 365, the Thunderbird way** — OAuth sign-in (XOAUTH2) with the
  provider's consent page in the browser and the return on `127.0.0.1`, tokens renewed
  automatically. Like Thunderbird, a build can carry its OAuth client inside
  (`MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID` at build time), so users just press *Sign in with
  Google*; otherwise the client is pasted in Settings, `mailcraft-cli oauth`, or environment
  variables. App passwords still work. See [`docs/oauth.md`](docs/oauth.md).
- **Smart replies from your own assistant** — behind a framework with an `assist` endpoint
  (`--assist-url`), the web app shows up to five suggested replies under each message and a
  *Write with the assistant* box in the compose window; local models only unless you allow the
  cloud.
- **Write like on Gmail** — the same two toolbars: font, size, bold, italic, underline, text
  colour, alignment, lists, indent, undo/redo above the text (toggled by "Aa"), and Send with
  its menu, attach, link, emoji, image, signature, print and more below it. What you see is
  what goes out: the text box draws the styles (bigger, coloured, bold, lists, centred lines)
  and the message leaves as text + HTML; a print button for any message or draft; the compose
  window drags anywhere so what is behind it stays readable.
- **Signatures with a logo, recipients that complete themselves** — paste the HTML of your
  signature (logo, bold, links): messages go out as text + HTML with the logo inline, replies
  and forwards included. While you type a recipient, the people seen in your mail or written
  to are offered (click or Enter), newest and most written-to first.
- **Your folders, your way** — the account form lists the server's folders: untick one to hide
  it from the navigation (IMAP subscription), pick the Sent, Drafts, Trash, Spam and Archive
  folders when the server names them unusually, create folders. Sent mail is copied to the
  Sent folder, which MailCraft creates when the server has none.
- **Several accounts, one inbox** — switch account from the avatar at the top right, like on
  Gmail, or choose *All accounts*: a unified inbox (and starred, sent, drafts… across accounts)
  where every row carries its account's colour, actions go to the right server and the compose
  window has a *From* selector. Programmatically it is `account: "*"` (`mail.select`,
  `mail.list`, `mail.search`, the MCP `list_messages`/`search`).
- **Configuration in plain language (MCP)** — `mailcraft-cli mcp` is a Model Context Protocol
  server. Connect it to Claude Code, Claude Desktop or any MCP client and say *"configura la mia
  casella Aruba info@example.it, password …"* or *"dark theme, sync every 2 minutes"*: the
  `suggest_settings` tool knows the documented servers of Gmail, Outlook, Yahoo, iCloud, AOL,
  Aruba, Aruba PEC, Legalmail, Libero, Virgilio, Tiscali, TIM, Fastweb, GMX, WEB.DE, mail.com,
  Zoho, Fastmail, Proton Mail Bridge, Posteo, mailbox.org, Yandex and Infomaniak (with the notes
  that matter: app passwords, OAuth-only providers, bridges); unknown domains are probed with the
  conventional host names and the big hosting providers. The assistant also syncs, lists, reads,
  searches, replies, forwards, archives and files your mail, and never sees a password it did not
  receive from you.
- **Everything is a command** — the interface, the CLI and the MCP server call the same engine
  commands (`account.add`, `mail.search`, `mail.reply`…), so anything you can click, an agent can
  do, and nothing else.
- **Never crashes** — no `unwrap`/`panic` in product code, bounded parsing of hostile input
  (servers, files, parameters), a worker thread that catches transport panics.
- **Privacy** — everything stays on your computer: the configuration in `~/.config/mailcraft`
  (`%APPDATA%\mailcraft`, `~/Library/Application Support/mailcraft`), passwords in a file only
  your user can read (0600), the cache per account. No telemetry, no cloud.

## Run it

### Web app (like Gmail, in your browser)

A browser cannot speak IMAP, so the web app mirrors a small server that keeps your accounts
and connections — your own computer by default:

```sh
cargo install --path apps/mailcraft-cli
mailcraft-cli serve --open            # http://127.0.0.1:7878, opens the browser
```

Without a token the server answers only to this machine: requests must name it in `Host`
(`127.0.0.1`, `localhost` or `[::1]` on the server's port, so a web site cannot reach it through
DNS rebinding) and come from one of its pages (`http://127.0.0.1:*`, `http://localhost:*`) or
from an origin given with `--allow-origin` (or `MAILCRAFT_ALLOW_ORIGINS`, comma separated); any
other web page gets 403, so the sites you visit cannot read or send your mail. Serve the page from
the server itself: build the app once (`cd apps/mailcraft-web && trunk build --release`) and run
`mailcraft-cli serve --static dist/web --open`. The page on **https://rickyb82.github.io/mailcraft/**
(it connects to `http://127.0.0.1:7878`; change the server in Settings › *Web app server*) needs
`mailcraft-cli serve --allow-origin https://rickyb82.github.io`. To reach your mail from
anywhere, run the server on a machine of yours behind an HTTPS reverse proxy with
`mailcraft-cli serve --bind 0.0.0.0:7878 --token SECRET` (at least 32 characters:
`openssl rand -hex 32`) and open the page with `#token=SECRET` (the fragment never reaches a
server or a log; the page takes it out of the address bar at once). `--open` hands the browser
the address with the token in the fragment. `mailcraft-cli serve --demo` shows the built-in
mailbox.

### In a framework, for many users (Docker)

`docker compose up -d` with `MAILCRAFT_TOKEN` set runs a multi-user server: one mailbox client
per user of your framework, data per user under `/data/users/<user>/`, per-user tokens for the
web page and for a per-user MCP assistant (`mailcraft-cli mcp --server URL --token T`). See
[docs/deploy.md](docs/deploy.md).

### Desktop app

```sh
cargo run --release -p mailcraft            # the desktop app (opens Settings until an account exists)
cargo run --release -p mailcraft -- --demo  # the built-in demo mailbox, no account needed
```

### Headless (CLI)

```sh
cargo install --path apps/mailcraft-cli
mailcraft-cli suggest anna@gmail.com --lang it         # the provider's servers and notes
mailcraft-cli add anna@gmail.com --password 'app-pw'   # saves, probes unknown domains, tests IMAP+SMTP
mailcraft-cli accounts
mailcraft-cli sync && mailcraft-cli list --unread
mailcraft-cli read 1234
mailcraft-cli search 'from:aruba has:attachment newer_than:30d'
mailcraft-cli send --to luca@example.com --subject Ciao --body 'A presto' --attach preventivo.pdf
mailcraft-cli set theme dark
mailcraft-cli run mail.reply '{"uid": 1234, "body": "Grazie!"}'
```

### AI assistant (MCP)

```sh
claude mcp add mailcraft -- mailcraft-cli mcp
```

Then: *"/setup_account la mia mail Aruba info@sirius-example.it, password …, firma Utente Esempio"*,
*"/triage_inbox"*, *"/write_email a Luca, conferma il sopralluogo di giovedì"*, *"/configure tema
scuro e sincronizza ogni 2 minuti"*. Tools, prompts and the Claude Desktop configuration are in
[docs/mcp.md](docs/mcp.md).

### Look at the UI without a window

```sh
cargo run -p mailcraft-ui-egui --example snapshot -- out.png [--lang it] [--dark] [--open 0] [--compose] [--settings] [--search fattura] [--folder sent]
```

## Develop

- Workspace layout (checked by `cargo xtask layers`): `core` (model, autoconfig, MIME, mock
  backend) → `net` (IMAP/SMTP) → `engine` (session, commands, cache, worker) → `ui-egui` / `mcp`
  / `remote` (the engine mirrored over HTTP) → `apps/mailcraft`, `apps/mailcraft-cli` (CLI, MCP,
  `serve`), `apps/mailcraft-web` (WebAssembly). See [docs/architecture.md](docs/architecture.md).
- `cargo xtask ci` runs fmt, clippy (`-D warnings`), the tests, the layer check and the wasm
  build. The transport is tested against small IMAP and SMTP servers that run inside the tests
  and the web path against a `serve` instance started by the tests, so no network is needed; a
  real-server smoke test is `mailcraft-cli add you@… --password …`.
- Icons, themes and the demo mailbox are drawn or written in code: no vendor artwork
  ([ATTRIBUTION.md](ATTRIBUTION.md)).

## Licence

MIT OR Apache-2.0.
