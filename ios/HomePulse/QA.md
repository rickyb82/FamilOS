# Verifica Home Pulse

Consegna del 24 novembre 2026. Progetto Xcode 27.1, minimo iOS 17, Swift 5; nessuna dipendenza esterna.

| Verifica | Esito |
|---|---|
| Build simulatore ARM64 | Riuscita |
| Build iPhone ARM64 senza firma | Riuscita |
| Test unitari su iPhone 17 Pro / iOS 26.1 simulato | 18 passati, 0 errori |
| Test interfaccia sullo stesso simulatore | 1 passato, 0 errori |
| Associazione server / BLE su iPhone fisico | Da eseguire dopo firma e passaggio dal Mac |
| Geofence/notifiche con posizione fisica | Da verificare sul telefono dopo autorizzazioni iOS |

`test-results.json` è il riepilogo esportato da `DeliveryTests.xcresult`. La suite usa soltanto fixture sintetiche e un archivio UI di prova separato. Il simulatore non contiene credenziali personali o dati sensore reali.

I test coprono AES con vettore noto, IBI valido con movimento, intervallo di visualizzazione senza eliminare il raw, framing frammentato/concatenato, esclusione auth, cursore storico, riapertura archivio, ACK parziali e sconosciuti, deduplicazione, checkpoint, timer oltre il piano, MET e dati mancanti, calendario Europe/Rome al cambio dell’ora, rilevamento sostenuto/freschezza/cooldown, candidato persistente e importazione di sessioni web senza sovrascrivere eventi offline pendenti.

Il test UI ha aperto tutte e cinque le schede, avviato una sessione senza rete/anello, messo in pausa, ripreso, terminato e verificato il diario dopo rilancio. Sono state ispezionate visivamente le schermate esportate, senza valori dimostrativi che sembrino misure reali.

- [Home](screenshots/01-home.png)
- [Allenamento](screenshots/02-allenamento.png)
- [Timer offline](screenshots/03-sessione-offline.png)
- [Diario](screenshots/04-diario.png)
- [Calendario](screenshots/05-calendario.png)
- [Impostazioni](screenshots/06-impostazioni.png)

La build ARM64 non firmata si trova in `.device-build/Build/Products/Debug-iphoneos/HomePulse.app`. Non è stata installata sul telefono. Il simulatore verifica interfaccia e logica; non dimostra associazione, cifratura BLE di iOS, consegna geofence reale o continuità in background. La prossima prova deve mantenere un solo owner Bluetooth, scollegando il ponte Mac prima del collegamento iPhone.
