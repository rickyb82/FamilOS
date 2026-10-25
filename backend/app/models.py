"""Modelli dati di FamilAI."""
from __future__ import annotations

import enum
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Data/ora sempre in UTC e sempre "aware": SQLite scarta il fuso, quindi un valore letto dal DB tornava senza fuso e
    il browser lo prendeva per ora locale (due ore indietro in Italia). Qui si salva in UTC e si rilegge con tzinfo=UTC,
    cosi' l'API emette "…Z" e il browser converte nell'ora locale dell'utente."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None) if dialect.name == "sqlite" else value
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def new_id() -> str:
    return uuid.uuid4().hex


class Role(str, enum.Enum):
    admin = "admin"    # genitore amministratore
    adult = "adult"    # adulto
    child = "child"    # bambino/a


class AgeGroup(str, enum.Enum):
    young = "young"    # ~5-8 anni
    kid = "kid"        # ~9-12 anni
    teen = "teen"      # 13+
    adult = "adult"


class Family(Base):
    __tablename__ = "families"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    # impostazioni globali della famiglia (provider abilitati, modelli, ecc.)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    members: Mapped[list[User]] = relationship(back_populates="family")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    username: Mapped[str] = mapped_column(String(60))
    display_name: Mapped[str] = mapped_column(String(120))
    avatar: Mapped[str] = mapped_column(String(16), default="🙂")
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.child)
    age_group: Mapped[AgeGroup] = mapped_column(Enum(AgeGroup), default=AgeGroup.kid)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)  # adulti
    pin_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)       # bambini
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # policy specifica dell'utente (sovrascrive i default del gruppo di età)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    recovery_codes: Mapped[list] = mapped_column(JSON, default=list)  # hash dei codici
    totp_secret: Mapped[str | None] = mapped_column(String(300), nullable=True)          # app authenticator (cifrato)
    totp_pending: Mapped[str | None] = mapped_column(String(300), nullable=True)         # in attesa di conferma
    totp_last_counter: Mapped[int] = mapped_column(BigInteger, default=-1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # reparti aziendali (modalita' azienda): ["tecnico", "commerciale", "amministrativo"]; decidono quali cartelle di
    # reparto (documenti condivisi dell'organizzazione) l'utente vede e il modello usa come base nelle sue chat
    departments: Mapped[list] = mapped_column(JSON, default=list)
    prefs: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # preferenze personali (es. strumenti dell'assistente spenti)
    # software del framework che l'utente puo' usare (services/apps.py): None = tutti (utenti pre-migrazione), [] = nessuno
    apps: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # software di cui e' amministratore (poteri di configurazione dell'app, non il pannello Amministrazione)
    admin_apps: Mapped[list] = mapped_column(JSON, default=list)
    # quota di spazio in byte (file + versioni di cui e' proprietario); NULL = default dell'organizzazione (default_quota_gb), 0 = illimitata
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # scelta sul secondo fattore quando e' facoltativo: "" = non ancora chiesto, "skip" = l'utente ha scelto di non usarlo
    mfa_choice: Mapped[str] = mapped_column(String(10), default="")

    family: Mapped[Family] = relationship(back_populates="members")
    credentials: Mapped[list[WebAuthnCredential]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("family_id", "username"),)

    @property
    def is_child(self) -> bool:
        return self.role == Role.child

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin


class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[list] = mapped_column(JSON, default=list)
    name: Mapped[str] = mapped_column(String(120), default="Passkey")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    user: Mapped[User] = relationship(back_populates="credentials")


class Session(Base):
    """Sessione di login (cookie opaco HttpOnly)."""
    __tablename__ = "sessions"
    screen_locked: Mapped[bool] = mapped_column(Boolean,default=False)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: secrets.token_urlsafe(48))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(12), default="password")  # password | google | pin


class TrustedDevice(Base):
    """Dispositivo approvato da un genitore: solo da qui i bambini possono accedere con il PIN."""
    __tablename__ = "trusted_devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: secrets.token_urlsafe(32))
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    name: Mapped[str] = mapped_column(String(120))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class SecurityNotice(Base):
    __tablename__ = "security_notices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    event: Mapped[str] = mapped_column(String(60))
    account: Mapped[str] = mapped_column(String(200))
    ip: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_to: Mapped[list] = mapped_column(JSON, default=list)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")  # istruzioni di sistema del progetto
    icon: Mapped[str] = mapped_column(String(16), default="📁")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="Nuova chat")
    provider: Mapped[str] = mapped_column(String(40), default="ollama")
    model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict)   # stato per-chat (es. estrazione memoria)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None = assistente
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool
    # contenuto "ricco": lista di blocchi {type: text|image|file|tool_use|tool_result, ...}
    content: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)   # segnalato dal filtro contenuti
    flag_reason: Mapped[str] = mapped_column(String(300), default="")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class FileAsset(Base):
    """File caricato o generato (immagine, video, documento)."""
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20), default="upload")  # upload | generated
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    # RAG: none (non indicizzabile) | pending | indexed | failed
    index_status: Mapped[str] = mapped_column(String(12), default="none")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # cartella dell'utente in Documenti (None = radice)
    folder_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Craft Sync (services/sync.py): numero di cambiamento globale (il cursore dei client), impronta del contenuto
    # ("" = da ricalcolare) e ultima modifica di contenuto, nome o cartella
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class FileVersion(Base):
    """Cronologia delle modifiche di un file (come Google Drive): a ogni salvataggio che sovrascrive il contenuto, il
    contenuto precedente viene messo da parte in data/versions/<file>/ con chi e quando. Le versioni ravvicinate dello
    stesso autore (salvataggio automatico) vengono accorpate; si tengono le ultime 30."""
    __tablename__ = "file_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(32), index=True)
    author_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author_name: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(String(200), default="")     # "editor", "assistente (write_document)", "ripristino"…
    path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Folder(Base):
    """Cartella di Documenti, creata dall'utente (annidabile). I file dei progetti restano visibili anche come cartelle
    automatiche, questa e' l'organizzazione personale."""
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(20), default="")   # tinta dell'icona (opzionale)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    # "" = cartella personale; "org" = radice dei documenti dell'organizzazione (una per famiglia/azienda, solo admin ci scrive);
    # "dept" = cartella dentro l'albero org (dept = reparto che la vede, "" = tutti); "chat" = radice CHAT dell'utente;
    # "conv" = sottocartella automatica di una conversazione (conversation_id), il cui contenuto e' contesto di quella chat
    kind: Mapped[str] = mapped_column(String(12), default="")
    dept: Mapped[str] = mapped_column(String(40), default="")
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Craft Sync: numero di cambiamento e ultima modifica (nome, cartella superiore)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Chunk(Base):
    """Frammento di testo di un file, con embedding opzionale, per la ricerca semantica (RAG)."""
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    folder_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)   # cartella del file (RAG per cartella)
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(80), default="")


class NativeDevice(Base):
    """Un telefono con l'app Emily installata (iPhone: token APNs; Android: token FCM). Se il servizio dice che il token non
    vale piu' (410 / UNREGISTERED) il dispositivo si cancella."""
    __tablename__ = "native_devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(10))           # ios | android
    token: Mapped[str] = mapped_column(String(400), unique=True)
    device: Mapped[str] = mapped_column(String(120), default="")
    app_version: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PushSubscription(Base):
    """Un telefono o un browser che riceve le notifiche push di Emily (Web Push: iPhone con l'app sulla schermata Home, Android,
    computer). L'endpoint e le chiavi li da' il browser; se il servizio push risponde 404/410 la sottoscrizione si cancella."""
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    device: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Notification(Base):
    """Notifica per un utente (campanella): condivisioni ricevute, ecc. Pubblicata anche in tempo reale (services/events)."""
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))           # share_file | share_conversation | share_project | info
    text: Mapped[str] = mapped_column(String(300))
    link: Mapped[str] = mapped_column(String(300), default="")
    actor: Mapped[str] = mapped_column(String(120), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Share(Base):
    """Condivisione di una risorsa con un membro (target_user_id) o con tutta la famiglia (None)."""
    __tablename__ = "shares"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    resource_type: Mapped[str] = mapped_column(String(20))  # conversation | project | file
    resource_id: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    target_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    permission: Mapped[str] = mapped_column(String(10), default="read")  # read | write
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)

    __table_args__ = (UniqueConstraint("resource_type", "resource_id", "target_user_id"),)


class UsageLog(Base):
    __tablename__ = "usage_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    kind: Mapped[str] = mapped_column(String(30))  # message | image | video | web_search | tool
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)     # token di input NON in cache
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)   # token di input letti dalla cache (scontati)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)  # token scritti in cache (Anthropic: +25%)
    detail: Mapped[str] = mapped_column(String(300), default="")


class AuditLog(Base):
    """Registro accessi e attivita': eventi di sicurezza (login, passkey, cambi policy, dispositivi) scritti dai router, piu'
    ogni operazione che cambia qualcosa e ogni apertura di un'app, scritte dal middleware di services/activity.py."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    event: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    # sezione o software (emily, files, chat, quotecraft, …) per filtrare il Log
    app: Mapped[str] = mapped_column(String(30), default="emily")
    # esito: i tentativi falliti o negati sono registrati come gli altri
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    # l'oggetto (id di file, progetto, utente, …) quando c'e'
    target: Mapped[str] = mapped_column(String(200), default="")


class MemoryItem(Base):
    """Memoria di famiglia: un fatto durevole estratto dalle chat (o dettato), filtrato e approvato."""
    __tablename__ = "memory_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))           # chi l'ha generata
    subject_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)             # se riguarda solo un membro
    scope: Mapped[str] = mapped_column(String(10), default="family")                           # family | user
    category: Mapped[str] = mapped_column(String(20), default="altro")
    text: Mapped[str] = mapped_column(String(400))
    confidence: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)             # pending | approved | rejected
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)                               # sempre nel prompt
    source_conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)


class ApprovalRequest(Base):
    """Richiesta di permesso di un bambino a un genitore (es. compiti): notificata agli adulti, decisa dal pannello."""
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="homework")
    text: Mapped[str] = mapped_column(Text)                       # la richiesta del bambino
    reason: Mapped[str] = mapped_column(String(200), default="")  # motivo del classificatore
    subject: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)   # pending | approved | denied | expired
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)           # approve | approve_tutor | deny
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)   # permesso "per oggi"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)                    # la risposta e' stata poi generata


class AppSetting(Base):
    """Impostazioni modificabili dal pannello admin (chiavi API cifrate, modelli di default).
    Sovrascrivono i valori del file .env."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)  # True = valore cifrato con SECRET_KEY
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Artifact(Base):
    """Contenuto autonomo creato dall'assistente (pagina HTML, SVG, codice, markdown) con anteprima e versioni."""
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))       # html | svg | markdown | code | mermaid
    language: Mapped[str] = mapped_column(String(40), default="")  # per kind=code: python, javascript, java...
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)  # versioni precedenti [{version, content, created_at}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)


class McpServer(Base):
    """Server MCP configurato dal pannello (sostituisce mcp_servers.json, che resta come import iniziale)."""
    __tablename__ = "mcp_servers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    type: Mapped[str] = mapped_column(String(10), default="stdio")      # stdio | http
    command: Mapped[str] = mapped_column(String(300), default="")
    args: Mapped[list] = mapped_column(JSON, default=list)
    url: Mapped[str] = mapped_column(String(500), default="")
    headers_enc: Mapped[str] = mapped_column(Text, default="")           # JSON cifrato (token/API key)
    env_enc: Mapped[str] = mapped_column(Text, default="")               # JSON cifrato
    min_role: Mapped[str] = mapped_column(String(10), default="adult")   # child | adult | admin
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)


class EmailToken(Base):
    """Codici monouso inviati via email: invito (imposta password), reset password, secondo fattore."""
    __tablename__ = "email_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))   # invite | reset | mfa
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class StarlinkSample(Base):
    """Una lettura di un terminale Starlink (services/starlink): lo storico per i grafici del dettaglio. Solo numeri, niente
    dati personali; tenuta 7 giorni (starlink.HISTORY_DAYS)."""
    __tablename__ = "starlink_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String(80), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, index=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    down_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    up_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    drop_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    obstruction: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_deg: Mapped[float | None] = mapped_column(Float, nullable=True)


class MsgRoom(Base):
    """Conversazione di Messaggi tra persone (non con l'assistente): diretta tra due utenti o gruppo con nome."""
    __tablename__ = "msg_rooms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="direct")   # direct | group
    name: Mapped[str] = mapped_column(String(120), default="")         # solo per i gruppi
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, index=True)   # ultimo messaggio (ordinamento)


class MsgMember(Base):
    __tablename__ = "msg_members"
    room_id: Mapped[str] = mapped_column(ForeignKey("msg_rooms.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)        # puo' rinominare il gruppo e gestire i membri
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)


class MsgMessage(Base):
    """Messaggio: testo, file (ogni membro ha la sua copia in Documenti › Messaggi: file_ids = {user_id: file_id}),
    evento di sistema (gruppo creato, membro aggiunto) o riepilogo di una chiamata."""
    __tablename__ = "msg_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("msg_rooms.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="text")      # text | file | system | call
    text: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(String(300), default="")
    file_mime: Mapped[str] = mapped_column(String(120), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- Craft Sync ----
class SyncCounter(Base):
    """Un solo contatore: il numero di cambiamento globale di file e cartelle (services/sync.py)."""
    __tablename__ = "sync_counter"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class SyncDevice(Base):
    """Un computer collegato con Craft Sync: il token (solo l'hash) e' la sua credenziale, revocabile da Impostazioni."""
    __tablename__ = "sync_devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(20), default="")      # windows | macos | linux | ...
    app_version: Mapped[str] = mapped_column(String(40), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncPairingCode(Base):
    """Codice di collegamento mostrato in Impostazioni (10 minuti, un solo uso): l'agente lo scambia con il token."""
    __tablename__ = "sync_pairing_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class BrowserSiteApproval(Base):
    """Persistent exact-domain decisions, scoped to a family and age band."""
    __tablename__ = "browser_site_approvals"
    __table_args__ = (UniqueConstraint("family_id", "domain", "age_group"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    age_group: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="review")
    source: Mapped[str] = mapped_column(String(16), default="ai")
    revision: Mapped[str] = mapped_column(String(32), default=new_id)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)

    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str] = mapped_column(String(200), default="")


class BrowserReviewSchedule(Base):
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    __tablename__ = "browser_review_schedules"
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer, default=6)
    hour: Mapped[int] = mapped_column(Integer, default=3)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Rome")
    model: Mapped[str] = mapped_column(String(200), default="")
    last_slot: Mapped[str] = mapped_column(String(16), default="")
    last_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str] = mapped_column(String(200), default="")


class SyncTombstone(Base):
    """Lapide di un file o di una cartella eliminati: cosi' un client che torna dopo giorni sa cosa togliere."""
    __tablename__ = "sync_tombstones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(8))                        # file | folder
    resource_id: Mapped[str] = mapped_column(String(32), index=True)
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[str] = mapped_column(String(32))
    org: Mapped[bool] = mapped_column(Boolean, default=False)           # stava nell'archivio dell'organizzazione
    dept: Mapped[str] = mapped_column(String(40), default="")           # reparto della cartella ("" = per tutti)
    deleted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class SavedGame(Base):
    """Independent personal game snapshot, retained when the source chat is deleted."""
    __tablename__ = 'saved_games'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey('families.id'), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sharing: Mapped[str] = mapped_column(String(20), default='private')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    runtime: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)
