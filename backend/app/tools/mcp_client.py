"""Client MCP con connessioni gestibili a caldo dal pannello admin.

Ogni server gira in un task dedicato che apre la sessione (stdio o Streamable HTTP), registra i tool come
`mcp__<server>__<tool>` e resta in attesa finche' non viene chiesta la disconnessione: cosi' l'apertura e la
chiusura dei context manager avvengono nello stesso task (requisito di anyio/mcp).

Configurazione: tabella `mcp_servers` (pannello Famiglia → MCP). Al primo avvio, se la tabella e' vuota,
vengono importati i server di `mcp_servers.json`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import json
import logging
import re
import time
from dataclasses import dataclass, field

from ..config import get_settings
from ..models import McpServer, Role
from ..providers.base import ToolSpec
from .registry import Tool, ToolContext, ToolResult, register_tool, unregister_prefix

log = logging.getLogger(__name__)
settings = get_settings()

ROLE_RANK = {Role.child: 0, Role.adult: 1, Role.admin: 2}
MIN_ROLE_RANK = {"child": 0, "adult": 1, "admin": 2}
CONNECT_TIMEOUT = 45


@dataclass
class ServerConfig:
    name: str
    type: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    min_role: str = "adult"
    enabled: bool = True
    per_user: bool = False   # una sessione MCP per utente (MailCraft): la connessione "madre" serve solo a elencare i tool


@dataclass
class Connection:
    cfg: ServerConfig
    user_id: str = ""        # connessione per utente (per_user): a chi appartiene
    last_used: float = 0.0
    task: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    session: object | None = None
    tools: list[dict] = field(default_factory=list)
    error: str = ""
    connected: bool = False


def _attr(obj, *names, default=None):
    """Campo di un oggetto della libreria mcp: camelCase (mcp 1.x: inputSchema, isError, mimeType) o snake_case (versioni
    successive: input_schema, is_error, mime_type). Cosi' un aggiornamento della libreria non spegne i server MCP."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


_READ_ONLY_PREFIXES = ("list_", "get_", "inspect", "render", "read_", "describe", "search", "find", "validate", "export",
                       "select_", "help", "info", "preview", "snapshot")


def _mutating(tool: str) -> bool:
    """I tool che cambiano il documento (execute, batch, insert_*, add_*, update_*, delete_*…) fanno scattare il salvataggio
    automatico; quelli di sola lettura o selezione no."""
    return not tool.startswith(_READ_ONLY_PREFIXES) and not tool.startswith(("open_", "new_", "save", "close"))


_HEX_ID = re.compile(r"^(?:/files/)?([0-9a-f]{32})(?:\?.*)?$")


async def _resolve_file_ids(args, ctx: ToolContext):
    """Il modello indica spesso un file con il suo id di chat (o /files/<id>) dove l'app vuole un percorso: ovunque negli
    argomenti una stringa cosi' corrisponda a un file della famiglia, la sostituiamo con il percorso su disco (visibile alle
    app in /data). Stringhe che non sono id di file restano com'erano."""
    from ..models import FileAsset

    cache: dict[str, str | None] = {}

    async def path_for(fid: str) -> str | None:
        if fid not in cache:
            asset = await ctx.db.get(FileAsset, fid)
            cache[fid] = asset.path if asset and asset.family_id == ctx.user.family_id and asset.path else None
        return cache[fid]

    async def walk(v):
        if isinstance(v, str):
            m = _HEX_ID.match(v.strip())
            if m:
                p = await path_for(m.group(1))
                return p or v
            return v
        if isinstance(v, list):
            return [await walk(x) for x in v]
        if isinstance(v, dict):
            return {k: await walk(x) for k, x in v.items()}
        return v

    try:
        return await walk(args)
    except Exception as e:
        log.debug("risoluzione id file negli argomenti MCP fallita: %s", e)
        return args


async def _protect_save_path(server: str, tool: str, args: dict, ctx: ToolContext, open_path: str | None = None) -> dict:
    """Un save_* (o execute file.saveAs) verso un percorso gia' usato da un file di un'altra conversazione o di un altro
    utente viene dirottato su "nome 2.ext": il modello crede di scegliere il nome, ma non puo' calpestare file altrui."""
    from ..models import FileAsset
    from ..services.naming import unique_path
    from .artcraft import APPS
    app = APPS[server]
    cmd = str(args.get("command") or args.get("id") or "")
    where = None
    if tool in app["save_tools"] and args.get("path"):
        where = ("path",)
    elif tool in ("execute", "run_command", "run") and cmd.startswith("file.save"):
        prm = args.get("params") or args.get("args")
        if isinstance(prm, dict) and prm.get("path"):
            where = ("params" if "params" in args else "args", "path")
    if not where:
        return args
    target = args[where[0]] if len(where) == 1 else args[where[0]][where[1]]
    pth = Path(str(target))
    if not pth.exists():
        return args
    from sqlalchemy import select as _select
    owner_row = (await ctx.db.execute(_select(FileAsset).where(FileAsset.path == str(pth)))).scalars().first()
    if not owner_row:
        return args
    from ..services import workspace as _ws
    if await _ws.protected_template(ctx.db, owner_row):
        divert = True                                   # modello dell'organizzazione (carta intestata, listino…): mai sovrascritto dalla chat
    elif owner_row.owner_id != ctx.user.id:
        divert = True                                   # file di un altro utente: mai
    elif open_path and str(pth) == str(open_path):
        divert = False                                  # e' il documento aperto in questa chat: si sovrascrive (e' una modifica)
    else:
        divert = (owner_row.conversation_id or "") != (ctx.conversation_id or "")   # file di un'altra chat non aperto qui: copia
    if divert:
        new = str(unique_path(pth))
        log.info("MCP %s: %s salverebbe su %s (file di un'altra chat): dirottato su %s", server, tool, pth, new)
        if len(where) == 1:
            return {**args, "path": new}
        return {**args, where[0]: {**args[where[0]], "path": new}}
    return args


def _per_user_service(name: str):
    if name == "mailcraft":
        from ..services import mailcraft
        return mailcraft.svc
    return None


async def _snapshot_path(path: str, ctx: ToolContext, note: str) -> None:
    """Se il percorso e' un file registrato, ne salva la versione attuale nella cronologia prima che venga sovrascritto."""
    try:
        from sqlalchemy import select as _select

        from ..models import FileAsset
        from ..services import versions
        if not path or not Path(path).is_file():
            return
        asset = (await ctx.db.execute(_select(FileAsset).where(FileAsset.path == str(path)))).scalars().first()
        if asset:
            await versions.snapshot(ctx.db, asset, ctx.user, note)
            await ctx.db.commit()
    except Exception as e:
        log.debug("snapshot versione di %s saltato: %s", path, e)


async def _snapshot_before_save(server: str, tool: str, args: dict, ctx: ToolContext) -> None:
    from .artcraft import APPS
    app = APPS[server]
    cmd = str(args.get("command") or args.get("id") or "")
    target = ""
    if tool in app["save_tools"]:
        target = str(args.get("path") or "")
    elif tool in ("execute", "run_command", "run") and cmd.startswith("file.save"):
        prm = args.get("params") or args.get("args")
        target = str(prm.get("path") or "") if isinstance(prm, dict) else ""
    if target:
        await _snapshot_path(target, ctx, f"assistente ({tool})")


async def _mail_args(tool: str, args: dict, ctx: ToolContext):
    """MailCraft: gli allegati di send_mail possono essere indicati con l'id di un file della chat (o /files/<id>): li
    traduciamo nel percorso su disco, che il container MailCraft vede in /data. Il percorso di save_attachment deve stare
    in data/mailcraft (l'unica cartella scrivibile per la posta)."""
    from ..models import FileAsset
    if tool == "send_mail" and isinstance(args.get("attachments"), list):
        out = []
        for a in args["attachments"]:
            ref = a.get("path") if isinstance(a, dict) else a
            m = _HEX_ID.match(str(ref or "").strip())
            if m:
                asset = await ctx.db.get(FileAsset, m.group(1))
                if not asset or asset.family_id != ctx.user.family_id or not asset.path:
                    return ToolResult(f"Allegato {ref} non trovato tra i file della chat.", is_error=True)
                out.append({"path": asset.path})
            else:
                out.append(a if isinstance(a, dict) else {"path": str(a)})
        args = {**args, "attachments": out}
    if tool == "save_attachment":
        base = settings.data_dir / "mailcraft"
        pth = str(args.get("path") or "")
        if not pth or not pth.startswith(str(base)):
            name = Path(pth).name if pth and not pth.endswith("/") else ""
            args = {**args, "path": str(base / name) if name else str(base)}
    return args


def _harvest_saved(parts: list[str], fallback: str) -> str:
    """Percorso scritto da save_attachment: dal risultato se c'e' (quando path era una cartella), altrimenti l'argomento."""
    m = re.search(r"(/data/mailcraft/[^\s\"']+)", "\n".join(parts))
    return m.group(1) if m else fallback


def _describe(e: BaseException) -> str:
    """Errore leggibile: un ExceptionGroup (anyio/mcp) viene aperto fino alle eccezioni vere."""
    group = getattr(e, "exceptions", None)
    if group:
        return "; ".join(_describe(x) for x in group[:3])
    return f"{type(e).__name__}: {str(e)[:300]}"


class MCPManager:
    def __init__(self):
        self._open: dict[tuple[str, str], str] = {}   # (server, conversazione) -> file aperto con i tool MCP
        self.conns: dict[str, Connection] = {}
        self.user_conns: dict[tuple[str, str], Connection] = {}   # (server, utente) -> sessione per utente (MailCraft)

    # ---------------- ciclo di vita ----------------
    async def start(self) -> None:
        """All'avvio: importa il JSON se il DB e' vuoto, poi connette i server abilitati."""
        from sqlalchemy import select

        from ..db import SessionLocal
        from ..services import settings_store

        async with SessionLocal() as db:
            rows = (await db.execute(select(McpServer))).scalars().all()
            if not rows and settings.mcp_config_path.exists():
                try:
                    cfg = json.loads(settings.mcp_config_path.read_text())
                    for name, sc in cfg.get("servers", {}).items():
                        db.add(McpServer(name=name, type=sc.get("type", "stdio"), command=sc.get("command", ""),
                                         args=sc.get("args", []), url=sc.get("url", ""),
                                         headers_enc=settings_store.encrypt(json.dumps(sc.get("headers") or {})),
                                         env_enc=settings_store.encrypt(json.dumps(sc.get("env") or {})),
                                         min_role=sc.get("min_role", "adult"), enabled=bool(sc.get("enabled", False)),
                                         description="importato da mcp_servers.json"))
                    await db.commit()
                    rows = (await db.execute(select(McpServer))).scalars().all()
                    log.info("MCP: importati %d server da mcp_servers.json", len(rows))
                except Exception as e:
                    log.error("MCP: import di mcp_servers.json fallito: %s", e)
            for row in rows:
                if row.enabled:
                    asyncio.create_task(self.connect(self.config_from_row(row)))
            # app ArtCraft (extras/artcraft): MCP stdio esposto via socat sulla rete Docker
            from .artcraft import APPS, HTTP_APPS, host_for, register_tools as _ac_reg
            for app in APPS:
                if app in HTTP_APPS:
                    if host_for(app):
                        _ac_reg(app)   # CLI via HTTP (IdeaCraft): nessuna sessione MCP, il tool c'e' se l'host e' configurato
                    continue
                if host_for(app) and not any(r.name == app for r in rows):
                    asyncio.create_task(self.connect(ServerConfig(
                        name=app, type="stdio", command="socat", args=["-", f"TCP:{host_for(app)}"],
                        min_role="adult", enabled=True)))
            # MailCraft: una sessione MCP per utente. La connessione principale (token di un utente di servizio) serve solo
            # a elencare i tool; ogni chiamata vera passa dalla sessione dell'utente (user_connection).
            for name, mcp_host in (("mailcraft", settings.mailcraft_mcp_host),):
                svc = _per_user_service(name)
                if svc and svc.enabled() and mcp_host and not any(r.name == name for r in rows):
                    asyncio.create_task(self._connect_per_user(name, mcp_host))

    async def _connect_per_user(self, name: str, mcp_host: str) -> None:
        svc = _per_user_service(name)
        # il token va NEL flusso TCP (prima riga), seguito dal dialogo MCP che arriva dal nostro stdin: printf da solo lo
        # avrebbe scritto sullo stdout verso il backend, non verso il container
        cmd = '{ printf "%s\\n" "$USER_TOKEN"; exec cat; } | exec socat - TCP:' + mcp_host
        cfg = ServerConfig(name=name, type="stdio", command="sh", args=["-c", cmd], min_role="adult", enabled=True, per_user=True)
        for attempt in range(6):   # il container puo' partire dopo il backend
            try:
                cfg.env = {"USER_TOKEN": await svc.user_token("familai-system")}
                break
            except Exception as e:
                cfg.env = {}
                log.info("%s non ancora raggiungibile (%s), riprovo…", svc.name, e)
                await asyncio.sleep(10 * (attempt + 1))
        if not cfg.env:
            self.conns[name] = Connection(cfg=cfg, error=f"server {svc.name} non raggiungibile (token di servizio?)")
            return
        await self.connect(cfg)

    async def stop(self) -> None:
        for name in list(self.conns):
            await self.disconnect(name)

    @staticmethod
    def config_from_row(row: McpServer) -> ServerConfig:
        from ..services import settings_store

        def dec(v: str) -> dict:
            try:
                return json.loads(settings_store.decrypt(v)) if v else {}
            except Exception:
                return {}

        return ServerConfig(name=row.name, type=row.type, command=row.command, args=list(row.args or []), url=row.url,
                            headers=dec(row.headers_enc), env=dec(row.env_enc), min_role=row.min_role, enabled=row.enabled)

    # ---------------- connessioni per utente (MailCraft) ----------------
    USER_IDLE_S = 30 * 60

    async def user_connection(self, server: str, user_id: str) -> Connection:
        """Sessione MCP dell'utente verso un server per_user: creata al primo uso con il token dell'utente (MailCraft lo conia
        con il token di servizio), riusata finche' resta attiva, chiusa dopo 30 minuti di inattivita'."""
        key = (server, user_id)
        conn = self.user_conns.get(key)
        if conn and conn.connected and conn.session:
            svc = _per_user_service(server)
            same = True
            if svc and getattr(svc, "fresh_identity", False):
                # NetCraft: il ruolo (admin/operator) viaggia nella prima riga; se nel frattempo e' cambiato (o l'app e' stata tolta)
                # la sessione si chiude e, se l'utente puo' ancora, se ne apre una nuova con l'identita' di adesso
                try:
                    same = conn.cfg.env.get("USER_TOKEN") == await svc.user_token(user_id)
                except Exception:
                    same = False
            if same:
                conn.last_used = time.time()
                return conn
        if conn:
            await self._close(conn)
            self.user_conns.pop(key, None)
        # pulizia di quelle inattive
        now = time.time()
        for k, c in list(self.user_conns.items()):
            if now - c.last_used > self.USER_IDLE_S:
                await self._close(c)
                self.user_conns.pop(k, None)
        base = self.conns[server].cfg
        env = dict(base.env or {})
        svc = _per_user_service(server)
        if svc:
            env["USER_TOKEN"] = await svc.user_token(user_id)
        cfg = ServerConfig(name=base.name, type=base.type, command=base.command, args=list(base.args), url=base.url,
                           headers=dict(base.headers), env=env, min_role=base.min_role, enabled=True, per_user=True)
        conn = Connection(cfg=cfg, user_id=user_id)
        self.user_conns[key] = conn
        conn.task = asyncio.create_task(self._run(conn), name=f"mcp-{server}-{user_id[:8]}")
        try:
            await asyncio.wait_for(conn.ready.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            conn.error = f"timeout di connessione ({CONNECT_TIMEOUT}s)"
            await self._close(conn)
        return conn

    async def drop_user_connections(self, user_id: str) -> None:
        for k, c in list(self.user_conns.items()):
            if k[1] == user_id:
                await self._close(c)
                self.user_conns.pop(k, None)

    async def _close(self, conn: Connection) -> None:
        conn.stop.set()
        if conn.task:
            try:
                await asyncio.wait_for(conn.task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                conn.task.cancel()
            except Exception as e:
                log.debug("MCP %s: chiusura con errore %s", conn.cfg.name, e)
        conn.connected = False
        conn.session = None

    # ---------------- connessione / disconnessione a caldo ----------------
    async def connect(self, cfg: ServerConfig) -> Connection:
        if cfg.name in self.conns:
            await self.disconnect(cfg.name)
        conn = Connection(cfg=cfg)
        self.conns[cfg.name] = conn
        conn.task = asyncio.create_task(self._run(conn), name=f"mcp-{cfg.name}")
        try:
            await asyncio.wait_for(conn.ready.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            conn.error = f"timeout di connessione ({CONNECT_TIMEOUT}s)"
            await self.disconnect(cfg.name, keep_entry=True)
        return conn

    async def disconnect(self, name: str, keep_entry: bool = False) -> None:
        conn = self.conns.get(name)
        if not conn:
            return
        conn.stop.set()
        if conn.task:
            try:
                await asyncio.wait_for(conn.task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                conn.task.cancel()
            except Exception as e:
                log.debug("MCP %s: chiusura con errore %s", name, e)
        unregister_prefix(f"mcp__{name}__")
        conn.connected = False
        conn.session = None
        if not keep_entry:
            self.conns.pop(name, None)

    async def _run(self, conn: Connection) -> None:
        """Task dedicato: apre la sessione, registra i tool, attende lo stop, chiude tutto nello stesso task.
        Una connessione per utente (conn.user_id) non tocca il registro dei tool: li ha gia' registrati quella principale."""
        cfg = conn.cfg
        main = not conn.user_id
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            if cfg.type == "http":
                from mcp.client.streamable_http import streamablehttp_client

                transport = streamablehttp_client(cfg.url, headers=cfg.headers or None)
            else:
                import os

                env = {**os.environ, **(cfg.env or {})} if cfg.env else None
                transport = stdio_client(StdioServerParameters(command=cfg.command, args=cfg.args, env=env))

            async with transport as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if main:
                        listed = await session.list_tools()
                        unregister_prefix(f"mcp__{cfg.name}__")
                        conn.tools = []
                        for t in listed.tools:
                            full = f"mcp__{cfg.name}__{t.name}"
                            conn.tools.append({"name": t.name, "description": t.description or ""})
                            register_tool(Tool(
                                spec=ToolSpec(full, f"[MCP {cfg.name}] {t.description or t.name}",
                                              _attr(t, "inputSchema", "input_schema") or {"type": "object", "properties": {}}),
                                fn=self._make_caller(cfg.name, t.name),
                            ))
                    conn.session = session
                    conn.connected = True
                    conn.error = ""
                    conn.last_used = time.time()
                    from .artcraft import APPS as _ARTCRAFT, register_tools as _ac_register
                    if main and cfg.name in _ARTCRAFT:
                        _ac_register(cfg.name)
                    conn.ready.set()
                    log.info("MCP %s%s: connesso%s", cfg.name, f" (utente {conn.user_id[:8]})" if conn.user_id else "",
                             f", {len(conn.tools)} tool" if main else "")
                    await conn.stop.wait()
        except Exception as e:
            conn.error = _describe(e)
            log.error("MCP %s: %s", cfg.name, conn.error, exc_info=True)
        finally:
            conn.connected = False
            conn.session = None
            conn.ready.set()
            if main:
                unregister_prefix(f"mcp__{cfg.name}__")
                from .artcraft import APPS as _ARTCRAFT, unregister_tools as _ac_unregister
                if cfg.name in _ARTCRAFT:
                    _ac_unregister(cfg.name)

    def _make_caller(self, server: str, tool: str):
        async def call(args: dict, ctx: ToolContext) -> ToolResult:
            conn = self.conns.get(server)
            if not conn or not conn.connected or not conn.session:
                return ToolResult(f"Server MCP {server} non connesso", is_error=True)
            if ROLE_RANK.get(ctx.user.role, 0) < MIN_ROLE_RANK.get(conn.cfg.min_role, 1):
                return ToolResult("Non hai il permesso di usare questo strumento", is_error=True)
            if conn.cfg.per_user:   # MailCraft: la sessione e' quella dell'utente, con il suo token (vede solo le sue caselle)
                try:
                    conn = await self.user_connection(server, ctx.user.id)
                except Exception as e:
                    return ToolResult(f"{server}: sessione utente non disponibile: {type(e).__name__}: {str(e)[:200]}", is_error=True)
                if not conn.connected or not conn.session:
                    return ToolResult(f"{server}: sessione utente non connessa ({conn.error or 'errore sconosciuto'})", is_error=True)
            from .artcraft import APPS as _ARTCRAFT_APPS
            if server in _ARTCRAFT_APPS or server == "mailcraft":
                args = await _resolve_file_ids(args, ctx)   # id di un file della chat al posto di un percorso -> percorso in /data
            if server == "mailcraft":
                args = await _mail_args(tool, args, ctx)
                if isinstance(args, ToolResult):
                    return args
            if server in _ARTCRAFT_APPS:
                args = await _protect_save_path(server, tool, args, ctx, self._open.get((server, ctx.conversation_id or "")))   # mai sovrascrivere file altrui
                await _snapshot_before_save(server, tool, args, ctx)   # cronologia versioni: il contenuto precedente va in data/versions
            try:
                res = await asyncio.wait_for(conn.session.call_tool(tool, arguments=args), timeout=180)
            except Exception as e:
                return ToolResult(f"Errore MCP: {type(e).__name__}: {e}", is_error=True)
            parts, artifacts = await self._collect(res, ctx)
            if server == "mailcraft" and tool == "save_attachment" and not _attr(res, "isError", "is_error", default=False):
                from .artcraft import register_saved_file
                art = await register_saved_file(ctx, _harvest_saved(parts, str(args.get("path") or "")))
                if art:
                    artifacts.append(art)
                    parts.append(f"Allegato {art.get('filename', '')} salvato e mostrato all'utente in chat (id {art['file_id']}).")
            from .artcraft import APPS as _ARTCRAFT, harvest_paths, register_saved_file, sidecar_files
            if server in _ARTCRAFT and not _attr(res, "isError", "is_error", default=False):
                app = _ARTCRAFT[server]
                key = (server, ctx.conversation_id or "")
                paths: list[str] = []
                cmd = str(args.get("command") or args.get("id") or "")
                if tool in app["save_tools"]:
                    paths = [str(args.get("path") or self._open.get(key) or "")]
                elif tool in ("execute", "run_command", "run") and cmd.startswith("file.save"):
                    # Salva/Salva con nome passati come comando generico: il percorso sta nei parametri
                    prm = args.get("params") or args.get("args") or {}
                    paths = [str((prm.get("path") if isinstance(prm, dict) else "") or self._open.get(key) or "")]
                elif not app["save_tools"]:   # PdfCraft: i percorsi scritti stanno nel risultato
                    paths = harvest_paths("\n".join(parts))
                if tool.startswith("open_") and args.get("path"):
                    self._open[key] = str(args["path"])
                elif tool.startswith("new_"):
                    self._open.pop(key, None)
                for pth in paths:
                    if pth:
                        self._open[key] = pth
                    for extra in [pth] + sidecar_files(pth):
                        art = await register_saved_file(ctx, extra)
                        if art:
                            artifacts.append(art)
                            parts.append(f"File {art.get('filename', '')} salvato e mostrato all'utente come allegato scaricabile (id {art['file_id']}).")
                # Salvataggio automatico: ogni modifica fatta con i tool MCP finisce subito sul file aperto, cosi' l'utente
                # trova il documento aggiornato (il modello spesso mostra l'anteprima e dimentica save_*).
                if not paths and app["save_tools"] and self._open.get(key) and _mutating(tool):
                    try:
                        await _snapshot_path(self._open[key], ctx, "assistente")
                        sv = await asyncio.wait_for(conn.session.call_tool(app["save_tools"][0], arguments={"path": self._open[key]}), timeout=60)
                        if not _attr(sv, "isError", "is_error", default=False):
                            art = await register_saved_file(ctx, self._open[key], only_new=True)
                            if art:
                                artifacts.append(art)
                            parts.append(f"(file salvato automaticamente: {Path(self._open[key]).name})")
                    except Exception as e:  # il salvataggio automatico non deve far fallire la modifica
                        log.warning("MCP %s: salvataggio automatico di %s fallito: %s", server, self._open.get(key), e)
            return ToolResult("\n".join(parts) or "(vuoto)", is_error=bool(_attr(res, "isError", "is_error", default=False)), artifacts=artifacts or None)
        return call

    async def _collect(self, res, ctx: ToolContext) -> tuple[list[str], list[dict]]:
        """Testo per il modello + allegati per la chat: le immagini restituite da un tool MCP (es. render_page di
        WordCraft) vengono salvate come file generati e mostrate all'utente."""
        parts: list[str] = []
        artifacts: list[dict] = []
        for c in res.content:
            ctype = getattr(c, "type", "")
            if ctype == "text":
                parts.append(c.text)
            elif ctype == "image" and getattr(c, "data", None):
                try:
                    import base64

                    from .media import _save_generated
                    mime = _attr(c, "mimeType", "mime_type", default="") or "image/png"
                    ext = "jpg" if "jpeg" in mime else mime.split("/")[-1] or "png"
                    asset = await _save_generated(ctx, base64.b64decode(c.data), ext, mime, "anteprima")
                    artifacts.append({"type": "image", "file_id": asset.id, "url": f"/files/{asset.id}", "mime": mime})
                    parts.append(f"[immagine mostrata all'utente (id {asset.id})]")
                except Exception as e:
                    parts.append(f"[immagine non salvata: {e}]")
            else:
                parts.append(f"[{ctype or 'contenuto'} non testuale]")
        return parts, artifacts

    async def call(self, server: str, tool: str, args: dict, timeout: int = 180):
        """Chiamata diretta a un tool MCP dal codice (usata dai tool nativi che orchestrano WordCraft)."""
        conn = self.conns.get(server)
        if not conn or not conn.connected or not conn.session:
            raise RuntimeError(f"server MCP {server} non connesso")
        res = await asyncio.wait_for(conn.session.call_tool(tool, arguments=args), timeout=timeout)
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if _attr(res, "isError", "is_error", default=False):
            raise RuntimeError(text[:500] or f"{tool} fallito")
        return text

    async def call_raw(self, server: str, tool: str, args: dict, timeout: int = 180):
        """Come call() ma restituisce il risultato MCP grezzo (per contenuti non testuali, es. immagini)."""
        conn = self.conns.get(server)
        if not conn or not conn.connected or not conn.session:
            raise RuntimeError(f"server MCP {server} non connesso")
        res = await asyncio.wait_for(conn.session.call_tool(tool, arguments=args), timeout=timeout)
        if _attr(res, "isError", "is_error", default=False):
            raise RuntimeError("\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")[:500] or f"{tool} fallito")
        return res

    def is_connected(self, server: str) -> bool:
        c = self.conns.get(server)
        return bool(c and c.connected)

    # ---------------- stato / permessi ----------------
    def allowed_for(self, role: Role, tool_name: str) -> bool:
        if not tool_name.startswith("mcp__"):
            return True
        server = tool_name.split("__")[1]
        conn = self.conns.get(server)
        if not conn:
            return False
        return ROLE_RANK.get(role, 0) >= MIN_ROLE_RANK.get(conn.cfg.min_role, 1)

    @property
    def status(self) -> dict[str, dict]:
        return {name: {"enabled": c.cfg.enabled, "connected": c.connected, "error": c.error,
                       "tools": [t["name"] for t in c.tools], "min_role": c.cfg.min_role}
                for name, c in self.conns.items()}


mcp_manager = MCPManager()
