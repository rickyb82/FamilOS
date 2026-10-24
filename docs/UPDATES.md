> I backup sono gestiti manualmente dall’amministratore. Nessun aggiornamento crea archivi automatici o richiede password GPG.

# Aggiornamenti da interfaccia

In **Installazione → Aggiornamenti e app**, un amministratore può controllare le
release e installarle manualmente. Gli adulti senza ruolo amministratore e i
bambini non possono usare queste API. Le operazioni che modificano il server
richiedono un login negli ultimi 15 minuti, sessione sbloccata, MFA quando
previsto e protezione CSRF.

## Canali

- **Stabile** è il valore iniziale: esclude draft, prerelease e tag con suffissi.
- **Sperimentale** include le prerelease pubblicate e richiede una conferma
  aggiuntiva per installarle. Non segue i commit di `main`.
- **Sviluppo** segue l’ultimo commit del ramo predefinito GitHub, con conferma sperimentale esplicita. È il canale da usare dopo un push senza pubblicare una release. Il commit viene ricontrollato e bloccato durante la preparazione.
- Sono supportati tag `v1.2.3`, `v1.3.0-alpha.1`, `v1.3.0-beta.1`,
  `v1.3.0-preview.1`, `v1.3.0-rc.1` (anche senza `v`). La selezione considera
  le prime 100 release restituite da GitHub e sceglie la versione più alta,
  indipendentemente dall'ordine di pubblicazione.
- Il canale viene salvato quando si controllano le release. Cambiarlo invalida
  nell'interfaccia l'offerta del canale precedente.
- Non sono ammessi downgrade. Passando da una sperimentale allo stabile si
  attende una versione stabile più recente. Una versione stabile dello stesso
  numero è successiva alla sua prerelease.
- Per FamilOS il contenuto di `VERSION` deve corrispondere al tag.

## Prima attivazione standalone su Ubuntu

È necessario Docker Compose e un'installazione di produzione già funzionante,
con checkout Git pulito e remoto `origin` GitHub. Dal progetto:

```sh
./familos.sh updater install
docker compose up -d --build backend
```

Il primo comando usa `sudo`, copia l'agente in `/usr/local/lib/familos-updater`,
registra la configurazione in `/etc/familos-updater/config.json` e abilita
`familos-updater.service`. È un servizio privilegiato del server: gestisce
Docker, ferma i container e legge i dati per il backup. Non aprire porte per
questo servizio. Non viene eseguito automaticamente da questa modifica.

Il backend comunica mediante un socket Unix locale, montato in una directory
dedicata; non riceve il socket Docker. Il socket è accessibile a root (il
backend Docker attuale usa root). Deploy rootless/userns o backend non-root
richiedono una configurazione dedicata dei permessi e non sono coperti da
questo installer. La configurazione attuale supporta una sola installazione
per VM e nessuna replica dei servizi.

Il repository del framework deriva da `origin`, quindi un fork segue il proprio
repository. Le sei app predefinite sono WordCraft, GridCraft, PdfCraft,
DeckCraft, IdeaCraft e MailCraft; i loro repository sono configurabili soltanto
sul server nel file dell'agente. Dopo una modifica riavviare il servizio.
Nel deploy standalone questo catalogo comprende le sei app integrate. Il catalogo manifest descritto sotto richiede l’adattatore Orbita.

I repository privati richiedono `private/github-token` con permesso di lettura
dei repository interessati. Il token non viene restituito al browser. Nel canale stabile una release non pubblicata non compare; per seguire i commit usare Sviluppo.
Il repository non viene reso pubblico da questo sistema; pubblicazione e scelta
della licenza rimangono decisioni del proprietario.

## Installazione, dati e fiducia

L'offerta dura 15 minuti. Prima dell'installazione si ricontrollano release,
canale e commit; se il tag è stato spostato si interrompe l'operazione. La build
delle app usa il commit esatto, non soltanto una chiave di cache.

Il codice dei repository configurati, compresi Dockerfile e Compose, è codice
fidato dall'amministratore. Il controllo del commit impedisce cambiamenti
silenziosi tra verifica e installazione, ma **non equivale a una firma del
publisher o a un audit della release**. Non configurare repository non fidati.

Le immagini vengono compilate mentre il sistema è disponibile. I servizi vengono poi fermati per applicare la modifica. Nessun archivio automatico viene creato. Preparare e conservare autonomamente i backup necessari; le copie locali di rollback Orbita non costituiscono un backup completo.

L'agente avvia i servizi e verifica il backend prima di riavviare Caddy. Questa
è una verifica operativa minima, non un collaudo completo delle singole app.
Non cancellare le vecchie immagini Docker durante un aggiornamento. Nessun
backup viene eliminato automaticamente: controllare lo spazio libero.

Se il processo fallisce prima della manutenzione, codice e `.env` vengono
ripristinati. Se fallisce dopo, i servizi vengono fermati e il lavoro resta
bloccato per **recupero manuale**: non si effettua un downgrade automatico di un
database già migrato. Un riavvio inatteso dell'agente richiede anch'esso una
verifica manuale. I log e lo stato persistono fuori dal container backend.

## Recupero sulla VM

Consultare prima `sudo journalctl -u familos-updater` e
`sudo cat /var/lib/familos-updater/state.json`. Il log dell'ultimo comando è in
`/var/lib/familos-updater/last-command.log`: può contenere informazioni riservate,
non pubblicarlo integralmente. `recovery.json` contiene commit e servizi
precedenti, `rollback-images.json` gli ID delle vecchie immagini e `recovery.env`
la configurazione precedente anche quando l'archivio dati non è stato completato.

1. Fermare l'agente (`sudo systemctl stop familos-updater`) e i servizi FamilOS.
2. Verificare l'integrità dell'archivio indicato nello stato (`sudo tar -tzf ...`).
   Usare un backup manuale verificato; non viene creato un backup durante l’aggiornamento.
3. Per tornare indietro dopo una migrazione, conservare separatamente i dati
   attuali e ripristinare **insieme** codice del commit precedente, `.env` e dati
   del backup verificato. Non sovrascrivere un database mentre un servizio lo usa.
   Conservare la directory `private/updater-socket` e non estrarre archivi esterni.
4. Avviare con le immagini precedenti usando il Compose ripristinato e
   `-f /var/lib/familos-updater/rollback-images.json`, con `--no-build`. Avviare
   prima i servizi applicativi elencati in `recovery.json`, poi Caddy dopo i
   controlli. Non eseguire un prune delle immagini prima del recupero.
5. Verificare login e documenti. Conservare una copia di `state.json`, impostare
   il suo campo `job` a `null` con un editor sulla VM solo a recupero completato,
   poi riavviare l'agente. Non rimuovere `installed` né i backup.

La procedura è intenzionalmente locale per evitare che un clic nel browser
elimini dati successivi a un aggiornamento. Provare il ciclo completo su una
copia della VM prima di affidargli l'installazione di produzione.


## Orbita: attivazione e aggiornamenti dal browser

Il percorso gestito è `/opt/orbita/apps/familos`, con Caddy condiviso nel progetto Orbita. L’adattatore preserva questo layout e non avvia un secondo proxy. Dopo avere installato questa versione del framework e i relativi helper Orbita, eseguire una sola volta sul nodo:

```sh
cd /opt/orbita/apps/familos
sudo python3 extras/updater/install.py
```

Richiede Python 3 con `venv`, Docker Compose e systemd. Il setup non chiede password di backup. Il servizio usa un virtualenv e un socket locale montato nel backend; il backend viene ricreato per applicare il mount.

Da **Installazione → Aggiornamenti e app**:

1. Seleziona FamilOS e il canale Sviluppo per installare le modifiche pubblicate sul ramo predefinito. Stabile/Sperimentale continuano a usare release GitHub.
2. Controlla, conferma manutenzione e versione sperimentale se richiesta, quindi Installa.
3. Il servizio host esegue build, aggiornamento e controlli senza backup automatico. La pagina può scollegarsi durante il riavvio; l’operazione continua su systemd e lo stato torna disponibile alla riconnessione.
4. Le app Craft sono installabili/aggiornabili selezionandole nello stesso pannello. I repository privati richiedono il token già salvato in `private/github-token` sul nodo.

Il backup include tutti i servizi del progetto Orbita, immagini Docker e dati interni alla piattaforma; non è un backup della VM Proxmox né di Pi-hole se esterno allo stack. Un mount persistente esterno causa un errore esplicito. Il browser grafico upstream viene incluso dall’adattatore del core, con rete interna, token, seccomp e limiti risorse. Il layout Compose upstream è verificato per hash: cambi strutturali non riconosciuti bloccano l’aggiornamento prima dello stop e richiedono aggiornare l’adattatore.

I log riservati restano in `/var/lib/familos-updater/last-command.log` e `last-compose.log`; sul web arrivano solo fasi controllate. Gli snapshot di rollback restano protetti sulla VM. L’adattatore core ripristina codice, dati e immagini precedenti se l’attivazione fallisce; anche dopo un tentativo di rollback l’agente richiede una verifica amministrativa prima di nuovi lavori. Nessun backup o vecchia immagine viene eliminato automaticamente.

## Catalogo manifest

Importa JSON e PNG, seleziona gli adulti autorizzati, conferma e installa. La scheda mostra versione e stato; l’app compare nel desktop e in Tutte le app. Il pulsante Disattiva conserva dati e immagini. Per aggiornare importa un manifest con versione maggiore e nuovi digest. “Salva accessi selezionati” sostituisce gli adulti autorizzati; gli amministratori mantengono accesso e i minori non sono supportati dal runtime v1.

I dati vivono in `/opt/orbita/data/app-catalog/<id>/data/`, il Compose generato in `compose.apps.json`; nessuna porta host è pubblicata. Il gateway applica sandbox CSP e autorizzazione per ogni richiesta. La coerenza dei dati personali dentro il servizio resta responsabilità dello sviluppatore; l’esempio Note mostra la separazione per utente. Vedi `packages/app-sdk/README.md` per il contratto e i limiti HTTP.

Le installazioni verificano i servizi e conservano una copia locale per il rollback, senza generare backup completi. Modifiche al formato dati o ai mount non sono applicate automaticamente. Si accettano soltanto publisher approvati dall’amministratore; non esiste una verifica automatica della reputazione delle immagini.

## Ripristino Orbita su nuova macchina

Usare gli strumenti di ripristino Orbita con la password conservata esternamente. Il backup contiene catalogo, configurazioni Compose, immagini e dati; `data/updater-host-config.json` conserva anche la configurazione host da confrontare con quella nuova. Dopo aver verificato DNS, IP, TLS e dati, reinstallare l’agente con il comando di setup senza configurare password di backup automatici. Lo stato dei lavori e il socket sono transitori e non devono essere ripristinati come servizi attivi. Il test definitivo del backup resta un ripristino su un nodo isolato.

Per aggiornare il codice del servizio host dopo future modifiche al protocollo/adattatore, rieseguire il setup a lavori terminati. L’aggiornamento del framework non riavvia il proprio agente nel mezzo del lavoro.
