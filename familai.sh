#!/usr/bin/env bash
# Gestione FamilAI: stesso script sul Mac (anteprima) e sulla macchina Ubuntu (produzione).
#   ./familai.sh dev        avvia in locale su http://localhost:8080 (senza certificati)
#   ./familai.sh prod       avvia in produzione (HTTPS: certificato pubblico, o CA interna con TLS_MODE=internal per la rete locale)
#   ./familai.sh ca-cert    esporta la CA interna (data/emily-ca.crt) da installare sui PC quando TLS_MODE=internal
#   ./familai.sh update     aggiorna: git pull + rebuild + riavvio (solo i servizi cambiati) + backup automatico
#   ./familai.sh stop|logs|status|backup|restore <file>|shell
#   ./familai.sh reset-passkey EMAIL     emergenza: rimuove le passkey di un utente (rientri con la password)
#   ./familai.sh set-password EMAIL PASS emergenza: nuova password per un utente
#   ./familai.sh users                   elenca gli utenti
#   ./familai.sh deploy [user@host]      dal Mac: git push + "update" sulla macchina Ubuntu via SSH (host anche in .deploy)
#   ./familai.sh github-token [TOKEN|check]  token GitHub di sola lettura per i repository privati (build, upgrade, update)
#   ./familai.sh portainer up|down|logs  pannello web Docker (Portainer) su https://<ip>:9443 per gestire altri container
#   ./familai.sh wordcraft up|down|logs|upgrade  word processor WordCraft: documenti Word/PDF dalla chat (MCP) + editor nel browser
#   ./familai.sh gridcraft up|down|logs|upgrade  foglio di calcolo GridCraft: file Excel dalla chat (MCP) + editor nel browser
#   ./familai.sh pdfcraft up|down|logs|upgrade   PdfCraft: unire/dividere/modificare PDF dalla chat (MCP) + editor PDF nel browser
#   ./familai.sh deckcraft up|down|logs|upgrade  DeckCraft: presentazioni PowerPoint dalla chat (MCP) + editor nel browser
#   ./familai.sh ideacraft up|down|logs|upgrade  IdeaCraft: mappe mentali e diagrammi dalla chat (SVG + file nativo) + editor nel browser
#   ./familai.sh mailcraft up|down|logs|upgrade  MailCraft: posta elettronica (IMAP/SMTP) per utente, web app + tool in chat
#   ./familai.sh turn up|lan|down|logs            server TURN (coturn) per le chiamate di 💬 Messaggi: up = anche da Internet, lan = solo rete locale
set -euo pipefail
cd "$(dirname "$0")"

MODE_FILE=".familai-mode"
# Token GitHub di sola lettura (private/github-token) per i repository privati rickyb82/*: segreto di build delle app
# (docker-compose.yml) e credenziale di git pull / ls-remote qui sotto. Vuoto = repository pubblici. ./familai.sh github-token
GH_TOKEN_FILE=private/github-token
if [ ! -e "$GH_TOKEN_FILE" ]; then
  mkdir -p private && : > "$GH_TOKEN_FILE"
  chmod 600 "$GH_TOKEN_FILE"
fi
# git con il token (se c'e') solo per github.com e solo per questo comando: niente token nei file di configurazione di git
ghgit() {
  local t; t=$(tr -d ' \r\n' < "$GH_TOKEN_FILE" 2>/dev/null || true)
  if [ -n "$t" ]; then
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" \
      GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$t" | base64 | tr -d '\n')" git "$@"
  else
    git "$@"
  fi
}

# --- individua Docker e Compose e spiega chiaramente cosa manca ---
if ! command -v docker >/dev/null 2>&1; then
  echo "!! 'docker' non trovato. Installa Docker Desktop (Mac) o Docker Engine (Ubuntu) e riapri il terminale."; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "!! Docker non e' in esecuzione. Su Mac apri Docker Desktop dalle Applicazioni e attendi che la balena nella barra dei menu sia ferma; su Ubuntu: sudo systemctl start docker (e aggiungi il tuo utente al gruppo docker)."; exit 1
fi
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "!! Il plugin Docker Compose manca. Su Mac di solito significa che nel PATH c'e' un altro 'docker' (es. Homebrew) prima di quello di Docker Desktop:"
  echo "   which -a docker     -> dovrebbe comparire /usr/local/bin/docker o /Applications/Docker.app/Contents/Resources/bin/docker"
  echo "   Rimedio rapido: brew uninstall docker  (oppure usa il percorso completo di Docker Desktop)."
  echo "   Su Ubuntu: sudo apt install docker-compose-v2"; exit 1
fi

compose() {
  local mode extra=()
  mode=$(cat "$MODE_FILE" 2>/dev/null || echo prod)
  if [ "$mode" = "dev" ]; then
    $DC -f docker-compose.yml -f docker-compose.dev.yml ${extra[@]+"${extra[@]}"} "$@"
  else
    $DC -f docker-compose.yml ${extra[@]+"${extra[@]}"} "$@"
  fi
}

# Ricarica Caddy dopo una modifica del Caddyfile. Il file e' montato da solo nel container: git pull lo sostituisce con un
# file nuovo e il container resta agganciato al vecchio, quindi un semplice "caddy reload" rilegge la versione vecchia.
# Se il container vede un Caddyfile diverso da quello sul disco lo riavviamo (si riaggancia al file nuovo).
caddy_reload() {
  local cf=Caddyfile
  [ "$(cat "$MODE_FILE" 2>/dev/null || echo prod)" = dev ] && cf=Caddyfile.dev   # in anteprima e' montato Caddyfile.dev
  if compose exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null | cmp -s - "$cf"; then
    compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || true
  else
    echo ">> Caddyfile cambiato: riavvio Caddy"
    compose up -d --force-recreate caddy
  fi
}

need_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    # SECRET_KEY casuale al primo avvio
    local key
    key=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=$key/" .env && rm -f .env.bak
    echo ">> Creato .env con SECRET_KEY casuale. Controlla ADMIN_EMAIL/ADMIN_PASSWORD (e PUBLIC_HOST per la produzione)."
  fi
  mkdir -p data backups
}

backup() {
  mkdir -p backups private
  local out="backups/familai-$(date +%Y%m%d-%H%M%S).tar.gz" rc=0 err excl=()
  err=$(mktemp)
  # mai buttare l'archivio per un avviso: con un file illeggibile tar salva tutto il resto (prima si ripiegava su data/ soltanto,
  # perdendo .env e private/ senza dirlo)
  tar czf "$out" ${excl[@]+"${excl[@]}"} data .env $( [ -d private ] && echo private ) 2>>"$err" || rc=$?
  ls -t backups/familai-*.tar.gz | tail -n +15 | xargs -r rm -f   # tieni gli ultimi 14
  if [ "$rc" -eq 0 ] || { [ "$rc" -eq 1 ] && ! grep -v 'file changed as we read it' "$err" | grep -q .; }; then
    if grep -q '^!!' "$err"; then grep '^!!' "$err"; fi
    echo ">> Backup: $out"
  else
    echo "!! Backup INCOMPLETO: $out (tar: codice $rc). Cosa non e' entrato:"
    grep -v 'file changed as we read it' "$err" | grep . | head -n 8 | sed 's/^/   /'
  fi
  rm -f "$err"
}

case "${1:-}" in
  domain-apply)
    need_env
    cfg=data/installation/domain.env
    [ -f "$cfg" ] || { echo "!! Salva prima il dominio nell’app Installazione."; exit 1; }
    origin=$(sed -n 's/^WEBAUTHN_ORIGIN=//p' "$cfg")
    case "$origin" in https://*) ;; *) echo "!! Per localhost HTTP usa ./familos.sh dev. Il dominio pubblico richiede HTTPS."; exit 1;; esac
    mkdir -p private
    cp .env private/env-before-domain-apply
    chmod 600 private/env-before-domain-apply
    cp .env private/domain-env.next
    chmod 600 private/domain-env.next
    while IFS='=' read -r key value; do
      case "$key" in
        PUBLIC_HOST|WEBAUTHN_RP_ID|WEBAUTHN_ORIGIN|COOKIE_SECURE|TLS_MODE|ACME_EMAIL|ACME_STAGING|ACME_CA|HTTPS_PORT|INSTALLATION_REVISION)
          case "$value" in *[!a-zA-Z0-9.:/@_%+-]*) echo "!! Valore non valido per $key"; exit 1;; esac
          if grep -q "^$key=" private/domain-env.next; then
            sed "s#^$key=.*#$key=$value#" private/domain-env.next > private/domain-env.tmp
            mv private/domain-env.tmp private/domain-env.next
          else
            printf '%s=%s\n' "$key" "$value" >> private/domain-env.next
          fi;;
        *) echo "!! Impostazione non riconosciuta: $key"; exit 1;;
      esac
    done < "$cfg"
    mv private/domain-env.next .env
    previous_mode=$(cat "$MODE_FILE" 2>/dev/null || echo prod)
    echo prod > "$MODE_FILE"
    mkdir -p data/installation/tls
    compose config --quiet
    if ! compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile; then
      cp private/env-before-domain-apply .env
      echo "$previous_mode" > "$MODE_FILE"
      echo "!! Configurazione Caddy non valida: .env precedente ripristinato, servizi non riavviati."
      exit 1
    fi
    compose up -d --force-recreate backend caddy
    echo ">> Configurazione applicata: $origin"
    echo ">> Let’s Encrypt rinnova automaticamente. Diagnostica: ./familos.sh logs caddy"
    ;;
  dev)
    need_env; echo dev > "$MODE_FILE"
    compose up -d --build
    ip=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
    echo ">> FamilOS in anteprima su http://localhost:8080${ip:+  (dagli altri PC della rete: http://$ip:8080)}  (log: ./familai.sh logs)";;
  prod)
    need_env; echo prod > "$MODE_FILE"
    grep -q "PUBLIC_HOST=familai.example.com" .env && { echo "!! Imposta PUBLIC_HOST (nome DNS o IP del server), ACME_EMAIL e WEBAUTHN_* nel .env prima della produzione"; exit 1; }
    compose up -d --build
    host=$(grep ^PUBLIC_HOST= .env | cut -d= -f2)
    hp=$(grep ^HTTPS_PORT= .env | cut -d= -f2 || true); [ -n "$hp" ] && [ "$hp" != 443 ] && host="$host:$hp"
    echo ">> FamilOS in produzione su https://$host"
    if grep -q '^TLS_MODE=internal' .env; then
      echo ">> TLS con CA interna: sui PC va installata la CA (./familai.sh ca-cert → data/emily-ca.crt)"
    else
      echo ">> Certificato pubblico automatico: $host deve risolvere su questo server e le porte 80/443 devono essere raggiungibili da internet"
    fi;;
  ca-cert)
    # Esporta la CA interna di Caddy (TLS_MODE=internal) da installare come autorita' attendibile su PC, Mac e telefoni
    need_env
    mkdir -p data
    compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > data/emily-ca.crt 2>/dev/null \
      && echo ">> CA salvata in data/emily-ca.crt. Installala come 'autorita' di certificazione radice attendibile':" \
      && echo "   macOS: doppio clic → Accesso Portachiavi → Sistema → fidati sempre;  Windows: certmgr → Autorita' radice attendibili;" \
      && echo "   iPhone/iPad: invia il file, Impostazioni → Profilo scaricato → installa, poi Generali → Info → Impostazioni certificati → abilita;" \
      && echo "   Android: Impostazioni → Sicurezza → Credenziali → installa certificato CA." \
      || echo "!! Caddy non e' in esecuzione in modalita' prod con TLS_MODE=internal";;
  update)
    need_env
    if [ "${2:-}" = --pulled ]; then
      :   # ripartito con lo script appena scaricato: il pull e' gia' fatto
    elif [ -d .git ] && git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      before=$(git rev-parse HEAD 2>/dev/null || true)
      echo ">> git pull"; ghgit pull --ff-only
      # lo script in esecuzione e' quello di prima del pull: se e' cambiato riparte con il nuovo (nuovi file da creare,
      # nuovi segreti nel compose…), cosi' l'aggiornamento non si ferma a meta'
      if [ "$before" != "$(git rev-parse HEAD 2>/dev/null || true)" ] && ! git diff --quiet "$before" HEAD -- familai.sh 2>/dev/null; then
        echo ">> familai.sh e' cambiato: riparto con la versione nuova"
        exec bash ./familai.sh update --pulled
      fi
    elif [ -d .git ]; then
      echo ">> (nessun remote configurato: uso i file gia' presenti nella cartella)"
    else
      echo ">> (nessun repository git: aggiorno solo i container con i file presenti)"
    fi
    backup
    compose build --pull
    compose up -d --remove-orphans      # riavvia solo i servizi la cui immagine e' cambiata
    caddy_reload
    docker image prune -f >/dev/null
    echo ">> Aggiornato alla versione $(cat VERSION 2>/dev/null || echo ?). Stato:"; compose ps;;
  stop)    compose down;;
  logs)    compose logs -f --tail=200 "${2:-}";;
  status)  compose ps; echo; echo "Versione: $(cat VERSION 2>/dev/null)"; echo "Modalita': $(cat $MODE_FILE 2>/dev/null || echo prod)";;
  backup)  backup;;
  restore)
    [ -f "${2:-}" ] || { echo "uso: ./familai.sh restore backups/<file>.tar.gz"; exit 1; }
    compose down; tar xzf "$2"
    compose up -d; echo ">> Ripristinato $2";;
  shell)   compose exec backend bash;;
  reset-passkey) compose exec backend python -m app.cli reset-passkey "${2:?email}";;
  set-password)  compose exec backend python -m app.cli set-password "${2:?email}" "${3:?password}";;
  unlock)        compose exec backend python -m app.cli unlock "${2:?email}";;
  users)         compose exec backend python -m app.cli list-users;;
  github-token)
    # Il token GitHub di sola lettura per i repository privati: ./familai.sh github-token [TOKEN] lo salva (senza argomento lo
    # chiede senza mostrarlo) e prova a leggere Emily e ogni app; ./familai.sh github-token check prova quello salvato.
    if [ "${2:-}" != check ]; then
      tok="${2:-}"
      if [ -z "$tok" ]; then printf "Token GitHub (github_pat_…, non viene mostrato): "; IFS= read -rs tok; echo; fi
      tok=$(printf '%s' "$tok" | tr -d ' \r\n')
      [ -n "$tok" ] || { echo "!! nessun token"; exit 1; }
      umask 077; printf '%s' "$tok" > "$GH_TOKEN_FILE"; chmod 600 "$GH_TOKEN_FILE"
      echo ">> token salvato in $GH_TOKEN_FILE (fuori da git, nel backup)"
    fi
    [ -s "$GH_TOKEN_FILE" ] || echo "   (nessun token salvato: provo l'accesso anonimo)"
    ko=0
    for r in FamilOS wordcraft gridcraft pdfcraft deckcraft ideacraft mailcraft; do
      if ghgit ls-remote "https://github.com/rickyb82/$r" HEAD >/dev/null 2>&1; then echo "   ok   rickyb82/$r"; else echo "   NO   rickyb82/$r"; ko=1; fi
    done
    if [ "$ko" = 1 ]; then
      echo "!! qualche repository non si legge: il token deve avere accesso a tutti i repository di rickyb82 (All repositories) con Contents: Read-only, e non essere scaduto"; exit 1
    fi
    origin=$(git remote get-url origin 2>/dev/null || true)
    case "$origin" in
      *@github.com*|https://*:*@*) echo "!! l'origine di questo repository contiene credenziali: git remote set-url origin https://github.com/rickyb82/emily";;
    esac
    echo ">> il token legge tutti i repository: build, upgrade e ./familai.sh update funzionano anche con i repository privati";;
  turn)
    # coturn per le chiamate di Messaggi: segreto condiviso col backend (credenziali temporanee per utente), URL dal dominio
    need_env
    set_env() { if grep -q "^$1=" .env; then sed -i.bak "s#^$1=.*#$1=$2#" .env && rm -f .env.bak; else echo "$1=$2" >> .env; fi; }
    case "${2:-up}" in
      up|lan)
        if [ "${2:-up}" = lan ]; then set_env TURN_ALLOW_PRIVATE 1; else set_env TURN_ALLOW_PRIVATE ""; fi
        host=$(grep '^WEBAUTHN_ORIGIN=' .env | cut -d= -f2- | sed -e 's#^[a-z]*://##' -e 's#[:/].*$##')
        [ -n "$host" ] || { echo "!! WEBAUTHN_ORIGIN non impostato nel .env: serve il dominio di Emily"; exit 1; }
        grep -q '^TURN_SECRET=.\+' .env || set_env TURN_SECRET "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        set_env TURN_URLS "turn:$host:3478?transport=udp,turn:$host:3478?transport=tcp"
        set_env TURN_REALM "$host"
        if grep -q '^COMPOSE_PROFILES=' .env; then
          grep -q "^COMPOSE_PROFILES=.*turn" .env || { sed -i.bak "s/^COMPOSE_PROFILES=\(.*\)\$/COMPOSE_PROFILES=\1,turn/" .env && rm -f .env.bak; }
        else
          echo "COMPOSE_PROFILES=turn" >> .env
        fi
        compose --profile turn up -d coturn
        compose up -d backend
        if [ "${2:-up}" = lan ]; then
          echo ">> TURN attivo in rete locale su $host:3478 (fa da ponte fra VLAN e Wi-Fi diverse). Il nome $host deve risolversi"
          echo "   all'IP locale del server dai PC; se c'e' un firewall interno, aprite 3478 UDP/TCP e 49160-49200 UDP verso il server."
        else
          echo ">> TURN attivo su $host:3478. Apri sul router/firewall: 3478 UDP e TCP, 49160-49200 UDP verso questa macchina."
          echo "   Se la macchina e' dietro NAT, metti l'IP pubblico in TURN_EXTERNAL_IP=<ip pubblico>/<ip locale> nel .env e rilancia."
        fi;;
      down) compose --profile turn stop coturn; set_env TURN_URLS ""; compose up -d backend;;
      logs) compose --profile turn logs -f --tail=100 coturn;;
      *) echo "uso: ./familai.sh turn up|lan|down|logs"; exit 1;;
    esac;;
  portainer)
    case "${2:-up}" in
      up)   $DC -f extras/portainer.yml up -d; echo ">> Portainer: https://$(hostname -I 2>/dev/null | awk '{print $1}' || echo localhost):9443  (crea l'amministratore entro 5 minuti dal primo avvio)";;
      down) $DC -f extras/portainer.yml down;;
      logs) $DC -f extras/portainer.yml logs -f --tail=100;;
      update) $DC -f extras/portainer.yml pull && $DC -f extras/portainer.yml up -d;;
      *) echo "uso: ./familai.sh portainer up|down|logs|update"; exit 1;;
    esac;;
  wordcraft|gridcraft|pdfcraft|deckcraft|ideacraft)
    # App ArtCraft: CLI+MCP per il modello e editor web (extras/artcraft). Stessa logica per entrambe.
    need_env
    app="$1"; APPU=$(echo "$app" | tr a-z A-Z)
    port=7982; [ "$app" = gridcraft ] && port=7983; [ "$app" = pdfcraft ] && port=7984; [ "$app" = deckcraft ] && port=7985; [ "$app" = ideacraft ] && port=7986
    web=1   # tutte le app hanno l'editor web (per le app future senza: web=0)
    svcs="$app"; [ "$web" = 1 ] && svcs="$app $app-web"
    set_env() { # set_env CHIAVE valore (idempotente)
      if grep -q "^$1=" .env; then sed -i.bak "s#^$1=.*#$1=$2#" .env && rm -f .env.bak; else echo "$1=$2" >> .env; fi
    }
    case "${2:-up}" in
      up|build)
        mkdir -p "data/$app"
        if grep -q '^COMPOSE_PROFILES=' .env; then
          grep -q "^COMPOSE_PROFILES=.*$app" .env || { sed -i.bak "s/^COMPOSE_PROFILES=\(.*\)\$/COMPOSE_PROFILES=\1,$app/" .env && rm -f .env.bak; }
        else
          echo "COMPOSE_PROFILES=$app" >> .env
        fi
        set_env "${APPU}_HOST" "$app:$port"
        [ "$web" = 1 ] && set_env "${APPU}_WEB" 1
        echo ">> Build di $app (compila Rust: la prima volta anche 30-60 minuti)…"
        # shellcheck disable=SC2086
        DOCKER_BUILDKIT=1 compose --profile "$app" build $svcs
        # shellcheck disable=SC2086
        compose --profile "$app" up -d $svcs
        compose up -d --build backend
        caddy_reload
        case "$app" in
          wordcraft) echo ">> WordCraft attivo. In chat: 'scrivimi una lettera in Word'; nel menu: 📝 Documenti per l'editor completo.";;
          gridcraft) echo ">> GridCraft attivo. In chat: 'fammi il bilancio in Excel'; nel menu: 📝 Documenti per l'editor completo.";;
          pdfcraft)  echo ">> PdfCraft attivo. In chat: 'unisci questi PDF', 'estrai le pagine 2-4'; ✏️ accanto ai PDF apre l'editor.";;
          deckcraft) echo ">> DeckCraft attivo. In chat: 'fammi una presentazione di 6 slide su…'; ✏️ accanto ai .pptx apre l'editor.";;
          ideacraft) echo ">> IdeaCraft attivo. In chat: 'fammi una mappa mentale su…'; ✏️ accanto alle mappe apre l'editor.";;
        esac;;
      down)
        # shellcheck disable=SC2086
        compose --profile "$app" stop $svcs
        set_env "${APPU}_HOST" ""
        [ "$web" = 1 ] && set_env "${APPU}_WEB" ""
        compose up -d backend;;
      logs) compose --profile "$app" logs -f --tail=100 $svcs;;
      upgrade)
        # prende l'ultimo commit del repository dell'app e ricompila (incrementale grazie alla cache di cargo)
        repo="https://github.com/rickyb82/$app"
        sha=$(ghgit ls-remote "$repo" HEAD 2>/dev/null | cut -f1)
        [ -n "$sha" ] || { echo "!! non riesco a leggere $repo (rete?)"; exit 1; }
        if grep -q "^${APPU}_SHA=$sha" .env; then echo ">> $app e' gia' all'ultimo commit (${sha:0:10})"; exit 0; fi
        echo ">> $app: aggiorno al commit ${sha:0:10} e ricompilo…"
        set_env "${APPU}_SHA" "$sha"
        # shellcheck disable=SC2086
        DOCKER_BUILDKIT=1 compose --profile "$app" build $svcs || { echo "!! Build fallita: se l'errore viene da familai-web-patch.py il sorgente upstream e' cambiato e la patch va aggiornata."; exit 1; }
        # shellcheck disable=SC2086
        compose --profile "$app" up -d $svcs
        docker image prune -f >/dev/null
        echo ">> $app aggiornato.";;
      *) echo "uso: ./familai.sh $app up|down|logs|upgrade"; exit 1;;
    esac;;
  mailcraft)
    # MailCraft (extras/artcraft/Dockerfile.mailcraft): posta elettronica per utente (IMAP/SMTP), web app su /mailcraft/,
    # tool MCP per utente in chat. Un solo container; dati in ./private/mailcraft (fuori da ./data).
    need_env
    set_env() { if grep -q "^$1=" .env; then sed -i.bak "s#^$1=.*#$1=$2#" .env && rm -f .env.bak; else echo "$1=$2" >> .env; fi; }
    case "${2:-up}" in
      up|build|upgrade)
        mkdir -p private/mailcraft data/mailcraft
        if grep -q '^COMPOSE_PROFILES=' .env; then
          grep -q "^COMPOSE_PROFILES=.*mailcraft" .env || { sed -i.bak "s/^COMPOSE_PROFILES=\(.*\)\$/COMPOSE_PROFILES=\1,mailcraft/" .env && rm -f .env.bak; }
        else
          echo "COMPOSE_PROFILES=mailcraft" >> .env
        fi
        # token di servizio (backend <-> server MailCraft): generato una volta, mai mostrato agli utenti
        grep -q '^MAILCRAFT_TOKEN=.\+' .env || set_env MAILCRAFT_TOKEN "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        set_env MAILCRAFT_URL "http://mailcraft:7878"
        set_env MAILCRAFT_MCP_HOST "mailcraft:7889"
        if [ "$2" = upgrade ]; then
          sha=$(ghgit ls-remote https://github.com/rickyb82/mailcraft HEAD 2>/dev/null | cut -f1)
          [ -n "$sha" ] || { echo "!! non riesco a leggere il repository mailcraft (rete?)"; exit 1; }
          if grep -q "^MAILCRAFT_SHA=$sha" .env; then echo ">> mailcraft e' gia' all'ultimo commit (${sha:0:10})"; exit 0; fi
          echo ">> mailcraft: aggiorno al commit ${sha:0:10} e ricompilo…"
          set_env MAILCRAFT_SHA "$sha"
        fi
        echo ">> Build di MailCraft (compila Rust + WebAssembly: la prima volta anche 20-40 minuti)…"
        DOCKER_BUILDKIT=1 compose --profile mailcraft build mailcraft
        compose --profile mailcraft up -d mailcraft
        compose up -d --build backend
        caddy_reload
        echo ">> MailCraft attivo. Menu 🧰 Produttivita' > ✉️ Posta: ogni adulto configura le sue caselle (anche piu' di una)."
        echo "   In chat: 'leggi le mail non lette', 'rispondi a Luca…', 'configura la mia casella Aruba…'.";;
      down)
        compose --profile mailcraft stop mailcraft
        set_env MAILCRAFT_URL ""
        set_env MAILCRAFT_MCP_HOST ""
        compose up -d backend;;
      logs) compose --profile mailcraft logs -f --tail=100 mailcraft;;
      *) echo "uso: ./familai.sh mailcraft up|build|down|logs|upgrade"; exit 1;;
    esac;;
  deploy)
    # Aggiorna la macchina di produzione da qui: push del repo e "update" remoto.
    # Destinazione: argomento (user@host[:dir]) oppure file .deploy con una riga  user@host[:dir]
    target="${2:-$(cat .deploy 2>/dev/null || true)}"
    [ -n "$target" ] || { echo "uso: ./familai.sh deploy user@host[:/percorso/FamilAI]   (oppure scrivi la destinazione nel file .deploy)"; exit 1; }
    rhost="${target%%:*}"; rdir="${target#*:}"; [ "$rdir" = "$target" ] && rdir="FamilAI"
    if [ -d .git ] && git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      echo ">> git push"; git push
    else
      echo ">> (nessun remote git: copio i file via rsync)"
      rsync -az --delete --exclude data --exclude backups --exclude .env --exclude .familai-mode --exclude node_modules --exclude .git ./ "$rhost:$rdir/"
    fi
    echo ">> update su $rhost:$rdir"
    ssh -t "$rhost" "cd '$rdir' && ./familai.sh update";;
  *) sed -n '2,18p' "$0";;
esac
