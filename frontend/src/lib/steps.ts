/** Nomi in italiano, riassunti e durate dei passaggi dell'agente (chiamate ai tool) per il blocco Attività della chat.
 *  Il nome tecnico (mcp__quotecraft__add_line) resta nei dettagli: qui si legge "Aggiunta riga · FTTH-1000 ×3 → 450,00 €". */

const APP_NAMES: Record<string, string> = {
  quotecraft: 'Preventivi', covercraft: 'Copertura', mailcraft: 'Posta', remotecraft: 'Assistenza', wordcraft: 'WordCraft',
  gridcraft: 'GridCraft', deckcraft: 'DeckCraft', pdfcraft: 'PdfCraft', ideacraft: 'IdeaCraft',
}
const APP_ICONS: Record<string, string> = {
  quotecraft: '💶', covercraft: '📡', mailcraft: '✉️', remotecraft: '🛟', wordcraft: '📝', gridcraft: '📊', deckcraft: '📽️', pdfcraft: '📕', ideacraft: '🧠',
}

/** Titoli per tool: nativi e MCP (server/tool). */
const TITLES: Record<string, string> = {
  web_search: 'Ricerca web', generate_image: 'Immagine', generate_video: 'Video', read_document: 'Lettura documento',
  search_documents: 'Ricerca nei documenti', create_artifact: 'Nuovo artifact', update_artifact: 'Aggiornamento artifact',
  list_artifacts: 'Elenco artifact', delete_artifact: 'Eliminazione artifact', run_code: 'Esecuzione codice',
  write_document: 'Documento Word', write_spreadsheet: 'Foglio di calcolo', pdf_tool: 'PDF', write_presentation: 'Presentazione',
  mind_map: 'Mappa mentale', ask_model: 'Consulto un altro modello', remember: 'Memorizzo', forget: 'Dimentico',
  // QuoteCraft
  'quotecraft/list_items': 'Cerco a listino', 'quotecraft/get_item': 'Voce di listino', 'quotecraft/save_item': 'Salvo voce di listino',
  'quotecraft/remove_item': 'Tolgo voce di listino', 'quotecraft/import_items': 'Importo listino', 'quotecraft/list_customers': 'Cerco il cliente',
  'quotecraft/save_customer': 'Salvo cliente', 'quotecraft/remove_customer': 'Elimino cliente', 'quotecraft/list_quotes': 'Elenco preventivi',
  'quotecraft/new_quote': 'Nuovo preventivo', 'quotecraft/get_quote': 'Leggo il preventivo', 'quotecraft/set_quote': 'Modifico il preventivo',
  'quotecraft/delete_quote': 'Elimino preventivo', 'quotecraft/duplicate_quote': 'Duplico preventivo', 'quotecraft/add_line': 'Aggiunta riga',
  'quotecraft/set_line': 'Modifica riga', 'quotecraft/remove_line': 'Tolgo riga', 'quotecraft/move_line': 'Sposto riga',
  'quotecraft/quote_html': 'Preventivo in HTML', 'quotecraft/make_documents': 'Documenti del preventivo', 'quotecraft/send_quote': 'Invio del preventivo',
  'quotecraft/attach_document': 'Allego documento', 'quotecraft/remove_document': 'Tolgo documento', 'quotecraft/list_letterheads': 'Carte intestate',
  'quotecraft/import_letterhead': 'Importo carta intestata', 'quotecraft/remove_letterhead': 'Tolgo carta intestata', 'quotecraft/get_company': 'Dati azienda',
  'quotecraft/set_company': 'Salvo dati azienda', 'quotecraft/get_preferences': 'Impostazioni preventivi', 'quotecraft/set_preferences': 'Salvo impostazioni',
  'quotecraft/check_bridges': 'Verifico collegamenti', 'quotecraft/write_quote': 'Scrivo il preventivo',
  // CoverCraft
  'covercraft/verify_coverage': 'Verifica copertura', 'covercraft/search_comune': 'Cerco il comune', 'covercraft/search_street': 'Cerco la via',
  'covercraft/list_civici': 'Civici', 'covercraft/find_cell': 'Cerco la cella', 'covercraft/open_history': 'Storico verifiche',
  'covercraft/configure_database': 'Configuro il database', 'covercraft/test_database': 'Provo il database', 'covercraft/set_preferences': 'Preferenze copertura',
  // MailCraft
  'mailcraft/list_messages': 'Leggo la casella', 'mailcraft/read_message': 'Leggo il messaggio', 'mailcraft/send_mail': 'Invio e-mail',
  'mailcraft/save_attachment': 'Salvo allegato', 'mailcraft/search_messages': 'Cerco nella posta',
  // RemoteCraft
  'remotecraft/find_customers': 'Cerco il cliente', 'remotecraft/get_customer': 'Scheda cliente', 'remotecraft/add_customer': 'Nuovo cliente',
  'remotecraft/find_device': 'Cerco il PC', 'remotecraft/list_devices': 'Elenco PC', 'remotecraft/connect_device': 'Collegamento al PC',
  'remotecraft/start_intervention': 'Avvio intervento', 'remotecraft/stop_intervention': 'Chiudo intervento', 'remotecraft/log_intervention': 'Registro intervento',
  'remotecraft/time_report': 'Rapporto ore', 'remotecraft/record_consent': 'Registro consenso', 'remotecraft/gdpr_export': 'Esportazione GDPR',
  // app di produttivita'
  'wordcraft/open_document': 'Apro il documento', 'wordcraft/save_document': 'Salvo il documento', 'wordcraft/new_document': 'Nuovo documento',
  'gridcraft/save_workbook': 'Salvo il foglio', 'gridcraft/open_workbook': 'Apro il foglio', 'deckcraft/save_presentation': 'Salvo la presentazione',
  'deckcraft/export': 'Esporto la presentazione', 'ideacraft/save_map': 'Salvo la mappa',
}

export function splitTool(name: string): { app: string; tool: string } {
  if (name.startsWith('mcp__')) {
    const [, app = '', ...rest] = name.split('__')
    return { app, tool: rest.join('__') }
  }
  return { app: '', tool: name }
}

export function appIcon(name: string): string {
  const { app, tool } = splitTool(name)
  if (app) return APP_ICONS[app] ?? '🔌'
  return { web_search: '🔎', generate_image: '🎨', generate_video: '🎬', run_code: '⚙️', write_document: '📝', write_spreadsheet: '📊', pdf_tool: '📕',
    write_presentation: '📽️', mind_map: '🧠', ask_model: '🤝', remember: '🧠', forget: '🧠' }[tool] ?? '📄'
}

/** Titolo leggibile del passo ("Aggiunta riga"); per tool sconosciuti il nome tecnico ripulito ("Preventivi · set line"). */
export function stepTitle(name: string): string {
  const { app, tool } = splitTool(name)
  const key = app ? `${app}/${tool}` : tool
  if (TITLES[key]) return TITLES[key]
  const human = tool.replace(/_/g, ' ')
  return app ? `${APP_NAMES[app] ?? app} · ${human}` : human
}

const money = (v: any) => (v === undefined || v === null || v === '' ? '' : String(v))
const short = (s: any, n = 70) => { const t = String(s ?? '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t }

/** Cosa sta facendo / ha chiesto: dagli argomenti. */
export function stepInput(name: string, input: any): string {
  if (!input || typeof input !== 'object') return ''
  const { app, tool } = splitTool(name)
  const i = input
  if (!app) {
    if (tool === 'web_search') return `“${short(i.query)}”`
    if (tool === 'generate_image' || tool === 'generate_video') return `“${short(i.prompt, 90)}”`
    if (tool === 'create_artifact') return `${i.title ?? ''}${i.kind ? ` (${i.kind})` : ''}`
    if (tool === 'update_artifact' || tool === 'delete_artifact') return i.title ?? i.artifact_id ?? ''
    if (tool === 'run_code') return `${i.language ?? ''}${i.code ? ` · ${String(i.code).split('\n').length} righe` : ''}`
    if (tool === 'read_document' || tool === 'search_documents') return i.query ? `“${short(i.query)}”` : (i.file_id ?? '')
    if (tool === 'remember') return `“${short(i.text, 90)}”`
    if (tool === 'forget') return `“${short(i.query)}”`
    if (tool === 'ask_model') return `${i.provider ?? ''}${i.model ? ` (${i.model})` : ''}: “${short(i.prompt, 80)}”`
    if (tool === 'write_document' || tool === 'write_spreadsheet' || tool === 'write_presentation' || tool === 'mind_map') return i.title ?? i.filename ?? i.path ?? ''
  }
  if (app === 'quotecraft') {
    if (tool === 'list_items') return [i.query && `“${i.query}”`, i.category].filter(Boolean).join(' · ')
    if (tool === 'list_customers') return i.query ? `“${i.query}”` : ''
    if (tool === 'new_quote') return [i.customer_name ?? i.name ?? i.customer, i.channel].filter(Boolean).join(' · ')
    if (tool === 'add_line' || tool === 'set_line') return [i.code ?? i.name, i.qty !== undefined && `×${i.qty}`, i.discount_pct && `−${i.discount_pct}%`, !i.code && money(i.one_off) && `${money(i.one_off)} €`].filter(Boolean).join(' ')
    if (tool === 'make_documents') return (i.formats || []).join(', ').toUpperCase()
    if (tool === 'send_quote') return i.to ? `a ${i.to}` : ''
    if (tool === 'attach_document' || tool === 'remove_document') return i.name ?? ''
    if (tool === 'get_quote' || tool === 'quote_html') return i.id ? `#${i.id}` : ''
  }
  if (app === 'covercraft') {
    if (tool === 'verify_coverage') return [i.address ?? i.street, i.civic ?? i.number, i.comune ?? i.city].filter(Boolean).join(' ')
    if (tool === 'search_comune' || tool === 'search_street') return `“${short(i.query ?? i.name ?? i.comune)}”`
    if (tool === 'find_cell') return [i.mcc, i.mnc, i.lac ?? i.tac, i.cid].filter((x) => x !== undefined).join('-')
  }
  if (app === 'mailcraft') {
    if (tool === 'send_mail') return [i.to && `a ${i.to}`, i.subject && `“${short(i.subject, 50)}”`].filter(Boolean).join(' · ')
    if (tool === 'list_messages') return [i.mailbox ?? i.account, i.unread_only && 'non letti'].filter(Boolean).join(' · ')
    if (tool === 'read_message' || tool === 'save_attachment') return i.subject ?? i.filename ?? ''
  }
  if (i.path) return String(i.path).split('/').pop() ?? ''
  if (i.query) return `“${short(i.query)}”`
  const first = Object.entries(i).find(([, v]) => typeof v === 'string' || typeof v === 'number')
  return first ? `${first[0]}: ${short(first[1], 50)}` : ''
}

/** Com'e' andata: dal risultato (testo), in una manciata di parole. */
export function stepOutcome(name: string, content: any, isError: boolean): string {
  const text = String(content ?? '').trim()
  if (isError) return short(text.replace(/^(Errore( MCP)?|Error)\s*[:\-]?\s*/i, ''), 90) || 'errore'
  if (!text) return ''
  const { app, tool } = splitTool(name)
  let json: any = null
  if (text.startsWith('{') || text.startsWith('[')) { try { json = JSON.parse(text) } catch { /* testo libero */ } }
  if (app === 'quotecraft' && json) {
    if (tool === 'new_quote' && (json.number || json.quote?.number)) return `→ ${json.number ?? json.quote.number}`
    if (tool === 'list_items' && Array.isArray(json.items)) return `${json.items.length} voci`
    if (tool === 'list_customers' && Array.isArray(json.customers)) return json.customers.length === 1 ? `→ ${json.customers[0].name ?? ''}` : `${json.customers.length} clienti`
    if ((tool === 'add_line' || tool === 'get_quote' || tool === 'set_line') && json.totals) {
      const t = json.totals
      const eur = (c: any) => typeof c === 'number' ? (c / 100).toLocaleString('it-IT', { minimumFractionDigits: 2 }) + ' €' : ''
      return [eur(t.one_off_net ?? t.one_off) , eur(t.monthly_net ?? t.monthly) && `${eur(t.monthly_net ?? t.monthly)}/mese`].filter(Boolean).join(' + ')
    }
    if (tool === 'make_documents' && Array.isArray(json.files)) return json.files.map((f: any) => String(f.name ?? f).split('/').pop()).join(', ')
    if (tool === 'send_quote') return json.sent || json.ok ? 'inviato' : ''
    if (tool === 'attach_document' && json.attached) return `→ ${json.attached}`
  }
  if (app === 'covercraft' && json) {
    if (tool === 'verify_coverage') return short(json.summary ?? json.result ?? json.coverage ?? '', 90)
  }
  if (!app) {
    if (tool === 'web_search') { const n = (text.match(/^\s*\d+[.)]\s|^- /gm) || []).length; return n ? `${n} risultati` : '' }
    if (tool === 'run_code') return short(text.split('\n').filter(Boolean).pop() ?? '', 80)
  }
  const first = text.split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('{') && !l.startsWith('['))
  return short(first ?? '', 90)
}

export function fmtDuration(ms: number | undefined): string {
  if (!ms || ms < 0) return ''
  if (ms < 1000) return `${(ms / 1000).toFixed(1).replace('.', ',')} s`
  if (ms < 60000) return `${Math.round(ms / 1000)} s`
  const m = Math.floor(ms / 60000), s = Math.round((ms % 60000) / 1000)
  return `${m} min${s ? ` ${s} s` : ''}`
}

/** Frase di riepilogo del turno: dall'app piu' usata e dall'ultimo passo utile. */
export function turnSummary(blocks: any[]): { live: string; done: string } {
  const apps = blocks.map((b) => splitTool(b.name).app).filter(Boolean)
  const main = apps.sort((a, b) => apps.filter((x) => x === b).length - apps.filter((x) => x === a).length)[0] ?? ''
  const n = blocks.length
  const steps = `${n} ${n === 1 ? 'passaggio' : 'passaggi'}`
  const quoteNo = blocks.map((b) => { const o = stepOutcome(b.name, b.content, false); return splitTool(b.name).tool === 'new_quote' && o.startsWith('→ ') ? o.slice(2) : '' }).find(Boolean)
  const byApp: Record<string, [string, string]> = {
    quotecraft: ['Sto preparando il preventivo', quoteNo ? `Preventivo ${quoteNo} pronto` : 'Preventivo'],
    covercraft: ['Sto verificando la copertura', 'Copertura verificata'],
    mailcraft: ['Sto lavorando sulla posta', 'Posta'],
    remotecraft: ['Sto lavorando sull\'assistenza', 'Assistenza'],
    wordcraft: ['Sto scrivendo il documento', 'Documento'], gridcraft: ['Sto preparando il foglio', 'Foglio di calcolo'],
    deckcraft: ['Sto preparando la presentazione', 'Presentazione'], pdfcraft: ['Sto lavorando sul PDF', 'PDF'], ideacraft: ['Sto disegnando la mappa', 'Mappa'],
  }
  const native = blocks.map((b) => splitTool(b.name).tool)
  let live = 'Ci sto lavorando', done = 'Fatto'
  if (main && byApp[main]) [live, done] = byApp[main]
  else if (native.includes('web_search')) [live, done] = ['Sto cercando sul web', 'Ricerca web']
  else if (native.includes('run_code')) [live, done] = ['Sto eseguendo il codice', 'Codice eseguito']
  else if (native.some((t) => t.startsWith('generate_'))) [live, done] = ['Sto generando', 'Generato']
  else if (native.includes('search_documents') || native.includes('read_document')) [live, done] = ['Sto leggendo i documenti', 'Documenti consultati']
  return { live, done: `${done} · ${steps}` }
}
