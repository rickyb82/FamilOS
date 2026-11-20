# MCP server

> **Last updated:** 2026-10-13

MailCraft speaks the [Model Context Protocol](https://modelcontextprotocol.io) so AI agents
(Claude Code, Claude Desktop and any other MCP client) can configure it in plain language and
work your mail: `mailcraft-cli mcp` serves MCP over stdio (newline-delimited JSON-RPC 2.0,
protocol `2025-06-18`; `2025-03-26` and `2024-11-05` are accepted). Every tool is an engine
command, so the assistant can do exactly what the interface does.

## Install and connect

```sh
cargo install --path apps/mailcraft-cli
claude mcp add mailcraft -- mailcraft-cli mcp          # Claude Code
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"mailcraft": {"command": "mailcraft-cli", "args": ["mcp"]}}}
```

`mailcraft-cli mcp --demo` serves the built-in demo mailbox (no account, no network), handy to
try the tools. `--home DIR` uses another configuration directory.

## Configuration tools

| Tool | What it does |
|---|---|
| `suggest_settings` | IMAP/SMTP settings for an address: the provider's documented servers (Gmail, Outlook.com, Yahoo, iCloud, AOL, Aruba, Aruba PEC, Legalmail, Libero, Virgilio, Tiscali, TIM, Fastweb, GMX, WEB.DE, mail.com, Zoho, Fastmail, Proton Mail Bridge, Posteo, mailbox.org, Yandex, Infomaniak) with notes (app passwords, OAuth-only accounts, bridges), or conventional guesses for unknown domains |
| `add_account` | Save an account from address + password (servers from the suggestion unless given), probe unknown domains, test IMAP and SMTP; returns the account, the test and the notes |
| `list_accounts` | Accounts, servers, status, unread counts (never passwords) |
| `update_account` | Change name, servers, signature, special folders, password… |
| `remove_account` | Remove an account, its password and cache |
| `test_account` | Log in to IMAP and SMTP |
| `find_servers` | Try the suggested server pairs until one accepts the login; save it |
| `set_password` | Store or replace a password |
| `get_preferences` / `set_preferences` | language, theme, sync_minutes, page_size, reading_pane, conversations, mark_read_on_open, undo_send_seconds, default_account, assist_suggestions, assist_cloud, time_offset (auto, UTC or +02:00) |

### Sign-in with Google / Microsoft 365

| Tool | What it does |
|---|---|
| `oauth_providers` | Google and Microsoft 365: whether a client is configured, where to create one |
| `configure_oauth` | Save the client id and secret (once) |
| `sign_in` | Start the sign-in of an account: returns the URL the person opens; the sign-in completes on its own, then `test_account` |

See [`oauth.md`](oauth.md).

## Mail tools

| Tool | What it does |
|---|---|
| `sync` | Refresh folders and the first page |
| `list_mailboxes` | Folders with roles and counts |
| `list_messages` | A page of messages (uid, date, sender, subject, snippet, ● unread ★ starred 📎 attachment); `unread_only`, `role`; `account: "*"` merges every account (each message names its account) |
| `search` | `from:` `to:` `subject:` `is:unread` `is:starred` `has:attachment` `newer_than:7d` plus words; `account: "*"` searches every account |
| `read_message` | Headers, text body (HTML reduced to text), attachment list; marks read unless `mark_read=false` |
| `save_attachment` | An attachment of a read message: without `path` the file itself, as an embedded resource (`{"type": "resource", "resource": {"uri", "mimeType", "blob"}}`, base64, up to 25 MB) with `name`, `mime`, `size` in `structuredContent`; with `path`, saved there (refused on a shared server) |
| `send_mail`, `reply`, `forward` | Send (signature added, HTML signature as a text+HTML message; optional `html` body; `attachments: [{name, data (base64), mime?}]`, up to 25 MB each, or `[{path}]` where host paths are allowed), reply with quote (`all`), forward with attachments |
| `search_contacts` | People seen in mail or written to, best match first (resolve a name into an address) |
| `mark`, `archive`, `trash`, `spam`, `move` | Flags and moves by uid |
| `save_draft`, `create_folder`, `subscribe_folder` | Drafts and folders (hide a folder from the navigation with `subscribed: false`) |
| `list_commands`, `run_command` | Every engine command |

Resources: `mailcraft://accounts` (accounts and status) and `mailcraft://inbox` (the current
inbox, one line per message).

## Prompts (plain language → configuration and mail)

| Prompt | Arguments | What it does |
|---|---|---|
| `setup_account` | `description` (required), `language` | Reads the address from the description, calls `suggest_settings`, asks for the password when it is missing (never guesses one), calls `add_account` with test, probes unknown domains, explains authentication failures with the provider's notes, applies requested preferences |
| `triage_inbox` | `account`, `focus`, `language` | Syncs, reads the unread messages that matter, groups them (reply / action / info / noise), proposes replies, archiving and deletions, acts only after a yes |
| `write_email` | `to`, `about` (required), `tone`, `language` | Drafts the message, shows it, sends or saves as draft on confirmation |
| `configure` | `request` (required) | Maps a settings request to `set_preferences` / `update_account` / the setup flow |

Example, in Claude Code: *"/setup_account la mia mail Aruba info@sirius-example.it, password
Xy…, firma «Riccardo Burrai – Sirius Technology», tema scuro"*. The assistant calls
`suggest_settings` (Aruba: `imaps.aruba.it:993`, `smtps.aruba.it:465`), then `add_account` with
`test=true`, reports the mailbox count and the SMTP login, and sets the theme.

## Scope and safety

The server runs on your machine with your rights and edits your real mailboxes. Passwords go
into `secrets.json` (0600) and are never returned by any tool. The tool descriptions tell the
assistant to show what it will send or delete and wait for a yes; keep that in mind when you
automate. There is no live control channel into a running desktop app yet (the app reads the
same configuration and cache files, so an account added through MCP appears at the next start).
