# Home Pulse per iPhone

App nativa SwiftUI, iOS 17 o successivi. Progetto Xcode con runtime PyTorch locale opzionale, SQLite, CoreBluetooth, CoreLocation, notifiche locali e grafici Swift Charts. Il server personale si configura con il QR di associazione; l’indirizzo mostrato prima dell’associazione è un esempio.

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

Allenamento permette di modificare fasi, durata, ripetizioni, obiettivi FC e MET facoltativi. Timer, pausa, ripresa e conclusione funzionano senza server o anello. Il diario offline contiene le sessioni e i campioni ancora da inviare; lo storico sincronizzato si consulta dal server. Il calcolo energetico usa peso e MET dichiarati: kcal lorde `MET × 3,5 × kg / 200 × minuti`, kcal nette con `MET − 1`. I tratti senza peso/MET rimangono senza stima.

La scheda e il profilo vengono fotografati all’avvio della sessione. Il tempo attivo continua fino a Termina anche oltre la scheda: il tratto aggiuntivo resta senza stima MET. Le modifiche successive valgono per la sessione seguente. Il calendario iniziale è vuoto e usa Europe/Rome; gli appuntamenti vengono configurati dal proprietario. Il calendario non avvia automaticamente gli allenamenti.

## Palestra e notifiche

Nessuna palestra è preconfigurata: inserire il proprio indirizzo nelle impostazioni. Nessuna coordinata è inventata: cercare una volta l’indirizzo nell’app e controllare il punto sulla mappa; il punto può essere corretto toccando la mappa o inserendo coordinate. La posizione risolta viene conservata localmente.

Autorizzare posizione Sempre e notifiche nella schermata Palestra. L’app monitora il confine dell’area, senza raccogliere o inviare un percorso GPS continuo. Il rilevamento funziona anche fuori dai tre orari settimanali. Usa una regola tecnica modificabile: riferimento recente, aumento iniziale +15 BPM sostenuto per 30 secondi e almeno sei campioni validi. Un campione vecchio, una lunga interruzione o una sessione manuale attiva non possono avviare una nuova sessione.

Il candidato crea una sessione provvisoria e mostra esattamente:

> Sei in palestra e hai iniziato allenamento? Scrivi cosa stai facendo

Scrivi attività aggiorna la stessa sessione. Non mi alleno la contrassegna come rilevamento escluso, conservando i dati originali. Stato, candidato e attesa tra rilevamenti sono persistenti. iOS decide quando risvegliare l’app: autorizzazioni e modalità background non garantiscono CPU o Bluetooth ininterrotti, soprattutto dopo una chiusura forzata.

## Origine e conservazione dei dati

I frame sensori vengono scritti nel journal SQLite prima del filtro fisiologico. Nonce e risposte di autenticazione vengono esclusi dopo la ricostruzione del frame, anche quando arrivano frammentati o concatenati. Il formato storico FE non decodificato viene conservato come trasporto opaco e interrompe l’avanzamento del cursore.

Gli eventi hanno UUID stabile, sequenza locale e ora di acquisizione. WAL e transazioni mantengono atomici dati storici e checkpoint; il download è limitato a 64 record per richiesta, 32 pagine per passaggio. Se il backlog continua, il passaggio successivo riprende dal checkpoint; a storico esaurito viene ricontrollato ogni 60 secondi durante i risvegli disponibili. Anche stato e ultimi dati della funzione cardio attività vengono letti ogni 60 secondi quando il collegamento è libero. Non vengono inviati comandi di cancellazione memoria. Il cursore resta invariato se conteggio, ordine o formato sono incoerenti.

Una coda separata alimenta URLSession background, con ritentativi al ritorno della rete o dell’app in primo piano. La risposta server deve confermare il batch e gli ID specifici: vengono eliminati dalla coda soltanto gli ID accettati, e i rispettivi payload vengono eliminati anche dal journal locale dopo il commit della conferma. Un errore o una conferma parziale non cancella i campioni rimanenti.

Il battito live richiede stato di misura e IBI dichiarato valido dal firmware; le segnalazioni di movimento restano avvisi e non scartano da sole l’IBI. Solo i valori tra 20 e 250 BPM alimentano visualizzazione, statistiche e rilevamento palestra; i valori fuori intervallo rimangono nel journal. L’ultimo battito valido resta visibile durante le interruzioni. La Home mostra soltanto il valore BPM e la batteria; età del campione e diagnostica sono disponibili nelle Impostazioni. I campioni mancanti non vengono ricreati nei grafici o nelle statistiche. Il campo age dell’anello resta grezzo perché l’unità non è provata.

La temperatura storica usa il campo 1 dei record 0x46, convertito in °C secondo le evidenze del parser originale. È indicata come temperatura dell’anello: non temperatura interna. L’ora dei record grezzi rimane non risolta fino all’elaborazione nativa sul server. L’header storico usa tick da 100 ms; il cursore resta un UInt32 opaco e avanza di uno dopo l’archiviazione della pagina. La conversione in UTC richiede le ancore TimeSync, senza usare l’ora del download come acquisizione. La temperatura live viene mostrata solo come raw.

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


Con lo schermo spento, finché il firmware indica carica in corso o completa, l’app programma con `CBConnectPeripheralOptionStartDelayKey` un nuovo collegamento Bluetooth dopo 60 secondi. È il sistema a gestire la richiesta pendente e a risvegliare l’app al collegamento; non dipende da un timer Swift rimasto attivo. Ogni controllo autentica nuovamente il proprietario, legge batteria e storico e conserva cursori e credenziali. Al ritorno in primo piano il collegamento riprende subito. Una lettura non in carica termina questo ciclo e ripristina la connessione continua; Scollega interrompe anche i controlli pendenti. La consegna della notifica usa un task breve di completamento, che termina dopo la singola richiesta a iOS.

## Interfaccia e archivio indipendenti

Apertura dell’archivio, migrazioni, ricostruzione dei frame BLE, scritture e preparazione dei lotti avvengono fuori dal thread dell’interfaccia. Le viste usano contatori e riepiloghi in memoria. Gli allenamenti sono confermati in interfaccia dopo la scrittura durevole, senza bloccare la navigazione. Il test UI tiene intenzionalmente occupato per 15 secondi solo un archivio isolato e verifica che Home, Diario e Impostazioni continuino a rispondere.

La coda usa un indice sulla sequenza originale: il recupero del lotto successivo non ordina ripetutamente tutti i payload pendenti. Un lotto già preparato viene riproposto con gli stessi UUID fino alla conferma. Su Wi-Fi o Ethernet non vincolati si inviano fino a 1.000 eventi per richiesta e si prosegue subito dopo la conferma completa; su altre reti il lotto è 128. Errori e conferme parziali conservano i record e introducono un’attesa prima del tentativo successivo. Lo storico riconosciuto è archiviato una sola volta per anello e contenuto; lo stesso record ricevuto di nuovo non crea altri eventi. Il cursore avanza solo dopo la convalida dell’intera pagina.

I grafici server vengono preparati fuori dall’interfaccia e visualizzano al massimo 300 punti reali, conservando estremi e orari originali. Il contenuto completo resta nell’archivio e nelle statistiche.

## Sonno

La scheda Sonno legge `mobile/sleep?days=14` e conserva la risposta per consultazione offline. Mostra i periodi restituiti dal motore nativo, tempo a letto, sonno totale e riepiloghi delle fasi quando presenti. La sequenza delle fasi usa il passo nativo 30 secondi o 5 minuti e un asse relativo: non viene inventato l’allineamento assoluto di epoche parziali. Valori non restituiti restano assenti. Il calendario è raggiungibile da Allenamento → Calendario allenamenti.

Il decoder è verificato sul contratto nativo e su fixture sintetiche. La disponibilità di periodi reali dopo l’aggiornamento del server deve essere verificata separatamente. La prova fisica della notifica di carica completa a schermo spento resta aperta: nella precedente prova il messaggio è arrivato solo all’apertura dell’app.


## SpO₂ automatica e continuità

Per il Gen3 con capability4 versione1/2, il ponte verifica la modalità SpO₂ e imposta AUTOMATIC come nell’app originale; quindi applica i parametri HealthSpo2 verificati (ciclo richiesto1minuto, lavoro100%, ritardo iniziale3minuti, correnti LED30/24 e restanti byte0). Cache locale e ACK impediscono riscritture a ogni controllo. Il risultato grezzo di GetFeatureStatus resta distinto dalla conferma del comando e non viene presentato come una misura. Per capability3+ si mantiene la parametrizzazione del firmware finché l’estensioneHFM non è verificata.

Il ciclo è una richiesta al firmware. Il software non crea percentuali a ogni minuto e non interpreta la modalità AUTOMATIC o l’ACK come prova di una nuova misura valida. I valori mostrati provengono dai record realmente elaborati, con data/ora di acquisizione. La coesistenza con HRlive e la disponibilità di misure diurne richiedono osservazione sul dispositivo.

I contatori degli invii sono osservati solo dalle viste di sincronizzazione, senza invalidare l’intera schermata a ogni conferma. Il timer usa TimelineView nella sola scheda Allenamento; il grafico locale rappresenta fino a150 punti reali preservando estremi, orari e identità. Tutti i campioni restano archiviati. I callback UIKit e il ripristino Bluetooth inizializzano il ponte anche senza una scena visibile. La distribuzione personale usa la configurazione Release ottimizzata.


## Protezione dai blocchi su diagnostica e riconnessione

Le fotografie tecniche di batteria/notifiche usano una coda dedicata: attese su file e codifica non vengono eseguite dall’attore UI. Le richieste ravvicinate si accorpano nell’ultimo stato e le scritture sono distanziate di almeno5secondi. I file derivati sono sostituiti tramite un file temporaneo nella stessa directory, permessi0600, protezione dopo il primo sblocco e renamePOSIX; non si usa la ricerca Foundation della directory di sostituzione. Il journal sensori durevole resta il riferimento per i file di upload ricostruibili.

Il test UI blocca intenzionalmente per15secondi una scrittura diagnostica in un archivio isolato, passa alla Home dell’iPhone, riattiva l’app e verifica la navigazione prima che la scrittura termini. Il salvataggio di uno stato chiave/valore invariato non modifica nuovamente SQLite; il rilevamento palestra non salva lo stato quando il motore non lo modifica.

I callback di connessione rifiutata non rilanciano subito un’altra richiesta: i tentativi hanno attese5/10/20/30/60secondi e si annullano con Scollega. I controlli di carica pianificati mantengono il collegamento ritardato gestito dal sistema. Una connessione già pendente non viene duplicata e una disconnessione in corso deve terminare prima del tentativo seguente.


## Tastiera e preparazione del cardio allenamento

I moduli del profilo, scheda, palestra e allenamento hanno il pulsante Fine sopra la tastiera, invio Done sui campi compatibili e chiusura interattiva con lo scorrimento e chiusura toccando fuori dai campi. Il gesto non consuma i tocchi dei pulsanti e non chiude la tastiera passando a un altro campo. Fine conclude l’immissione e nasconde la tastiera; Salva impostazioni conserva e sincronizza il profilo. Il test UI inserisce un’età sintetica nel solo archivio isolato, chiude il tastierino numerico e cambia scheda senza riavviare l’app.

Con capabilityExerciseHR3 versione positiva, i parametri di registrazione usano attività tecnica UNIFIED0, come il caller originale; lo sport scelto resta nei metadati della sessione. Il risultato grezzo di GetFeatureStatus non viene trattato come ACK del comando successivo. Il grafico locale può aggiornarsi fino a ogni200ms quando riceve nuovi campioni; questo non stabilisce la frequenza del firmware e non genera valori mancanti.

La diagnostica contiene contatori di pacchetti ricevuti/validi e di IBI validi scartati dai filtri di stato/flag, senza esportare valori cardiaci. Servono a distinguere problemi di ricezione, qualità e presentazione durante le prove di movimento. La validazione del flusso liveExerciseHR resta distinta dalla configurazione accettata dall’anello.


La scheda Cardio durante l’attività mostra l’avanzamento della richiesta e distingue gli errori di preparazione, parametri e avvio. La diagnostica tecnica conserva al massimo16 conferme mode/parametri delle funzioni2/3/4 (solo header, funzione, esito, fase e orario) e lo stato grezzo della funzione3; non contiene battiti, parametri del profilo o campioni SpO₂. Un esito32 resta non identificato: non viene tradotto in una limitazione hardware né ignorato. Un ACK di avvio indica richiesta accettata, non prova di misurazione o di battito valido in movimento.

Il setter delle modalità legge lo stato della funzione prima di scrivere e salta la scrittura se la modalità è già quella richiesta, come il setter dell’app originale. Il byte risultato di GetStatus non viene trattato come ACK della query. La sottoscrizione live viene comunque inviata. La diagnostica include le conferme tecniche anche durante il ricollegamento, e registra le scritture saltate. Un rifiuto nel ripristino della registrazione o nell’inizializzazione del battito viene conservato e mostrato, mentre il trasporto autenticato resta disponibile per batteria, storico e successiva diagnosi; gli arresti pendenti restano durevoli. Questo non dimostra che il codice32 significhi modalità già impostata.

Il controllo cardio ha una scadenza indipendente dalla batteria: obiettivo5 secondi mentre esiste un comando manuale da gestire,60 negli altri casi, nei limiti dei risvegli e delle operazioni Bluetooth in corso. Lo storico usa pagine più corte durante il controllo manuale. L’arresto aggiorna l’indicazione e la lettura successiva mostra la modalità effettiva. La frequenza delle richieste non impone una frequenza di misurazione al firmware.

La diagnostica di prova aggiunge soltanto contatori di notifiche, ritardi di elaborazione, campioni nel range di visualizzazione, validità IBI e metadati grezzi della risposta cardio dedicata. Il valore cardiaco di quest’ultima rimane locale e non alimenta il battito live né le fasce; i metadati non equivalgono a validazione clinica o a una misura aggiornata.


La prova Cardio registrato usa la registrazione allenamento come sorgente: prima allinea lo storico esposto, poi sospende la sola sottoscrizione live e riporta DAYTIME_HR in AUTOMATIC, conservando modalità e sottoscrizione precedenti nel controllo durevole. Avvia EXERCISE_HR REQUESTED e legge soltanto i nuovi record ogni5 secondi, con pagine fino255 come il client originale; il cursore avanza soltanto dopo la pagina convalidata e archiviata. Il controllo precedente viene ripristinato dopo l’arresto. Una riconnessione durante la prova conserva la scelta della sorgente registrata.

I contatori della prova separano dati iniziali, sessione aperta e arrivi dopo l’arresto; deduplicano gli stessi frame e distinguono0x73tracce spettrali,0x74intensità movimento e0x6cconfini di sessione. Questi record non sono letti direttamente comeBPM. Il calcolo locale usa il modello privato WHR U2.5.1 recuperato dalla versione 4.13.2. Il modello e il runtime nativo sono installati nella build iPhone; non sono richiesti server o rete per elaborare le tracce. Il client non richiede account, API o app ufficiale Oura: il collegamento è Bluetooth diretto, con archivio locale e sincronizzazione al server personale FamilOS. Durante la raccolta la UI non presenta il vecchio numero del live come se provenisse dal flusso registrato.


## Cardio registrato interamente locale

In Allenamento scegliere l’attività e premere **Avvia cardio registrato offline**. Se non esiste una sessione, viene avviata anche la scheda. Il ponte allinea lo storico prima di fissare il confine della nuova registrazione, sospende il live a riposo e recupera i record incrementali. **Arresta registrazione cardio** chiude soltanto la misura nell’anello; **Termina** chiude anche la sessione della scheda. Il live viene ripristinato dopo lo stop.

Il modello WHR U2.5.1 viene eseguito con il JIT completo LibTorch 1.12.0 sulla CPU dell’iPhone, su un actor distinto dall’interfaccia e dall’archiviazione. Il simulatore usa soltanto il bridge di indisponibilità: non genera finti risultati del modello. La registrazione resta nel journal e il contesto locale permette di riprendere l’elaborazione dopo una riapertura. Ogni elaborazione è limitata a 6 ore e 21.601 punti; questi sono limiti del nostro adattatore, non del sensore.

I record 0x73 mantengono candidati e valori grezzi; finestre a 5×6, periodo 12/12 secondi quando cap3>=3, con endpoint [2,12], [14,24]. La scala ring-time è 100 ms e l’UTC viene soltanto dalle ancore TimeSync 0x42: nessun allineamento all’ora del download. Un reset invalida l’ancora precedente. Intensità 0x74 e confini 0x6c restano distinti dai BPM stimati. Sesso non dichiarato usa l’ingresso 0; età, peso, altezza e FC a riposo vengono dal profilo fotografato all’avvio. Quest’ultima è dichiarata come FC del profilo, non come media notturna misurata. Il modello applica internamente i propri limiti ai dati corporei senza modificare il profilo.

Le uscite conservano il valore continuo, qualità per punto e classe di sessione: signalCheck 0=Good,−1=Average,−2=Bad. La classe Bad non alimenta numero, grafici o statistiche accettate. L’uscita interpolata a 1 secondo resta una stima del modello, non una sequenza di nuove misure indipendenti. I punti oltre il confine temporale disponibile sono conservati nell’uscita originale ma esclusi dalla pubblicazione; gli ultimi punti mancanti non vengono inventati.

L’elaborazione si aggiorna al massimo ogni 15 secondi quando arrivano nuove tracce e iOS concede esecuzione. Ogni revisione viene salvata insieme al suo evento `recorded_cardio` nella transazione locale e inviata al server personale dalla normale coda durevole. FamilOS sostituisce atomicamente la proiezione della stessa sessione/dispositivo con una revisione più recente. Le revisioni precedenti restano negli eventi; un invio fuori ordine non può riaprire un risultato finale. Il recupero durante un aggiornamento include risultati già ricevuti da un server precedente che non conosceva questo tipo di evento. Grafici, statistiche, CSV ed Emily conservano origine, versione e stato provvisorio/finale.

Per preparare una build fisica, fornire privatamente `Research/whr_2_5_1.pt` già decifrato e il runtime ufficiale LibTorch 1.12.0 in `Research/LibTorch/` (`install/include`, `install/lib`, `LICENSE`). Distribuzione ufficiale: https://ossci-ios.s3.amazonaws.com/libtorch_ios_1.12.0.zip; SHA256 c33f77d4c3984ea20aef83d8e755ece5e22cc5233f19459b7c6b3c1085405e80. La fase di build controlla identità e integrità dell’archivio modello, lo copia nell’app firmata e include la licenza del runtime. Nessuna chiave modello viene inclusa nell’app. Tutto Research è escluso da Git e dagli ZIP dei sorgenti.

La compatibilità numerica sintetica e la qualità sui segnali reali del Gen3 sono prove distinte. La modalità è sperimentale finché la prova con l’anello non è completata. Non sono richiesti account, app ufficiale o API Oura. FamilOS resta il server personale di sincronizzazione, senza un servizio Oura intermedio.

## Monitoraggio separato dalle sessioni

In Impostazioni, **Cardio attività anche fuori allenamento** controlla il monitoraggio esteso, attivo come impostazione iniziale. La scelta resta salvata sul dispositivo e una disattivazione esplicita viene rispettata agli avvii successivi. Inizia e Termina delimitano le sessioni del diario; il riavvio del segmento non disattiva il monitoraggio esteso. Solo il suo interruttore salva una disattivazione esplicita. Durante la carica il monitoraggio esteso viene sospeso. Nessuna sessione fittizia viene creata fuori allenamento.

Il calcolo usa segmenti limitati: dopo un’ora di tracce recenti conclude il segmento, attende l’elaborazione finale e richiede il successivo. Le eventuali pause di transizione restano vuoti. I nuovi inizi emessi dal firmware dopo una fine acquisizione vengono elaborati con identità separate. La durata della batteria, la ripresa fisica dopo rimozione e il comportamento su un’intera giornata richiedono verifiche sul dispositivo; l’opzione non garantisce misure 24 ore su 24.

Nel live normale fuori sessione, il numero e il grafico usano una media mobile di cinque nuovi campioni validi, senza riutilizzare valori trattenuti. Un vuoto oltre otto secondi azzera la finestra. Le letture originali sono trasferite al server per le analisi. I grafici conservano le interruzioni anche quando il numero di punti disegnati viene ridotto.

Emily può usare `allenamento_campioni` per leggere i punti intermedi di ciascuna metrica, filtrati per secondi dall’inizio della sessione. Le pagine espongono un cursore e non riducono la serie: la chat deve seguire il cursore per un’analisi completa. I tempi restano quelli di acquisizione, con sorgente, qualità e interpolazione; il post allenamento richiede l’opzione esplicita di recupero.

## Energia giornaliera ed Emily

Il server calcola automaticamente `daily_energy` (anche nel bootstrap iPhone), con base stimata da profilo e kcal attive da MET per minuto acquisiti dall’anello; le fasi con MET esplicito coprono gli intervalli mancanti. Le sovrapposizioni non vengono contate due volte. L’integrazione segue il giorno Europe/Rome, inclusi i cambi d’ora, ed espone serie oraria e minuti privi di stima. Non sono calorie misurate né un dispendio totale completo: attività senza MET e termogenesi alimentare non sono inventate.

Quando la composizione è coerente si usa la stima di Cunningham già presente; altrimenti, con peso/altezza/età, Mifflin–St Jeor. `energy_sex` sceglie il parametro della formula; senza parametro si conserva l’intervallo tra le due formule. Una vecchia versione del server non può cancellare silenziosamente la preferenza in coda sul telefono.

I tool `allenamento_grafico` e `allenamento_energia` completano i cinque tool già presenti. Il grafico è un artifact locale alla chat personale: contiene tutte le sei serie disponibili e una tabella paginata dei punti, senza caricare script o dati da siti esterni. Il disegno usa canvas e la tabella mostra cento righe per pagina, per conservare tutti i campioni senza un DOM enorme.


SpO₂: il server cataloga tutti gli output medi nativi datatype6 con UTC, fuso, qualità e provenienza. La tab Sonno mostra media arrotondata, minimo, massimo e numero di campioni nel periodo selezionato; l’Home mostra l’ultimo riepilogo del sonno quando disponibile. La media replica la regola recuperata dal builder originale (85..100, media aritmetica senza filtro quality1), senza correzioni opzionali non configurate. La selezione per periodo nativo è esplicita: la selezione proprietaria DbSleep.day/LongestSleepDaily non è ancora interamente replicata. Il valore è riferito a quel sonno, non a una misura live. Il catalogo viene ricostruito in background dagli output già presenti, senza nuovi download Bluetooth.


Il Diario apre i grafici sulle ultime2ore e permette di scegliere4,8,24ore,2o7giorni. L’endpoint personale `/wellness/v1/mobile/diary` prepara una serie leggera preservando estremi reali, istanti e interruzioni del segnale; i campioni completi restano nel server per statistiche ed Emily. Le viste offline mostrano soltanto i campioni ancora da sincronizzare e il buffer del monitoraggio corrente. I grafici scaricati dal server rimangono esclusivamente nella memoria della schermata. Allenamenti e cronologia sono raccolti in un menu espandibile, e ogni sessione apre i propri grafici limitati al suo intervallo. Il grafico cardiaco è stato spostato dalla Home al Diario.

La pulizia del diario usa regole esplicite con intervallo da conservare e istante limite: conserva le sessioni sovrapposte all’intervallo e quelle future. Le regole sono personali e persistono sulle due parti, così vecchi batch non fanno ricomparire le sessioni escluse. I record completi restano sul server. L’iPhone elimina le copie confermate e conserva la coda non sincronizzata.


## Archivio principale sul server

La versione 18 conserva sul telefono la coda non ancora confermata da FamilOS. L’ACK deve identificare batch e singoli eventi: solo quelli accettati vengono rimossi, nella stessa transazione che aggiorna la coda. Conferme parziali, risposte errate e errori locali mantengono gli altri eventi. La migrazione ripulisce in background il precedente journal già confermato, per piccoli blocchi, e recupera spazio fisico nel database.

Il calcolo cardio mantiene temporaneamente una sola finestra corrente di tracce, il suo ancoraggio temporale e il risultato necessario al ripristino del monitoraggio. Dopo il risultato finale confermato, le tracce diventano eliminabili e rimangono solo i metadati di transizione finché inizia il segmento seguente. Una cache limitata a8192hash recenti e i cursori evitano di riscaricare/reinviare duplicati: non contengono i payload dei sensori. Gli identificatori delle tracce restano stabili anche dopo la compattazione SQLite.

Lo storico, i grafici ricevuti da FamilOS, le sessioni concluse sincronizzate e le copie ripetute dei risultati non vengono archiviati sul telefono. Profilo, configurazione, credenziali, stato batteria e allenamento attivo restano disponibili per il funzionamento offline. I file temporanei degli upload confermati o abbandonati vengono rimossi.


## Ripristino cardio e sonno Gen3

La versione19 prepara un nuovo contesto cardio senza sostituire quello corrente prima dell’ACK dell’anello. Le tracce fra preparazione e conferma restano protette dalla pulizia dopo l’upload. Un avvio fallito conserva il contesto precedente; lo stato pendente viene verificato alla riconnessione. Se l’allineamento iniziale richiede più di8pagine, l’intenzione di avvio resta valida per15minuti e riprende automaticamente. Una cattura vuota o chiusa senza risultati può essere liberata soltanto con storico allineato e feature3 confermata inattiva; i metadati di recupero e i record originali restano nel server.

Il sonno Gen3 recupera i periodi a letto dal record firmware0x76 usando l’orologio verificato dal motore nativo. Questa sorgente mostra inizio, fine e durata a letto; fasi, latenza, efficienza e sonno totale rimangono assenti. Il formato legacy continua a usare la callback originale. Nessun movimento viene trasformato arbitrariamente in una fase del sonno.


La versione20 mostra nel riepilogo Sonno i campioni respiratori Gen3 elaborati da FamilOS, con media, estremi e conteggio riferiti al periodo scelto. Home e Diario ricevono la stessa serie storica. Richiede il backend con decoder0x6a aggiornato; l’aggiornamento rielabora i record già archiviati senza nuovi download dell’anello.
