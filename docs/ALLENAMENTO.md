# Allenamento opzionale

Il branch `codex/allenamento` aggiunge il modulo personale, le app native e i tool Emily. Nelle nuove installazioni è disattivato. Un’installazione Home Pulse già presente viene riconosciuta e mantenuta attiva; un comando esplicito di disattivazione ha sempre precedenza e conserva dati e associazioni.

L’amministratore lo attiva da **Installazione → Aggiornamenti e app → Allenamento → Installa Allenamento**, nella stessa pagina degli altri software. In alternativa: `./familos.sh allenamento up|down|status`. La parte web e i tool sono inclusi nel framework e non richiedono un secondo servizio web. Il worker nativo di elaborazione dello storico Oura è separato: librerie, chiavi dell’anello e dati personali non vengono distribuiti nel repository. Il modulo permette live, scheda e statistiche anche senza tale worker; SpO₂ e altri risultati storici richiedono il motore configurato.

## Fasce e statistiche

Nella scheda ogni fase può avere FC minima e massima in BPM, durata e MET facoltativi. Le fasce sono scelte dall’utente; non si deducono da età o peso. Il riepilogo della sessione conserva la scheda salvata all’avvio, anche se le preferenze cambiano successivamente.

Seleziona una sessione dal pannello Allenamento per vedere tempo sotto, dentro e sopra fascia, copertura delle misure e intervalli senza dati. L’app iPhone riporta gli stessi risultati nella cronologia FamilOS. Le durate usano il valore iniziale fra due campioni reali consecutivi distanti al massimo 8 secondi. I vuoti più lunghi, le pause, i conflitti e un campione isolato non creano tempo in fascia; il tempo non configurato è separato. Sono statistiche del segnale raccolto, non una misura continua nei periodi mancanti.

## Emily

Quattro tool nativi sono disponibili agli adulti abilitati, anche con i modelli locali:

- `allenamento_stato`: scheda, letture e sessioni del profilo corrente.
- `allenamento_scheda`: modifica fasi e fasce su richiesta, per le nuove sessioni.
- `allenamento_analisi`: riepilogo e tempo nelle fasce di una propria sessione.
- `allenamento_controlla`: avvio, pausa, ripresa e conclusione su richiesta.

Non accettano un proprietario alternativo. I dati sono disponibili esclusivamente nella propria chat non condivisa, fuori da progetti condivisi. Nessuna credenziale o journal grezzo viene restituito al modello; i dati sanitari non vanno nella memoria di famiglia.

## Trasporto live

Con Home Pulse aperta, i frame sono prima salvati nel journal e poi inviati con una sessione HTTP immediata, con cadenza di invio non superiore a circa un batch al secondo. I campioni recenti hanno precedenza sullo storico ancora da inviare; UUID, sequenze e timestamp originali non cambiano. L’ACK rimuove solo i riferimenti della coda, mai il journal. Anche a schermo bloccato una nuova notifica Bluetooth apre una finestra breve per l’invio HTTP immediato; un task di completamento protegge soltanto la singola richiesta. In assenza di eventi live, URLSession background conserva la sincronizzazione differita e durevole. Il ritorno in primo piano riprende lo stesso batch durevole, con deduplicazione lato server. Con la pagina visibile, la dashboard interroga lo stato ogni secondo e i grafici ogni cinque secondi. Lo storico viene richiesto solo nella Panoramica; passando a un’altra sezione, oppure nascondendo la pagina, le richieste non necessarie si fermano e riprendono al ritorno. La latenza effettiva dipende dalla rete e dalla disponibilità di nuove misure valide.

## Fluidità della dashboard

I grafici sono componenti memorizzati: gli aggiornamenti del timer e del solo stato live non ricalcolano lo storico. Il pannello Sonno è isolato dagli aggiornamenti della Panoramica e sospende le richieste quando la pagina non è visibile. I formattatori di data e ora vengono riutilizzati anziché ricreati per ogni punto.

Ogni serie disegna al massimo 640 campioni reali con orari originali: primo, minimo, massimo e ultimo di ciascun intervallo temporale del disegno. I confini di interruzione e qualità vengono assegnati sulla serie completa prima della selezione, evitando di unire campioni separati da un’interruzione o di scambiare la selezione grafica per un’assenza di segnale. Le serie complete rimangono disponibili per statistiche e CSV; non vengono riscritte né interpolate.

I test includono uno storico sintetico di 200.000 punti, picchi nello stesso intervallo, interruzioni, isolamento dal timer, cambio sezione e sospensione/ripresa della pagina.

## Orbita

L’integrazione nel repository FamilOS aggiunge il comando opzionale Allenamento all’entry point gestito. Durante gli aggiornamenti, `cp -a` conserva proprietari e permessi di `data`, `private` e `.env`, inclusa la libreria privata del worker UID10001. Non è una modifica al motore Orbita distribuito separatamente e non richiede pubblicare altri file della sua copia di lavoro.

Il branch non modifica `main`. Gli aggiornamenti futuri del modulo passano dalla versione del framework che lo contiene.
