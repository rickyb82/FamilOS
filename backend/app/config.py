from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Identita' personalizzabile della distribuzione
    brand_name: str = "FamilOS"

    # Base
    data_dir: Path = Path("./data")
    database_url: str = "sqlite+aiosqlite:///./data/familai.db"
    secret_key: str = "dev-secret-change-me"
    log_level: str = "info"
    cookie_secure: bool = True
    security_login_alerts: bool = True
    updater_socket: str = "/run/familos-updater/control.sock"
    browser_worker_url: str = "http://browser:8080"
    browser_worker_token: str = ""
    allow_code_execution: bool = False  # shared sandbox lacks per-job isolation; opt-in only

    # Esposizione / WebAuthn
    public_host: str = "localhost"
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "FamilOS"
    webauthn_origin: str = "http://localhost:5173"
    require_mfa_for_adults: bool = True
    session_hours_adult: int = 168
    session_hours_child: int = 12
    session_days_passkey: int = 90     # accesso con la sola passkey (telefono): la sessione si rinnova finche' la si usa

    # Bootstrap admin
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme"
    admin_name: str = "Admin"
    family_name: str = "Famiglia"

    # Provider
    openai_api_key: str = ""
    openai_default_model: str = "gpt-6-luna"
    openai_cache_mode: str = "auto"
    openai_reasoning_effort: str = "auto"   # auto | none | low | medium | high (modelli OpenAI con ragionamento)
    enable_mock_provider: bool = False  # utile per test senza chiavi
    # Modello locale (Ollama). Vuoto = disabilitato. Mac: http://host.docker.internal:11434 (Ollama nativo, usa la GPU);
    # Linux con profilo compose 'ollama': http://ollama:11434
    ollama_url: str = ""
    ollama_default_model: str = "qwen3:8b"
    # Server LLM di casa con API compatibile OpenAI (vLLM, llama.cpp, LM Studio...): es. http://192.168.4.2:8000
    # Router "Auto": un modello locale piccolo decide se rispondere in locale o passare al cloud
    max_output_tokens: int = 32768      # lunghezza massima di UNA chiamata al modello (serve alta per app/artifact e codice)
    max_total_output_tokens: int = 256000   # oltre questo totale la risposta si ferma davvero (continuazioni automatiche incluse)
    auto_continue: str = "true"
    max_tool_rounds: int = 500          # passaggi con strumenti per turno: una domanda che ne richiede centinaia li fa tutti
    turn_tool_chars: int = 300000       # oltre questo totale di risultati nel turno, i piu' vecchi si riducono a un promemoria
    thinking_mode: str = "cloud"        # off | cloud (OpenAI) | all (anche i modelli locali, piu' lenti)
    debug_llm: str = "off"              # off | admins | all: pannello debug sotto le risposte (parametri, tempi, ragionamento nascosto)
    ollama_temperature: str = ""        # vuoto = temperatura del Modelfile del modello
    ollama_top_p: str = ""
    ollama_seed: str = ""
    ollama_think: str = "auto"          # auto (Qwen3: /no_think se il ragionamento non e' visibile) | on | off
    thinking_budget: int = 4096         # token di ragionamento per risposta (modelli compatibili)         # se la risposta si interrompe per lunghezza, il backend chiede di continuare da solo

    # Tool
    web_search_provider: str = "auto"      # auto | searxng | openai
    searxng_url: str = ""                  # es. http://searxng:8080 (servizio nel compose): ricerca web in casa, senza chiavi
    web_search_read_pages: int = 3         # pagine da leggere per arricchire i risultati SearXNG (0 = solo snippet)
    web_search_summarize: str = "auto"     # auto (modello locale se c'e') | none: riassunto delle pagine lette
    web_search_model_openai: str = "gpt-4.1-mini"
    image_provider: str = "auto"          # openai
    image_model: str = "gpt-image-2.5-flare"
    # Voce (sintesi vocale naturale per lettura messaggi e giochi/app negli artifact)
    tts_provider: str = "auto"            # auto | openai | none
    tts_model_openai: str = "gpt-4o-mini-tts"
    tts_voice_openai: str = "nova"
    tts_style: str = ""                   # istruzioni di stile (vuoto = default: voce calda e chiara per bambini)
    child_moderation: bool = True
    mcp_config_path: Path = Path("mcp_servers.json")
    wordcraft_host: str = ""          # host:porta del container WordCraft (es. wordcraft:7982): documenti Word/PDF via MCP
    wordcraft_web: bool = False       # editor WordCraft nel browser servito su /wordcraft/ (container wordcraft-web)
    gridcraft_host: str = ""          # host:porta del container GridCraft (es. gridcraft:7983): fogli di calcolo via MCP
    gridcraft_web: bool = False       # editor GridCraft nel browser servito su /gridcraft/ (container gridcraft-web)
    pdfcraft_host: str = ""           # host:porta del container PdfCraft (es. pdfcraft:7984): PDF via MCP
    pdfcraft_web: bool = False        # editor PdfCraft nel browser servito su /pdfcraft/ (container pdfcraft-web)
    deckcraft_host: str = ""          # host:porta del container DeckCraft (es. deckcraft:7985): presentazioni via MCP
    deckcraft_web: bool = False       # editor DeckCraft nel browser servito su /deckcraft/ (container deckcraft-web)
    ideacraft_host: str = ""          # host:porta del container IdeaCraft (es. ideacraft:7986): mappe mentali via CLI/HTTP
    ideacraft_web: bool = False       # editor IdeaCraft nel browser servito su /ideacraft/ (container ideacraft-web)
    org_mode: str = "0"               # FamilOS starts in family mode; legacy ORG_MODE is ignored.
    mfa_optional: str = "0"          # "1" = facoltativo; "0" = obbligatorio per gli adulti
    user_models_json: str = ""       # JSON ["provider/modello", …]: i modelli che gli utenti (non admin) possono scegliere; vuoto = solo i default
    user_default_model: str = ""     # "provider/modello" proposto agli utenti nelle nuove chat (deve stare nell'elenco sopra)
    default_quota_gb: str = "50"     # quota di spazio per utente (file + versioni) quando non impostata sul singolo membro; 0 = illimitata
    passkeys: str = "1"              # "0" = niente passkey: il secondo fattore e' solo l'app authenticator (TOTP), es. server locale
    upload_scan_cmd: str = ""        # antivirus sugli upload, es. "clamdscan --no-summary" (riceve il percorso del file); vuoto = nessuno
    upload_scan_fail_open: bool = True  # scanner non raggiungibile: True = accetta il file, False = rifiuta
    app_name: str = "Emily"           # nome con cui l'assistente si presenta e firma le email (i nomi tecnici restano FamilAI)
    mailcraft_url: str = ""           # URL del server MailCraft multi-utente (es. http://mailcraft:7878): posta per utente
    mailcraft_mcp_host: str = ""      # host:porta del ponte MCP per utente di MailCraft (es. mailcraft:7889)
    mailcraft_token: str = ""         # token di servizio del server MailCraft (conia i token utente); generato da familai.sh
    turn_urls: str = ""               # server TURN per le chiamate di Messaggi (es. turn:emily.example.it:3478), separati da virgola
    turn_secret: str = ""             # segreto condiviso con coturn (static-auth-secret): le credenziali sono temporanee, per utente
    stun_urls: str = ""               # STUN in piu' (vuoto = quello del TURN, se c'e'; altrimenti nessuno: vanno bene le reti locali)

    # App nativa Emily (iPhone e Android, repository emily-app): notifiche native e passkey dentro l'app
    apns_key_p8_path: str = ""        # chiave APNs (.p8) di Apple Developer › Certificates › Keys; con APNS_KEY_ID e APNS_TEAM_ID accende le notifiche su iPhone
    apns_key_id: str = ""
    apns_team_id: str = ""            # Team ID Apple (anche per /.well-known/apple-app-site-association: passkey dentro l'app)
    apns_bundle_id: str = "it.example.familos"
    apns_sandbox: bool = False        # true per le build di sviluppo installate da Xcode (ambiente APNs sandbox)
    fcm_service_account_path: str = ""  # service account JSON del progetto Firebase: accende le notifiche su Android
    testflight_url: str = ""   # link pubblico TestFlight dell'app iPhone (mostrato in /app agli iPhone nel browser)
    android_cert_sha256: str = ""     # impronte SHA-256 del certificato di firma Android, separate da virgola (/.well-known/assetlinks.json)

    @field_validator("wordcraft_web", "gridcraft_web", "pdfcraft_web", "deckcraft_web", "ideacraft_web", mode="before")
    @classmethod
    def _empty_is_false(cls, v):
        # docker-compose passa WORDCRAFT_WEB=${WORDCRAFT_WEB:-}: una stringa vuota vale "spento", non e' un errore
        if isinstance(v, str) and not v.strip():
            return False
        return v

    # Email (SMTP) — configurabile dal pannello
    smtp_host: str = ""
    smtp_port: str = ""               # vuoto = 587 (starttls) / 465 (ssl)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_from_name: str = "FamilAI"
    smtp_security: str = "starttls"   # starttls | ssl | none
    email_mfa_fallback: str = "true"  # consente il codice via email al posto della passkey al login
    mail_transport: str = "auto"      # auto | gmail_oauth | smtp  (auto = Gmail OAuth se collegato, altrimenti SMTP)
    gmail_oauth_refresh_token: str = ""   # ottenuto con "Collega account Google" (cifrato nel DB)
    gmail_oauth_email: str = ""

    # Accesso con Google (OAuth client 'Web application' su console.cloud.google.com)
    google_client_id: str = ""
    google_client_secret: str = ""

    # Costi e budget (pannello Costi)
    pricing_json: str = ""            # override del listino prezzi (JSON), vedi services/costs.py
    budget_openai: str = ""
    openai_admin_key: str = ""        # Admin API key OpenAI per leggere la spesa reale

    # Sandbox esecuzione codice (servizio separato nel compose; vuoto = esecuzione disabilitata)
    sandbox_url: str = ""
    sandbox_timeout: int = 60
    artifact_max_history: int = 20

    # Compattazione delle chat lunghe: riassunto cumulativo + archivio cercabile (RAG) dei messaggi vecchi
    compaction_enabled: str = "true"
    context_budget_tokens: int = 24000    # oltre questa stima di token di cronologia si compatta
    context_keep_tokens: int = 8000       # messaggi recenti da tenere integrali

    # Memoria di famiglia (fatti estratti dalle chat, con approvazione)
    memory_enabled: str = "true"
    memory_auto_approve: str = "false"    # true = le voci sicure (confidenza alta, autore adulto) entrano senza approvazione
    memory_model: str = "auto"            # modello per l'estrazione: auto | provider/modello (es. openai/gpt-4.1-mini)
    memory_top_k: int = 8

    # RAG (ricerca semantica sui file di progetto)
    embedding_provider: str = "auto"      # auto | ollama | openai | none
    embedding_model: str = ""             # vuoto = default del provider
    rag_chunk_chars: int = 2500           # dimensione frammenti (caratteri)
    rag_chunk_overlap: int = 300
    rag_top_k: int = 6                    # frammenti inseriti automaticamente nel contesto
    rag_inline_max_chars: int = 15000     # documenti più piccoli vengono inseriti interi

    @property
    def uploads_dir(self) -> Path:
        p = self.data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def generated_dir(self) -> Path:
        p = self.data_dir / "generated"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.org_mode = "0"
    s.image_provider = "openai"
    if s.tts_provider not in ("auto", "openai", "none"):
        s.tts_provider = "openai"
    if s.embedding_provider not in ("auto", "ollama", "openai", "none"):
        s.embedding_provider = "auto"
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def org_mode_on() -> bool:
    """FamilOS usa sempre la modalita' famiglia."""
    return False


def passkeys_on() -> bool:
    """Passkey disponibili per registrazione e login (spente: 2FA solo con app authenticator)."""
    return str(get_settings().passkeys).strip().lower() not in ("0", "false", "no", "off")


def mfa_optional_on() -> bool:
    """Il secondo fattore e' una scelta dell'utente (chiesta al primo accesso) invece di un obbligo."""
    return str(get_settings().mfa_optional).strip().lower() in ("1", "true", "yes", "on")
