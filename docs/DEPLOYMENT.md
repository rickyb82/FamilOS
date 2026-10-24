# Deployment e sviluppo

## Configurazione

Copiare `.env.example` in `.env`; impostare SECRET_KEY casuale, credenziali amministratore e provider. Conservare SECRET_KEY nei backup: cifra le impostazioni sensibili nel database.
I valori del pannello prevalgono sull'ambiente. Le vecchie chiavi dei provider e servizi rimossi vengono ignorate. Mock disabilitato normalmente; `ENABLE_MOCK_PROVIDER=1` lo abilita per le sole prove.

## HTTPS

Impostare PUBLIC_HOST, ACME_EMAIL, WEBAUTHN_RP_ID e WEBAUTHN_ORIGIN sul dominio, COOKIE_SECURE=true; eseguire `./familos.sh prod`. Per ACME, porte 80/443 raggiungibili.
Per rete privata: TLS_MODE=internal ed esportazione CA con `./familai.sh ca-cert`.
`./familos.sh dev` espone l'anteprima HTTP su localhost:8080, con cookie non Secure, per sviluppo.

## Suite e backup

`./familos.sh productivity up|down|upgrade|status` gestisce i cinque editor e MailCraft. Singola app: `./familai.sh wordcraft up`, analogamente per le altre.
Il token GitHub di lettura per i repository privati si configura con `./familai.sh github-token`, resta in `private/github-token` escluso dal Git e viene passato come segreto di build.
MailCraft crea sessioni separate per utente; configurare le caselle nella sua interfaccia.

`./familos.sh backup` archivia dati, .env e private/. Proteggere gli archivi: contengono segreti.
`./familos.sh restore backups/<archivio>.tar.gz` sostituisce i dati correnti.
`./familos.sh update` aggiorna da origin FamilOS; `productivity upgrade` aggiorna le app Craft.

## Sviluppo locale

Python 3.12+ e Node 22.12+:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend
COOKIE_SECURE=0 REQUIRE_MFA_FOR_ADULTS=0 OLLAMA_URL=http://127.0.0.1:11434 ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In un altro terminale:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Aprire http://127.0.0.1:5173. Bootstrap dall'ambiente o backend/.env. Vite inoltra API/file a :8000; editor a porte locali 19781–19785, MailCraft a 17878. Questi servizi vanno avviati separatamente; in Docker completo li instrada Caddy.

## Limiti

Layout del desktop salvato nel browser, non sincronizzato tra dispositivi. I bambini usano l'interfaccia protetta precedente.
La PWA è installabile ma non sincronizza documenti offline. Backend e revisioni richiedono un singolo worker.
I costi sono stime modificabili: verificare il listino rispetto al modello e all'account OpenAI.

## App Installazione e certificati

Solo gli amministratori accedono a /installation e alle API di gestione. Il logo pubblico è visibile anche sulla pagina di login.
Impostare il dominio e la modalità TLS: Let’s Encrypt con rinnovo automatico, staging, CA interna o caricamento PEM di certificato e chiave corrispondente.
Il pannello valida dominio, scadenza e corrispondenza della chiave; non restituisce mai la chiave privata. Il controllo HTTPS legge il certificato realmente esposto dal dominio.
Il salvataggio aggiorna subito gli URL OAuth e il RP ID delle passkey. Per applicare la configurazione al proxy eseguire sul server ./familos.sh domain apply: conserva il precedente .env in private/, verifica Caddy e ricrea backend e proxy. Non modifica automaticamente DNS o firewall.
Le vecchie passkey restano legate al dominio precedente e vanno registrate sul nuovo. La chiave privata PEM resta in data/installation/tls con permessi 600. I backup cifrati dal pannello includono branding, configurazione installazione e versioni.
