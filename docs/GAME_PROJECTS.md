# Giochi dalla chat

Premi **Crea un gioco** nella chat e descrivi regole, stile e controlli. Il pulsante prepara il messaggio: invialo quando hai finito. Lo strumento Artifact deve essere abilitato per il tuo account.

Il pannello offre Gioca, Codice e risorse, Console, Avvia/riavvia e Ferma. Le modifiche dell'assistente aggiornano il progetto corrente; una nuova versione viene creata solo su richiesta. Salva modifiche conserva la versione, Salva nuova versione archivia quella precedente. Il selettore delle versioni permette di consultare quelle precedenti in sola lettura.

**Porta gli errori in chat** prepara una richiesta con le ultime righe diagnostiche, senza inviarla automaticamente. L'assistente può rileggere il progetto ed aggiornare solo i file interessati. I log non devono essere interpretati come istruzioni affidabili.

## Ambienti

- JavaScript e TypeScript (anche JSX/TSX con le dipendenze necessarie), compilati con esbuild WebAssembly. Import relativi tra file, CSS e JSON. Le risorse importate diventano data URL.
- Phaser 3.90.0 con `libraries: ["phaser"]`, disponibile come globale `Phaser`.
- Three.js e altre librerie browser tramite moduli ES HTTPS, con versioni esatte su jsDelivr o esm.sh. Esempio: `"three": "https://cdn.jsdelivr.net/npm/three@0.174.0/build/three.module.js"`. Le dipendenze vengono anche mappate per i moduli esterni. Niente pacchetti Node o addon nativi.
- Python 3 tramite Pyodide 0.27.7, Canvas e API JavaScript: `from js import document`. Pacchetti disponibili nella distribuzione Pyodide dichiarati in `python_packages`. Pygame standard, tkinter e programmi desktop nativi non sono supportati. Usare loop asincroni e `pyodide.ffi.create_proxy` per callback mantenute in vita.

## Manifest

```json
{
  "runtime": "javascript",
  "entry": "main.ts",
  "files": {
    "main.ts": "import {color} from './palette'; const c=game.canvas.getContext('2d'); c.fillStyle=color; c.fillRect(20,20,100,100);",
    "palette.ts": "export const color = '#a78bfa'"
  },
  "dependencies": {},
  "libraries": [],
  "python_packages": []
}
```

`game.canvas` è il canvas 800×600 iniziale e `game.root` il contenitore. Per Phaser passare `canvas: game.canvas, type: Phaser.CANVAS` (oppure `Phaser.WEBGL`, non `AUTO` con canvas fornito); per Three.js usare `new THREE.WebGLRenderer({canvas: game.canvas})`. `game.asset('assets/sprite.png')` restituisce un URL blob per una risorsa incorporata. I file possono contenere testo, SVG o una data URI base64 per immagini, audio e altri dati. Gli upload inseriscono le risorse sotto `assets/` e vanno salvati. Non usare percorsi assoluti, `..`, URL del server o risorse inesistenti.

Massimo 100 file e 12 MiB per progetto; upload 4 MiB per risorsa. Internet necessario per runtime e dipendenze. Audio soggetto al consenso/interazione del browser. Per i giochi Python il primo caricamento è più lungo.

Il progetto è eseguito in un iframe senza accesso all'origine FamilOS, ai cookie o alle API. La CSP limita rete e librerie ai CDN indicati e blocca form e navigazione della pagina principale. Questa separazione protegge i dati, ma non garantisce limiti CPU: evitare loop sincroni infiniti; se una pagina resta bloccata, ricaricarla. Non è un ambiente per eseguibili arbitrari o giochi nativi.

## Esportazione

Scarica progetto ZIP include sorgenti, risorse, manifest `familos-game.json` e `index.html` giocabile nel browser con connessione Internet. Per reimportare in un artifact gioco usa Importa progetto FamilOS JSON. Il file `docs/games/acchiappa-la-stella.json` è un esempio completo con punteggio, timer, input mouse/touch e riavvio.

## Strumenti assistente

`create_artifact(kind="game", project=manifest)` crea un gioco. `list_artifacts(artifact_id=...)` legge il contenuto salvato. `update_artifact(artifact_id=..., files={"main.ts": "..."})` modifica o aggiunge i soli file indicati. `delete_files` rimuove file; non può eliminare il file di avvio senza un manifest sostitutivo valido. `project` sostituisce l'intero manifest. `new_version=true` è riservato alle richieste esplicite dell'utente.

## Cartella Giochi e famiglia

**Salva in Giochi** conserva una copia indipendente dal messaggio: eliminare la chat non elimina il gioco. Dal desktop aprire **Giochi** per avviare, importare un manifest JSON o aggiungere **Ragnoman Rooftops**, il prototipo 3D incluso. Ogni account può conservare fino a 100 giochi.

I bambini possono creare e giocare ai propri giochi privati. **Chiedi di condividere** notifica gli adulti della famiglia: possono provare il gioco e approvare o rifiutare quella precisa revisione. Gli adulti possono condividere direttamente i propri giochi. Cambiare titolo o codice di una copia salvata revoca la condivisione e richiede una nuova approvazione. La revoca interrompe l'accesso multiplayer ai successivi controlli del server.

## Multiplayer familiare

Per un gioco condiviso scegliere **Crea stanza**, poi gli altri account della stessa famiglia possono entrare dalla cartella Giochi. Massimo 8 giocatori per stanza, 8 stanze per famiglia e 2 create da ciascun utente. Nessun accesso pubblico o chat incorporata nei giochi. Le richieste passano per la sessione FamilOS: il codice del gioco non riceve cookie o credenziali.

Questa prima versione sincronizza pose degli avatar in modalità cooperativa, circa cinque volte al secondo. Non è un server di fisica competitivo: punteggi, collisioni e oggetti restano locali, salvo ulteriore implementazione specifica. Le stanze sono temporanee e in memoria, per il deployment attuale a singolo processo; un riavvio le chiude. Per più worker servirà un servizio condiviso per le stanze. I giocatori inattivi scadono dopo 12 secondi.

Il gioco usa esclusivamente questo ponte:

```js
game.multiplayer.publish({ x: 1, y: 2, z: 3, rotation: 0, action: 'run' });
game.multiplayer.onPeers(players => {
  // players: [{ id, name, state: { x, y, z, rotation, action } }]
  // Aggiornare gli avatar e rimuovere quelli assenti dall'elenco.
});
```

Le azioni ammesse sono `idle`, `run`, `jump`, `swing`; le coordinate devono essere numeri finiti. Il ponte scarta messaggi provenienti da altre finestre e il server ricontrolla famiglia, approvazione e revisione a ogni scambio. Il multiplayer funziona dentro Giochi, non nell'HTML esportato, che resta giocabile in modalità individuale.

### Ragnoman Rooftops

Modello procedurale stilizzato, città 3D, corsa, salto, ragnatela, caduta e riavvio; supporta gli avatar degli altri membri nelle stanze familiari. **WASD/frecce** per muoversi, **Spazio** per saltare, **E** per la ragnatela, **R** per ricominciare; disponibili anche pulsanti touch. È un prototipo cooperativo, non un gioco completo commerciale. Manifest: `docs/games/ragnoman-rooftops.json`.
