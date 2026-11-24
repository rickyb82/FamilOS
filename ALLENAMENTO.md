# Allenamento FamilOS e Home Pulse

Modulo nativo `/wellness`, disponibile ai profili adulti. Il browser usa le sessioni e le protezioni CSRF/MFA di FamilOS; l’iPhone usa un token distinto, revocabile e limitato al proprietario. I dati restano separati per profilo, anche per gli amministratori.

## Flusso

1. Importare il binding privato dell’anello e il calendario nel profilo adulto corrispondente. L’importatore rifiuta nomi ambigui e non modifica gli altri profili.
2. Aprire **Allenamento → iPhone e anello** e creare il QR monouso, valido dieci minuti.
3. Nell’app nativa **Home Pulse**, associare FamilOS. Coordinare il passaggio Bluetooth dal ponte Mac prima di connettere il Gen3.
4. Avviare, mettere in pausa o terminare l’allenamento da iPhone o dashboard. L’iPhone mantiene timer, scheda e diario senza rete; il server riceve gli eventi al ritorno della connessione.
5. Sulla TV aprire FamilOS, entrare nel proprio profilo e selezionare **Allenamento → Vista TV**.

La dashboard presenta battito, SpO₂ storica, temperatura dell’anello, movimento, passi e respiro quando esiste un record decodificabile. Non interpola i dati mancanti. I timestamp storici vengono risolti dal motore nativo; l’ora di ricezione non viene usata al loro posto. Le segnalazioni di movimento non annullano da sole un IBI valido. I valori fuori dall’intervallo software di visualizzazione restano archiviati e separati dalle statistiche principali.

Il respiro non viene calcolato dal battito. La SpO₂ elaborata appartiene a finestre storiche; non dimostra la disponibilità di saturazione continua durante uno sforzo. Il campo temperatura storico1 non è etichettato come temperatura interna. Le quantità grezze non ancora interpretate restano nel journal.

## Allenamento e stime

Le fasi hanno durata, nome, intervallo cardiaco personale e MET facoltativo. Scheda e profilo vengono fotografati all’avvio della sessione. Il timer conserva la durata reale anche oltre la scheda; i tratti senza MET restano senza stima. Energia lorda e attiva sono stime da peso, durata e MET. Le percentuali di composizione dichiarate non vengono corrette o sommate automaticamente; se non sono coerenti il metabolismo basale resta vuoto.

Il calendario usa giorni ISO e Europe/Rome, mantenendo gli orari locali attraverso l’ora legale. Il riconoscimento della palestra non dipende dall’orario del calendario. Geofence e aumento sostenuto della FC possono aprire una sessione provvisoria: la risposta alla notifica assegna il nome alla stessa sessione, mentre **Non mi alleno** la contrassegna come rilevamento escluso senza eliminare i segnali.

## Durabilità

Il telefono scrive il frame prima della decodifica. Journal, coda e cursore storico sono transazionali. Il server conferma solo gli ID effettivamente salvati; retry e batch fuori ordine non creano doppioni. Il journal locale resta dopo l’ACK. Gli eventi del server sono append-only; le sessioni sono proiezioni ricostruibili dagli eventi originali. Un batch conflittuale non viene confermato parzialmente.

La chiave proprietario è cifrata sul server e conservata nel Keychain sull’iPhone. Non viene restituita alle API del browser o inviata al motore nativo. Nonce e risposte di autenticazione non entrano nell’archivio sensoristico. Il QR contiene un codice temporaneo, non la chiave dell’anello.

## Installazione

Su Docker Compose standard il servizio privato usa il profilo `wellness`: predisporre prima `private/homepulse/libappecore-compat.so` e `HOME_PULSE_CORE_TOKEN`, poi avviare `docker compose --profile wellness up -d --build`. La libreria non è inclusa nei sorgenti o nelle immagini. Vedere [extras/homepulse-core/README.md](extras/homepulse-core/README.md).

Su Orbita usare l’updater del core e, dopo il rilascio del lock di migrazione, `extras/updater/orbita_support/wellness.py --library … --bootstrap … --owner-name …`. Il helper costruisce e verifica il worker prima di ricreare il backend, conserva le altre reti/servizi e ripristina la configurazione precedente in caso di errore. Richiede sudo sul server. Il bootstrap è privato0600; nessun segreto va negli argomenti della riga di comando.

L’app iPhone è in [ios/HomePulse](ios/HomePulse/README.md): compila senza dipendenze esterne. La prova su simulatore non sostituisce quella di Bluetooth, geofence e background sul telefono reale. Per installarla sul proprio iPhone va selezionato il Team Apple in Xcode e completata la firma.

## Stato verificato

- Backend: suite completa675 test superati;21 test specifici del modulo, incluso il rifiuto di risultati nativi associati a un input diverso.
- Frontend:78 test, typecheck e build superati; avvio, pausa, ripresa e conclusione verificati nel browser locale autenticato.
- Motore amd64: replay dei record autorizzati e fixture sintetica; POST HTTP con seccomp verificato su Ubuntu amd64 nativo il24 novembre2026. Nessun dato personale è stato usato per quella prova HTTP.
- iPhone: compilazione e test logici/UI su simulatore; prove radio e installazione sul dispositivo fisico da completare.

Il motore accetta65536 record per job, massimo4MB; il journal originale non viene troncato. Un’elaborazione non completata resta in attesa e conserva l’ultimo risultato valido. Questi controlli di dimensione del singolo job non limitano la raccolta grezza.
