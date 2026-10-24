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

## Prima attivazione su Ubuntu

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
Solo queste app integrate sono installabili: nuove integrazioni richiedono una
release del framework e, se cambia il catalogo dell'agente, la sua reinstallazione.

I repository privati richiedono `private/github-token` con permesso di lettura
dei repository interessati. Il token non viene restituito al browser. Una
release non pubblicata non compare: un semplice tag o un commit non basta.
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

Le immagini vengono compilate mentre il sistema è ancora disponibile. Poi
vengono fermati i servizi del solo progetto Compose FamilOS e creato un archivio
in `backups/update-<id>.tar.gz` con `data`, `.env` precedente e `private`
(escluso il socket). Link e file speciali nei dati bloccano il backup, invece di
essere ignorati. Proteggere gli archivi: contengono segreti e dati familiari.
I volumi Docker di Caddy, cache di ricerca e modelli Ollama non sono inclusi.
Non vengono modificati container appartenenti a progetti separati, come Pi-hole
o SMTP; non inserirli nello stesso progetto Compose di FamilOS.

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
   Se il backup è incompleto, non usarlo: i dati originali sono ancora sul server.
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
