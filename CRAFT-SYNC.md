# Craft Sync — i Documenti di Emily sul computer

Craft Sync è l'agente della **Suite Craft** (Windows e macOS) che tiene una cartella del computer uguale ai Documenti di
Emily: i file personali, la cartella CHAT e l'archivio dell'organizzazione del proprio reparto. Si lavora in locale con le
app native (WordCraft, GridCraft, DeckCraft, PdfCraft, IdeaCraft), anche senza rete; quando la rete c'è, le modifiche
tornano su Emily con la cronologia delle versioni, e quelle fatte da Emily (browser, chat, colleghi) arrivano sul computer.

Questo documento descrive il lato Emily: come si collega un computer e le API `/api/sync/*` che l'agente usa. L'agente
stesso vive nel repository `craftsuite`.

## Collegare un computer

1. In **Impostazioni › Craft Sync** l'utente (adulto) preme *Collega un computer*: compare un codice di otto lettere e
   cifre (`ABCD-EFGH`), valido dieci minuti e una volta sola. Un nuovo codice annulla il precedente.
2. Nell'agente scrive l'indirizzo di Emily e il codice. L'agente chiama `POST /api/sync/pair` e riceve il **token del
   dispositivo** (`cs_…`), mostrato una volta sola: lo conserva nel portachiavi del sistema (Credential Manager / Keychain).
   Emily conserva solo l'hash del token.
3. Da quel momento ogni chiamata porta `Authorization: Bearer cs_…`. Il dispositivo compare in Impostazioni con nome,
   sistema, ultimo contatto e indirizzo; *Scollega* lo revoca all'istante. L'amministratore vede e revoca i computer di
   tutta l'organizzazione (*tutti i computer dell'organizzazione*).

In alternativa al codice, l'agente può chiedere direttamente **email e password di Emily** (`POST /api/sync/login`): valgono
le stesse regole del login nel browser (account attivo, adulto, non bloccato; cinque password sbagliate bloccano l'account
per un quarto d'ora). Chi ha attivato l'**app authenticator** manda anche il codice TOTP nel campo `totp`: senza, la
risposta è 401 `{"detail": {"code": "totp_required", …}}`; sbagliato, 401 `totp_invalid`. Chi ha **solo la passkey** non
può entrare così (l'agente non fa WebAuthn): 403 `{"detail": {"code": "passkey_only", …}}` e l'agente propone il codice di
Impostazioni, che resta sempre disponibile. Senza secondo fattore si entra con la sola password, come nel browser. La rotta
è limitata a 10 tentativi ogni 5 minuti per indirizzo IP **e** per email (e per utente sul codice TOTP), così né un IP può
martellare né un account può essere forzato da molti indirizzi. La risposta è identica a quella di `/pair`.

I bambini non possono collegare computer. Collegamento e revoca finiscono nel registro attività (`sync_device_paired`,
`sync_device_login`, `sync_device_revoked`, `sync_pairing_code`; i tentativi con le credenziali lasciano gli stessi eventi
del login nel browser: `login_failed`, `login_refused`, `mfa_totp_ok`, `mfa_totp_failed`), come le operazioni sui file
fatte dall'agente (attribuite all'utente).

## Il modello

- Ogni file e ogni cartella hanno un **numero di cambiamento** `seq`, preso da un contatore globale: creazione,
  sovrascrittura, rinomina, spostamento ed eliminazione lo fanno avanzare. Il client conserva l'ultimo `cursor` ricevuto e
  chiede solo ciò che è cambiato dopo.
- Un'eliminazione lascia una **lapide** (`deleted: true`) con il suo `seq`, così un computer spento per giorni sa cosa togliere.
- Il contenuto ha un'impronta **`sha256`**. Il client la confronta con quella del file locale per capire chi è cambiato e la
  manda in `If-Match` quando carica: se il file è cambiato sul server nel frattempo la scrittura è rifiutata (412) e il client
  tiene la copia locale come *«nome (conflitto di Anna, 2026-10-23 10.42).docx»*, poi scarica quella del server.
  **Emily vince, niente va perso.**
- Ogni sovrascrittura mette la versione precedente in **cronologia** (*Craft Sync · nome del computer*), con le stesse regole
  dell'editor nel browser (30 versioni per file, salvataggi ravvicinati dello stesso autore accorpati).
- I file nati prima di Craft Sync non hanno `seq` né impronta: li ricevono la prima volta che un client li chiede.
- Visibilità: i propri file (e cartelle, CHAT compresa) e quelli dell'archivio dell'organizzazione che l'utente vede nel
  browser (software *Archivio* e reparto). Nell'archivio scrive solo chi lo amministra; gli altri lo ricevono in sola lettura.
  I file condivisi da colleghi (*Condivisioni*) non rientrano nella sincronizzazione, per ora.

## API

Tutte le rotte stanno sotto `/api/sync`. Date in ISO 8601 UTC (`…Z`). Errori come nel resto di Emily: `{"detail": "…"}`.

### Con la sessione del browser (adulti)

| Rotta | Cosa fa |
|---|---|
| `GET /devices` (`?all=1` per l'amministratore) | I computer collegati: `id, name, platform, app_version, created_at, last_seen_at, last_ip, revoked` (+ `user_name` con `all`) |
| `POST /devices/pairing-code` | `{code, expires_at, minutes}` |
| `PATCH /devices/{id}` `{name}` | Rinomina |
| `DELETE /devices/{id}` | Revoca (il token smette di funzionare) |

### Collegamento (senza autenticazione, 10 tentativi per IP ogni 5 minuti)

| Rotta | Cosa fa |
|---|---|
| `POST /pair` `{code, name, platform, app_version}` | Scambia il codice di Impostazioni con il token. Codice sbagliato, scaduto o già usato: 400 |
| `POST /login` `{email, password, totp?, name, platform, app_version}` | Scambia le credenziali di Emily con il token (anche 10 tentativi per email ogni 5 minuti). Credenziali sbagliate, account bloccato o bambino: 401 `"Credenziali non valide"`; app authenticator attiva e `totp` mancante: 401 `{"code": "totp_required", "message": "Inserisci il codice dell'app authenticator"}`; codice sbagliato: 401 `{"code": "totp_invalid", "message": "Codice non valido"}`; solo passkey: 403 `{"code": "passkey_only", "message": "Questo account usa solo la passkey: collega il computer con il codice di Impostazioni › Craft Sync"}` (gli oggetti stanno in `detail`) |

Entrambe rispondono `{token, device, user: {id, username, display_name, role, departments}, server: {name, version, api,
pairing_minutes, changes_limit}}`.

### Con il token del dispositivo

| Rotta | Cosa fa |
|---|---|
| `GET /me` | Utente, dispositivo, server (controllo del token all'avvio) |
| `GET /tree` | `{folders: [...], cursor}`: tutte le cartelle visibili (l'albero lo ricostruisce il client con `parent_id`) |
| `GET /changes?since=N&limit=500` | `{since, cursor, more, items}`: file, cartelle e lapidi con `seq > N`, in ordine. `since=0` è lo stato completo. Con `more: true` si continua da `cursor` |
| `GET /events` | SSE: `{"kind":"sync"}` o `"files"` quando qualcosa cambia (poi `/changes`); `: ping` ogni 25 s |
| `GET /files/{id}` | Metadati del file |
| `GET /files/{id}/content` | Il contenuto; header `ETag` (= sha256) e `X-Sync-Seq` |
| `PUT /files/{id}/content` | Sovrascrive. **`If-Match`** obbligatorio: lo sha256 di partenza, o `*` per forzare. 412 (con `file` attuale) se il server è cambiato; 428 senza header; 403 in sola lettura; 413 oltre la dimensione massima o la quota. Stesso contenuto: 200 senza nuova versione |
| `POST /files?folder_id=&filename=&on_conflict=fail|rename` (corpo = contenuto) | File nuovo. 409 (con `file` esistente) se il nome è già usato nella cartella; `on_conflict=rename` lo chiama *nome 2.ext* |
| `PATCH /files/{id}` `{filename?, folder_id?}` | Rinomina e/o sposta (`folder_id: ""` = radice). Stesse regole dei Documenti |
| `DELETE /files/{id}` | Elimina (lapide per gli altri client) |
| `GET /files/{id}/versions`, `GET /files/{id}/versions/{vid}` | Cronologia e contenuto di una versione |
| `POST /folders` `{name, parent_id?}` | Cartella nuova |
| `PATCH /folders/{id}` `{name?, parent_id?}` | Rinomina e/o sposta |
| `DELETE /folders/{id}` | Elimina: file e sottocartelle risalgono di un livello (come nei Documenti) |

Un elemento di `items`:

```json
{"type": "file", "id": "…", "seq": 1042, "folder_id": "…", "filename": "offerta.docx", "size": 18230,
 "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "sha256": "…",
 "kind": "upload", "owner_id": "…", "access": "owner", "project_id": null, "conversation_id": null,
 "created_at": "…", "updated_at": "…"}
{"type": "folder", "id": "…", "seq": 1040, "parent_id": null, "name": "Tecnico", "folder_kind": "dept", "dept": "tecnico",
 "owner_id": "…", "conversation_id": null, "can_write": false, "created_at": "…", "updated_at": "…"}
{"type": "file", "id": "…", "seq": 1043, "deleted": true, "deleted_at": "…"}
```

`access`: `owner` (i propri), `write` (archivio, per chi lo amministra), `read`. `folder_kind`: `""` (personale), `org`
(radice dell'organizzazione), `dept` (reparto e sottocartelle), `chat`, `conv`, `msg`.

### Come lavora l'agente (riassunto)

1. All'avvio `GET /me`, poi `GET /changes?since=<cursore salvato>` finché `more` è falso; applica file, cartelle e lapidi.
2. Resta in ascolto su `GET /events`; a ogni evento rilegge `/changes`. In ogni caso rilegge ogni cinque minuti (un evento
   pubblicato mezzo secondo dopo la scrittura può non arrivare a un client appena riconnesso).
3. Osserva la cartella locale. Un file cambiato → `PUT …/content` con `If-Match` = impronta dell'ultima versione sincronizzata;
   nuovo → `POST /files`; rinominato o spostato → `PATCH`; cancellato → `DELETE` (le cancellazioni locali di file dell'archivio
   di sola lettura vengono annullate riscaricando il file).
4. 412 → copia locale rinominata come conflitto, poi scarica quella del server. Offline → la coda aspetta la rete.

## Sicurezza, in breve

- Il token non passa mai dal browser, Emily ne conserva solo l'hash, la revoca vale dalla richiesta successiva (il flusso
  SSE si chiude al battito seguente). `GET /events` consegna soltanto `sync` e `files`: mai messaggi, chiamate o notifiche.
- Ogni scrittura dal computer passa dagli stessi controlli dei Documenti nel browser: permessi, quota, dimensione massima,
  antivirus (`UPLOAD_SCAN_CMD`, anche sulle sovrascritture: il contenuto nuovo è controllato in un file temporaneo e
  sostituito in modo atomico), nomi unici, versioni, indicizzazione.
- Quando un file esce dal perimetro di chi lo vedeva (spostato in un altro reparto o nelle cartelle personali
  dell'amministratore) il suo computer riceve una lapide. Chi lo vede ancora riceve, nella stessa risposta, solo la riga
  viva. Le lapidi dell'archivio arrivano solo a chi vede quel reparto.
- L'impronta calcolata da un lettore viene salvata solo se nel frattempo nessuno ha toccato il file (stesso `seq`,
  impronta ancora vuota): un `If-Match` non può mai essere soddisfatto da un'impronta del contenuto vecchio.

## Limiti noti

- Spostare una sottocartella dell'archivio in un altro reparto cambia la visibilità dei file che contiene senza farne
  avanzare il `seq`: i client la vedono al successivo `GET /tree` (che l'agente fa all'avvio e ogni ora).
- I token non scadono e sopravvivono al cambio di password: si revocano da Impostazioni.
- I file condivisi da colleghi (*Condivisioni*) non rientrano nella sincronizzazione.
