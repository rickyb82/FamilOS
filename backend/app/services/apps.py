"""Catalogo dei software del framework e chi puo' usarli / amministrarli.

Tre livelli:
- **Super amministratore** (`Role.admin`): vede e amministra tutto (pannello Amministrazione, provider, utenti, tutte le app).
- **Amministratore di app**: un utente (`Role.adult`) con uno o piu' software in `User.admin_apps`: in quelle app ha i poteri
  di configurazione che Emily riserva all'amministratore (es. database e preferenze di CoverCraft, caricamento nelle cartelle
  di reparto per l'Archivio). Non entra nel pannello Amministrazione.
- **Utente finale**: usa solo i software in `User.apps` (pagine, icone nella cartella APP, tool in chat).

`User.apps = None` significa "tutti" (gli utenti creati prima di questa versione): la migrazione abilita i software a tutti.
Una lista vuota significa nessun software.
"""
from __future__ import annotations

from ..models import Role, User

APPS: list[dict] = [{'id': 'wordcraft',
  'label': 'Documenti Word',
  'app': 'WordCraft',
  'glyph': 'W',
  'desc': 'editor di testi nel browser e tool write_document in chat'},
 {'id': 'gridcraft',
  'label': 'Fogli di calcolo',
  'app': 'GridCraft',
  'glyph': 'X',
  'desc': 'fogli Excel nel browser e tool write_spreadsheet'},
 {'id': 'deckcraft',
  'label': 'Presentazioni',
  'app': 'DeckCraft',
  'glyph': 'P',
  'desc': 'slide nel browser e tool write_presentation'},
 {'id': 'ideacraft',
  'label': 'Mappe e diagrammi',
  'app': 'IdeaCraft',
  'glyph': '◎',
  'desc': 'mappe mentali e schemi, tool mind_map'},
 {'id': 'pdfcraft', 'label': 'PDF', 'app': 'PdfCraft', 'glyph': 'PDF', 'desc': 'editor PDF e tool pdf_tool'},
 {'id': 'mailcraft',
  'label': 'Posta',
  'app': 'MailCraft',
  'glyph': '✉',
  'desc': 'caselle IMAP/SMTP personali e tool di posta in chat'},
 {'id': 'messaggi',
  'label': 'Messaggi',
  'app': 'Messaggi',
  'glyph': '💬',
  'desc': 'chat tra colleghi, gruppi, foto e documenti, chiamate e videochiamate'},
 {'id': 'archivio',
  'label': "Archivio dell'organizzazione",
  'app': 'Documenti',
  'glyph': '🏢',
  'desc': "le cartelle di reparto (lettura secondo i reparti dell'utente)",
  'admin': 'carica, sposta ed elimina i documenti nelle cartelle di reparto'}]
CHILD_APP_IDS = {"wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "messaggi"}

APP_IDS = [a["id"] for a in APPS]
PRODUCTIVITY_IDS = {"wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft", "messaggi", "archivio"}


def available_app_ids() -> set[str]:
    from ..config import org_mode_on
    return set(APP_IDS) if org_mode_on() else set(PRODUCTIVITY_IDS)
# software che non si abilitano da soli agli utenti "tutti" (User.apps = None): li vede il super amministratore e chi li ha
# avuti esplicitamente nella scheda del membro (l'Intranet e' riservata ai super amministratori)
OPT_IN = {a["id"] for a in APPS if a.get("opt_in")}
# tool "nativi" (non mcp__) che appartengono a un'app
NATIVE_TOOL_APP = {"write_document": "wordcraft", "write_spreadsheet": "gridcraft", "pdf_tool": "pdfcraft",
                   "write_presentation": "deckcraft", "mind_map": "ideacraft", "starlink_status": "starlink"}


def app_of_tool(tool_name: str) -> str | None:
    """L'app a cui appartiene un tool della chat (None = tool generico del framework)."""
    if tool_name.startswith("mcp__"):
        server = tool_name.split("__", 2)[1] if tool_name.count("__") >= 2 else ""
        return server if server in APP_IDS else None
    return NATIVE_TOOL_APP.get(tool_name)


def is_superadmin(user: User) -> bool:
    return user.role == Role.admin


def user_apps(user: User) -> set[str]:
    """I software che l'utente puo' usare. None nel DB = tutti (utenti esistenti prima della migrazione)."""
    available = available_app_ids()
    if user.role == Role.child:
        permitted = available & CHILD_APP_IDS
        return permitted if user.apps is None else permitted & set(user.apps)
    if user.role == Role.admin:
        return available
    if user.apps is None:
        return available - OPT_IN
    return {a for a in (user.apps or []) if a in available}


def app_enabled(user: User, app: str) -> bool:
    return app in user_apps(user)


def admin_apps(user: User) -> set[str]:
    if user.role == Role.admin:
        return available_app_ids()
    if user.role == Role.child:
        return set()
    return {a for a in (user.admin_apps or []) if a in APP_IDS}


def is_app_admin(user: User, app: str) -> bool:
    """Super amministratore, oppure amministratore di quella app (che deve anche poterla usare)."""
    return user.role == Role.admin or (app in admin_apps(user) and app_enabled(user, app))


def role_label(user: User) -> str:
    if user.role == Role.admin:
        return "super amministratore"
    if user.role == Role.child:
        return "bambino/a"
    return "amministratore app" if admin_apps(user) else "utente"
