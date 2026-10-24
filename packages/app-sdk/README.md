# Contratto applicazioni FamilOS v1

Questa directory definisce il formato dei pacchetti applicativi e il validatore per gli sviluppatori. Il caricamento dinamico nel catalogo, il gateway delle app e l'installazione da interfaccia web devono ancora essere collegati a questo contratto: un manifest valido non rende già installabile l'app nella versione corrente di FamilOS.

## Linguaggi e piattaforme

Lo sviluppatore può usare Python, JavaScript, Java, Rust, Go, .NET o altri linguaggi e sviluppare su qualsiasi sistema. Il risultato deve essere un'applicazione web distribuita in immagini OCI Linux compatibili con l'architettura del server. Un eseguibile desktop Windows/macOS, da solo, non è un'app FamilOS. L'interfaccia deve funzionare in un browser moderno senza plugin Java o installazioni sul dispositivo dell'utente.

Il pacchetto contiene `familos-app.json` e `assets/icon.png`. Le immagini sono pubblicate separatamente e identificate da digest immutabile. L'immagine dell'esempio è un segnaposto, non è scaricabile. Lo schema JSON è utilizzabile in editor e strumenti di qualsiasi linguaggio; il validatore Python aggiunge i controlli sui riferimenti tra servizi e sui nomi dei volumi.

```sh
python -m pip install -r packages/app-sdk/requirements.txt
python packages/app-sdk/manifest.py percorso/familos-app.json
```

## Requisiti obbligatori dell'app

- Servizio HTTP sulla porta interna dichiarata, ascolto su `0.0.0.0`; nessuna porta da chiedere all'utente. Il gateway sceglie l'indirizzo e instrada HTTP e WebSocket. La porta è un dettaglio per lo sviluppatore.
- Interfaccia apribile in una finestra FamilOS; URL pubblico configurabile e asset senza indirizzi localhost o IP del server incorporati. Il gateway deve usare origini isolate per le app: script di terze parti non devono condividere l'origine della console.
- Endpoint di salute senza autenticazione, interno alla rete del servizio; risposta HTTP 200 solo quando l'app è pronta. Deve terminare rapidamente e non esporre segreti.
- Esecuzione con UID non root, senza privilegi, socket Docker, rete host, dispositivi o mount arbitrari. Il manifest non accetta Compose, comandi shell o hook di installazione. Il runtime deve imporre limiti CPU/RAM, capability rimosse e `no-new-privileges`.
- Accesso solo attraverso il gateway FamilOS. Il gateway deve verificare sessione e permessi per ogni app, rimuovere header di identità del client e bloccare ogni accesso diretto al container. Il contratto non autorizza a considerare attendibile un header inviato dal browser. Fino a disponibilità di un protocollo di identità verificabile, le app non devono implementare isolamento utenti basandosi su header inventati.
- I dati persistenti risiedono esclusivamente nei volumi dichiarati sotto `/app-data/`; FamilOS assegna i percorsi host. I volumi sono specifici dell'app, non costituiscono isolamento tra utenti: questo deve essere implementato dall'app quando ospita dati personali.
- Su SIGTERM: completare o interrompere in modo coerente le scritture e terminare entro 60 secondi. Il backup ferma tutti i servizi dell'app e copia tutti i volumi; dopo arresto forzato o errore non può essere dichiarato consistente. Database esterni non sono supportati da questo contratto.
- Disinstallare conserva i dati salvo cancellazione esplicita separata. Un aggiornamento con migrazione dati richiede backup e ripristino congiunto di immagini precedenti e volumi; il solo downgrade dell'immagine non è un rollback sufficiente. `data_version` dichiara il formato dei dati, non esegue migrazioni.

## Permessi e documenti

Il manifest dichiara richieste, non concede autorizzazioni. Solo l'amministratore può installare e approvare i permessi; l'accesso dei profili resta governato da FamilOS. `children: true` non abilita automaticamente i minori.

`documents: none` consente solo i dati propri dell'app. I valori `user-selected-read` e `user-selected-read-write` riservano l'integrazione con il selettore documenti: il runtime deve rifiutarli finché non implementa un ponte autenticato e limitato ai documenti scelti. Non montare l'intero archivio come scorciatoia. Anche `network` richiede controllo effettivo del traffico nel runtime; una dichiarazione nel file non è un firewall.

## Verifiche prima dell'installazione

Il runtime deve rifiutare versioni SDK sconosciute, funzionalità non implementate e proprietà aggiuntive. Deve verificare digest, architettura effettiva dell'immagine, provenienza approvata del catalogo, PNG locale valido, compatibilità del gateway, disponibilità risorse e permessi. Non eseguire codice durante la sola importazione del manifest. Dopo l'avvio deve verificare salute, apertura dell'interfaccia, autorizzazioni e persistenza. Pubblicare un'app nel catalogo richiede anche una prova di backup/ripristino. Il validatore statico non sostituisce queste prove.
