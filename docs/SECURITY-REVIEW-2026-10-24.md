# Verifica di sicurezza FamilOS — 24 ottobre 2026

## Esito e perimetro

Sono stati corretti problemi di autenticazione, revoca e isolamento individuati nel codice e nelle prove locali. La verifica comprende il backend FamilOS, il proxy delle app, i flussi di login e le dipendenze Python/JavaScript installate nell'ambiente di prova. Non è una certificazione né una garanzia di assenza di vulnerabilità.

Risultati: **439 test backend superati**, **7 test frontend superati**, controllo TypeScript e build frontend riusciti. Le prove aggiunte sono in `backend/tests/test_security.py` e `backend/tests/test_security_alerts.py`; includono una matrice parametrica delle rotte, quindi il numero di test non equivale al numero di vulnerabilità o scenari indipendenti.

## Problemi corretti o mitigati

| Problema | Condizione e impatto | Correzione |
|---|---|---|
| Callback Google non legato al browser | Uno state valido poteva essere trasferito a un altro browser, provocando un login nell'account di chi aveva iniziato il flusso | Cookie HttpOnly/SameSite=Lax dedicato, verifica costante dello state, scadenza e consumo monouso. PKCE mantenuto |
| Callback Gmail amministrativo | Il collegamento non verificava il browser e la validità attuale della sessione amministrativa iniziale | Cookie di collegamento e verifica di sessione, ruolo, stato attivo, MFA, scadenza e blocco schermo prima di salvare il token |
| Sessioni precedenti a un cambio di ruolo | Una sessione ottenuta prima del cambio, anche tramite PIN, poteva ereditare i nuovi privilegi | Revoca delle sessioni, dei token Sync e dei codici di associazione su cambi di ruolo/credenziali/stato; controllo aggiuntivo delle sessioni PIN |
| Revoca incompleta dopo cambio/reset password | I token di Craft Sync potevano sopravvivere al cambio delle credenziali | Revoca anche dei dispositivi Sync e dei codici di associazione pendenti |
| Riutilizzo dei secondi fattori | TOTP riusabile nel suo intervallo; codici email/recovery consumati senza confronto atomico | Contatore TOTP persistente condiviso tra web e Sync, aggiornamenti condizionati per i codici monouso; test concorrenti |
| Identificatore pre-MFA conservato | Un identificatore copiato prima della MFA veniva promosso insieme alla sessione | Identificatore nuovo al completamento della MFA e durata di 10 minuti per le sessioni in attesa |
| Percorsi alternativi alla MFA | Sync poteva entrare senza iscrizione obbligatoria; Google era considerato automaticamente un secondo fattore | Stesse regole di iscrizione per Sync; Google non attesta la MFA locale. Nuove installazioni con `MFA_OPTIONAL=0` |
| Accesso diretto alle app Craft | Il proxy non applicava i permessi di FamilOS a tutti i percorsi delle app | Controllo centrale della sessione, MFA, blocco schermo e app abilitate, sia in Caddy sia in Vite. Non implica che MailCraft fosse privo della propria autenticazione |
| Controlli CSRF e IP incompleti | Alcuni endpoint pubblici di autenticazione non applicavano lo stesso controllo; X-Real-IP era accettato direttamente dall'app | Header custom e controllo dell'origine sulle operazioni mutanti di autenticazione; IP ricavato dal peer ASGI, con proxy fidato configurato dal server |
| Flussi eventi già aperti | Un flusso SSE poteva continuare dopo blocco o revoca | Ricontrollo prima di inviare eventi e a ogni heartbeat, anche per Sync |
| Link nei backup | La verifica dei soli nomi non escludeva link simbolici, hard link o file speciali nell'archivio importato da un amministratore | Accettati solo file e directory, rifiuto dei percorsi di attraversamento, estrazione con filtro `data`; i backup rifiutati non restano applicabili al riavvio |
| Formule nel CSV del registro | Un valore controllato da un utente poteva essere interpretato come formula aprendo il CSV | Celle pericolose esportate come testo; test di regressione |
| Sandbox condivisa | I processi di esecuzioni diverse usavano lo stesso utente e filesystem del container | **Mitigazione: `ALLOW_CODE_EXECUTION=false` per impostazione predefinita**, applicata sia alla disponibilità del tool sia all'esecuzione. Non è stato implementato un nuovo isolamento per singola esecuzione |
| Configurazione iniziale debole | Una configurazione HTTPS poteva partire con i segreti di esempio | Avvio rifiutato con SECRET_KEY di esempio/corta; primo amministratore rifiutato con password di esempio/corta |

## Prove eseguite

- Accesso anonimo a tutte le rotte private censite tramite OpenAPI, comprese le API file e Sync.
- Chiamata diretta delle rotte amministrative come adulto, bambino e anonimo, senza affidarsi ai menu.
- Sessioni scadute, disattivate, bloccate e in attesa di MFA; logout e utilizzo di cookie precedenti.
- Login PIN su dispositivo non approvato, rifiuto degli account adulti, blocco dopo password errate, tentativo di modificare ruolo/policy attraverso le preferenze personali.
- Cookie HttpOnly/Secure/SameSite, mancato riuso dell'identificatore scelto dal client, durata delle sessioni pre-MFA.
- OAuth Google simulato: state trasferito, replay, verifica email non valida, secondo fattore locale. Callback Gmail dopo logout e da un altro browser.
- WebAuthn con **vere firme ECDSA sintetiche**, verificate dalla libreria: origine, RP ID, challenge, firma, user handle, flag di verifica utente e replay. Non sono state simulate come riuscite le verifiche crittografiche di questi test.
- Consumo concorrente di codici recovery e reset email, riuso TOTP, invalidazione dei token Sync dopo reset.
- Verifiche preesistenti su ACL dei documenti, file condivisi, salvataggi concorrenti, SSRF del browser protetto, contenuti attivi e regole dei bambini.
- Test degli avvisi email con trasporto simulato: destinatari amministratori, riusciti/respinti, assenza di segreti, raggruppamento, limite orario, retry e nessun avviso di login completato alla sola prima fase MFA.
- Caddy: validazione delle configurazioni sviluppo e HTTPS con CA interna. Prova HTTP attraverso un container Caddy temporaneo e attraverso Vite: **tutte e sei le app restituiscono 401 senza sessione**. Container di prova rimosso alla fine.

## Analisi automatica

- `pip-audit`: nessuna vulnerabilità nota rilevata tra **119 pacchetti dell'ambiente Python analizzato**; non equivale all'analisi delle immagini Docker o dei componenti Rust esterni.
- `npm audit`: nessuna vulnerabilità nota nelle dipendenze frontend analizzate.
- Bandit su backend e sandbox: **0 segnalazioni alte, 3 medie e 12 basse**. Le tre medie riguardano due query con nomi tabella ricavati da tuple costanti, non da input utente, e una stringa `0.0.0.0` usata per riconoscere URL locali, non per aprire un listener. Le basse sono prevalentemente gestione delle eccezioni e stringhe interpretate come password. Non sono state nascoste tramite `nosec`.
- La CI esegue ora anche `pip-audit`, `npm audit --audit-level=high` e Bandit con soglia alta, oltre alla suite funzionale.

Comandi riproducibili:

```sh
cd backend
python -m pytest -q
pip-audit
bandit -r app ../sandbox -lll
cd ../frontend
npm run typecheck
npm test
npm run build
npm audit
```

## Avvisi agli amministratori

`SECURITY_LOGIN_ALERTS=true` è il valore predefinito; il pannello Amministrazione → Provider → Email di sistema permette di modificarlo. Serve un trasporto SMTP o Gmail già configurato. I destinatari sono gli amministratori attivi con email; i tentativi per account sconosciuti raggiungono gli amministratori dell'installazione.

Le notifiche riportano account, data UTC, IP ed evento. Un accesso riuscito significa credenziali accettate, non prova che la persona fosse il titolare. Non includono password, PIN, codici, token, corpo della richiesta o dettagli delle eccezioni. I tentativi sconosciuti non riportano l'indirizzo fornito da chi attacca.

La coda è persistente: fino a cinque tentativi di invio, con attese crescenti, senza bloccare il login in caso di guasto SMTP. La consegna completata per ciascun destinatario viene conservata. Duplicati dello stesso esito/account/IP vengono raggruppati per cinque minuti; massimo venti avvisi per esito e famiglia ogni ora. Il registro accessi conserva gli eventi registrati anche quando non parte un'email. Le righe della coda vengono eliminate dopo trenta giorni.

Quando l'email non è configurata, gli avvisi non vengono accodati per un invio retroattivo: resta il registro. La consegna a una casella reale va provata sulla VM dopo la configurazione del trasporto.

## Limiti e controlli prima dell'esposizione pubblica

1. **VM Ubuntu non raggiunta né sottoposta a scansione in questa verifica.** Restano da controllare versione e aggiornamenti del sistema, porte realmente pubblicate da Docker, firewall del router, reverse proxy, certificato e DNS. Il backend e le porte dirette delle app devono rimanere interni; esporre solo il proxy previsto. Pi-hole DNS e dashboard devono restare sulla rete domestica.
2. Il flusso reale Google, Face ID/Windows Hello su hardware e la consegna SMTP reale non sono stati collaudati qui. I test OAuth sono simulati e le firme WebAuthn sono sintetiche.
3. I repository e le immagini di WordCraft, GridCraft, PdfCraft, DeckCraft, IdeaCraft e MailCraft non hanno ricevuto in questo turno una revisione integrale del loro codice. Il controllo di accesso di FamilOS non sostituisce il loro audit.
4. Mantenere **un solo worker backend**, come previsto dal deployment: challenge e rate limit sono in memoria. I contatori TOTP, i codici consumati e il blocco account sono persistenti; i rate limit per IP ripartono al riavvio. Non sono state eseguite prove di DDoS o saturazione della VM.
5. La sandbox condivisa è disabilitata per impostazione predefinita. Riabilitare `ALLOW_CODE_EXECUTION` senza isolamento per esecuzione riapre il rischio individuato.
6. Le installazioni esistenti possono conservare un override `mfa_optional=1` nel database: controllare il valore effettivo e impostarlo a `0` prima della pubblicazione. Fare un backup prima di aggiornare e applicare le nuove immagini backend/proxy; modificare soltanto i file sul server non aggiorna i container già avviati.

La conclusione è una riduzione concreta dei rischi verificati, non un via libera automatico all'esposizione su Internet. Il collaudo della configurazione effettiva sulla VM completa questa verifica applicativa.
