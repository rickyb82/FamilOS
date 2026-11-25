# HomePulse core locale, Linux amd64

Servizio HTTP privato per elaborare offline i record sensoristici dell’anello con ecore3.6.2 estratto dal proprio APK Oura7.10.0. Non esegue l’interfaccia Android, non usa JNI, Bluetooth, account o chiavi. Il processo HTTP non carica la libreria: ogni elaborazione usa un child nuovo, con UserInfo a zero e MAC sintetico. Non espone calorie o metriche che richiedono un profilo personale.

Il port amd64 è stato verificato con fixture sintetiche. La documentazione pubblica non include catture personali, risultati fisiologici reali o impronte di archivi utente. Il manifest `EVIDENCE.json` separa prove tecniche riuscite e limiti.

Il 24 novembre 2026 è stato verificato anche **l’intero percorso HTTP su Ubuntu amd64 nativo**, con seccomp obbligatorio: 16 record sintetici mappati con identità verificata, `process_true` e deinit riusciti, core 3.6.2. La richiesta POST priva di token ha restituito 401. La prova ha usato un processo temporaneo su loopback, nessun dato personale e nessun servizio persistente installato.

## Preparazione privata

Non mettere APK o librerie proprietarie in Git, ZIP pubblici o nell’immagine Docker. Il codice include soltanto lo shim e il bridge. `prepare_native.py` accetta APK/APKM o la libreria originale già estratta, richiede l’hash esatto della versione studiata e crea una nuova cartella0700 con file0600. Adatta32byte di metadati del linker; `.text` e `.rodata` devono restare identiche.

```sh
python3 prepare_native.py --source /percorso/privato/oura.apkm --out /percorso/privato/homepulse-native-amd64
docker build --platform linux/amd64 -t homepulse-core:local .
```

La copia deve avere SHA256 `163e10037c93ef70bd939641e0d391441e74cb4dd339946cd0a5421b91703f21`. L’originale x86_64 ha SHA256 `a4e8a213f87283cae183f70ab0ed17d0e30aa259868d757a1385189d1a43ca08`. Il Dockerfile compila lo shim con GCC Debian; non legge la libreria privata durante la build.

Il file montato deve essere leggibile dall’UID del servizio senza allargare i permessi0600. Si può assegnare al container lo stesso UID/GID del proprietario tramite `HOME_PULSE_CORE_UID` e `HOME_PULSE_CORE_GID`. Il valore predefinito dell’immagine è10001:10001. La preparazione non modifica gruppi Docker, sudo o permessi del server: l’avvio operativo richiede un utente già autorizzato ad amministrare Docker.

## Collegamento a FamilOS

Usare `compose.private.yml` come riferimento da integrare: rete `homepulse_net` interna, soltanto backend e worker collegati, nessuna porta host pubblicata e nessun Docker socket montato. Nel backend: `HOME_PULSE_CORE_URL=http://homepulse-core:8081`. Il servizio riceve la libreria tramite un singolo bind mount RO. Lo shim viene dall’immagine ed è RO con il filesystem root.

Variabili del worker:

- `HOME_PULSE_NATIVE_LIBRARY`, predefinito `/native/libappecore-compat.so`.
- `HOME_PULSE_NATIVE_SHIM`, predefinito `/compat/libx.so`.
- `HOME_PULSE_CORE_PORT`, predefinito8081.
- `HOME_PULSE_CORE_BIND`, predefinito0.0.0.0 all’interno del container privato.
- `HOME_PULSE_CORE_TOKEN`, facoltativo token condiviso con il backend. Se impostato, POST richiede `Authorization: Bearer TOKEN`. Non viene passato al child né registrato nei log.

`GET /health` verifica configurazione e hash senza caricare ELF. `configured=true` **non dimostra** che una successiva elaborazione sia riuscita: controllare sempre `process_true`, mappatura e `deinit_returned` della risposta POST.

```http
POST /process
Content-Type: application/json

{"records":["420de803000000e10b5e0000000000"]}
```

Il body accetta soltanto `records`: da1 a65536 stringhe hex di record standard completi, ognuno6..20byte; i record di profilo0x5c sono rifiutati. Limite body4.000.000byte. Nessuna età, sesso, peso, identità o credenziale viene accettata. Il file input canonico riceve un SHA256 restituito come `source_input_sha256`.

La risposta conserva lo schema del bridge precedente:

- `status`, `process_true`, `version`, `deinit_returned`, `input_record_count`, `processed_record_count`, `mapping`, `excluded_metadata`.
- `spo2_outputs`: tutti gli output medi tipo6, anche con qualità insufficiente, con `average_raw`, `quality_raw` e `timestamp_utc_ms_native`. Nessun clamp o promozione automatica. Gli altri tipi callback sono contati, senza reinterpretare strutture non verificate.
- `mapped_temperatures`: hash record, UTC, campo1 °C; identità del sensore e qualità restano non attribuite, `core_body_temperature=false`.
- `mapped_sensor_records`: hash record, UTC e tag per movimento0x47, passi0x50 e respiro firmware0x62. Sono date già restituite dal resolver; questo bridge non inventa il contenuto fisiologico di quei record.
- `sleep_outputs`: copie degli output nativi della callback7 di `ecore_init`, ABI `ecore-3.6.2-linux-x86_64-sleep-output-200-v1`. Inizio/fine letto sono `bedtime_start_utc_seconds` e `bedtime_end_utc_seconds`, in secondi Unix; i relativi fusi sono minuti. `time_in_bed_seconds` viene dal core. `phases_30_seconds` e `phases_5_minutes` sono stringhe native già decodificate: `1` profondo, `2` leggero, `3` REM, `4` sveglio, rispettivamente30 e300secondi per carattere. Non vengono ricavate da soglie arbitrarie di movimento. Buffer o riepiloghi assenti restano `null`.
- `summary2`, quando presente in un output sonno, conserva durate native di veglia/REM/leggero/profondo/sonno totale e latenza; `score_raw`, `efficiency_raw` e `wake_up_count_raw` sono valori grezzi del core. Non dimostrano validità clinica o personalizzazione: UserInfo resta zero e ogni output dichiara `user_profile_used=false`, `published_as_valid_measurement=false`, `medical_validation_claimed=false`.
- `sleep_capture_complete` descrive soltanto la riuscita della cattura delle callback, non la completezza di una notte. `sleep_callback_errors` contiene diagnostica limitata senza indirizzi. Un errore di decodifica svuota soltanto `sleep_outputs`, preservando gli altri output validi. Mapping/process/deinit non riusciti sopprimono tutti gli output di misura.
- `published_as_valid_measurement=false`, `medical_validation_claimed=false`, `user_profile_used=false`.

La cattura sonno copia i200byte verificati e i buffer interessati durante la callback: i puntatori prestati dal core non escono dal child. Accetta periodi fino a16ore, stringhe con terminatore NUL fino a1920/192 caratteri e al massimo1024 callback sonno; simboli sconosciuti o limiti superati falliscono in modo chiuso. Conserva le lunghezze native e le possibili epoche parziali senza adattarle artificialmente alla durata. `timestamp_utc_seconds` è la chiave giorno sonno nativa, distinta dall’inizio letto. Versione e ABI sono incluse in ogni output. La verifica statica del contratto e i test con buffer ctypes sintetici sono riusciti; la produzione di periodi sonno da una cattura reale resta da verificare separatamente. Nessuna callback sonno significa lista vuota, senza periodi inventati.

Il backend conserva i record originali e applica esplicitamente le proprie regole di visualizzazione. Non cancellare la cache valida su errore, timeout, mappatura incompleta o dati nuovi assenti. La SpO₂ elaborata riguarda finestre storiche/postallenamento; non è una misura live durante l’esercizio.

Errori HTTP:400 input invalido/profilo,401 token,413 body oltre limite,503 occupato/configurazione mancante,504 timeout,502 errore child/identità/risultato. Una mappatura incompleta può tornare200 con `process_true=false`: i campi misura vengono comunque svuotati. Nessun dato fisiologico non mappato viene eliminato per forzare un risultato. La sola eccezione osservata è un prefisso massimo64 record di debug0x43/0x61 precedente a RingStart: è escluso dal solo calcolo e indicato nei metadati; il catalogo originale resta intero.

## Isolamento e verifica

Il container richiede root filesystem RO, capability rimosse, no-new-privileges, utente nonroot, massimo64PID,512MB, una CPU e core dump disabilitati. Il parent HTTP può utilizzare soltanto la rete Docker privata. Prima di caricare ELF, il child installa seccomp amd64 che vieta socket/rete, exec/fork/clone e io_uring; rifiuta ABI x32 e architetture diverse. I suoi file input/output sono in una cartella temporanea0700, cancellata alla fine. Non eredita token, LD_PRELOAD, socket o altri descriptor del servizio. Timeout30secondi, terminazione dell’intero gruppo del solo child posseduto. Risultato massimo32MB, ulteriormente limitato nel child da RLIMIT_FSIZE.

Lo shim **non implementa Bionic FILE/stdio in generale**. La traccia è disabilitata; il worker chiama realmente deinit e termina con `os._exit` per evitare i distruttori stream C++ dipendenti da quell’ABI.

```sh
python3 -m unittest test_service.py
```

Venti test verificano ABI dati, rifiuto profilo/hash errato, limiti, pulizia job, timeout, ambiente child, programma BPF per rete/processi/x32, mappature dei tag47/50/62, HTTP su loopback e cattura sonno. I test sonno usano soltanto buffer ctypes allocati e una libreria simulata: copertura di callback7, unità/offset, copia della memoria prestata, riepiloghi mancanti, simboli/limiti e cancellazione dei risultati su mapping/process/deinit falliti. Nessun ELF viene caricato nei test host. Il test BPF interpreta il filtro generato senza installarlo nel processo di test. La fixture `synthetic_fixture.json` è interamente sintetica, data fissa gennaio2020; non contiene dati utente.

Sul Docker Desktop del Mac la libreria amd64 e lo shim funzionano, ma l’ambiente emulato rifiuta il filtro seccomp aggiuntivo con EINVAL. Il percorso HTTP è stato osservato fallire in modo chiuso prima di caricare il core, senza pubblicare misure. La successiva prova temporanea su Ubuntu amd64 nativo ha completato lo stesso percorso HTTP con seccomp attivo. Non sono stati inviati record personali né installati servizi persistenti. L’installazione nel progetto Orbita resta un passaggio distinto, con verifica Docker e rollback propri.

Per un test CLI su un container esplicitamente `--network none`, esiste `worker.py --outer-offline-test`: verifica anche l’assenza di interfacce non-loopback attive o instradate. È il percorso usato per verificare la libreria amd64 sotto emulazione. Il servizio HTTP non passa né accetta quel flag. Non usarlo per un child del servizio con rete privata.

Esempio della sola fixture in container offline, con output nuovo e privato:

```sh
mkdir -m700 /percorso/privato/homepulse-witness
docker run --rm --platform linux/amd64 --name homepulse-core-witness \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 64 --memory 512m --cpus 1 --ulimit core=0 \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --mount type=bind,source=/percorso/privato/homepulse-native-amd64/libappecore-compat.so,target=/native/libappecore-compat.so,readonly \
  --mount type=bind,source=/percorso/assoluto/synthetic_fixture.json,target=/input/records.json,readonly \
  --mount type=bind,source=/percorso/privato/homepulse-witness,target=/output \
  homepulse-core:local python -B /app/worker.py --input /input/records.json \
  --output /output/fixture-result.json --outer-offline-test
```

Il timeout di produzione è applicato dal servizio; per una CLI manuale interrompere soltanto il container col proprio nome se non termina entro30secondi.
