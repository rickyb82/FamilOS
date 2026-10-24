"""Loop agentico: provider LLM + tool + policy famiglia, con streaming di eventi verso il client."""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Conversation, FileAsset, Folder, Message, Project, Role, User
from ..policy.permissions import (
    TOOL_ALIASES,
    check_daily_quota,
    check_time_window,
    effective_policy,
    log_usage,
    system_prompt_for,
    tool_allowed,
)
from ..policy.safety import CHILD_BLOCK_MESSAGE, check_text
from ..providers.registry import available_providers, get_provider
from ..security.sessions import user_has_passkey
from ..services import rag
from ..tools.documents import extract_text
from ..tools.mcp_client import mcp_manager
from ..tools.registry import ToolContext, all_tools, get_tool

log = logging.getLogger(__name__)
settings = get_settings()
# giri di tool per turno (MAX_TOOL_ROUNDS, default 500): una domanda che richiede centinaia di chiamate le fa tutte; al limite il
# modello viene fermato con un avviso visibile e "continua" lo fa riprendere
MAX_TOOL_ROUNDS = max(1, int(getattr(settings, "max_tool_rounds", 500) or 500))
TOOL_RESULT_CHARS = 50_000   # un risultato oltre questa lunghezza viene tagliato, con un avviso al modello
KEEP_RECENT_RESULTS = 2      # i risultati degli ultimi giri restano sempre interi
LOCAL_TOOLS = {"create_artifact", "update_artifact", "list_artifacts", "delete_artifact", "search_documents", "read_document", "ask_model", "remember", "forget"}
INLINE_DOC_CHARS = settings.rag_inline_max_chars


def _clip_result(text: str) -> str:
    """Un risultato troppo lungo per il contesto: tagliato, e il modello lo sa (mai una parte spacciata per il tutto)."""
    if len(text) <= TOOL_RESULT_CHARS:
        return text
    return (text[:TOOL_RESULT_CHARS] + f"\n\n[RISULTATO TAGLIATO: mostrati {TOOL_RESULT_CHARS:,} caratteri su {len(text):,}. "
            "I dati sopra NON sono completi: richiedi meno righe per volta (limit/offset o filtri) oppure usa uno strumento che conta o "
            "riassume nel database; non rispondere come se avessi visto tutto.]")


def _shrink_old_results(history: list[dict], start: int, budget: int) -> None:
    """Turni con centinaia di chiamate: oltre `budget` caratteri di risultati nel turno, i risultati piu' vecchi (non gli ultimi
    giri) diventano un promemoria, cosi' il contesto del modello non si riempie. Si sostituiscono gli elementi di `history` (copie):
    i messaggi salvati restano interi."""
    turn = [i for i in range(start, len(history)) if history[i].get("role") == "tool"]
    size = sum(len(str(b.get("content") or "")) for i in turn for b in history[i].get("content") or [])
    for i in turn[:-KEEP_RECENT_RESULTS] if len(turn) > KEEP_RECENT_RESULTS else []:
        if size <= budget:
            return
        blocks = []
        for b in history[i].get("content") or []:
            text = str(b.get("content") or "")
            if len(text) > 400:
                size -= len(text) - 400
                b = {**b, "content": text[:300] + f"\n[… risultato gia' letto in un passaggio precedente ({len(text):,} caratteri), "
                     "tolto per fare spazio: usa i conti e le note che hai gia' scritto, o richiama lo strumento se ti serve di nuovo.]"}
            blocks.append(b)
        history[i] = {**history[i], "content": blocks}


def sse(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, default=str)


async def _hydrate(blocks: list[dict], db: AsyncSession) -> list[dict]:
    """Carica i dati base64 delle immagini referenziate per file_id (solo per il provider)."""
    out = []
    for b in blocks:
        if b.get("type") == "image" and b.get("file_id") and not b.get("data"):
            asset = await db.get(FileAsset, b["file_id"])
            if asset and Path(asset.path).exists():
                out.append({"type": "image", "mime": asset.mime, "data": base64.b64encode(Path(asset.path).read_bytes()).decode()})
            continue
        if b.get("type") in ("image", "text", "tool_use", "tool_result", "openai_reasoning"):
            out.append(b)
    return out


def repair_history(hist: list[dict]) -> list[dict]:
    """Rende la cronologia coerente per tutti i provider: ogni tool_use deve avere il suo tool_result nel
    messaggio successivo (turni interrotti), e nessun tool_result deve restare senza tool_use."""
    out: list[dict] = []
    pending: list[dict] = []   # tool_use in attesa di risultato
    for m in hist:
        if m["role"] == "tool":
            ids = {tu["id"] for tu in pending}
            kept = [b for b in m["content"] if b.get("type") == "tool_result" and b.get("tool_use_id") in ids]
            answered = {b["tool_use_id"] for b in kept}
            for tu in pending:
                if tu["id"] not in answered:
                    kept.append({"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"],
                                 "content": "(esecuzione interrotta: nessun risultato)", "is_error": True})
            pending = []
            if kept:
                out.append({"role": "tool", "content": kept})
            continue
        if pending:  # l'assistente aveva chiamato tool ma non e' seguito un messaggio tool
            out.append({"role": "tool", "content": [
                {"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"],
                 "content": "(esecuzione interrotta: nessun risultato)", "is_error": True} for tu in pending]})
            pending = []
        if m["role"] == "assistant":
            pending = [b for b in m["content"] if b.get("type") == "tool_use"]
            if not m["content"]:
                continue
        out.append(m)
    if pending:  # cronologia che termina con tool_use senza risultato
        out.append({"role": "tool", "content": [
            {"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"],
             "content": "(esecuzione interrotta: nessun risultato)", "is_error": True} for tu in pending]})
    # il primo messaggio deve essere dell'utente
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


async def build_history(conv: Conversation, db: AsyncSession) -> list[dict]:
    msgs = (await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))).scalars().all()
    try:
        from .compaction import maybe_compact
        msgs = await maybe_compact(db, conv, msgs)
    except Exception as e:
        log.warning("Compattazione non riuscita: %s", e)
    hist = []
    for m in msgs:
        blocks = await _hydrate(m.content, db)
        if blocks:
            hist.append({"role": m.role, "content": blocks})
    return repair_history(hist)


async def build_system_prompt(user: User, conv: Conversation, db: AsyncSession) -> str:
    parts = [system_prompt_for(user)]
    from .apps import user_apps as _user_apps
    _apps_on = _user_apps(user)   # i software abilitati per questo utente: il prompt descrive solo quelli
    if conv.project_id:
        proj = await db.get(Project, conv.project_id)
        if proj:
            parts.append(f"\n# Progetto: {proj.name}\n{proj.description}\n\nIstruzioni del progetto:\n{proj.instructions}")
            files = (await db.execute(select(FileAsset).where(FileAsset.project_id == proj.id))).scalars().all()
            if files:
                parts.append("File del progetto (cerca con search_documents, oppure leggi per intero con read_document):\n" +
                             "\n".join(f"- {f.filename} (id: {f.id}, {f.chunk_count} parti indicizzate)" for f in files))
    # Documenti dell'organizzazione (cartelle di reparto che l'utente vede) e della cartella CHAT di questa conversazione
    if user.role != Role.child:
        from . import workspace as _ws
        org = [f for f in await _ws.visible_org_folders(db, user) if f.kind in _ws.ORG_KINDS] if "archivio" in _apps_on else []
        if org:
            from ..models import Family as _Fam
            fam = await db.get(_Fam, user.family_id)
            ids = await _ws.descendants(db, {f.id for f in org}, user.family_id)
            files = (await db.execute(select(FileAsset).where(FileAsset.folder_id.in_(list(ids))).order_by(FileAsset.filename))).scalars().all()
            by_folder: dict[str, list[FileAsset]] = {}
            for f in files:
                by_folder.setdefault(f.folder_id or "", []).append(f)
            names = {f.id: f for f in (await db.execute(select(Folder).where(Folder.id.in_(list(ids))))).scalars().all()}
            lines = []
            for fid_, fl in by_folder.items():
                fo = names.get(fid_)
                label = fo.name if fo else "?"
                if fo and fo.dept:
                    label = f"{_ws.DEPARTMENTS.get(fo.dept, fo.dept)}" + (f" / {fo.name}" if fo.name != _ws.DEPARTMENTS.get(fo.dept) else "")
                for f in fl[:40]:
                    lines.append(f"- [{label}] {f.filename} (id: {f.id}" + (f", {f.chunk_count} parti indicizzate" if f.chunk_count else "") + ")")
            parts.append(
                f"\n# Documenti condivisi di {(fam.name if fam else 'famiglia')}\n"
                "Sono i documenti messi a disposizione della famiglia dall'amministratore. Usali come riferimento, "
                "cerca con search_documents o leggi con read_document. Per creare una variante salva una nuova copia, "
                "senza sovrascrivere gli originali condivisi. Non inventare informazioni assenti dai documenti.\n"
                + ("\n".join(lines) if lines else "(spazio famiglia ancora vuoto)"))
        parts.append("\n# Cartella CHAT\nI file creati o caricati in questa chat finiscono in Documenti > CHAT > <titolo della chat>; "
                     "tutto cio' che l'utente mette in quella cartella (anche sottocartelle) e' contesto di questa conversazione e ti arriva "
                     "come estratti o lo trovi con search_documents.")
    parts.append("\nLe immagini sono generate da OpenAI quando il relativo strumento e' attivo; l'utente vede il risultato automaticamente. Non inventare link.")
    parts.append("\nI tool elencati sono realmente disponibili su questo server (es. web_search cerca davvero su internet): "
                 "non dire mai di non avere internet o di non poter accedere a servizi di casa se il tool corrispondente esiste; "
                 "usalo. Se un tool non e' nell'elenco, spiega semplicemente che qui non e' attivo.")
    parts.append(
        "\n# Come lavori con gli strumenti\n"
        "- L'utente vede i tuoi passaggi in un riquadro Attivita' ripiegato: non raccontarli. Mentre lavori NON scrivere testo tra una "
        "chiamata e l'altra; al massimo, prima di un gruppo di chiamate, UNA frase brevissima su cosa stai per fare (es. \"Cerco il documento "
        "e le informazioni della famiglia.\"): diventa lo stato mostrato nel riquadro.\n"
        "- Chiama insieme, nello stesso messaggio, gli strumenti indipendenti tra loro (es. list_files e list_projects): ogni giro in meno e' "
        "una risposta piu' rapida. Non rileggere dati che hai gia' (niente riletture dopo ogni modifica: una volta alla fine basta).\n"
        "- Quando hai finito scrivi UNA sola risposta finale, completa e ordinata: il risultato (numeri, file prodotti, cosa resta da "
        "decidere), senza ripetere i passaggi intermedi ne' gli errori gia' risolti. Se ti serve una conferma (es. prima di inviare), "
        "chiedila li', in fondo.")
    parts.append(
        "\n# Coding\n"
        "- Per pagine web, giochi, app HTML/CSS/JS, grafica SVG, diagrammi o programmi completi usa create_artifact: "
        "l'utente li vede in un pannello con anteprima live (HTML e SVG eseguiti nel browser). Per modifiche successive usa "
        "update_artifact (preferibilmente con replacements mirati) invece di ricreare l'artifact; list_artifacts elenca quelli "
        "esistenti e delete_artifact li elimina quando l'utente lo chiede.\n"
        "- Per calcoli, analisi dati, grafici e per verificare che un programma funzioni usa run_code (sandbox senza internet); "
        "i file prodotti (png, csv…) vengono mostrati all'utente. Se l'esecuzione fallisce, correggi e riprova.\n"
        "- Brevi frammenti di codice o spiegazioni vanno direttamente nel messaggio con blocchi ```lingua```.")
    if any(getattr(settings, f"{a}_web", False) for a in ("wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft")):
        parts.append(
            "\n# Editor integrati\n"
            "Accanto a ogni file in chat (docx, xlsx, pdf, pptx…) l'utente ha gia' il pulsante ✏️ Modifica che lo apre "
            "nell'editor integrato di " + settings.app_name + " (WordCraft, GridCraft, PdfCraft, DeckCraft, IdeaCraft). Se chiede di aprire o modificare un "
            "file 'nell'editor', NON dire che non puoi: rispondi che basta cliccare ✏️ Modifica accanto al file (o il menu "
            "📝 Documenti). Per convertire tra formati (Word → PDF, xlsx → csv…) usa i tool write_document/write_spreadsheet/"
            "write_presentation con source_file_id, non script Python.")
    missing = [a for a in ("wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft") if not mcp_manager.is_connected(a)]
    if missing and len(missing) < 5:
        parts.append(
            "Le app ArtCraft NON attive su questo server: " + ", ".join(missing) + ". Per i formati che coprono (Word, Excel, PDF, "
            "PowerPoint) usa run_code con le librerie disponibili nella sandbox (python-docx, openpyxl, python-pptx, reportlab, "
            "pypdf) e di' all'utente che l'amministratore puo' attivare l'app con ./familai.sh <app> up per avere l'editor integrato.")
    if mcp_manager.is_connected("wordcraft") and "wordcraft" in _apps_on:
        parts.append(
            "\n# Documenti Word/PDF (WordCraft)\n"
            "- Per lettere, relazioni, tesine, curriculum, verbali o qualsiasi documento da stampare/scaricare usa write_document: "
            "scrivi il contenuto in Markdown (titoli #, elenchi, **grassetto**, tabelle) e scegli format docx/pdf/odt. "
            "L'utente riceve il file in chat: non proporre di 'copiare il testo in Word'.\n"
            "- Per convertire o modificare un allegato passa source_file_id (l'id e' indicato accanto all'allegato).\n"
            "- Per interventi precisi (stili, tabelle, intestazioni, revisioni, anteprima di una pagina) usa i tool mcp__wordcraft__*: "
            "open_document con il percorso del file, list_commands per trovare il comando, execute/batch, render_page per "
            "vedere il risultato, save_document in " + str(settings.data_dir / "wordcraft") + "/<nome>.<ext> (l'unica cartella scrivibile). "
            "Dopo open_document ogni modifica viene salvata automaticamente sullo stesso file: l'anteprima (render_page) NON e' il "
            "file, e per dare all'utente un file con un altro nome usa save_document con il nuovo percorso. Le immagini si "
            "inseriscono con insert.picture da un PNG/JPG presente in data/ (per una mappa IdeaCraft usa il .png, non l'.svg): come path "
            "va bene l'id del file in chat, " + settings.app_name + " lo traduce nel percorso; non "
            "chiedere mai all'utente di inserire a mano cio' che puoi fare tu."
            + ("\n- L'utente puo' aprire e modificare a mano ogni documento con l'editor WordCraft integrato (pulsante ✏️ Modifica "
               "accanto al file, o menu 📝 Documenti): quando ha senso, diglielo." if settings.wordcraft_web else ""))
    if mcp_manager.is_connected("gridcraft") and "gridcraft" in _apps_on:
        parts.append(
            "\n# Fogli di calcolo (GridCraft)\n"
            "- Per tabelle da scaricare, bilanci, elenchi con totali, preventivi, registri e qualsiasi cosa 'da Excel' usa "
            "write_spreadsheet: passa le righe (prima riga = intestazioni) con numeri veri (non stringhe) e formule Excel per i "
            "totali (es. \"=SUM(B2:B9)\"); aggiungi formats (numberFormat Currency/Percentage, bold) e charts quando servono. "
            "L'utente riceve il file .xlsx in chat: non proporre di 'copiare la tabella in Excel'.\n"
            "- Per leggere o modificare un allegato xlsx/csv/ods passa source_file_id oppure usa i tool mcp__gridcraft__* "
            "(open_workbook con il percorso del file, read_range, evaluate_formula, format_range, insert_chart, save_workbook in "
            + str(settings.data_dir / "gridcraft") + "/<nome>.xlsx)."
            + ("\n- L'utente puo' aprire e modificare ogni foglio con l'editor GridCraft integrato (✏️ Modifica accanto al file, "
               "o menu 📝 Documenti)." if settings.gridcraft_web else ""))
    if mcp_manager.is_connected("pdfcraft") and "pdfcraft" in _apps_on:
        parts.append(
            "\n# PDF (PdfCraft)\n"
            "- Per unire, dividere, estrarre/ruotare/togliere pagine, comprimere, mettere una filigrana, trasformare immagini in PDF "
            "o mostrare una pagina come immagine usa pdf_tool con l'id dell'allegato (indicato accanto all'allegato); il testo di un "
            "PDF e' spesso gia' nel contesto, altrimenti pdf_tool action=text. I file prodotti compaiono in chat: non inventare link.\n"
            "- Per moduli da compilare, firme, commenti, redazione, OCR, confronto tra versioni e il resto usa i tool mcp__pdfcraft__*: "
            "doc_open con il percorso del file, poi tool_search per trovare il tool giusto e tool_call per eseguirlo; salva con "
            "doc_save in " + str(settings.data_dir / "pdfcraft") + "/<nome>.pdf (l'unica cartella scrivibile)."
            + ("\n- L'utente puo' aprire ogni PDF nell'editor PdfCraft integrato (✏️ accanto al file) per annotare, compilare "
               "moduli e firmare a mano." if settings.pdfcraft_web else ""))
    if mcp_manager.is_connected("deckcraft") and "deckcraft" in _apps_on:
        parts.append(
            "\n# Presentazioni (DeckCraft)\n"
            "- Per slide, presentazioni, lezioni, pitch o 'un PowerPoint' usa write_presentation: una voce per diapositiva con "
            "title, body (una riga per punto, tab per i sottolivelli) e layout; scegli un tema adatto. L'utente riceve il .pptx "
            "in chat: non proporre di 'copiare in PowerPoint'.\n"
            "- Per immagini, tabelle, grafici o modifiche a una presentazione allegata usa i tool mcp__deckcraft__* "
            "(open_presentation con il percorso del file, inspect_slide, set_text, insert_picture, insert_chart, render_slide "
            "per controllare il risultato, save_presentation in " + str(settings.data_dir / "deckcraft") + "/<nome>.pptx)."
            + ("\n- L'utente puo' aprire e modificare ogni presentazione con l'editor DeckCraft integrato (✏️ accanto al file)."
               if settings.deckcraft_web else ""))
    if mcp_manager.is_connected("ideacraft") and "ideacraft" in _apps_on:
        parts.append(
            "\n# Mappe mentali e diagrammi (IdeaCraft)\n"
            "- Per mappe mentali, mappe concettuali, brainstorming, schemi di studio o 'una mappa di…' usa mind_map con un outline "
            "Markdown annidato (titolo come idea centrale, rami e sotto-rami come elenco; 'A -> B: perche'' per collegamenti "
            "trasversali). L'immagine della mappa compare in chat; il .mm si apre in FreeMind/Freeplane/XMind. Con source_file_id "
            "converte una mappa allegata (.mm, .xmind, .opml, Mermaid). Per ritocchi (aggiungere/spostare idee, collegamenti) usa i tool "
            "mcp__ideacraft__* e poi save_map in " + str(settings.data_dir / "ideacraft") + "/<nome>.ideacraft.json.\n"
            "- Per DIAGRAMMI (schema di rete, flowchart, organigramma, architettura, UML/BPMN) usa i tool mcp__ideacraft__*: "
            "mcp__ideacraft__build_map con mode 'diagram' e un unico spec (nodes con label, kind dagli stencil es. net.router, net.switch, "
            "net.firewall, net.server, net.pc, net.wifi, net.internet, basic.container, flow.decision, props come ip, vlan, gateway, status; links from/to per "
            "label con label e style), poi mcp__ideacraft__layout se serve e mcp__ideacraft__save_map in " + str(settings.data_dir / "ideacraft")
            + "/<nome>.svg (immagine in chat) e .ideacraft.json (modificabile); .drawio per draw.io/Visio. Per gli schemi di rete, dopo "
            "build_map esegui mcp__ideacraft__validate_network (language 'it') e correggi gli errori (indirizzi, maschere, gateway, VLAN) "
            "prima di salvare; riporta all'utente i rilievi rimasti. mcp__ideacraft__list_stencils elenca le forme disponibili."
            + ("\n- L'utente puo' aprire e modificare mappe e diagrammi con l'editor IdeaCraft integrato (✏️ accanto al file .ideacraft.json, .mm, .opml, .drawio)."
               if settings.ideacraft_web else ""))
    if mcp_manager.is_connected("mailcraft") and user.role != Role.child and "mailcraft" in _apps_on:
        parts.append(
            "\n# Posta elettronica (MailCraft)\n"
            "- L'utente ha la sua posta in " + settings.app_name + " (menu Produttivita' > Posta): i tool mcp__mailcraft__* agiscono SOLO sulle caselle "
            "dell'utente con cui stai parlando. Per leggere: sync, list_messages (unread_only), search (from:, subject:, is:unread, "
            "has:attachment, newer_than:7d), read_message; con piu' caselle account=\"*\" interroga tutte insieme (le righe indicano @account). Per scrivere: reply/forward/send_mail — mostra SEMPRE prima destinatari, "
            "oggetto e testo e invia solo dopo un si' esplicito; stessa regola per trash, spam, remove_account.\n"
            "- Per configurare una casella: suggest_settings con l'indirizzo (conosce Gmail, Outlook, Aruba, PEC, Libero, TIM, Fastweb…), "
            "poi add_account con test=true; se manca la password chiedila (non inventarla) e ricorda che Gmail/Outlook vogliono una "
            "password per le app. Piu' caselle per utente sono normali (default_account in set_preferences).\n"
            "- Allegati in uscita: in send_mail passa attachments=[{\"path\": \"<id del file in chat>\"}] (l'id accanto all'allegato "
            "o quello restituito da write_document/mind_map/save_*), oppure il percorso sotto " + str(settings.data_dir) + " di un file "
            "creato dalle app. Allegati in arrivo: save_attachment con path " + str(settings.data_dir / "mailcraft") + "/<nome> li porta "
            "in chat come file (poi puoi aprirli con read_document o convertirli con le app).\n"
            "- Riassumi, non copiare intere mail; non leggere o inviare posta di altri membri della famiglia (non puoi comunque)."
        )
    pol = effective_policy(user)
    if "ask_model" in pol.get("allowed_tools", []):
        from ..tools.ask_model import describe_available
        avail = describe_available(pol.get("allowed_providers", []))
        if avail:
            parts.append(
                "\n# Altri modelli\n"
                f"Con il tool ask_model puoi consultare altri modelli: {avail}. Fallo quando l'utente lo chiede "
                "(es. 'chiedi a OpenAI', 'cosa direbbe GPT', 'confrontate', 'fai la ricerca con il modello locale') o per una "
                "seconda opinione utile; riporta sempre chi ha risposto. I modelli locali (ollama/custom) girano su server di "
                "casa raggiungibili da qui: puoi interrogarli, anche con with_web_search=true perche' cerchino sul web. "
                "Non proporre script da eseguire a mano al posto di usare il tool. Se l'utente vuole invece che d'ora in poi "
                "risponda un altro modello, ricordagli che puo' cambiarlo dal menu in alto nella chat.")
    from .memory import enabled as memory_enabled
    if memory_enabled():
        parts.append(
            "\n# Memoria\n"
            "Quando l'utente dice esplicitamente di ricordare qualcosa ('ricordati che…', 'segnati che…') usa il tool remember; "
            "quando chiede di dimenticare usa forget. Non salvare dati sanitari, finanziari, credenziali o opinioni politiche/religiose. "
            "Le voci della 'Memoria di famiglia' nel contesto sono fatti verificati: usali per personalizzare, senza recitarli.")
    from .tts import enabled as tts_enabled
    if tts_enabled():
        parts.append(
            "- Voce naturale negli artifact HTML: quando un gioco o un'app deve parlare (leggere lettere, parole, frasi, "
            "istruzioni) usa la funzione gia' disponibile `await FamilAI.speak(testo, {style})` (restituisce una Promise "
            "che si risolve a fine riproduzione; `FamilAI.stopSpeaking()` interrompe). Produce una voce umana di qualita' "
            "(TTS del provider), molto migliore di speechSynthesis: usa speechSynthesis solo come fallback se "
            "`window.FamilAI` non esiste. Evita di chiamarla per ogni piccolo evento: le frasi vengono messe in cache, "
            "quindi ripetere lo stesso testo e' immediato.")
    return "\n".join(parts)


async def prepare_user_blocks(text: str, file_ids: list[str], user: User, db: AsyncSession) -> list[dict]:
    blocks: list[dict] = []
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for fid in file_ids:
        from .access import require_access
        asset, _ = await require_access(db, "file", fid, user)
        if asset.mime.startswith("image/"):
            blocks.append({"type": "image", "file_id": asset.id, "mime": asset.mime, "filename": asset.filename})
        else:
            if asset.extracted_text is None:
                asset.extracted_text = extract_text(Path(asset.path), asset.mime) or ""
            txt = asset.extracted_text or ""
            if 0 < len(txt) <= INLINE_DOC_CHARS:
                blocks.append({"type": "text", "text": f"[Allegato: {asset.filename} (id {asset.id})]\n{txt}", "attachment": asset.id})
            else:
                blocks.append({"type": "text", "attachment": asset.id,
                               "text": f"[Allegato: {asset.filename} (id {asset.id}) — documento lungo: usa search_documents "
                                       f"per cercare i passaggi rilevanti o read_document per leggerlo per intero]"})
    await db.commit()
    return blocks


def resolve_provider_model(user: User, policy: dict, provider_id: str | None, model: str | None,
                           conv: Conversation | None = None) -> tuple[str, str]:
    """Bambini: provider e modello fissati dal genitore. Adulti: scelta libera tra i provider consentiti."""
    if provider_id and provider_id not in {"openai", "ollama", "mock"}:
        raise HTTPException(400, "Provider non supportato: scegli OpenAI oppure Ollama")
    if user.role == Role.child:
        pid = policy.get("provider") or (conv.provider if conv else None)
        if pid not in policy["allowed_providers"]:
            pid = next((p for p in policy["allowed_providers"] if p in {x["id"] for x in available_providers()}), None)
        provider = get_provider(pid)
        chosen = policy.get("model") or provider.default_model
        known = {m["id"] for m in provider.models()}
        return provider.id, (chosen if not known or chosen in known else provider.default_model)
    # utenti: dentro l'elenco deciso dall'amministratore; admin con blocco personale: sempre il suo modello
    from . import model_catalog
    provider_id, model = model_catalog.coerce(user, policy, provider_id or (conv.provider if conv else None),
                                              model or (conv.model if conv and conv.provider == (provider_id or conv.provider) else None))
    pid = provider_id or (conv.provider if conv else None)
    if pid == "auto":  # conversazioni importate
        pid = None
    if pid and pid not in policy["allowed_providers"] and pid != "mock":
        raise HTTPException(403, "Provider non consentito per questo utente")
    provider = get_provider(pid)
    chosen = model or (conv.model if conv and conv.provider == provider.id else "") or provider.default_model
    known = {m["id"] for m in provider.models()}
    if known and chosen not in known:
        chosen = provider.default_model   # es. modello ritirato dal provider
    return provider.id, chosen


async def run_turn(db: AsyncSession, user: User, conv: Conversation, text: str | None, file_ids: list[str],
                   provider_id: str | None, model: str | None, requested_tools: list[str] | None,
                   approval_id: str | None = None) -> AsyncIterator[str]:
    """Esegue un turno. Con text=None rigenera la risposta all'ultimo messaggio utente (es. dopo un cambio modello)."""
    policy = effective_policy(user)

    # ---- controlli preliminari ----
    # la passkey obbligatoria e' verificata a livello di sessione (require_auth + /me → pagina di registrazione)
    check_time_window(policy)
    await check_daily_quota(db, user, policy, "message")

    conv.provider, conv.model = resolve_provider_model(user, policy, provider_id, model, conv)
    provider = get_provider(conv.provider)
    model = conv.model

    system_extra_tutor = ""
    if text is None:
        # ---- rigenerazione: elimina tutto ciò che segue l'ultimo messaggio utente ----
        msgs = (await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))).scalars().all()
        last_user = next((m for m in reversed(msgs) if m.role == "user"), None)
        if not last_user:
            raise HTTPException(400, "Nessun messaggio da rigenerare")
        removed = [m.id for m in msgs if m.created_at > last_user.created_at]
        for m in msgs:
            if m.id in removed:
                await db.delete(m)
        await db.commit()
        text = "".join(b.get("text", "") for b in last_user.content if b.get("type") == "text" and not b.get("attachment"))
        yield sse({"type": "regenerating", "removed": removed, "provider": provider.id, "model": model})
    else:
        # ---- messaggio utente + filtro contenuti ----
        # Ripresa dopo il permesso del genitore: il messaggio del bambino e' gia' salvato, non lo duplichiamo.
        approved_decision = None
        if approval_id:
            from ..models import ApprovalRequest as _AR
            req = await db.get(_AR, approval_id)
            if req and req.child_id == user.id and req.status == "approved" and not req.consumed:
                approved_decision = req.decision
                req.consumed = True
                # togli l'avviso "ho chiesto il permesso" cosi' la cronologia termina con la domanda del bambino
                notes = (await db.execute(select(Message).where(Message.conversation_id == conv.id, Message.role == "assistant",
                                                                Message.flag_reason.like("compiti:%"))
                                          .order_by(Message.created_at.desc()).limit(1))).scalars().all()
                for n in notes:
                    await db.delete(n)
                await db.commit()
                yield sse({"type": "approval_resumed", "removed": [n.id for n in notes]})
        safety = await check_text(text, policy)
        if not approved_decision:
            user_blocks = await prepare_user_blocks(text, file_ids, user, db)
            umsg = Message(conversation_id=conv.id, author_id=user.id, role="user", content=user_blocks,
                           flagged=safety.flagged, flag_reason=safety.reason)
            db.add(umsg)
            if conv.title == "Nuova chat" and text.strip():
                conv.title = text.strip()[:60]
                from . import workspace as _ws
                await _ws.sync_conv_title(db, conv)
            await db.commit()
            yield sse({"type": "user_message", "message": _msg_out(umsg)})

        # ---- guardia compiti (solo bambini) ----
        tutor_mode = policy.get("education_mode") == "tutor"
        from . import homework as _hw
        hw_mode = _hw.mode_for(policy, user)
        if hw_mode != "allow" and not safety.block:
            if not approved_decision:
                approved_decision = await _hw.has_pass(db, user)   # permesso "per oggi"
            if approved_decision == "approve_tutor":
                tutor_mode = True
            elif not approved_decision:
                tail_rows = (await db.execute(select(Message).where(Message.conversation_id == conv.id)
                                              .order_by(Message.created_at.desc()).limit(3))).scalars().all()
                tail = [" ".join(b.get("text", "") for b in m.content if b.get("type") == "text")[:300] for m in reversed(tail_rows) if m.role == "user"]
                verdict = None if hw_mode == "tutor" else await _hw.classify(text, tail)
                if verdict and verdict.category == "do_homework" and verdict.confidence >= 2:
                    if hw_mode == "tutor":
                        tutor_mode = True
                    else:
                        if hw_mode == "ask_parent":
                            req = await _hw.create_request(db, user, conv.id, text, verdict)
                            note = ("Questo sembra un compito per la scuola 📚: ho chiesto il permesso a un genitore. "
                                    "Appena risponde continuo! Nel frattempo, se vuoi, posso spiegarti come si fa.")
                        else:
                            req = None
                            note = "Questo sembra un compito per la scuola 📚: i compiti si fanno da soli! Se vuoi ti spiego come si fa, ma la risposta la trovi tu."
                        amsg = Message(conversation_id=conv.id, role="assistant", content=[{"type": "text", "text": note}],
                                       flagged=True, flag_reason=f"compiti: {verdict.reason}")
                        db.add(amsg)
                        await db.commit()
                        yield sse({"type": "text_delta", "text": note})
                        if req:
                            yield sse({"type": "approval_required", "approval_id": req.id, "message_text": text, "file_ids": file_ids})
                        yield sse({"type": "done", "message": _msg_out(amsg)})
                        return
        if tutor_mode:
            system_extra_tutor = _hw.TUTOR_RULES
        else:
            system_extra_tutor = ""

        if safety.block:
            amsg = Message(conversation_id=conv.id, role="assistant", content=[{"type": "text", "text": CHILD_BLOCK_MESSAGE}],
                           flagged=True, flag_reason="risposta bloccata")
            db.add(amsg)
            await db.commit()
            yield sse({"type": "text_delta", "text": CHILD_BLOCK_MESSAGE})
            yield sse({"type": "done", "message": _msg_out(amsg)})
            return

    # ---- tool consentiti ----
    tools = []
    for name, t in all_tools().items():
        if not tool_allowed(policy, name) or not mcp_manager.allowed_for(user.role, name):
            continue
        if requested_tools is not None:
            group = "mcp" if name.startswith("mcp__") else TOOL_ALIASES.get(name, name)
            if name not in requested_tools and group not in requested_tools:
                continue
        tools.append(t.spec)

    if provider.id == "ollama" and getattr(provider, "is_small", lambda m: True)(model):
        local_ok = set(LOCAL_TOOLS) | ({"web_search"} if settings.searxng_url else set()) | ({"write_document"} if mcp_manager.is_connected("wordcraft") else set()) | ({"write_spreadsheet"} if mcp_manager.is_connected("gridcraft") else set()) | ({"pdf_tool"} if mcp_manager.is_connected("pdfcraft") else set()) | ({"write_presentation"} if mcp_manager.is_connected("deckcraft") else set()) | ({"mind_map"} if mcp_manager.is_connected("ideacraft") else set())
        # Modelli locali piccoli (Qwen3 8B & co.) usano i tool, ma reggono meglio un set ridotto: artifact e documenti.
        # I modelli grandi (es. gpt-oss:120b) ricevono tutti i tool.
        tools = [t for t in tools if t.name in local_ok]

    system = await build_system_prompt(user, conv, db)
    # Parte variabile (data/ora, estratti RAG): va DOPO il punto di cache, cosi' il prefisso stabile
    # (tool + prompt di sistema) viene riusato dalla cache del provider a ogni turno.
    from ..policy.permissions import dynamic_context
    system_dynamic = dynamic_context()
    if system_extra_tutor:
        system_dynamic += "\n" + system_extra_tutor
    from .compaction import summary_block
    sb = summary_block(conv)
    if sb:
        system_dynamic += "\n\n" + sb
    # Memoria di famiglia: fatti approvati pertinenti (sempre quelli fissati)
    try:
        from . import memory as _memory
        mem_items = await _memory.recall(db, user, text or "")
        if mem_items:
            system_dynamic += "\n\n" + _memory.format_for_prompt(mem_items)
            for it in mem_items:
                it.used_count = (it.used_count or 0) + 1
    except Exception as e:
        log.warning("Memoria non disponibile: %s", e)
    # RAG: recupero automatico dei passaggi pertinenti dai documenti del progetto / della chat
    if text.strip():
        try:
            from . import workspace as _ws
            hits = await rag.search(db, text, user.family_id,
                                    project_ids=[conv.project_id] if conv.project_id else None,
                                    conversation_ids=[conv.id],
                                    folder_ids=await _ws.context_folder_ids(db, user, conv) or None,
                                    lexical_only=user.role == Role.child and not policy.get("cloud_allowed",False))
            if hits:
                system_dynamic += ("\n\n# Estratti dai documenti dell'utente pertinenti alla richiesta corrente\n"
                           "Usali se rispondono alla domanda e cita il nome del file; se non bastano usa search_documents.\n\n"
                           + rag.format_hits(hits))
                yield sse({"type": "rag", "hits": [{"file_id": h.file_id, "filename": h.filename, "idx": h.idx,
                                                   "score": h.score} for h in hits]})
        except Exception as e:
            log.warning("RAG non disponibile: %s", e)
    history = await build_history(conv, db)
    ctx = ToolContext(user=user, policy=policy, db=db, conversation_id=conv.id, project_id=conv.project_id)
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    # Debug LLM (Impostazioni provider > Comportamento in chat): parametri effettivi, tempi, ragionamento nascosto, prompt di sistema
    dbg_mode = str(getattr(settings, "debug_llm", "off") or "off").lower()
    dbg_on = dbg_mode == "all" or (dbg_mode == "admins" and user.role == Role.admin)
    dbg_rounds: list[dict] = []
    from ..providers.base import debug_sink as _debug_sink

    turn_start = len(history)
    for _round in range(MAX_TOOL_ROUNDS + 1):
        if _round:
            _shrink_old_results(history, turn_start, int(getattr(settings, "turn_tool_chars", 300000) or 300000))
        text_acc = ""
        tool_uses: list[dict] = []
        think_acc, think_blocks = "", []   # ragionamento visibile (extended thinking / <think>)
        yield sse({"type": "status", "text": "Sto pensando…" if _round == 0 else "Sto elaborando i risultati…"})
        dbg_cur: dict | None = None
        dbg_token = None
        if dbg_on:
            dbg_cur = {"round": _round + 1, "provider": provider.id, "provider_label": provider.label, "model": model,
                       "max_tokens": settings.max_output_tokens, "thinking_visible": bool(provider.thinking),
                       "tools_available": len(tools), "history_messages": len(history),
                       "system_stable_chars": len(system), "system_dynamic_chars": len(system_dynamic)}
            if _round == 0:
                dbg_cur["system_prompt"] = system[:60000]
                dbg_cur["system_dynamic"] = system_dynamic[:20000]
            dbg_token = _debug_sink.set(dbg_cur)
        try:
            # Continuazione automatica: se il modello si ferma per il limite di token della singola chiamata,
            # gli chiediamo di proseguire (senza ripetere) finche' finisce o si supera MAX_TOTAL_OUTPUT_TOKENS.
            cont_history = history
            continuations = 0
            while True:
                truncated = False
                async for ev in provider.stream(model, system, cont_history, tools, max_tokens=settings.max_output_tokens,
                                                system_dynamic=system_dynamic,
                                                **({"cache_scope": f"{user.family_id}:{user.id}"} if provider.id == "openai" else {})):
                    if ev.type == "text_delta":
                        text_acc += ev.text
                        yield sse({"type": "text_delta", "text": ev.text})
                    elif ev.type == "tool_use" and ev.tool_use:
                        tool_uses.append(ev.tool_use)
                    elif ev.type == "thinking_delta":
                        think_acc += ev.text
                        yield sse({"type": "thinking_delta", "text": ev.text})
                    elif ev.type == "hidden_thinking":
                        if dbg_on:   # ragionamento che il modello fa ma l'utente non vedrebbe: in debug si mostra
                            yield sse({"type": "thinking_delta", "text": ev.text, "hidden": True})
                    elif ev.type == "thinking_block" and ev.tool_use:
                        blk = dict(ev.tool_use)
                        if blk.get("type") == "thinking":
                            blk["thinking"] = think_acc
                            think_acc = ""
                        think_blocks.append(blk)
                    elif ev.type == "status":
                        yield sse({"type": "status", "text": ev.text})
                    elif ev.type == "truncated":
                        truncated = True
                    elif ev.type == "error":
                        raise RuntimeError(ev.error or "Risposta del provider non disponibile")
                    elif ev.type == "usage":
                        for k in total_usage:
                            total_usage[k] += int(ev.usage.get(k, 0) or 0)
                auto = str(settings.auto_continue).lower() in ("1", "true", "yes")
                if not truncated or tool_uses or not auto:
                    if truncated and not auto:
                        yield sse({"type": "text_delta", "text": "\n\n⚠️ Risposta interrotta per lunghezza massima: chiedimi di continuare."})
                        text_acc += "\n\n⚠️ Risposta interrotta per lunghezza massima: chiedimi di continuare."
                    break
                if total_usage["output_tokens"] >= int(settings.max_total_output_tokens) or continuations >= 24:
                    note = f"\n\n⚠️ Risposta fermata: superato il limite totale di {int(settings.max_total_output_tokens):,} token."
                    yield sse({"type": "text_delta", "text": note})
                    text_acc += note
                    break
                continuations += 1
                yield sse({"type": "status", "text": f"✍️ Continuo la risposta ({continuations})…"})
                cont_history = history + [
                    {"role": "assistant", "content": [{"type": "text", "text": text_acc}]},
                    {"role": "user", "content": [{"type": "text", "text":
                        "Continua ESATTAMENTE da dove ti sei interrotto, senza ripetere nulla di quanto gia' scritto, "
                        "senza preamboli e mantenendo formato e numerazione."}]},
                ]
        except Exception as e:
            log.exception("Errore provider")
            err = f"Errore del provider {provider.label}: {e}"
            amsg = Message(conversation_id=conv.id, role="assistant", content=[{"type": "text", "text": text_acc + f"\n\n⚠️ {err}"}])
            if dbg_on and dbg_cur is not None:
                dbg_cur["error"] = str(e)[:2000]
                dbg_rounds.append(dbg_cur)
                amsg.usage = {"debug": {"rounds": dbg_rounds}}
            db.add(amsg)
            await db.commit()
            yield sse({"type": "error", "error": err})
            yield sse({"type": "done", "message": _msg_out(amsg)})
            return

        finally:
            if dbg_token is not None:
                try:
                    _debug_sink.reset(dbg_token)
                except ValueError:
                    _debug_sink.set(None)
        if dbg_on and dbg_cur is not None:
            dbg_cur.setdefault("tool_calls", [t["name"] for t in tool_uses])
            if continuations:
                dbg_cur["continuations"] = continuations
            dbg_rounds.append(dbg_cur)
            yield sse({"type": "debug", "data": {k: v for k, v in dbg_cur.items() if k not in ("system_prompt", "system_dynamic", "reasoning")}})

        if tool_uses and _round == MAX_TOOL_ROUNDS:
            # limite di giri raggiunto con altri tool in coda: non li eseguiamo e lo diciamo, invece di chiudere il turno in silenzio
            note = (f"\n\n⚠️ Mi sono fermato dopo {MAX_TOOL_ROUNDS} passaggi con strumenti (stavo per usare "
                    f"{', '.join(dict.fromkeys(t['name'] for t in tool_uses))}). Scrivi \"continua\" per farmi riprendere da qui.")
            tool_uses = []
            yield sse({"type": "text_delta", "text": note})
            text_acc += note

        blocks: list[dict] = []
        if think_acc and not think_blocks:
            think_blocks.append({"type": "thinking", "thinking": think_acc})   # modelli locali
        blocks += think_blocks
        if text_acc:
            blocks.append({"type": "text", "text": text_acc})
        blocks += [{"type": "tool_use", **tu} for tu in tool_uses]
        amsg = Message(conversation_id=conv.id, role="assistant", content=blocks)
        db.add(amsg)
        await db.commit()
        history.append({"role": "assistant", "content": blocks})

        if not tool_uses or _round == MAX_TOOL_ROUNDS:
            # risposta finale
            if policy.get("moderation") and text_acc:
                out_safety = await check_text(text_acc, policy)
                if out_safety.flagged:
                    amsg.flagged, amsg.flag_reason = True, "output: " + out_safety.reason
            amsg.usage = {**total_usage, "provider": provider.id, "model": model,
                          **({"debug": {"rounds": dbg_rounds}} if dbg_on else {})}
            await db.commit()
            await log_usage(db, user, "message", provider.id, model, total_usage["input_tokens"], total_usage["output_tokens"],
                            cache_read_tokens=total_usage["cache_read_tokens"], cache_write_tokens=total_usage["cache_write_tokens"])
            yield sse({"type": "done", "message": _msg_out(amsg)})
            try:
                import asyncio as _aio
                from . import memory as _memory
                _aio.get_event_loop().create_task(_memory.maybe_extract(conv.id, user.id))
            except Exception:
                pass
            return

        # ---- esecuzione tool ----
        results: list[dict] = []
        try:
          for tu in tool_uses:
              yield sse({"type": "tool_start", "id": tu["id"], "name": tu["name"], "input": tu["input"]})
              tool = get_tool(tu["name"])
              if not tool and "__" not in tu["name"]:
                  # Il modello chiama un tool MCP con il nome nudo (save_document invece di mcp__wordcraft__save_document):
                  # se corrisponde a un solo server connesso lo instradiamo noi.
                  cands = [n for n in all_tools() if n.startswith("mcp__") and n.endswith("__" + tu["name"])]
                  if len(cands) == 1:
                      tu["name"] = cands[0]
                      tool = get_tool(cands[0])
              if not tool or not tool_allowed(policy, tu["name"]):
                  res_content, is_err, artifacts = "Strumento non disponibile", True, None
              else:
                  try:
                      await check_daily_quota(db, user, policy, tool.usage_kind)
                      r = await tool.fn(tu["input"] or {}, ctx)
                      res_content, is_err, artifacts = r.content, r.is_error, r.artifacts
                      if not is_err:
                          u = r.usage or {}
                          await log_usage(db, user, tool.usage_kind, u.get("provider") or provider.id, u.get("model") or model,
                                          int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0),
                                          detail=u.get("detail") or tu["name"])
                  except HTTPException as he:
                      res_content, is_err, artifacts = he.detail, True, None
                  except Exception as e:
                      log.exception("Errore tool %s", tu["name"])
                      res_content, is_err, artifacts = f"Errore: {e}", True, None
              if not (res_content or "").strip():
                  res_content = "(il tool non ha restituito alcun dettaglio)" if is_err else "(nessun output)"
              if is_err:
                  log.warning("Tool %s fallito per %s: %s", tu["name"], user.username, res_content[:500])
              block = {"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"], "content": _clip_result(res_content),
                       "is_error": is_err, "artifacts": artifacts or [],
                       "input": tu["input"] if tu["name"] != "create_artifact" else {k: v for k, v in (tu["input"] or {}).items() if k != "content"}}
              results.append(block)
              yield sse({"type": "tool_result", **block, "content": res_content[:800]})
        finally:
            # anche se lo stream viene interrotto (stop dell'utente), lascia la cronologia coerente
            done_ids = {r["tool_use_id"] for r in results}
            for tu in tool_uses:
                if tu["id"] not in done_ids:
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"],
                                    "content": "(esecuzione interrotta)", "is_error": True, "artifacts": []})
            if results:
                db.add(Message(conversation_id=conv.id, role="tool", content=results))
                await db.commit()
        history.append({"role": "tool", "content": results})


def _msg_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "author_id": m.author_id, "content": m.content,
            "created_at": m.created_at, "flagged": m.flagged, "usage": m.usage or {}}
