"""Stima costi e budget per provider.

- Stima locale: usage_log (token in/out, immagini, video) x listino prezzi (modificabile dal pannello).
- Spesa reale (opzionale): OpenAI con chiave Admin dell'organizzazione.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import UsageLog, User

log = logging.getLogger(__name__)
settings = get_settings()

# Prezzi in USD: per milione di token (input/output), per immagine, per secondo di video.
# Le chiavi sono prefissi di nome modello; vince il prefisso piu' lungo che combacia. "*" = default del provider.
DEFAULT_PRICING: dict = {'mock': {'*': {'in': 0.0, 'out': 0.0}},
 'ollama': {'*': {'in': 0, 'out': 0}},
 'custom': {'*': {'in': 0, 'out': 0}},   # server compatibile OpenAI: listino da impostare nel pannello se a pagamento
 'anthropic': {'*': {'in': 3.0, 'out': 15.0}, 'claude-opus': {'in': 15.0, 'out': 75.0},
               'claude-sonnet': {'in': 3.0, 'out': 15.0}, 'claude-haiku': {'in': 1.0, 'out': 5.0}},
 'gemini': {'*': {'in': 0.3, 'out': 2.5}, 'gemini-2.5-pro': {'in': 1.25, 'out': 10.0}, 'gemini-2.5-flash': {'in': 0.3, 'out': 2.5},
            'gemini-2.5-flash-lite': {'in': 0.1, 'out': 0.4}},
 'openai': {'gpt-6-luna': {'in': 0.1, 'out': 0.5}, 'gpt-6.1-sol': {'in': 2.0, 'out': 10.0}, '*': {'in': 2.0, 'out': 8.0},
            'embedding': 0.02,
            'gpt-4.1': {'in': 2.0, 'out': 8.0},
            'gpt-4.1-mini': {'in': 0.4, 'out': 1.6},
            'gpt-4.1-nano': {'in': 0.1, 'out': 0.4},
            'gpt-4o': {'in': 2.5, 'out': 10.0},
            'gpt-4o-mini': {'in': 0.15, 'out': 0.6},
            'gpt-5': {'in': 1.25, 'out': 10.0},
            'gpt-5-mini': {'in': 0.25, 'out': 2.0},
            'image': 0.04,
            'o3': {'in': 2.0, 'out': 8.0},
            'o4-mini': {'in': 1.1, 'out': 4.4}}}


def pricing_table() -> dict:
    """Listino effettivo: default sovrascritto dal JSON salvato nel pannello (impostazione pricing_json)."""
    table = json.loads(json.dumps(DEFAULT_PRICING))
    raw = getattr(settings, "pricing_json", "") or ""
    if raw.strip():
        try:
            custom = json.loads(raw)
            for prov, entries in custom.items():
                table.setdefault(prov, {}).update(entries)
        except Exception as e:
            log.warning("pricing_json non valido: %s", e)
    return table


def _model_price(table: dict, provider: str, model: str) -> dict:
    prov = table.get(provider, {})
    best, best_len = prov.get("*", {"in": 0, "out": 0}), -1
    for prefix, price in prov.items():
        if isinstance(price, dict) and prefix != "*" and model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


def cache_factors(provider: str, model: str) -> tuple[float, float]:
    """(moltiplicatore lettura cache, moltiplicatore scrittura cache) rispetto al prezzo di input."""
    if provider == "openai":
        if model.startswith("gpt-6.1-sol"):
            return 0.05, 1.25
        if model.startswith(("gpt-6", "gpt-5.6")):
            return 0.1, 1.25
        return (0.25 if (model or "").startswith(("gpt-4.1", "gpt-5", "o3", "o4")) else 0.50), 1.0
    if provider == "anthropic":
        return 0.1, 1.25
    if provider == "gemini":
        return 0.25, 1.0
    return 1.0, 1.0


def estimate_cost(table: dict, provider: str, model: str, kind: str, input_tokens: int, output_tokens: int, count: int = 1,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    prov = table.get(provider, {})
    if kind == "message":
        p = _model_price(table, provider, model or "")
        rf, wf = cache_factors(provider, model or "")
        pin = float(p.get("in", 0))
        return (input_tokens * pin + cache_read_tokens * pin * rf + cache_write_tokens * pin * wf
                + output_tokens * float(p.get("out", 0))) / 1_000_000
    if kind == "image" and provider == "openai":
        if input_tokens or output_tokens:
            pi = table.get("openai", {})
            return (input_tokens * float(pi.get("image_in", 5.0)) + output_tokens * float(pi.get("image_out", 30.0))) / 1_000_000
        return count * float(table.get("openai", {}).get("image", 0.04))
    return 0.0


async def local_report(db: AsyncSession, family_id: str, since: datetime) -> dict:
    table = pricing_table()
    rows = (await db.execute(
        select(UsageLog.user_id, UsageLog.provider, UsageLog.model, UsageLog.kind, func.count(),
               func.sum(UsageLog.input_tokens), func.sum(UsageLog.output_tokens),
               func.sum(UsageLog.cache_read_tokens), func.sum(UsageLog.cache_write_tokens))
        .where(UsageLog.family_id == family_id, UsageLog.created_at >= since)
        .group_by(UsageLog.user_id, UsageLog.provider, UsageLog.model, UsageLog.kind))).all()
    users = {u.id: u.display_name for u in (await db.execute(select(User).where(User.family_id == family_id))).scalars()}
    by_provider: dict[str, dict] = {}
    by_user: dict[str, dict] = {}
    for uid, prov, model, kind, n, tin, tout, tcr, tcw in rows:
        prov = prov or "?"
        tin, tout, tcr, tcw = int(tin or 0), int(tout or 0), int(tcr or 0), int(tcw or 0)
        cost = estimate_cost(table, prov, model or "", kind, tin, tout, int(n), tcr, tcw)
        p = by_provider.setdefault(prov, {"cost": 0.0, "messages": 0, "input_tokens": 0, "output_tokens": 0, "images": 0, "videos": 0, "searches": 0,
                                          "cache_read_tokens": 0, "cache_write_tokens": 0, "cache_saved": 0.0})
        u = by_user.setdefault(uid, {"name": users.get(uid, uid), "cost": 0.0, "messages": 0, "images": 0, "videos": 0, "tokens": 0})
        p["cost"] += cost
        u["cost"] += cost
        if kind == "message":
            p["messages"] += n; u["messages"] += n
            p["input_tokens"] += tin + tcr + tcw; p["output_tokens"] += tout; u["tokens"] += tin + tcr + tcw + tout
            p["cache_read_tokens"] += tcr; p["cache_write_tokens"] += tcw
            if kind == "message" and tcr:
                # risparmio = quanto sarebbero costati i token letti dalla cache a prezzo pieno, meno quanto sono costati
                pin = float(_model_price(table, prov, model or "").get("in", 0))
                p["cache_saved"] += tcr * pin * (1 - cache_factors(prov, model or "")[0]) / 1_000_000
        elif kind == "image":
            p["images"] += n; u["images"] += n
            p["image_tokens"] = p.get("image_tokens", 0) + tin + tout
        elif kind == "video":
            p["videos"] += n; u["videos"] += n
        elif kind == "web_search":
            p["searches"] += n
    return {"providers": by_provider, "users": sorted(by_user.values(), key=lambda x: -x["cost"])}


# ------------------------------------------------------------------ spesa reale (chiavi Admin)


async def openai_spend(admin_key: str, since: datetime) -> dict:
    """Costi dell'organizzazione (richiede Admin API key)."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get("https://api.openai.com/v1/organization/costs",
                            params={"start_time": int(since.timestamp()), "bucket_width": "1d", "limit": 31},
                            headers={"Authorization": f"Bearer {admin_key}"})
            r.raise_for_status()
            data = r.json()
        total = 0.0
        for bucket in data.get("data", []):
            for res in bucket.get("results", []):
                amt = res.get("amount") or {}
                total += float(amt.get("value", 0) or 0)
        return {"ok": True, "usd": round(total, 4)}
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            return {"ok": False, "error": "la chiave non e' abilitata ai report di costo: serve una Admin key "
                                          "(platform.openai.com → Settings → Organization → Admin keys)"}
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:160]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


from ..providers.registry import PROVIDER_LABELS, PROVIDER_ORDER, local_ids  # noqa: E402

CLOUD_BILLED = ("openai", "anthropic", "gemini")


def _local_providers() -> set[str]:
    return {"ollama"} | local_ids()


def period_bounds(period: str) -> tuple[datetime, datetime | None, str]:
    """Periodo della tabella per utente: mese corrente, mese scorso, ultimi 7 o 30 giorni."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "prev":
        prev = (start - timedelta(days=1)).replace(day=1)
        return prev, start, "mese scorso"
    if period == "7d":
        return now - timedelta(days=7), None, "ultimi 7 giorni"
    if period == "30d":
        return now - timedelta(days=30), None, "ultimi 30 giorni"
    return start, None, "mese corrente"


async def users_report(db: AsyncSession, family_id: str, period: str = "month") -> dict:
    """Token per utente, provider per provider (cloud e locali) e modello per modello, con la stima del costo."""
    since, until, label = period_bounds(period)
    table = pricing_table()
    q = (select(UsageLog.user_id, UsageLog.provider, UsageLog.model, UsageLog.kind, func.count(),
                func.sum(UsageLog.input_tokens), func.sum(UsageLog.output_tokens),
                func.sum(UsageLog.cache_read_tokens), func.sum(UsageLog.cache_write_tokens))
         .where(UsageLog.family_id == family_id, UsageLog.created_at >= since))
    if until is not None:
        q = q.where(UsageLog.created_at < until)
    rows = (await db.execute(q.group_by(UsageLog.user_id, UsageLog.provider, UsageLog.model, UsageLog.kind))).all()
    people = {u.id: u for u in (await db.execute(select(User).where(User.family_id == family_id))).scalars()}
    users: dict[str, dict] = {}
    seen_providers: set[str] = set()
    local_set = _local_providers()
    for uid, prov, model, kind, n, tin, tout, tcr, tcw in rows:
        prov, model = prov or "?", model or ""
        tin, tout, tcr, tcw, n = int(tin or 0), int(tout or 0), int(tcr or 0), int(tcw or 0), int(n or 0)
        cost = estimate_cost(table, prov, model, kind, tin, tout, n, tcr, tcw)
        p = people.get(uid)
        u = users.setdefault(uid, {"id": uid, "name": p.display_name if p else uid, "avatar": p.avatar if p else "",
                                   "cost": 0.0, "tokens": 0, "messages": 0, "local_tokens": 0, "cloud_tokens": 0, "providers": {}})
        u["cost"] += cost
        pp = u["providers"].setdefault(prov, {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "messages": 0,
                                              "images": 0, "cost": 0.0, "models": {}})
        pp["cost"] += cost
        if kind == "image":
            pp["images"] += n
            continue
        if kind == "video":
            continue
        tok_in = tin + tcr + tcw
        seen_providers.add(prov)
        pp["input_tokens"] += tok_in; pp["output_tokens"] += tout; pp["cache_read_tokens"] += tcr
        mm = pp["models"].setdefault(model or "?", {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "messages": 0, "cost": 0.0})
        mm["input_tokens"] += tok_in; mm["output_tokens"] += tout; mm["cache_read_tokens"] += tcr; mm["cost"] += cost
        if kind == "message":
            pp["messages"] += n; mm["messages"] += n; u["messages"] += n
        u["tokens"] += tok_in + tout
        u["local_tokens" if prov in local_set else "cloud_tokens"] += tok_in + tout
    order = [p for p in PROVIDER_ORDER if p in seen_providers] + sorted(seen_providers - set(PROVIDER_ORDER))
    labels = PROVIDER_LABELS
    for u in users.values():
        u["cost"] = round(u["cost"], 4)
        for pp in u["providers"].values():
            pp["cost"] = round(pp["cost"], 4)
            pp["models"] = sorted(({"model": k, **v, "cost": round(v["cost"], 4)} for k, v in pp["models"].items()), key=lambda m: -(m["input_tokens"] + m["output_tokens"]))
    return {"period": period, "label": label, "since": since.isoformat(), "until": until.isoformat() if until else None,
            "providers": [{"id": p, "label": labels.get(p, p), "local": p in local_set} for p in order],
            "users": sorted(users.values(), key=lambda x: (-x["tokens"], x["name"]))}


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def full_report(db: AsyncSession, family_id: str) -> dict:
    since = month_start()
    local = await local_report(db, family_id, since)
    budgets = {}
    for prov in CLOUD_BILLED:
        try:
            budgets[prov] = float(getattr(settings, f"budget_{prov}", "") or 0)
        except ValueError:
            budgets[prov] = 0.0
    real = {}
    # usa la chiave Admin se presente, altrimenti tenta con la chiave normale (funziona solo se ha privilegi admin)
    o_key = settings.openai_admin_key or settings.openai_api_key
    if o_key:
        real["openai"] = await openai_spend(o_key, since)
        real["openai"]["via"] = "admin_key" if settings.openai_admin_key else "api_key"
    out = []
    for prov in CLOUD_BILLED:
        if prov != "openai" and not getattr(settings, f"{prov}_api_key", "") and not local["providers"].get(prov):
            continue   # provider mai configurato: nessuna scheda
        est = local["providers"].get(prov, {}).get("cost", 0.0)
        spent = real.get(prov, {}).get("usd") if real.get(prov, {}).get("ok") else None
        used = spent if spent is not None else est
        b = budgets[prov]
        out.append({"provider": prov, "configured": bool(getattr(settings, f"{prov}_api_key", "")),
                    "estimated_usd": round(est, 4), "real": real.get(prov), "budget_usd": b,
                    "remaining_usd": round(b - used, 4) if b > 0 else None,
                    "percent": round(min(100.0, used / b * 100), 1) if b > 0 else None,
                    "stats": local["providers"].get(prov, {})})
    # modello locale: nessun costo, ma mostriamo quanto lavoro ha assorbito e cosa sarebbe costato nel cloud
    lo_parts = [local["providers"].get(k, {}) for k in sorted(_local_providers())]
    lo = {"messages": sum(p.get("messages", 0) for p in lo_parts), "input_tokens": sum(p.get("input_tokens", 0) for p in lo_parts),
          "output_tokens": sum(p.get("output_tokens", 0) for p in lo_parts)} if any(lo_parts) else {}
    local_tokens_in = lo.get("input_tokens", 0)
    local_tokens_out = lo.get("output_tokens", 0)
    cloud_msgs = sum(local["providers"].get(p, {}).get("messages", 0) for p in CLOUD_BILLED)
    ref_prov = "openai"
    ref_model = getattr(settings, f"{ref_prov}_default_model", "")
    avoided = estimate_cost(pricing_table(), ref_prov, ref_model, "message", local_tokens_in, local_tokens_out) if lo else 0.0
    local_info = {"configured": bool(settings.ollama_url), "model": settings.ollama_default_model if settings.ollama_url else "",
                  "messages": lo.get("messages", 0), "input_tokens": local_tokens_in, "output_tokens": local_tokens_out,
                  "cloud_messages": cloud_msgs, "avoided_usd": round(avoided, 4), "reference": f"{ref_prov} / {ref_model}"}
    return {"since": since.isoformat(), "providers": out, "users": local["users"], "pricing": pricing_table(), "local": local_info}
