from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ---- auth ----
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class PinLoginRequest(BaseModel):
    user_id: str
    pin: str = Field(min_length=4, max_length=12)


class RecoveryLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class WebAuthnVerifyRequest(BaseModel):
    credential: dict[str, Any]
    name: str = "Passkey"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    avatar: str
    email: str | None
    role: str
    age_group: str
    birth_year: int | None
    is_active: bool
    has_passkey: bool = False
    has_totp: bool = False
    mfa_choice: str = ""
    last_login: datetime | None = None
    last_logout: datetime | None = None
    active_sessions: int = 0
    departments: list[str] = []
    apps: list[str] | None = None        # None = tutti i software
    admin_apps: list[str] = []
    role_label: str = ""
    quota_bytes: int | None = None      # None = default dell'organizzazione
    quota_effective: int = 0            # byte (0 = illimitata)
    storage_used: int = 0               # byte
    policy: dict = {}
    effective_policy: dict = {}

    model_config = {"from_attributes": True}


class MeOut(BaseModel):
    acting_as_child: bool = False
    session_locked: bool = False
    user: UserOut
    family_name: str
    mfa_verified: bool
    mfa_required: bool
    providers: list[dict]
    tools: list[str]
    features: dict = {}
    model_lock: str = ""          # admin: "provider/modello" che si e' bloccato da solo ("" = libero)
    prefs: dict = {}              # preferenze personali: {"tools_off": [gruppi di strumenti spenti]}


# ---- admin ----
class CreateMemberRequest(BaseModel):
    username: str = Field(min_length=2, max_length=60)
    display_name: str = Field(min_length=1, max_length=120)
    avatar: str = "🙂"
    role: str = "adult"
    age_group: str = "kid"
    birth_year: int | None = None
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)
    pin: str | None = Field(default=None, min_length=4, max_length=12)


class UpdateMemberRequest(BaseModel):
    display_name: str | None = None
    avatar: str | None = None
    role: str | None = None
    age_group: str | None = None
    birth_year: int | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)
    pin: str | None = Field(default=None, min_length=4, max_length=12)
    policy: dict | None = None
    departments: list[str] | None = None
    apps: list[str] | None = None        # elenco esplicito; ["*"] = tutti
    admin_apps: list[str] | None = None
    quota_gb: float | None = None       # -1 = torna al default dell'organizzazione, 0 = illimitata


class TrustDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


# ---- chat ----
class ConversationCreate(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    project_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    project_id: str | None = None
    archived: bool | None = None


class SendMessageRequest(BaseModel):
    text: str = Field(max_length=20000)
    file_ids: list[str] = []
    provider: str | None = None
    model: str | None = None
    tools: list[str] | None = None  # sottoinsieme dei tool consentiti; None = tutti
    approval_id: str | None = None   # risposta a una richiesta approvata dal genitore


class MessageOut(BaseModel):
    id: str
    role: str
    author_id: str | None
    content: list
    created_at: datetime
    flagged: bool
    usage: dict

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: str
    title: str
    provider: str
    model: str
    project_id: str | None
    owner_id: str
    created_at: datetime
    updated_at: datetime
    archived: bool
    shared: bool = False
    owner_name: str | None = None

    model_config = {"from_attributes": True}


# ---- progetti / condivisione ----
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    instructions: str = ""
    icon: str = "📁"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    icon: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    icon: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    shared: bool = False
    owner_name: str | None = None

    model_config = {"from_attributes": True}


class ShareRequest(BaseModel):
    resource_type: str  # conversation | project | file
    resource_id: str
    target_user_id: str | None = None  # None = tutta la famiglia
    permission: str = "read"


class ShareOut(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    owner_id: str
    target_user_id: str | None
    permission: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FileOut(BaseModel):
    id: str
    filename: str
    mime: str
    size: int
    kind: str
    owner_id: str
    project_id: str | None
    conversation_id: str | None
    created_at: datetime
    index_status: str = "none"
    chunk_count: int = 0
    folder_id: str | None = None
    access: str = "owner"          # owner | write | read | parent: cosa puo' farci chi ha chiesto l'elenco
    owner_name: str = ""
    url: str = ""
    sha256: str | None = None

    model_config = {"from_attributes": True}
