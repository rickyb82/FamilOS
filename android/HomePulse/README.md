# Home Pulse Android — prova Fold5

App nativa Java17 con componenti Android standard: Bluetooth Gen3, SQLite con journal durevole, credenziali Android Keystore, sincronizzazione HTTPS con FamilOS e gestione allenamento offline. Include cinque sezioni: dashboard, allenamento, diario, calendario e impostazioni. La raccolta usa un servizio in primo piano con notifica visibile.

Build con `python3 build.py`, Android SDK36/build-tools36 e JDK21. I percorsi si configurano con HOME_PULSE_ANDROID_SDK e HOME_PULSE_JDK. SDK e chiave di firma di prova sono esterni al repository. Nessun APK Oura, libreria proprietaria o credenziale del proprietario è incluso nell’APK.

`python3 build.py --instrumentation` aggiunge SelfTestInstrumentation, che verifica SQLite, Keystore, ACK parziali, checkpoint e gestione allenamento in un database temporaneo isolato. Non legge né modifica il database dell’utente, non usa la radio e non invia dati al server.

Stato della consegna: build e firma APK riuscite; protocollo verificato su fixture sintetiche. La prova strumentale e la connessione fisica sul Fold5 restano da eseguire: il telefono non era più disponibile quando è stata tentata l’installazione. L’installazione operativa è proseguita sull’iPhone richiesto dall’utente. La funzione batteria aggiunta su iOS non è ancora portata su Android. Il rilevamento geografico automatico non è attivo in questa versione Android.
