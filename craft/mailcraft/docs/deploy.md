# Deploying MailCraft for a framework (Docker, multi-user)

> **Last updated:** 2026-10-13

MailCraft's server (`mailcraft-cli serve`) can host **one mailbox client per user of your
framework**: each user has their own accounts, passwords and cache under `/data/users/<user>/`,
the framework authenticates with a service token, and users get per-user tokens for the web
page and for their MCP assistant. Nothing is shared between users.

## Run it

```sh
docker build -t mailcraft .
docker run -d --name mailcraft -p 127.0.0.1:7878:7878 -v mailcraft-data:/data \
  -e MAILCRAFT_TOKEN='a-long-random-secret' -e MAILCRAFT_LANG=it mailcraft
```

or `MAILCRAFT_TOKEN=… docker compose up -d`. The container serves the web app at `/` and the API
under `/api/`. Put your framework or an HTTPS reverse proxy in front: the service token and the
user tokens travel in `Authorization: Bearer …`, so the connection must be TLS beyond localhost.

Without Docker: `mailcraft-cli serve --bind 0.0.0.0:7878 --users-dir /data/users --token SECRET
--static dist/web` (the web app built with `trunk build --release` in `apps/mailcraft-web`).

## Identity: how the framework names the user

Every API call carries a token:

| Token | Who | Names the user with |
|---|---|---|
| the **service token** (`MAILCRAFT_TOKEN`, at least 32 characters) | the framework's backend | header `X-Mailcraft-User: <user id>` (only the header: a `?user=` is ignored) |
| a **user token** | the browser page or the user's MCP process | nothing: the token *is* the user |

User ids are slugged (`Anna Rossi` → `anna-rossi`); use your framework's stable user id.

The API takes the token only in the `Authorization: Bearer …` header, never in the URL (a
`?token=` on an API call is ignored), so tokens stay out of proxy and access logs. The server
never prints its service token: it is the `MAILCRAFT_TOKEN` / `--token` you started it with.
Every session the server serves works on the server's files for nobody: the attachment commands
take and give base64 (`attachments[].data`, `mail.attachment` without `path`); a `path` is
refused, in multi-user and single-user mode alike.

### Minting a user token (service token only)

```http
POST /api/users/<user>/token
Authorization: Bearer <service token>
→ {"user": "anna-rossi", "token": "64 hex chars"}
```

`POST /api/users/<user>/revoke` revokes every token of that user (log-out); `GET /api/users`
lists the users with data. Tokens are kept in `/data/users/tokens.json` (user-only file).

## Embedding the web app

When the user opens the mail section of your framework, mint (or reuse) their token and open
the page with it:

```
https://mail.example.com/?lang=it#token=<user token>
```

The token goes in the fragment (`#token=`): the browser never sends it to a server, so it
stays out of the proxy's and the server's access logs. The page reads it (the fragment first,
then `?token=` for older links), takes it out of the address bar at once and connects to its
own origin (the server that served it) sending the token in the `Authorization` header; the
API never reads it from the URL. Inside another page (the framework's iframe) the token is
kept only in memory; on its own page the browser keeps it in local storage. The page can also
be hosted elsewhere (GitHub Pages) and pointed at the server with
`?server=https://mail.example.com`: with a service token the server answers CORS for any
origin (the bearer token, which a browser never adds by itself, is what protects the data).

Without a token (a single user on their own computer) the server answers only to this
machine: `Host` must be `127.0.0.1`, `localhost` or `[::1]` on the server's port (against DNS
rebinding) and a browser's `Origin` must be a page of this machine or one given with
`--allow-origin` / `MAILCRAFT_ALLOW_ORIGINS`; `Access-Control-Allow-Private-Network` is sent
only on a preflight from an allowed origin. Binding beyond localhost needs a token of at least
32 characters.

## MCP per user (the assistant inside your framework)

Run one MCP process per logged-in user, pointed at the server with that user's token:

```sh
mailcraft-cli mcp --server https://mail.example.com --token <user token>
```

It exposes the same tools and prompts as the local MCP server (`docs/mcp.md`), executed on the
server against that user's accounts: *"configura la mia casella Aruba …"*, *"leggi le mail non
lette"*, *"rispondi a Luca…"*. The process is stateless apart from the token, so it can be
started and stopped with the conversation.

## Calling the API directly (framework backend)

```http
POST /api/cmd
Authorization: Bearer <service token>
X-Mailcraft-User: anna-rossi
Content-Type: application/json

{"command": "account.add", "params": {"email": "anna@example.it", "password": "…"}}
→ {"result": {...}}     or     {"error": "…"}
```

Network commands answer `{"result": {"job": 12}}`; `GET /api/snapshot?since=<revision>` returns
the user's state (folders, the mailbox in view, opened messages, finished jobs) when it changed,
`{"unchanged": true}` otherwise. `GET /api/health` needs no token. Every command is listed by
`mailcraft-cli commands` and in `crates/engine/src/cmd.rs`.

## Reply suggestions and drafts from your assistant

Start the server with `--assist-url /api/mail/assist` (or `MAILCRAFT_ASSIST_URL`): the web
app then `POST`s to that address on its own origin, with the page's session cookie and the
header `X-Requested-With: FamilAI`, and shows the answers in the mailbox:

- when a message is opened, `{"kind": "suggest", "subject", "sender", "text", "local_only",
  "language"}` → `{"suggestions": ["…", "…", "…"]}` becomes up to five chips under the message;
  a click opens the reply with that sentence;
- in the compose window, *Write with the assistant* sends `{"kind": "draft", "subject",
  "sender", "text", "to", "instruction", "tone", "local_only", "language"}` → `{"draft": "…"}`
  replaces the note above the quote.

`local_only` follows the preference *Let the assistant use cloud models too* (off by default,
so the mail never leaves the network); the preference *Reply suggestions* turns the chips off.
An error answer (`{"error": "…"}` or a non-2xx status) is shown in the status bar. The desktop
app and the CLI have no assistant (the chips do not appear there).

## Gmail and Microsoft 365 (OAuth)

The providers accept a redirect URI only on **HTTPS with a host name** (or on `localhost`,
which works only for someone sitting on the server itself): `http://192.0.2.80:8080/…`
is refused by Google with "device_id and device_name are required for private IP". So a
framework on a LAN needs a name and a certificate in front of MailCraft (a reverse proxy with
Let's Encrypt on a real domain, or an internal CA your browsers trust), and MailCraft must
know that address: set `MAILCRAFT_PUBLIC_URL=https://mail.example.com` so every user's
sign-in returns there whatever address they typed. MailCraft refuses to start a sign-in whose
return address a provider would reject, and says why.

Create a *Web application* OAuth client for each provider with the redirect URI
`https://mail.example.com/api/oauth/callback` and give it to the container:
`MAILCRAFT_GOOGLE_CLIENT_ID`, `MAILCRAFT_GOOGLE_CLIENT_SECRET` (and the `MICROSOFT` pair). Set
`MAILCRAFT_PUBLIC_URL=https://mail.example.com` when the proxy does not send
`X-Forwarded-Proto`/`X-Forwarded-Host`. Users then pick *Google* as sign-in in the account form
and approve in the browser; the provider sends them back to the callback, which completes the
sign-in of the session that started it. Details: [`oauth.md`](oauth.md).

## Data and backups

```
/data/users/<user>/config.json    accounts and preferences
/data/users/<user>/secrets.json   passwords (0600)
/data/users/<user>/cache/         folders and message lists
/data/users/tokens.json           user tokens
```

Back up `/data`; to remove a user, revoke their tokens and delete their directory.

## Limits (0.1)

- Polling, not push: the page asks for changes every 2 s (0.4 s while working).
- No HTTPS in `serve` itself: terminate TLS in the proxy or the framework.
- One server process keeps every active user's IMAP connections; a few hundred users per
  instance is the intended scale.
