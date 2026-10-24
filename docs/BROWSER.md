# Browser e supervisione dei siti

Sulla VM Ubuntu, dalla cartella di FamilOS già configurato:

```sh
./familos.sh browser up
```

Il comando crea il segreto locale, abilita il profilo Docker `browser`, compila e riavvia browser, backend e frontend. Il primo avvio richiede il download di Chromium. Il container ha un limite di 2 GB di RAM e tre sessioni contemporanee; prevedere risorse aggiuntive per Ollama. `./familos.sh browser down` arresta il browser. L’aggiornamento principale ricompila anche il browser se era in esecuzione.

Il browser visualizza pagine con grafica e JavaScript tramite Chromium sul server. Al dispositivo arrivano immagini, e vengono inoltrati clic, scorrimento e tastiera. Su iPad è disponibile anche il campo di inserimento testo. Cookie e sessioni dei siti sono temporanei e separati per sessione FamilOS.

## Autorizzazioni

- Primo accesso di un bambino a un dominio sconosciuto: OpenAI valuta schermata e testo prima di mostrare la pagina. Senza provider disponibile o risultato valido, la pagina resta nascosta.
- Genitori → Supervisione dei siti: valutazione anticipata del dominio, con Ollama predefinito e OpenAI selezionabile. Il genitore può catalogare un nuovo sito, ma non cambiare un blocco esistente.
- Le decisioni persistono nel database per famiglia, dominio esatto e fascia 5–8, 9–12 o 13–17. Nessuna autorizzazione implicita dei sottodomini. Non viene conservata la schermata nella tabella delle autorizzazioni.
- Amministrazione → Navigazione protetta: elenco esiti, revoca, autorizzazione manuale e richiesta di nuova verifica. La revoca prevale anche su una risposta AI in corso e viene verificata prima di restituire nuove immagini o inoltrare input. Una schermata già ricevuta viene rimossa al successivo controllo; non si possono ritirare immagini già scaricate dal dispositivo.

## Controllo settimanale

Predefinito: **domenica alle 03:00, Europe/Rome**, con gestione automatica dell’ora legale. Giorno, ora e modello Ollama sono modificabili dagli amministratori. Occorre un modello Ollama con capacità visive: un modello solo testuale non basta. Se il campo modello è vuoto, viene usato il modello Ollama configurato nel sistema.

Il processo interno al backend verifica in sequenza la pagina iniziale dei domini autorizzati, usando sessioni nuove senza login, sempre e soltanto con Ollama. Non serve una sessione amministratore aperta. Ogni ciclo è registrato nel database prima dell’esecuzione per evitare duplicazioni al riavvio. Un ciclo non ancora iniziato viene recuperato alla ripartenza; un ciclo interrotto resta segnalato come tale e non viene rieseguito automaticamente. Controllare gli esiti nel pannello.

Una valutazione negativa blocca il dominio. Errori, modello non disponibile, browser occupato e reindirizzamenti a un altro dominio conservano l’ultima decisione e producono un avviso sul sito. Non viene usato OpenAI come ripiego. I siti bloccati non vengono riabilitati automaticamente.

## Isolamento e limiti

Il container gira come utente non root, filesystem in sola lettura, senza volumi del server né socket Docker, con tutte le capability rimosse e sandbox Chromium attiva. La rete Docker è interna. Le richieste passano dal gateway FamilOS, che ammette solo HTTPS pubblico, controlla DNS e indirizzi privati anche sui reindirizzamenti e usa Pi-hole se configurato. Non esporre il servizio browser o il suo segreto su Internet. Se la VM impedisce la sandbox, correggere la configurazione del sistema: non disabilitare la sandbox e non avviare il container privilegiato.

Non sono implementati audio, webcam, upload/download di file, popup e WebSocket. I profili bambino non possono inviare richieste POST/PUT/PATCH/DELETE ai siti; alcuni moduli, consensi cookie e servizi quindi non funzionano. Alcuni siti e login rifiutano browser remoti automatizzati.

L’approvazione di un dominio è una scelta di fiducia, non una garanzia per tutte le pagine, gli annunci o i contenuti pubblicati successivamente. Il controllo settimanale riguarda la pagina iniziale visibile, non una scansione completa del sito. Pi-hole e questo browser non controllano altri browser o app del dispositivo: per quelli servono regole di rete e del dispositivo.

## Profilo seccomp

`extras/browser/seccomp_profile.json` deriva dal [profilo Playwright](https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json), progetto Microsoft, licenza Apache-2.0. Aggiunta: syscall `chroot` per la sandbox Chromium nel proprio user namespace; non viene concessa la capability sul namespace dell’host. La [documentazione Docker di Playwright](https://playwright.dev/python/docs/docker) descrive l’esecuzione non root con sandbox per pagine non attendibili.
