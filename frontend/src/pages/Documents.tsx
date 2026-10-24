import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import FolderDialog from '../components/FolderDialog'
import { useWindowActive } from '../lib/desktop'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { del, get, patch, post, type FileOut, type Project } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useLiveEvents } from '../lib/events'
import { HEADERS, fmtBytes } from '../lib/api'
import ShareDialog from '../components/ShareDialog'
import VersionHistory from '../components/VersionHistory'
import { APP_LABEL, canEdit, editorFor, editorPath, newFilePath, type ArtApp } from '../lib/artcraft'

/**
 * 📁 Documenti, stile Finder: cartelle create dall'utente (annidabili, drag & drop), i progetti come cartelle automatiche,
 * e la cartella "APP" (stile iOS) con le applicazioni: nuovo documento Word, foglio Excel, presentazione, mappa, PDF, posta.
 * Cartelle speciali (dal backend, kind): 🏢 <famiglia> con le cartelle di reparto (sola lettura, carica l'amministratore,
 * base di conoscenza del modello) e 💬 CHAT con una sottocartella automatica per ogni chat che ha file (contesto di quella chat).
 * Posizione corrente nell'URL: ?folder=<id> (cartella propria) oppure ?project=<id> (file di un progetto).
 */

interface Folder { id: string; name: string; parent_id: string | null; color: string; files: number; subfolders: number; kind?: string; dept?: string; conversation_id?: string | null; can_write?: boolean; system?: boolean }
const KIND_LABEL: Record<string, string> = { org: 'spazio famiglia', dept: 'condivisa', chat: 'file delle chat', conv: 'chat' }
const KIND_GLYPH: Record<string, string> = { org: '🏠', dept: '', chat: '💬', conv: '💬' }

/** Le app: icona (tile colorata), etichetta, cosa fa il click. */
const APPS: { key: string; app?: ArtApp; label: string; glyph: string; cls: string; name?: string; feature: string }[] = [
  { key: 'word', app: 'wordcraft', label: 'Documento', glyph: 'W', cls: 'app-word', name: 'documento.docx', feature: 'wordcraft_web' },
  { key: 'excel', app: 'gridcraft', label: 'Foglio', glyph: 'X', cls: 'app-excel', name: 'foglio.xlsx', feature: 'gridcraft_web' },
  { key: 'deck', app: 'deckcraft', label: 'Presentazione', glyph: 'P', cls: 'app-deck', name: 'presentazione.pptx', feature: 'deckcraft_web' },
  { key: 'map', app: 'ideacraft', label: 'Mappa', glyph: '◎', cls: 'app-map', name: 'mappa.ideacraft.json', feature: 'ideacraft_web' },
  { key: 'pdf', app: 'pdfcraft', label: 'PDF', glyph: 'PDF', cls: 'app-pdf', feature: 'pdfcraft_web' },
  { key: 'mail', label: 'Posta', glyph: '✉', cls: 'app-mail', feature: 'mailcraft_web' },
  { key: 'remote', label: 'Assistenza', glyph: '🛟', cls: 'app-remote', feature: 'remotecraft_web' },
  { key: 'cover', label: 'Copertura', glyph: '📡', cls: 'app-cover', feature: 'covercraft_web' },
  { key: 'quote', label: 'Preventivi', glyph: '💶', cls: 'app-quote', feature: 'quotecraft_web' },
]

/** Icona del file in base al tipo (stesse tinte delle app). */
function FileIcon({ name, mime }: { name: string; mime: string }) {
  const app = editorFor(name)
  const low = name.toLowerCase()
  const cls = app === 'wordcraft' ? 'app-word' : app === 'gridcraft' ? 'app-excel' : app === 'deckcraft' ? 'app-deck' : app === 'ideacraft' ? 'app-map' : app === 'pdfcraft' ? 'app-pdf' : ''
  const glyph = app === 'wordcraft' ? 'W' : app === 'gridcraft' ? 'X' : app === 'deckcraft' ? 'P' : app === 'ideacraft' ? '◎' : app === 'pdfcraft' ? 'PDF' : ''
  if (mime.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg)$/.test(low)) return <span className="file-icon generic">🖼️</span>
  if (!cls) return <span className="file-icon generic">{mime.startsWith('video/') ? '🎬' : mime.startsWith('audio/') ? '🎵' : /\.(zip|tar|gz)$/.test(low) ? '🗜️' : '📄'}</span>
  return <span className={`file-icon doc ${cls}`}><span className="file-icon-corner" /><b>{glyph}</b></span>
}

export default function Documents() {
  const windowActive = useWindowActive()
  const surface = useRef<HTMLDivElement>(null)
  const { me } = useAuth()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const folderId = params.get('folder') || ''
  const projectId = params.get('project') || ''
  const sharedView = params.get('shared') === '1'   // cartella automatica "Condivisi con me"
  const [files, setFiles] = useState<FileOut[]>([])
  const [projFiles, setProjFiles] = useState<FileOut[]>([])
  const [folders, setFolders] = useState<Folder[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [filter, setFilter] = useState('')
  const [err, setErr] = useState('')
  const [appsOpen, setAppsOpen] = useState(false)
  const [menu, setMenu] = useState<{ kind: 'file' | 'folder' | 'background'; id: string; x: number; y: number } | null>(null)
  const [folderDialog, setFolderDialog] = useState(false)
  useEffect(() => { if (!windowActive) setMenu(null) }, [windowActive])
  const [moveFor, setMoveFor] = useState<FileOut | null>(null)
  const [shareFor, setShareFor] = useState<FileOut | null>(null)
  const [histFor, setHistFor] = useState<FileOut | null>(null)
  const [dragOver, setDragOver] = useState('')
  const [selected, setSelected] = useState('')
  const [uploads, setUploads] = useState<{ id: number; name: string; pct: number; status: 'up' | 'done' | 'error'; err?: string }[]>([])
  const [osDrop, setOsDrop] = useState(false)   // trascinamento di file dal computer sopra la pagina
  const fileInput = useRef<HTMLInputElement>(null)
  const feats = me?.features || {}

  const load = () => Promise.all([
    get<FileOut[]>('/files').then(setFiles),
    get<Folder[]>('/folders').then(items => { setFolders(items); if (folderId && !items.some(folder => folder.id === folderId)) nav('/documents') }),
    get<Project[]>('/projects').then(setProjects).catch(() => {}),
  ]).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!projectId) { setProjFiles([]); return }
    get<FileOut[]>(`/files?project_id=${projectId}`).then(setProjFiles).catch((e) => setErr(e.message))
  }, [projectId])
  useEffect(() => { const close = () => setMenu(null); window.addEventListener('click', close); return () => window.removeEventListener('click', close) }, [])
  // aggiornamento in tempo reale: una condivisione ricevuta, un file creato in chat o un documento di reparto nuovo ricaricano l'elenco
  useLiveEvents((e) => { if (e.kind === 'files' || e.kind === 'sync') load() })
  // trascinamento di file dal computer: evidenzia la pagina e carica nella cartella corrente
  useEffect(() => {
    const target = surface.current
    if (!target) return
    let depth = 0
    const has = (e: DragEvent) => Array.from(e.dataTransfer?.types || []).includes('Files')
    const enter = (e: DragEvent) => { if (has(e)) { depth++; setOsDrop(true) } }
    const leave = (e: DragEvent) => { if (has(e)) { depth = Math.max(0, depth - 1); if (!depth) setOsDrop(false) } }
    const over = (e: DragEvent) => { if (has(e)) e.preventDefault() }
    const drop = (e: DragEvent) => { if (has(e)) { e.preventDefault(); depth = 0; setOsDrop(false); upload(e.dataTransfer!.files) } }
    target.addEventListener('dragenter', enter); target.addEventListener('dragleave', leave); target.addEventListener('dragover', over); target.addEventListener('drop', drop)
    return () => { target.removeEventListener('dragenter', enter); target.removeEventListener('dragleave', leave); target.removeEventListener('dragover', over); target.removeEventListener('drop', drop) }
  })

  // ---- navigazione ----
  const go = (p: { folder?: string; project?: string; shared?: boolean }) => { setSelected(''); setParams(p.folder ? { folder: p.folder } : p.project ? { project: p.project } : p.shared ? { shared: '1' } : {}) }
  const byId = useMemo(() => Object.fromEntries(folders.map((f) => [f.id, f])), [folders])
  const crumbs = useMemo(() => {
    const out: { label: string; folder?: string; project?: string; shared?: boolean }[] = [{ label: 'Documenti' }]
    if (projectId) out.push({ label: `📁 ${projects.find((p) => p.id === projectId)?.name ?? 'Progetto'}`, project: projectId })
    if (sharedView) out.push({ label: '🤝 Condivisi con me', shared: true })
    const chain: Folder[] = []
    let cur = byId[folderId]
    while (cur) { chain.unshift(cur); cur = cur.parent_id ? byId[cur.parent_id] : undefined as any }
    chain.forEach((f) => out.push({ label: f.name, folder: f.id }))
    return out
  }, [folderId, projectId, sharedView, byId, projects])
  const current = byId[folderId]

  // ---- contenuto della cartella corrente ----
  const q = filter.trim().toLowerCase()
  const mine = (f: FileOut) => f.owner_id === me?.user.id
  const isOrgFolder = (id: string | null | undefined) => { const k = id ? byId[id]?.kind : ''; return k === 'org' || k === 'dept' }
  const inOrg = (f: FileOut) => isOrgFolder(f.folder_id)
  const sharedWithMe = files.filter((f) => !mine(f) && !inOrg(f))
  const shownFolders = projectId || sharedView ? [] : folders.filter((f) => (f.parent_id || '') === folderId).filter((f) => !q || f.name.toLowerCase().includes(q))
  const shownFiles = (projectId ? projFiles : sharedView ? sharedWithMe : files.filter((f) => (mine(f) || inOrg(f)) && (f.folder_id || '') === folderId))
    .filter((f) => !q || f.filename.toLowerCase().includes(q))
  const searching = !!q && !projectId
  const searchHits = searching ? files.filter((f) => f.filename.toLowerCase().includes(q)) : []

  // ---- azioni ----
  /** Caricamento con avanzamento (XHR: fetch non espone il progresso in upload); pannello in basso a destra, una riga per file. */
  function uploadOne(f: File): Promise<void> {
    const id = Date.now() + Math.random()
    setUploads((u) => [...u, { id, name: f.name, pct: 0, status: 'up' }])
    const set = (patch: Partial<{ pct: number; status: 'up' | 'done' | 'error'; err: string }>) => setUploads((u) => u.map((x) => x.id === id ? { ...x, ...patch } : x))
    return new Promise((resolve) => {
      const fd = new FormData()
      fd.append('file', f)
      if (folderId) fd.append('folder_id', folderId)
      if (projectId) fd.append('project_id', projectId)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/files')
      Object.entries(HEADERS).forEach(([k, v]) => xhr.setRequestHeader(k, v))
      xhr.withCredentials = true
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) set({ pct: Math.round(100 * e.loaded / e.total) }) }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) { set({ pct: 100, status: 'done' }); setTimeout(() => setUploads((u) => u.filter((x) => x.id !== id)), 4000) }
        else { let msg = xhr.statusText; try { const j = JSON.parse(xhr.responseText); msg = typeof j.detail === 'string' ? j.detail : msg } catch {}; set({ status: 'error', err: msg }) }
        resolve()
      }
      xhr.onerror = () => { set({ status: 'error', err: 'rete non raggiungibile' }); resolve() }
      xhr.send(fd)
    })
  }
  async function upload(list: FileList | null) {
    if (!list || !list.length) return
    if (!canWriteHere) { setErr('In questa cartella carica solo l\'amministratore'); return }
    const files = Array.from(list)
    // tre alla volta: abbastanza per sfruttare la rete, senza aprire decine di connessioni
    let i = 0
    const worker = async () => { while (i < files.length) { const f = files[i++]; await uploadOne(f); load() } }
    await Promise.all([worker(), worker(), worker()])
    if (fileInput.current) fileInput.current.value = ''
    load()
  }
  async function newFolder(name: string) {
    await post('/folders', { name, parent_id: folderId || null })
    await load()
  }
  async function renameFolder(f: Folder) {
    const name = prompt('Nuovo nome della cartella', f.name)
    if (!name?.trim() || name.trim() === f.name) return
    try { await patch(`/folders/${f.id}`, { name: name.trim() }); load() } catch (e: any) { setErr(e.message) }
  }
  async function deleteFolder(f: Folder) {
    if (!confirm(`Eliminare la cartella "${f.name}"? I file che contiene tornano nella cartella superiore.`)) return
    try { await del(`/folders/${f.id}`); load() } catch (e: any) { setErr(e.message) }
  }
  async function renameFile(f: FileOut) {
    const ext = (f.filename.match(/(\.ideacraft)?\.[^.]+$/) || [''])[0]
    const typed = prompt('Nuovo nome del file', f.filename.replace(/(\.ideacraft)?\.[^.]+$/, ''))
    if (!typed?.trim()) return
    try { await patch(`/files/${f.id}`, { filename: typed.trim().replace(/(\.ideacraft)?\.[^.]+$/, '') + ext }); load() } catch (e: any) { setErr(e.message) }
  }
  async function removeFile(f: FileOut) {
    if (!confirm(`Eliminare "${f.filename}"?`)) return
    try { await del(`/files/${f.id}`); load() } catch (e: any) { setErr(e.message) }
  }
  async function moveFile(f: FileOut, target: string) {
    if ((f.folder_id || '') === target) return
    try { await patch(`/files/${f.id}`, { folder_id: target }); load() } catch (e: any) { setErr(e.message) }
  }
  async function moveFolder(f: Folder, target: string) {
    if (f.id === target || (f.parent_id || '') === target) return
    try { await patch(`/folders/${f.id}`, { parent_id: target }); load() } catch (e: any) { setErr(e.message) }
  }
  function openFile(f: FileOut) {
    if (canEdit(f.filename, feats)) nav(editorPath(f.id, f.filename))
    else window.open(f.url, '_blank')
  }
  async function launch(a: typeof APPS[number]) {
    setAppsOpen(false)
    if (a.key === 'mail') { nav('/mail'); return }
    if (a.key === 'remote') { nav('/remote'); return }
    if (a.key === 'cover') { nav('/cover'); return }
    if (a.key === 'quote') { nav('/quotes'); return }
    if (a.key === 'pdf') {
      try {
        const f = await post<FileOut>('/files/blank-pdf', { filename: 'documento.pdf', folder_id: folderId || null, project_id: projectId || null })
        nav(editorPath(f.id, f.filename))
      } catch (e: any) { setErr(e.message) }
      return
    }
    nav(newFilePath(a.app!, { folderId: folderId || null, projectId: projectId || null, name: a.name }))
  }

  // ---- drag & drop ----
  const onDragStart = (e: React.DragEvent, kind: 'file' | 'folder', id: string) => { e.dataTransfer.setData('text/familai', `${kind}:${id}`); e.dataTransfer.effectAllowed = 'move' }
  const onDropTo = (target: string) => (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragOver('')
    const d = e.dataTransfer.getData('text/familai'); if (!d) return
    const [kind, id] = d.split(':')
    if (kind === 'file') { const f = files.find((x) => x.id === id); if (f) moveFile(f, target) }
    else { const f = byId[id]; if (f) moveFolder(f, target) }
  }
  const dropProps = (target: string) => ({
    onDragOver: (e: React.DragEvent) => { if (e.dataTransfer.types.includes('text/familai')) { e.preventDefault(); e.stopPropagation(); setDragOver(target || 'root') } },
    onDragLeave: () => setDragOver(''),
    onDrop: onDropTo(target),
  })

  const apps = APPS.filter((a) => (feats as any)[a.feature])
  const projName = (id: string | null) => projects.find((p) => p.id === id)?.name
  const owner = (f: FileOut) => f.owner_id === me?.user.id
  const editable = (f: FileOut) => owner(f) || f.access === 'write'   // nella famiglia l'amministratore gestisce anche i file degli altri admin
  const canWriteHere = !current || current.can_write !== false
  const menuFile = menu?.kind === 'file' ? [...files, ...projFiles].find((f) => f.id === menu.id) : undefined
  const menuFolder = menu?.kind === 'folder' ? byId[menu.id] : undefined

  return (
    <div ref={surface} className={`page finder ${osDrop ? 'dropping' : ''}`} onClick={() => setSelected('')}
      onContextMenu={e => {
        if (projectId || sharedView || !canWriteHere || (e.target as HTMLElement).closest('.tile, button, input, .finder-bar, [role="dialog"]')) return
        e.preventDefault(); e.stopPropagation()
        setMenu({ kind: 'background', id: '', x: e.clientX, y: e.clientY })
      }}>
      {osDrop && <div className="drop-hint">📎 Rilascia per caricare in «{current?.name || (projectId ? (projects.find((p) => p.id === projectId)?.name ?? 'progetto') : 'Documenti')}»</div>}
      <div className="finder-bar row between wrap">
        <nav className="crumbs">
          {crumbs.map((c, i) => (
            <span key={i} className="row" style={{ gap: 4 }}>
              {i > 0 && <span className="muted">›</span>}
              {i < crumbs.length - 1
                ? <button className="crumb" {...dropProps(c.folder || '')} onClick={(e) => { e.stopPropagation(); go(c) }}>{c.label}</button>
                : <b className="crumb current">{c.label}</b>}
            </span>
          ))}
        </nav>
        <div className="row" style={{ gap: 6 }}>
          <input placeholder="Cerca…" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 200 }} />
          {!projectId && !sharedView && canWriteHere && <button className="ghost" onClick={(e) => { e.stopPropagation(); setFolderDialog(true) }}>📁 Nuova cartella</button>}
          {canWriteHere && <button className="ghost" onClick={(e) => { e.stopPropagation(); fileInput.current?.click() }}>📎 Carica</button>}
          {!canWriteHere && <span className="muted small" title="Documenti ufficiali del reparto: li carica l'amministratore; il modello li usa come base nelle tue chat">🔒 sola lettura · carica l'amministratore</span>}
          {me?.user && (me.user.quota_effective || 0) > 0 && (
            <span className={`storage-pill ${(me.user.storage_used || 0) >= 0.9 * (me.user.quota_effective || 1) ? 'warn' : ''}`} title="Spazio usato dai tuoi file (quota decisa dall'amministratore)">
              💾 {fmtBytes(me.user.storage_used || 0)} / {fmtBytes(me.user.quota_effective || 0)}
            </span>
          )}
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
        </div>
      </div>
      {err && <div className="error">{err}</div>}

      {searching ? (
        <>
          <p className="muted small">Risultati per «{filter}» in tutte le cartelle</p>
          <div className="finder-grid">
            {searchHits.map(renderFile)}
            {searchHits.length === 0 && <p className="muted small">Nessun file.</p>}
          </div>
        </>
      ) : (
        <div className="finder-grid" {...(!projectId && !sharedView && canWriteHere ? dropProps(current?.parent_id || '') : {})}>
          {/* cartella APP (solo alla radice) */}
          {!folderId && !projectId && !sharedView && apps.length > 0 && (
            <button className="tile folder-tile app-folder" onClick={(e) => { e.stopPropagation(); setAppsOpen(true) }} title="Applicazioni">
              <span className="ios-folder">
                {apps.slice(0, 9).map((a) => <span key={a.key} className={`mini-app ${a.cls}`}>{a.glyph}</span>)}
              </span>
              <span className="tile-name">APP</span>
            </button>
          )}
          {/* condivisi con me (radice): i file che altri hanno condiviso, raggruppati in una cartella automatica */}
          {!folderId && !projectId && !sharedView && sharedWithMe.length > 0 && (
            <button className="tile folder-tile" onClick={(e) => { e.stopPropagation(); go({ shared: true }) }} title="File che altri hanno condiviso con te">
              <span className="folder-icon shared"><span className="folder-tab" /></span>
              <span className="tile-name">Condivisi con me</span>
              <small className="muted">{sharedWithMe.length} file</small>
            </button>
          )}
          {/* progetti come cartelle automatiche (radice) */}
          {!folderId && !projectId && !sharedView && projects.map((p) => (
            <button key={p.id} className="tile folder-tile" onClick={(e) => { e.stopPropagation(); go({ project: p.id }) }} title={`Progetto ${p.name}`}>
              <span className="folder-icon project"><span className="folder-tab" /></span>
              <span className="tile-name">{p.name}</span>
              <small className="muted">progetto</small>
            </button>
          ))}
          {/* cartelle dell'utente */}
          {shownFolders.map((f) => (
            <button key={f.id} className={`tile folder-tile ${dragOver === f.id ? 'drop' : ''} ${f.kind ? `kind-${f.kind}` : ''}`} draggable={!f.system && f.kind !== 'conv' && f.can_write !== false} onDragStart={(e) => onDragStart(e, 'folder', f.id)} {...(f.can_write !== false ? dropProps(f.id) : {})}
              onClick={(e) => { e.stopPropagation(); go({ folder: f.id }) }} onContextMenu={(e) => { e.preventDefault(); setMenu({ kind: 'folder', id: f.id, x: e.clientX, y: e.clientY }) }}
              title={f.kind === 'org' ? 'Documenti condivisi della famiglia: li carica l\'amministratore e Emily può usarli come riferimento' : f.kind === 'chat' ? 'I file creati o caricati nelle chat, una sottocartella per chat: cio\' che metti li\' dentro e\' contesto di quella chat' : f.kind === 'msg' ? 'Foto e documenti ricevuti e inviati in 💬 Messaggi, una sottocartella per conversazione' : f.kind === 'dept' ? `Famiglia: documenti condivisi (sola lettura${f.can_write ? ', tu puoi caricare' : ''})` : f.name}>
              <span className={`folder-icon ${f.kind || ''}`} style={f.color ? { ['--folder' as any]: f.color } : undefined}><span className="folder-tab" />{KIND_GLYPH[f.kind || ''] && <span className="folder-glyph">{KIND_GLYPH[f.kind || '']}</span>}</span>
              <span className="tile-name">{f.name}</span>
              <small className="muted">{f.kind && KIND_LABEL[f.kind] ? `${KIND_LABEL[f.kind]} · ` : ''}{f.files + f.subfolders === 0 ? 'vuota' : `${f.files} file${f.subfolders ? ` · ${f.subfolders} cartelle` : ''}`}</small>
              <span className="tile-menu" onClick={(e) => { e.stopPropagation(); setMenu({ kind: 'folder', id: f.id, x: e.clientX, y: e.clientY }) }}>⋯</span>
            </button>
          ))}
          {/* file */}
          {shownFiles.map(renderFile)}
          {shownFolders.length === 0 && shownFiles.length === 0 && (folderId || projectId || sharedView) && <p className="muted small" style={{ gridColumn: '1 / -1' }}>{canWriteHere ? 'Cartella vuota. Trascina qui dei file o usa 📎 Carica.' : 'Cartella vuota: i documenti condivisi li carica l\'amministratore.'}</p>}
          {current?.kind === 'chat' && shownFolders.length === 0 && <p className="muted small" style={{ gridColumn: '1 / -1' }}>Qui compaiono, una cartella per chat, i file creati o caricati nelle conversazioni. Dentro la cartella di una chat puoi aggiungere documenti (anche in sottocartelle): il modello li usa come contesto di quella chat.</p>}
        </div>
      )}

      {/* menu contestuale */}
      {menu?.kind === 'background' && createPortal(
        <div className="finder-background-menu" role="menu" aria-label="Azioni della cartella" style={{ left: Math.max(8, Math.min(menu.x, window.innerWidth - 223)), top: Math.max(8, Math.min(menu.y, window.innerHeight - 105)) }}
          onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') setMenu(null) }}>
          <button role="menuitem" autoFocus onClick={() => { setMenu(null); setFolderDialog(true) }}><span aria-hidden="true">▰</span> Nuova cartella</button>
          <div className="folder-menu-divider" />
          <button role="menuitem" onClick={() => { setMenu(null); fileInput.current?.click() }}><span aria-hidden="true">↑</span> Carica file…</button>
        </div>, document.body)}
      {folderDialog && <FolderDialog parentName={current?.name || 'Documenti'} onCreate={newFolder} onClose={() => setFolderDialog(false)} />}
      {menu && menuFile && (
        <ul className="ctx-menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          <li onClick={() => { setMenu(null); openFile(menuFile) }}>{canEdit(menuFile.filename, feats) ? '✏️ Apri nell\'editor' : '👁 Apri'}</li>
          <li onClick={() => { setMenu(null); window.open(menuFile.url, '_blank') }}>⬇ Scarica</li>
          <li onClick={() => { setMenu(null); setHistFor(menuFile) }}>🕘 Cronologia…</li>
          {editable(menuFile) && <li onClick={() => { setMenu(null); renameFile(menuFile) }}>✎ Rinomina</li>}
          {editable(menuFile) && <li onClick={() => { setMenu(null); setMoveFor(menuFile) }}>📁 Sposta in…</li>}
          {owner(menuFile) && !inOrg(menuFile) && <li onClick={() => { setMenu(null); setShareFor(menuFile) }}>🔗 Condividi…</li>}
          {menuFile.conversation_id && <li onClick={() => { setMenu(null); nav(`/c/${menuFile.conversation_id}`) }}>💬 Vai alla chat</li>}
          {editable(menuFile) && <li className="danger" onClick={() => { setMenu(null); removeFile(menuFile) }}>🗑 Elimina</li>}
        </ul>
      )}
      {menu && menuFolder && (
        <ul className="ctx-menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          <li onClick={() => { setMenu(null); go({ folder: menuFolder.id }) }}>📂 Apri</li>
          {menuFolder.conversation_id && <li onClick={() => { setMenu(null); nav(`/c/${menuFolder.conversation_id}`) }}>💬 Vai alla chat</li>}
          {!menuFolder.system && menuFolder.can_write !== false && <li onClick={() => { setMenu(null); renameFolder(menuFolder) }}>✎ Rinomina{menuFolder.kind === 'conv' ? ' (anche la chat)' : ''}</li>}
          {!menuFolder.system && menuFolder.can_write !== false && <li className="danger" onClick={() => { setMenu(null); deleteFolder(menuFolder) }}>🗑 Elimina cartella</li>}
          {menuFolder.system && <li className="muted">cartella di sistema</li>}
        </ul>
      )}

      {shareFor && <ShareDialog type="file" id={shareFor.id} onClose={() => { setShareFor(null); load() }} />}
      {histFor && <VersionHistory file={histFor} canWrite={owner(histFor) || histFor.access === 'write'} onClose={() => { setHistFor(null); load() }} />}

      {/* "Sposta in…" */}
      {moveFor && (
        <div className="modal-bg" onClick={() => setMoveFor(null)}>
          <div className="modal stack" onClick={(e) => e.stopPropagation()}>
            <h3>Sposta «{moveFor.filename}»</h3>
            <ul className="move-list">
              <li onClick={() => { moveFile(moveFor, ''); setMoveFor(null) }}>📁 Documenti (radice)</li>
              {folders.filter((f) => f.can_write !== false).map((f) => <li key={f.id} onClick={() => { moveFile(moveFor, f.id); setMoveFor(null) }} style={{ paddingLeft: 12 + 14 * depth(f, byId) }}>{KIND_GLYPH[f.kind || ''] || '📁'} {f.name}{f.kind === 'dept' || f.kind === 'org' ? <small className="muted"> · famiglia</small> : ''}</li>)}
            </ul>
            <button className="ghost" onClick={() => setMoveFor(null)}>Annulla</button>
          </div>
        </div>
      )}

      {/* caricamenti in corso */}
      {uploads.length > 0 && (
        <div className="upload-panel" onClick={(e) => e.stopPropagation()}>
          <div className="row between"><b>📤 Caricamento {uploads.filter((u) => u.status === 'done').length}/{uploads.length}</b><button className="icon" onClick={() => setUploads((u) => u.filter((x) => x.status === 'up'))} title="Nascondi i completati">✕</button></div>
          {uploads.map((u) => (
            <div key={u.id} className={`upload-row ${u.status}`} title={u.err || u.name}>
              <span className="upload-name">{u.status === 'done' ? '✅ ' : u.status === 'error' ? '⚠️ ' : ''}{u.name}</span>
              <small className="muted">{u.status === 'error' ? (u.err || 'errore') : u.status === 'done' ? 'fatto' : `${u.pct}%`}</small>
              <div className="upload-bar"><span style={{ width: `${u.status === 'error' ? 100 : u.pct}%` }} /></div>
            </div>
          ))}
        </div>
      )}

      {/* cartella APP aperta, stile iOS */}
      {appsOpen && (
        <div className="ios-overlay" onClick={() => setAppsOpen(false)}>
          <div className="ios-panel" onClick={(e) => e.stopPropagation()}>
            <h3>APP</h3>
            <div className="ios-apps">
              {apps.map((a) => (
                <button key={a.key} className="ios-app" onClick={() => launch(a)} title={a.app ? APP_LABEL[a.app] : a.key === 'remote' ? 'RemoteCraft' : a.key === 'cover' ? 'CoverCraft' : a.key === 'quote' ? 'QuoteCraft' : 'MailCraft'}>
                  <span className={`app-icon ${a.cls}`}>{a.glyph}</span>
                  <span>{a.label}</span>
                </button>
              ))}
            </div>
            <p className="muted small">{folderId ? `I nuovi file nascono in «${current?.name}».` : projectId ? 'I nuovi file nascono in questo progetto.' : 'I nuovi file nascono in Documenti.'} App open source, niente installazioni, i file restano qui.</p>
          </div>
        </div>
      )}
    </div>
  )

  function renderFile(f: FileOut) {
    return (
      <button key={f.id} className={`tile file-tile ${selected === f.id ? 'selected' : ''}`} draggable={editable(f)} onDragStart={(e) => onDragStart(e, 'file', f.id)}
        onClick={(e) => { e.stopPropagation(); if (selected === f.id) openFile(f); else setSelected(f.id) }} onDoubleClick={() => openFile(f)}
        onContextMenu={(e) => { e.preventDefault(); setMenu({ kind: 'file', id: f.id, x: e.clientX, y: e.clientY }) }} title={`${f.filename} · ${(f.size / 1024).toFixed(0)} KB`}>
        <FileIcon name={f.filename} mime={f.mime} />
        <span className="tile-name">{f.filename}</span>
        <small className="muted">{new Date(f.created_at).toLocaleDateString()}{f.project_id && !projectId && projName(f.project_id) ? ` · ${projName(f.project_id)}` : ''}{!owner(f) ? ` · da ${f.owner_name || 'altri'}${f.access === 'write' ? ' (modifica)' : ''}` : ''}</small>
        <span className="tile-menu" onClick={(e) => { e.stopPropagation(); setMenu({ kind: 'file', id: f.id, x: e.clientX, y: e.clientY }) }}>⋯</span>
      </button>
    )
  }
}

function depth(f: Folder, byId: Record<string, Folder>): number {
  let d = 0; let cur = f
  while (cur.parent_id && byId[cur.parent_id]) { d++; cur = byId[cur.parent_id] }
  return d
}
