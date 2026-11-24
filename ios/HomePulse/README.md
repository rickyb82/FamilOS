# Home Pulse per iPhone

App nativa SwiftUI, iOS 17 o successivi. Progetto Xcode senza dipendenze esterne, con SQLite, CoreBluetooth, CoreLocation, notifiche locali e grafici Swift Charts. Il server personale si configura con il QR di associazione; l’indirizzo mostrato prima dell’associazione è un esempio.

## Aprire e verificare

Aprire `HomePulse.xcodeproj` e scegliere lo schema **HomePulse**. Sul simulatore la firma non è necessaria. Per un iPhone reale selezionare il proprio Team Apple in Signing & Capabilities: questo progetto non contiene identità di firma o credenziali personali.

```sh
xcodebuild -project HomePulse.xcodeproj -scheme HomePulse \
  -destination 'platform=iOS Simulator,name=iPhone Duo,OS=27.1' \
  -derivedDataPath .build CODE_SIGNING_ALLOWED=NO test
```

Il simulatore verifica logica, archivio e interfaccia. Bluetooth, associazione protetta, notifiche di ingresso reali e continuità in background richiedono una prova sul telefono. Una sola applicazione deve possedere il collegamento all’anello durante tale prova: scollegare prima il ponte Mac.

## Prima associazione

In Impostazioni scansionare il QR proprietario FamilOS oppure incollare il codice di associazione. È supportato il link `familos-homepulse://pair?server=…&code=…`. Il codice monouso viene scambiato con un token; token e chiave anello restano nel Keychain con accessibilità dopo il primo sblocco, limitata al dispositivo. La chiave non viene mostrata, registrata nel journal o inclusa nei log.

La chiave viene ricevuta esclusivamente dal bootstrap mobile autorizzato. Se manca, l’app attende la configurazione: non reimposta l’anello e non crea nuove chiavi. L’app non contiene routine di factory reset o provisioning. Premere Cerca anello e scegliere il dispositivo solo dopo aver coordinato il passaggio dal Mac. iOS può mostrare una richiesta di associazione Bluetooth.

## Uso offline

Allenamento permette di modificare fasi, durata, ripetizioni, obiettivi FC e MET facoltativi. Timer, pausa, ripresa, conclusione e diario funzionano senza server o anello. Il calcolo energetico usa peso e MET dichiarati: kcal lorde `MET × 3,5 × kg / 200 × minuti`, kcal nette con `MET − 1`. I tratti senza peso/MET rimangono senza stima.

La scheda e il profilo vengono fotografati all’avvio della sessione. Il tempo attivo continua fino a Termina anche oltre la scheda: il tratto aggiuntivo resta senza stima MET. Le modifiche successive valgono per la sessione seguente. Il calendario predefinito usa Europe/Rome: lunedì 13:30–14:15, mercoledì 14:45–15:30 e venerdì 08:45–09:30. Il calendario non avvia automaticamente gli allenamenti.

## Palestra e notifiche

L’indirizzo iniziale è Serendip Prato, Via Giuseppe Valentini 20, 59100 Prato. Nessuna coordinata è inventata: cercare una volta l’indirizzo nell’app e controllare il punto sulla mappa; il punto può essere corretto toccando la mappa o inserendo coordinate. La posizione risolta viene conservata localmente.

Autorizzare posizione Sempre e notifiche nella schermata Palestra. L’app monitora il confine dell’area, senza raccogliere o inviare un percorso GPS continuo. Il rilevamento funziona anche fuori dai tre orari settimanali. Usa una regola tecnica modificabile: riferimento recente, aumento iniziale +15 BPM sostenuto per 30 secondi e almeno sei campioni validi. Un campione vecchio, una lunga interruzione o una sessione manuale attiva non possono avviare una nuova sessione.

Il candidato crea una sessione provvisoria e mostra esattamente:

> Sei in palestra e hai iniziato allenamento? Scrivi cosa stai facendo

Scrivi attività aggiorna la stessa sessione. Non mi alleno la contrassegna come rilevamento escluso, conservando i dati originali. Stato, candidato e attesa tra rilevamenti sono persistenti. iOS decide quando risvegliare l’app: autorizzazioni e modalità background non garantiscono CPU o Bluetooth ininterrotti, soprattutto dopo una chiusura forzata.

## Origine e conservazione dei dati

I frame sensori vengono scritti nel journal SQLite prima del filtro fisiologico. Nonce e risposte di autenticazione vengono esclusi dopo la ricostruzione del frame, anche quando arrivano frammentati o concatenati. Il formato storico FE non decodificato viene conservato come trasporto opaco e interrompe l’avanzamento del cursore.

Gli eventi hanno UUID stabile, sequenza locale e ora di acquisizione. WAL e transazioni mantengono atomici dati storici e checkpoint; il download è limitato a 64 record per richiesta, 32 pagine per passaggio. Se il backlog continua, il passaggio successivo riprende dal checkpoint; a storico esaurito viene ricontrollato ogni 60 secondi durante i risvegli disponibili. Anche stato e ultimi dati della funzione cardio attività vengono letti ogni 60 secondi quando il collegamento è libero. Non vengono inviati comandi di cancellazione memoria. Il cursore resta invariato se conteggio, ordine o formato sono incoerenti.

Una coda separata alimenta URLSession background, con ritentativi al ritorno della rete o dell’app in primo piano. La risposta server deve confermare il batch e gli ID specifici: vengono eliminati dalla coda soltanto gli ID accettati, mentre il journal rimane sul telefono. Un errore o una conferma parziale non cancella i campioni rimanenti.

Il battito live richiede stato di misura e IBI dichiarato valido dal firmware; le segnalazioni di movimento restano avvisi e non scartano da sole l’IBI. Solo i valori tra 20 e 250 BPM alimentano visualizzazione, statistiche e rilevamento palestra; i valori fuori intervallo rimangono nel journal. L’ultimo battito valido resta visibile durante le interruzioni. La Home mostra soltanto il valore BPM e la batteria; età del campione e diagnostica sono disponibili nelle Impostazioni. I campioni mancanti non vengono ricreati nei grafici o nelle statistiche. Il campo age dell’anello resta grezzo perché l’unità non è provata.

La temperatura storica usa il campo 1 dei record 0x46, convertito in °C secondo le evidenze del parser originale. È indicata come temperatura dell’anello: non temperatura interna. L’ora dei record grezzi rimane non risolta fino all’elaborazione nativa sul server. La temperatura live viene mostrata solo come raw.

SpO₂ e frequenza respiratoria provengono dai risultati storici elaborati da FamilOS, memorizzati anche sul telefono. Non vengono ricavate dal battito né simulate. Il cardio dedicato all’attività conserva stato e valori grezzi separati: per richiederlo servono età, FC a riposo e scelta esplicita dell’attività. Non viene promessa una notifica live cap3 né una cadenza di pedalata.

## Contratto server

- `POST /bridges/enroll`: codice monouso e nome dispositivo.
- `GET /mobile/bootstrap`: impostazioni, ultimi valori, sessioni, calendario e binding riservato.
- `PATCH /mobile/settings`: profilo, scheda, calendario e rilevamento.
- `GET /mobile/analysis[?session_id=…]`: grafici da memorizzare offline.
- `POST /bridges/events`: batch persistente e ACK per ID esplicito.

Una sessione iniziata dal web viene adottata sul telefono, compresa la pausa, senza creare un secondo avvio. Le modifiche locali ancora nella coda hanno precedenza: lo stato server viene applicato soltanto dopo la conferma degli eventi di allenamento.

Gli endpoint dopo l’associazione usano Bearer token. Non sono necessarie API cloud Oura. Il calcolo nativo dei dati storici avviene sul server; nessuna libreria Android viene eseguita sul telefono.

`ACTIVITY_PROTOCOL.md` documenta le sole costanti verificate usate dal controllo attività. I sorgenti proprietari consultati in ricerca sono esclusi da Git. Tutte le fixture nei test sono sintetiche.

## Batteria e notifiche

La percentuale viene letta con `0c00`, risposta `0d` con livello, progresso di carica e raccomandazione distinti. Si richiede dopo l’autenticazione, quindi ogni 15 secondi quando il collegamento e i risvegli iOS lo consentono; un errore rinvia il tentativo di 60 secondi. La risposta originale rimane nel journal e segue la normale sincronizzazione. Percentuale e data dell’ultima lettura rimangono visibili anche dopo lo scollegamento. Nessuna risposta significa valore assente.

La scheda batteria mostra anche “Non in carica”, “In carica” o “Carica completa” dal campo chargingProgress dell’anello (0, 1–99, 100); un valore sconosciuto non produce uno stato inventato. La richiesta automatica viene ripetuta ogni 15 secondi quando il collegamento è libero.

Il Gen3 può aggiungere campi al pacchetto batteria: si leggono i primi tre byte del payload dopo aver verificato il framing completo, senza imporre una lunghezza fissa di cinque byte. Le risposte valide arrivate oltre il timeout aggiornano comunque la percentuale. Lo stato della lettura è nelle impostazioni.

Una notifica locale scatta alla prima lettura al 10% o meno, se autorizzata. Lo stato persistente evita avvisi ripetuti dopo la riapertura dell’app; una successiva lettura almeno al 20% riattiva l’avviso. La richiesta del permesso compare all’apertura; il comando nelle Impostazioni permette di ricontrollarlo. La notifica non richiede un servizio push remoto.

Una seconda notifica locale, “Oura completamente carico”, scatta quando il firmware segnala chargingProgress=100. Non basta una percentuale stimata al 100%. Il latch persistente evita duplicati dopo la riapertura e durante oscillazioni 99/100 sul caricatore; si riattiva dopo una lettura non in carica, oppure durante una successiva carica al 90% o meno se la fase di utilizzo è avvenuta fuori portata. Un dato sconosciuto non genera notifiche. L’avviso usa lo stesso permesso iOS delle notifiche batteria già presenti.

Le connessioni Bluetooth pendenti vengono annullate dopo 30 secondi e riprovate automaticamente. L’ultimo errore resta visibile fino a una connessione riuscita; Scollega interrompe i tentativi. I cambiamenti di stato vengono archiviati come eventi `device_status` senza credenziali.

Con l’app aperta i campioni vengono inviati con trasporto HTTP immediato, con priorità sui dati storici e conferma dei singoli UUID. Durante i risvegli generati dalle notifiche Bluetooth il live usa lo stesso canale HTTP immediato, con una finestra breve e un task di completamento limitato alla singola richiesta. La coda URLSession background resta il recupero durevole quando non c’è una finestra live; iOS può differirne l’avvio anche con isDiscretionary=false. I riepiloghi delle sessioni FamilOS includono i tempi nelle fasce scelte nella scheda.
