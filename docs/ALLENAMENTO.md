# Allenamento opzionale

Il branch `codex/allenamento` aggiunge il modulo personale, le app native e i tool Emily. Nelle nuove installazioni è disattivato. Un’installazione Home Pulse già presente viene riconosciuta e mantenuta attiva; un comando esplicito di disattivazione ha sempre precedenza e conserva dati e associazioni.

L’amministratore lo attiva da **Installazione → Aggiornamenti e app → Allenamento → Installa Allenamento**, nella stessa pagina degli altri software. In alternativa: `./familos.sh allenamento up|down|status`. La parte web e i tool sono inclusi nel framework e non richiedono un secondo servizio web. Il worker nativo di elaborazione dello storico Oura è separato: librerie, chiavi dell’anello e dati personali non vengono distribuiti nel repository. Il modulo permette live, scheda e statistiche anche senza tale worker; SpO₂ e altri risultati storici richiedono il motore configurato.

## Fasce e statistiche

Nella scheda ogni fase può avere FC minima e massima in BPM, durata e MET facoltativi. Le fasce sono scelte dall’utente; non si deducono da età o peso. Il riepilogo della sessione conserva la scheda salvata all’avvio, anche se le preferenze cambiano successivamente.

Seleziona una sessione dal pannello Allenamento per vedere tempo sotto, dentro e sopra fascia, copertura delle misure e intervalli senza dati. L’app iPhone riporta gli stessi risultati nella cronologia FamilOS. Le durate usano il valore iniziale fra due campioni reali consecutivi distanti al massimo 8 secondi. I vuoti più lunghi, le pause, i conflitti e un campione isolato non creano tempo in fascia; il tempo non configurato è separato. Sono statistiche del segnale raccolto, non una misura continua nei periodi mancanti.

## Emily

Otto tool nativi sono disponibili agli adulti abilitati, anche con i modelli locali:

- `allenamento_stato`: scheda, letture e sessioni del profilo corrente.
- `allenamento_sonno`: periodi personali con orari, durate, fasi disponibili, SpO₂ e respiro.
- `allenamento_campioni`: punti intermedi completi per metrica e intervallo.
- `allenamento_grafico`: grafico delle sei metriche e tabella dei campioni in chat.
- `allenamento_energia`: stima giornaliera con metodo e copertura.
- `allenamento_scheda`: modifica fasi e fasce su richiesta, per le nuove sessioni.
- `allenamento_analisi`: riepilogo e tempo nelle fasce di una propria sessione.
- `allenamento_controlla`: avvio, pausa, ripresa e conclusione su richiesta.

Non accettano un proprietario alternativo. I dati sono disponibili esclusivamente nella propria chat non condivisa, fuori da progetti condivisi. Nessuna credenziale o journal grezzo viene restituito al modello; i dati sanitari non vanno nella memoria di famiglia.

## Trasporto live

Con Home Pulse aperta, i frame sono prima salvati nel journal e poi inviati con una sessione HTTP immediata, con cadenza di invio non superiore a circa un batch al secondo. I campioni recenti hanno precedenza sullo storico ancora da inviare; UUID, sequenze e timestamp originali non cambiano. L’ACK rimuove dalla coda e dal journal soltanto gli eventi confermati; i record ancora necessari al modello corrente restano temporaneamente protetti. Anche a schermo bloccato una nuova notifica Bluetooth apre una finestra breve per l’invio HTTP immediato; un task di completamento protegge soltanto la singola richiesta. In assenza di eventi live, URLSession background conserva la sincronizzazione differita e durevole. Il ritorno in primo piano riprende lo stesso batch durevole, con deduplicazione lato server. Con la pagina visibile, la dashboard interroga lo stato ogni secondo e i grafici ogni cinque secondi. Lo storico viene richiesto solo nella Panoramica; passando a un’altra sezione, oppure nascondendo la pagina, le richieste non necessarie si fermano e riprendono al ritorno. La latenza effettiva dipende dalla rete e dalla disponibilità di nuove misure valide.

## Fluidità della dashboard

I grafici sono componenti memorizzati: gli aggiornamenti del timer e del solo stato live non ricalcolano lo storico. Il pannello Sonno è isolato dagli aggiornamenti della Panoramica e sospende le richieste quando la pagina non è visibile. I formattatori di data e ora vengono riutilizzati anziché ricreati per ogni punto.

Ogni serie disegna al massimo 640 campioni reali con orari originali: primo, minimo, massimo e ultimo di ciascun intervallo temporale del disegno. I confini di interruzione e qualità vengono assegnati sulla serie completa prima della selezione, evitando di unire campioni separati da un’interruzione o di scambiare la selezione grafica per un’assenza di segnale. Le serie complete rimangono disponibili per statistiche e CSV; non vengono riscritte né interpolate.

I test includono uno storico sintetico di 200.000 punti, picchi nello stesso intervallo, interruzioni, isolamento dal timer, cambio sezione e sospensione/ripresa della pagina.

## Orbita

L’integrazione nel repository FamilOS aggiunge il comando opzionale Allenamento all’entry point gestito. Durante gli aggiornamenti, `cp -a` conserva proprietari e permessi di `data`, `private` e `.env`, inclusa la libreria privata del worker UID10001. Non è una modifica al motore Orbita distribuito separatamente e non richiede pubblicare altri file della sua copia di lavoro.

Il branch non modifica `main`. Gli aggiornamenti futuri del modulo passano dalla versione del framework che lo contiene.


## Storico a blocchi e recupero sonno

Il backend elabora al massimo60000record per blocco, più gli ancoraggi originali. Fra blocchi mantiene18ore di sovrapposizione per conservare interi periodi notturni fino a16ore. Salva gli output validi e la loro identità; dopo un’interruzione riusa i blocchi riusciti. Il checkpoint complessivo avanza soltanto quando tutti i blocchi sono riusciti. I cicli successivi riprendono la coda temporale e gli eventuali record tardivi, senza eliminare lo storico principale. Limiti di densità o errori HTTP sono espliciti, senza trasformarli in una generica indisponibilità del motore.

Per Gen3 il record0x76 produce il periodo a letto solo con copertura completa nello stesso avvio e senza discontinuità dell’orologio. Non produce fasi né sonno totale. La tab Sonno distingue questa sorgente e mostra gli errori di elaborazione; le fasi native legacy, quando presenti, hanno precedenza sui riepiloghi incompleti.


## Respiro Gen3 e scale dei grafici

Il record SleepPeriodInfo2 standard0x6a fornisce media e deviazione respiratoria in ottavi di respiro/minuto. Il backend usa il mapping UTC nativo verificato, conserva sorgente, valore grezzo e metadati e pubblica i nuovi campioni su Diario, analisi, esportazioni e tool Emily. Zero resta nei record originali ma viene mostrato come non disponibile: è una policy dell’integrazione, non un sentinel dimostrato del firmware. Non si ricava il respiro dal battito e non si attribuisce una finestra temporale sconosciuta a ciascuna media.

Il pannello Sonno mostra media, minimo, massimo e numero di campioni respiratori nel periodo selezionato. La media è quella delle medie firmware disponibili, distinta dall’aggregazione notturna proprietaria Oura. I dati restano storici, con gli istanti di acquisizione originali.

Ogni grafico ha scale numeriche identiche a sinistra e destra, con riferimenti arrotondati leggibili allineati alla propria griglia e unità esplicita e dettaglio di ora/valore/unità al passaggio o al tocco sui punti. Le scale si adattano ai dati e possono differire fra metriche; le barre partono da zero. La linea di fine allenamento e l’asse temporale condividono la stessa superficie del disegno. Nei periodi di almeno24ore l’asse mostra anche le date. La serie Passi è affiancata a battito, SpO₂, temperatura, movimento e respiro. I campioni originali e le interruzioni restano invariati.


La lettura del sonno da Emily usa `allenamento_sonno`, con data di fine del periodo nel fuso Europe/Rome e paginazione esplicita. Il tool consulta lo stesso archivio del pannello Sonno; mantiene valori nulli, sorgente e stato dell’elaborazione. Le finestre sovrapposte non vanno sommate come notti distinte. L’accesso rimane limitato al proprio adulto in una chat personale non condivisa.

Gli strumenti Allenamento appartengono al gruppo **App e server MCP** in Impostazioni → Strumenti dell’assistente. Se il gruppo non è incluso nel turno, il prompt indica questa disattivazione e come verificarla; non dichiara che l’app o i dati siano assenti. Se manca il permesso del profilo, indica invece la verifica con l’amministratore. La patch rispetta gli interruttori e i permessi esistenti.


Le righe dei grafici hanno altezza definita e separazione propria; la dimensione intrinseca dell’SVG non può espandere gli assi sopra quelli della metrica seguente. La scala SpO₂ non supera100% quando i campioni sono entro tale limite. Le scale non modificano i valori registrati. Il grafico cardiaco breve indica esplicitamente gli ultimi cinque minuti, con data, anno, inizio/fine e orari sull’asse; se si consulta una sessione passata, lo specifica. Anche il confronto fra metriche riporta l’intero intervallo con data e ora di Roma.
