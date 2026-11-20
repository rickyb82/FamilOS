# Signing in with Google and Microsoft 365 (OAuth)

> **Last updated:** 2026-10-13

Gmail and Microsoft 365 no longer accept a plain password over IMAP/SMTP (Gmail still takes
an *app password* if two-step verification is on). MailCraft signs in the way Thunderbird and
Outlook do: the provider's consent page opens in the browser, the browser comes back with a
code, MailCraft exchanges it for an access token (used as the IMAP/SMTP password with the
XOAUTH2 mechanism, renewed automatically) and a refresh token (kept in `secrets.json`, the
user-only file).

Every provider requires the application to present an *OAuth client*. Thunderbird and
Outlook carry one inside the program (Mozilla's and Microsoft's); they expose no server, the
browser comes back to `127.0.0.1`, exactly as MailCraft does on the desktop. MailCraft is open
source and the public repository ships no client, so whoever distributes it registers one
(a few minutes, free) and either **compiles it in** (then users just press *Sign in with
Google*) or gives it to the running program. Note that the "secret" of a desktop client is
not secret in the provider's own terms (the app is public): Thunderbird keeps its own in its
source code.

## 1. Create the client

### Google (Gmail, Google Workspace)

1. Open <https://console.cloud.google.com/apis/credentials> (create a project if asked).
2. *OAuth consent screen*: external, app name "MailCraft", your e-mail; add the scope
   `https://mail.google.com/`; add the Google accounts that will use MailCraft as *test users*
   (or publish the app once it is verified).
3. *Create credentials › OAuth client ID*:
   - **Desktop app** for the desktop client and the CLI (the redirect is `http://127.0.0.1`
     with any port, which Google allows for this type);
   - **Web application** for the web app, with the *authorized redirect URI*
     `https://<your mailcraft server>/api/oauth/callback`.
4. Copy the *client ID* and the *client secret*.

### Microsoft 365 (Outlook.com, Exchange Online)

1. Open the Entra admin centre › *App registrations* › *New registration*; account type
   "personal and organizational".
2. Redirect URI: type *Mobile and desktop applications* with `http://127.0.0.1` for the desktop
   client, or type *Web* with `https://<your server>/api/oauth/callback` for the web app.
3. *API permissions* › *Office 365 Exchange Online*: `IMAP.AccessAsUser.All`, `SMTP.Send`,
   plus `offline_access`.
4. *Certificates & secrets*: create a client secret. The *Application (client) ID* is the client
   id.

## 2. Give the client to MailCraft

**Compile it in** (what Thunderbird does): build with the variables set, and every copy of
that build signs in without any configuration:

```sh
MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID='1234-abcd.apps.googleusercontent.com' \
MAILCRAFT_BUILTIN_GOOGLE_CLIENT_SECRET='GOCSPX-…' \
cargo build --release          # desktop app, CLI, server; the same for MICROSOFT
docker build --build-arg MAILCRAFT_BUILTIN_GOOGLE_CLIENT_ID=… --build-arg MAILCRAFT_BUILTIN_GOOGLE_CLIENT_SECRET=… .
```

Or give it to the running program, any of:

- **Settings › Sign in with Google or Microsoft 365**: paste the id and the secret, *Save
  client*.
- **CLI:** `mailcraft-cli oauth google CLIENT_ID CLIENT_SECRET`.
- **Assistant (MCP):** "configura il client Google: id … segreto …" (`configure_oauth`).
- **Environment** (servers, Docker): `MAILCRAFT_GOOGLE_CLIENT_ID`, `MAILCRAFT_GOOGLE_CLIENT_SECRET`,
  `MAILCRAFT_MICROSOFT_CLIENT_ID`, `MAILCRAFT_MICROSOFT_CLIENT_SECRET`. The configuration file
  wins over the environment, which wins over the built-in client.

The client is stored in `config.json` under `oauth`; the secret never leaves the machine
(`oauth.providers` reports whether a client exists, not what it is).

## 3. Sign in

- **Desktop / web app:** add the account; a Gmail or Outlook address selects the provider as
  *Sign-in* automatically (choose it by hand for a Google Workspace or Microsoft 365 custom
  domain: the servers then become the provider's, `imap.gmail.com` / `smtp.gmail.com` or
  `outlook.office365.com` / `smtp.office365.com`, whatever the domain). Press *Sign in with Google*: the browser opens, you approve, the page says you can
  close it, MailCraft loads the folders.
- **CLI:** `mailcraft-cli login you@gmail.com` prints the URL, opens the browser and waits.
- **Assistant:** "aggiungi la mia casella Gmail anna@gmail.com" → `add_account` → `sign_in`
  gives the URL to open; the sign-in completes on its own.

How the browser comes back:

| Where MailCraft runs | Redirect | Client type to create |
|---|---|---|
| desktop app, CLI, MCP on your computer | a listener on `127.0.0.1` (random port), started by `account.oauthStart` | Desktop app / Mobile and desktop |
| `mailcraft-cli serve` (web app, Docker, framework) | `<public URL>/api/oauth/callback`, injected by the server from `--public-url` / `MAILCRAFT_PUBLIC_URL` or the `X-Forwarded-Proto` / `X-Forwarded-Host` / `Host` headers | Web application / Web |

The callback carries the flow's `state`; only the session that started that flow accepts it,
so no token is needed on that route.

`redirect_uri_mismatch` means the return address MailCraft sent is not, character for
character, one of those registered in the client: MailCraft shows the address it sent in the
status bar when the browser opens (Google's error page shows it too under "request details").
Compare scheme, host, port, the framework's prefix (`/mailcraft`), the trailing part
(`/api/oauth/callback`, no slash after it) and the client type (a *Desktop app* client accepts
only `http://127.0.0.1:…`; a server needs a *Web application* client). After changing
`MAILCRAFT_PUBLIC_URL`, restart the container; after changing the client in the console, wait
a minute.

The providers accept the return address only on `localhost` or on **HTTPS with a host name**:
never a private IP, never plain `http://` on a name. A framework reached as
`http://192.168.x.y:8080` therefore needs a name with a certificate in front of it and
`MAILCRAFT_PUBLIC_URL=https://that-name`; MailCraft refuses to start such a sign-in and says
so, instead of sending the browser to an error page at Google.

## Commands and tools

| Command | Tool | What it does |
|---|---|---|
| `oauth.providers` | `oauth_providers` | The providers, whether a client is configured, where to create one |
| `oauth.configure {provider, client_id, client_secret}` | `configure_oauth` | Save (or remove) the client |
| `account.oauthStart {id|email, provider?, redirect?}` | `sign_in` | The URL to open; the state of the flow |
| `account.oauthFinish {state, code|error}` | — | What the browser's return calls (loopback listener or server route) |
| `account.setPassword` | `set_password` | Switches the account back to a password (an app password) |

Accounts carry `auth: password | google | microsoft`; `has_password` is true when a password
or OAuth tokens exist. An expired access token is renewed before the next job; when the refresh
token itself is revoked, the account shows an error and *Sign in again* fixes it.

## Revision history

| Date | Change |
|---|---|
| 2026-10-13 | First version: Google and Microsoft 365 with PKCE, loopback and server callbacks, CLI and MCP |
