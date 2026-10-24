# Contratto applicazioni FamilOS v1

Questa directory definisce il formato dei pacchetti applicativi e il validatore per gli sviluppatori. Il runtime Orbita permette di importare manifest e PNG da Installazione → Aggiornamenti e app, installare immagini OCI per digest, assegnare adulti autorizzati e aprire l’app nel desktop. Prima occorre attivare l’agente host: vedi `docs/UPDATES.md`. La validazione statica non sostituisce il collaudo dell’immagine.

## Linguaggi e piattaforme

Lo sviluppatore può usare Python, JavaScript, Java, Rust, Go, .NET o altri linguaggi e sviluppare su qualsiasi sistema. Il risultato deve essere un'applicazione web distribuita in immagini OCI Linux compatibili con l'architettura del server. Un eseguibile desktop Windows/macOS, da solo, non è un'app FamilOS. L'interfaccia deve funzionare in un browser moderno senza plugin Java o installazioni sul dispositivo dell'utente.

Il pacchetto contiene `familos-app.json` e `assets/icon.png`. Le immagini sono pubblicate separatamente e identificate da digest immutabile. L'immagine dell'esempio è un segnaposto, non è scaricabile. Lo schema JSON è utilizzabile in editor e strumenti di qualsiasi linguaggio; il validatore Python aggiunge i controlli sui riferimenti tra servizi e sui nomi dei volumi.

```sh
python -m pip install -r packages/app-sdk/requirements.txt
python packages/app-sdk/manifest.py percorso/familos-app.json
```

## Requisiti obbligatori dell'app

- Servizio HTTP sulla porta interna dichiarata, ascolto su `0.0.0.0`; nessuna porta da chiedere all'utente. Il gateway sceglie l’indirizzo e instrada HTTP. WebSocket, streaming e redirect non sono supportati in questa versione. La porta è un dettaglio per lo sviluppatore.
- Interfaccia apribile in una finestra FamilOS; URL pubblico configurabile e asset senza indirizzi localhost o IP del server incorporati. Il gateway impone un’origine opaca con sandbox CSP e iframe senza `allow-same-origin`: gli script non condividono l’origine della console. Non aggiungere `allow-same-origin` per aggirare problemi di compatibilità.
- Endpoint di salute senza autenticazione, interno alla rete del servizio; risposta HTTP 200 solo quando l'app è pronta. Deve terminare rapidamente e non esporre segreti.
- Esecuzione con UID non root, senza privilegi, socket Docker, rete host, dispositivi o mount arbitrari. Il manifest non accetta Compose, comandi shell o hook di installazione. Il runtime deve imporre limiti CPU/RAM, capability rimosse e `no-new-privileges`.
- Accesso solo attraverso il gateway FamilOS. Il gateway deve verificare sessione e permessi per ogni app, rimuovere header di identità del client e bloccare ogni accesso diretto al container. Il contratto non autorizza a considerare attendibile un header inviato dal browser. Il gateway rimuove cookie e header del client e imposta `X-FamilOS-User` con l’ID verificato. L’app può usare questo ID per separare i dati solo perché il container non è esposto pubblicamente. Non pubblicare porte né accettare quell’header attraverso un ingresso alternativo.
- I dati persistenti risiedono esclusivamente nei volumi dichiarati sotto `/app-data/`; FamilOS assegna i percorsi host. I volumi sono specifici dell'app, non costituiscono isolamento tra utenti: questo deve essere implementato dall'app quando ospita dati personali.
- Su SIGTERM: completare o interrompere in modo coerente le scritture e terminare entro 60 secondi. Il backup ferma tutti i servizi dell'app e copia tutti i volumi; dopo arresto forzato o errore non può essere dichiarato consistente. Database esterni non sono supportati da questo contratto.
- Disinstallare conserva i dati salvo cancellazione esplicita separata. Un aggiornamento con migrazione dati richiede backup e ripristino congiunto di immagini precedenti e volumi; il solo downgrade dell'immagine non è un rollback sufficiente. `data_version` dichiara il formato dei dati, non esegue migrazioni.

## Permessi e documenti

Il manifest dichiara richieste, non concede autorizzazioni. Solo l'amministratore può installare e approvare i permessi; l'accesso dei profili resta governato da FamilOS. `children: true` non abilita automaticamente i minori.

`documents: none` consente solo i dati propri dell'app. I valori `user-selected-read` e `user-selected-read-write` riservano l'integrazione con il selettore documenti: il runtime deve rifiutarli finché non implementa un ponte autenticato e limitato ai documenti scelti. Non montare l'intero archivio come scorciatoia. Anche `network` richiede controllo effettivo del traffico nel runtime; una dichiarazione nel file non è un firewall.

## Verifiche prima dell'installazione

Il runtime deve rifiutare versioni SDK sconosciute, funzionalità non implementate e proprietà aggiuntive. Deve verificare digest, architettura effettiva dell'immagine, provenienza approvata del catalogo, PNG locale valido, compatibilità del gateway, disponibilità risorse e permessi. Non eseguire codice durante la sola importazione del manifest. Dopo l'avvio deve verificare salute, apertura dell'interfaccia, autorizzazioni e persistenza. Pubblicare un'app nel catalogo richiede anche una prova di backup/ripristino. Il validatore statico non sostituisce queste prove.


## Profilo runtime attuale

L’installazione da manifest è disponibile nell’adattatore Orbita. Le sei app Craft preintegrate mantengono il proprio flusso Git e le proprie integrazioni documentali.

- Il catalogo accetta solo `documents: none`, `network: none`, `children: false`. Le altre richieste sono rifiutate, anche se conformi allo schema futuro. Gli adulti sono autorizzati esplicitamente; gli amministratori hanno accesso. Il gateway ricontrolla accessi, MFA, scadenza login e blocco schermo a ogni richiesta.
- L’app deve usare URL relativi (`./note`, `./assets/main.js`), senza URL assoluti `/api`, `/assets` o redirect. Per gli URL generati dal server è disponibile `X-Forwarded-Prefix`; non memorizzarlo perché contiene una capacità temporanea. `FAMILOS_BASE_PATH` vale `./`. Il servizio indicato da `launch.service` è l’unico ingresso HTTP; eventuali servizi aggiuntivi restano interni.
- L’apertura crea una capacità limitata all’app e alla sessione per un’ora. Non copiarne o registrarne l’URL. I log Uvicorn la oscurano; se abiliti log di accesso su proxy esterni devi oscurare il segmento successivo a `/api/app-gateway/<id>/`. La policy `no-referrer` impedisce l’invio come Referer. Dopo scadenza, riaprire la finestra. Il logout revoca l’accesso.
- Le richieste sono limitate a 16 MiB, le risposte a 32 MiB e 30 secondi. Fetch CORS è consentito all’origine opaca `null` solo dopo la verifica della capacità. Il gateway non inoltra cookie, Authorization, redirect o Set-Cookie dell’app. Non sono supportati Service Worker, accesso diretto allo storage della console, iframe annidati o JavaScript che richieda `eval`.
- La CSP consente script inline, moduli, WebAssembly, CSS e risorse servite dal percorso dell’app. Il container usa filesystem di sistema in sola lettura e `/tmp` volatile; l’immagine non deve dichiarare volumi Docker anonimi.
- La rete Docker di ogni app è interna. Condivide quella rete con il backend gateway: non considerare il backend o le altre componenti della stessa app un ambiente ostile isolato da una VM. Questo sistema non sostituisce una sandbox kernel per codice sconosciuto; importare solo publisher fidati.
- Aggiornare richiede una versione numerica maggiore. Cambiare `data_version`, servizi o layout dei volumi richiede una migrazione assistita; non viene eseguita una migrazione arbitraria dal manifest.

## Esempio eseguibile: Note personali

`examples/notes/` contiene Dockerfile, server Python, manifest e icona. Salva note per utente con l’identità del gateway, gestisce SIGTERM e scrive esclusivamente nel volume dichiarato.

1. Compila e pubblica l’immagine nel tuo registry (amd64 e/o arm64, secondo quanto dichiari).
2. Sostituisci nel manifest il riferimento `ghcr.io/example/...` e il digest composto da zeri con il digest reale pubblicato. Il digest identifica la versione immutabile; un tag `latest` è rifiutato.
3. Esegui il validatore, quindi importa `familos-app.json` e `assets/icon.png` nel catalogo FamilOS. I registry privati richiedono credenziali Docker già configurate sul nodo per root; il manifest non contiene password.
4. Prova apertura, salvataggio, separazione tra utenti, riavvio, disattivazione e ripristino del backup su un nodo isolato prima della distribuzione.

Nessun registro pubblico di publisher o firma viene presunto: l’amministratore deve approvare la provenienza del pacchetto. Un digest garantisce identità del contenuto, non affidabilità dell’autore.
