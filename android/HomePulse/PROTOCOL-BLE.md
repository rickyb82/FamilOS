# Modulo BLE Android originale

`OuraProtocol.java` e `RingBle.java` implementano il protocollo locale ricostruito nelle analisi Oura, senza librerie proprietarie, AndroidX o dipendenze Gradle. Compilazione Java 17, API minima 26. Non contengono chiavi, reset o provisioning.

## Contratto con il servizio

```java
RingBle(Context context, Store store, RingBle.Listener listener)
configure(String logicalRing, byte[] ownerKey)  // chiave proprietario già autorizzata
scan()
connect(String bluetoothAddress)
disconnect()
maintenance()
startSport(int activityId, int age, int restingHeartRate)
stopSport()
close()
```

`ready`, `connected`, `busy`, `status` sono pubblici e volatile; esistono anche `isReady/isConnected/isBusy/getStatus`. `getApiVersion`, `getCapabilities`, `getExerciseRaw` restituiscono copie.

I callback Listener avvengono sul main Handler: `onStatus`, `onDevices`, `onArchiveChanged` e `onLive(double bpm, long capturedAtMillis, List<String> warnings)`. **NaN significa campione non disponibile e deve cancellare il valore live; non deve alimentare grafici o rilevamento palestra.** Il servizio/UI deve applicare anche la scadenza di otto secondi senza nuovi campioni validi.

Il servizio foreground gestisce autorizzazioni runtime, notifica permanente, vita del processo e timer; dovrebbe chiamare `maintenance()` periodicamente. Le notifiche BLE richiamano a loro volta la manutenzione. Non viene promesso un processo ininterrotto dopo arresto forzato, revoca permessi o limitazioni energetiche Samsung.

`disconnect()` registra la scelta esplicita e tenta AUTO + disiscrizione prima di chiudere. È asincrono, fino a circa sei secondi: un `close()` immediato interrompe questo tentativo e rilascia comunque GATT. `close()` rilascia receiver, worker e chiave RAM; conserva l’intenzione precedente di riconnessione per il ripristino del servizio. Per una fermata voluta usare prima `disconnect()`.

## Archivio

Il modulo usa soltanto queste firme concordate:

```java
Store.append(String kind, JSONObject payload, long capturedAtMillis)
Store.archiveHistory(List<byte[]> frames, long nextCursor, String logicalRing, int[] api)
Store.getLong(String key, long fallback)
Store.setLong(String key, long value)
```

Il payload contiene `raw_hex`, `api_version`, `logical_ring_id`. I frame completi vengono archiviati prima del decoder fisiologico; frame nonce/autenticazione/provisioning sono esclusi dopo il framing, anche se concatenati o frammentati. Nessuna chiave o payload sensibile viene stampato nei log.

`archiveHistory` possiede la transazione unica record/journal/checkpoint `history.cursor.<logicalRing>`. La risposta GetEvent viene verificata per lunghezza, conteggio e ordinamento del cursore prima della transazione. Richieste di 64 record, massimo 32 pagine per passaggio: un backlog prosegue automaticamente dal checkpoint dopo almeno due secondi; a storico esaurito viene ricontrollato dopo 60 secondi nei risvegli disponibili. Nessuna cancellazione memoria.

Il controllo cardio usa un singolo long `sport.control.<logicalRing>`: `-1` nessuna proprietà locale, `100 + modoPrecedente` registrazione locale avviata, `200 + modoPrecedente` ripristino da completare. Il modo precedente ammesso è OFF 0 oppure AUTO 1. Il valore di ripristino viene salvato prima delle scritture e cancellato soltanto dopo ACK di ripristino.

## Trasporto e autenticazione

- Ricerca limitata a UUID servizio Oura oppure manufacturer ID 690, già usati nel ponte Mac; collegamento soltanto al dispositivo scelto.
- Servizio `98ed0001-a541-11e4-b6a0-0002a5d5c51b`, write 0002, notify 0003, CCCD standard 2902.
- Main Handler per callback e avvio delle operazioni GATT, singolo worker per la sequenza. Ogni exchange attende sia ACK ATT di scrittura sia risposta applicativa; timeout e cambio connessione invalidano la sequenza. Un’operazione della vecchia generazione non può scrivere sulla nuova sessione.
- Da API 33 vengono usate le overload GATT con `byte[]` e callback con copia del valore. I rami precedenti esistono per API 26–32. [Documentazione BluetoothGatt](https://developer.android.com/reference/android/bluetooth/BluetoothGatt), [BluetoothGattCallback](https://developer.android.com/reference/android/bluetooth/BluetoothGattCallback).
- Su errori GATT 5/15 viene richiesta l’associazione standard Android, senza PIN inventati, rimozione bond o reset. Dopo il bond si ricomincia da firmware, capacità e autenticazione proprietario. Il receiver osserva soltanto il dispositivo selezionato; il broadcast non autentica mai l’anello. Il flag exported serve ai broadcast Bluetooth provenienti dall’UID privilegiato del framework. [Broadcast Android](https://developer.android.com/develop/background-work/background-tasks/broadcasts).
- Challenge: `2f012b`, nonce di 15 byte nella risposta 2c; AES/ECB/PKCS5Padding con chiave proprietario di 16 byte, risposta 2d e risultato 2e. Nessun materiale segreto proviene dall’APK.
- Nessuna richiesta MTU necessaria per questi comandi: il più lungo è di 19 byte. Il framer ricostruisce notifiche frammentate/concatenate.

## Segnale e attività

Il battito cap2 richiede stato MEASURING, flag ON e IBI con validità 1, maggiore di zero e non oltre 2000 ms. Movimento, segnale debole e dita fredde restano avvisi; non costituiscono da soli un rifiuto dell’IBI. Soltanto 20–250 BPM alimentano il callback numerico, mentre il pacchetto originale resta archiviato. Temperatura live e age mantengono unità raw non risolte.

Stato e latest cap3 vengono letti ogni 60 secondi quando liberi; il risultato resta raw e non viene presentato come stream verificato. La richiesta sport manuale usa soltanto le categorie ricostruite: Altro 65535, Camminata 14, Corsa 12, Ciclismo 5, Pesi 8, Pilates 10, Yoga 15. Richiede età 18–100 e FC a riposo 30–200; prima legge lo stato e rifiuta di sostituire una registrazione REQUESTED già presente. Sequenza verificata: OFF → parametri 29 → REQUESTED, con ACK per ogni passaggio. Non ricava categoria da un’etichetta libera come “cyclette”.

## Prove e limiti

`tests/OuraProtocolTest.java` è eseguibile su JDK senza Android. **25 assertion passate**: AES noto, IBI con movimento, valori invalidi e fuori range, framing frammentato/concatenato con auth esclusa, summary/cursore/ordinamento storico, ACK, inventario capacità e parametri sport. Le fixture sono interamente sintetiche.

RingBle e OuraProtocol sono stati compilati con JDK 21 `--release 17` contro `android-36/android.jar` e lo Store reale del progetto. Questa verifica non dimostra ancora radio, pairing o background sul Fold5; nessun dispositivo è stato collegato durante questo sviluppo.

Il formato storico FE e il cursore esteso FD non sono decodificati. Un FE interrompe la sessione e conserva il checkpoint precedente; il buffer opaco non viene pubblicato come sensore perché non è possibile separare con certezza eventuali frame sensibili. I frame completi già precedenti al problema rimangono nel journal. FD viene conservato raw, ma non può avanzare un cursore non provato.

SpO₂, respirazione, cadenza e temperatura in °C non sono inventate da questo modulo. I risultati storici derivano dalla pipeline server già documentata; il modulo conserva i record necessari senza eseguire librerie Android vendor.
