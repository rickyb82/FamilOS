import { useLiveEvents } from '../lib/events'
import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { WindowCommands } from '../lib/desktop'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { get, patch, type FileOut } from '../lib/api'
import { useAuth } from '../lib/auth'
import { APP_ICON, APP_LABEL, UI_LANG, editorFor, type ArtApp } from '../lib/artcraft'
import VersionHistory from '../components/VersionHistory'
import EditorHint, { HintToggle } from '../components/EditorHint'

/**
 * Editor a tutta pagina: WordCraft (documenti), GridCraft (fogli), PdfCraft (PDF), DeckCraft (presentazioni) o IdeaCraft (mappe), app open source in WebAssembly in un iframe.
 * ?file=<id> apre quel file del server (l'app la decide l'estensione); con &new=1&app=... e' un file nuovo (l'id lo ha
 * scelto il browser). File > Salva nell'editor spedisce il file a /api/files/wordcraft-save, che lo crea o lo sovrascrive.
 */
export default function Editor() {
  const windowCommands = useContext(WindowCommands)
  const { me } = useAuth()
  const [params] = useSearchParams()
  const nav = useNavigate()
  const fileId = params.get('file') || ''
  // Keep the initial load mode stable when promoting the first saved document.
  // Changing the URL must not reload the iframe and discard edits made during save.
  const isNew = useRef(params.get('new') === '1').current
  const promoted = useRef(false)
  const [hasSaved, setHasSaved] = useState(false)
  const [file, setFile] = useState<FileOut | null>(null)
  const [err, setErr] = useState('')
  const [canWrite, setCanWrite] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [customName, setCustomName] = useState<string>('')
  const [history, setHistory] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)   // nome scelto qui (documento nuovo non ancora salvato)
  const loadStamp = useMemo(() => Date.now(), [fileId, reloadKey])
  const revision = useRef('new')
  const editSession = useRef(crypto.randomUUID().replace(/-/g, ''))
  const name = file?.filename || customName || params.get('name') || ''

  async function rename() {
    if (!canWrite) return
    const current = name || 'documento'
    const typed = prompt('Nome del file (l\'estensione resta quella attuale)', current.replace(/(\.ideacraft)?\.[^.]+$/, ''))
    if (!typed || !typed.trim()) return
    const ext = (current.match(/(\.ideacraft)?\.[^.]+$/) || [''])[0]
    const next = typed.trim().replace(/(\.ideacraft)?\.[^.]+$/, '') + ext
    try {
      const f = await patch<FileOut>(`/files/${fileId}`, { filename: next })
      setFile(f)
      window.dispatchEvent(new Event('familai:conversations'))
    } catch (e: any) {
      if (isNew && (e.status === 404 || String(e.message || '').includes('non trovat'))) {
        // non ancora salvato: il nome vale dal primo salvataggio (l'editor si ricarica)
        setCustomName(next)
      } else setErr(`Rinomina non riuscita: ${e.message}`)
    }
  }
  const app: ArtApp = (params.get('app') as ArtApp) || editorFor(name) || 'wordcraft'

  useEffect(() => {
    if (isNew || !fileId) return
    setLoaded(false)
    setFile(null)
    get<FileOut>(`/files/${fileId}/metadata`).then((f) => {
        setFile(f)
        revision.current = f.sha256 || 'new'
        setCanWrite(f.owner_id === me?.user.id || f.access === 'write')
    }).catch((e) => { setErr(e.message); setCanWrite(false) }).finally(() => setLoaded(true))
    // anche un file non in elenco (es. di un progetto condiviso) si apre: il download controlla i permessi
  }, [fileId, isNew, me?.user.id])
  const ready = isNew || loaded
  const [dirty, setDirty] = useState(false)
  const [saved, setSaved] = useState('')
  const frame = useRef<HTMLIFrameElement>(null)
  async function markSaved() {
    if (!isNew || promoted.current) return
    try {
      await get<FileOut>(`/files/${fileId}/metadata`)
      promoted.current = true
      setHasSaved(true)
      const next = new URLSearchParams(params)
      next.delete('new')
      nav(`/editor?${next}`, { replace: true })
    } catch { /* an export can finish before the source document is saved */ }
  }

  useLiveEvents(event => { if (isNew && !promoted.current && (event.kind === 'sync' || event.kind === 'files')) void markSaved() })

  // PdfCraft parla con la pagina che lo incornicia via postMessage (host=parent): riceviamo i salvataggi e li
  // scriviamo sul server, come fanno WordCraft/GridCraft con ?save=.
  useEffect(() => {
    const onMsg = async (ev: MessageEvent) => {
      if (ev.origin !== window.location.origin || ev.source !== frame.current?.contentWindow) return
      const d = ev.data || {}
      // WordCraft/GridCraft (patch FamilAI): esito di ogni salvataggio verso il server
      if (d.type === 'familai:saved') { window.dispatchEvent(new Event('familai:conversations')); setSaved(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })); setDirty(false); setErr(''); void markSaved(); return }
      if (d.type === 'familai:save_failed') { setErr(`Salvataggio di ${d.name || 'documento'} non riuscito: ${d.error || 'errore sconosciuto'}`); return }
      if (app !== 'pdfcraft') return
      if (d.type === 'pdfcraft:dirty') setDirty(!!d.dirty)
      else if (d.type === 'pdfcraft:failed') setErr(`PdfCraft non si è avviato: ${d.error || ''}`)
      else if (d.type === 'pdfcraft:save') {
        if (!canWrite) {
          const blob = new Blob([d.bytes], { type: 'application/pdf' })
          const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = d.name || 'documento.pdf'; a.click()
          return
        }
        const saveQ = new URLSearchParams({ file_id: fileId })
        const cid = params.get('conversation_id'); const pid = params.get('project_id'); const fo = params.get('folder_id')
        if (cid) saveQ.set('conversation_id', cid)
        if (pid) saveQ.set('project_id', pid)
        if (fo) saveQ.set('folder_id', fo)
        if (isNew && name) saveQ.set('filename', name)
        // PdfCraft ricava il nome dall'URL (?file=/files/<id>): senza estensione vale il nome vero del file
        const fname = d.name && /\.[a-z0-9]{1,5}$/i.test(d.name) ? d.name : (name || 'documento.pdf')
        try {
          const r = await fetch(`/api/files/wordcraft-save?${saveQ.toString()}`, { method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/octet-stream', 'X-Requested-With': 'FamilAI', 'X-Filename': encodeURIComponent(fname), 'If-Match': revision.current }, body: d.bytes })
          if (!r.ok) { const error = await r.json(); throw new Error(error.detail || `HTTP ${r.status}`) }
          const savedFile: FileOut = await r.json()
          if (savedFile.id === fileId) revision.current = savedFile.sha256 || revision.current
          if (savedFile.id === fileId) void markSaved()
          setDirty(false)
          setSaved(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
          setErr('')
        } catch (e: any) {
          setErr(`Salvataggio non riuscito: ${e.message}`)
        }
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [app, canWrite, fileId, name, params, isNew])

  const src = useMemo(() => {
    if (!fileId) return ''
    const saveQ = new URLSearchParams({ file_id: fileId })
    saveQ.set('edit_session', editSession.current)
    saveQ.set('base_revision', file?.sha256 || 'new')
    const cid = params.get('conversation_id'); const pid = params.get('project_id'); const fo = params.get('folder_id')
    if (cid) saveQ.set('conversation_id', cid)
    if (pid) saveQ.set('project_id', pid)
    if (fo) saveQ.set('folder_id', fo)
    if (isNew && name) saveQ.set('filename', name)
    const q = new URLSearchParams({ lang: UI_LANG })
    q.set('revision', file?.sha256 || 'new')
    if (app === 'pdfcraft') {
      // PdfCraft: parametri nativi (nessuna patch): ?file= apre dal server, host=parent manda i salvataggi alla pagina
      q.set('file', `/files/${fileId}?v=${loadStamp}`)
      q.set('host', 'parent')
      if (me?.user.display_name) q.set('author', me.user.display_name)
      return `/pdfcraft/?${q.toString()}`
    }
    if (!isNew) {
      q.set('open', `/files/${fileId}?v=${loadStamp}`)   // mai una copia dalla cache del browser
      q.set('name', name || 'documento')
    } else {
      q.set('name', name || 'documento')
      if (app === 'deckcraft') q.set('blank', '1')   // presentazione nuova: niente deck di esempio
    }
    if (canWrite) q.set('save', `/api/files/wordcraft-save?${saveQ.toString()}`)
    return `/${app}/?${q.toString()}`
  }, [fileId, isNew, name, canWrite, params, app, me?.user.display_name, loadStamp, file?.sha256])

  if (!me?.features?.[`${app}_web`]) {
    return (
      <div className="page">
        <h2>{APP_ICON[app]} Editor</h2>
        <p className="muted">L'editor {APP_LABEL[app]} non è attivo su questo server. Un amministratore può avviarlo con <code>./familai.sh {app} up</code>.</p>
      </div>
    )
  }
  if (!fileId) return <div className="page"><p className="error">Nessun documento indicato.</p><Link to="/documents">← Documenti</Link></div>

  return (
    <div className="editor-layout">
      <header className="chat-header">
        <div className="title" title={canWrite ? 'Clicca per rinominare il file' : undefined} style={canWrite ? { cursor: 'text' } : undefined} onClick={rename}>
          {APP_ICON[app]} {name || 'Documento'}{isNew && !file && !hasSaved && <small className="muted"> · nuovo</small>}
          <small className="muted"> · {APP_LABEL[app]}</small>
          {canWrite && <small className="muted"> ✏️</small>}
          {!canWrite && <small className="muted"> · sola lettura (Salva scarica una copia)</small>}
          {dirty && <small className="muted"> · modifiche non salvate</small>}
          {saved && !dirty && <small className="muted"> · ✓ salvato {saved}</small>}
        </div>
        <div className="row">
          <HintToggle id="editor" />
          {!isNew && file && <button className="ghost" onClick={() => setHistory(true)} title="Versioni precedenti del file">🕘 Cronologia</button>}
          {!isNew && <a className="ghost btn-link" href={`/files/${fileId}`} target="_blank" rel="noreferrer">⬇ Scarica</a>}
          <button className="ghost" onClick={() => windowCommands ? windowCommands.close() : nav('/documents')}>Chiudi</button>
        </div>
      </header>
      {err && <div className="error">{err}</div>}
      {history && file && <VersionHistory file={file} canWrite={canWrite} onClose={() => setHistory(false)} onRestored={(f) => { setFile(f); setHistory(false); setReloadKey((k) => k + 1) }} />}
      {ready ? (
        <iframe ref={frame} className="editor-frame" src={src} title={APP_LABEL[app]} allow="clipboard-read; clipboard-write"
          onError={() => setErr(`L'editor non si è caricato: il container ${app}-web è attivo?`)} />
      ) : <div className="editor-frame center muted">Caricamento…</div>}
      <EditorHint id="editor">
        Salva con <b>File › Salva</b> (o Ctrl/Cmd+S) dentro l'editor: il file viene aggiornato qui su Emily, non scaricato{app === 'wordcraft' && ' (e il salvataggio automatico resta attivo)'}. Per cambiare nome al file clicca sul titolo qui sopra.
        {app !== 'pdfcraft' && <><b> File › Esporta</b> (PDF, CSV…) crea un nuovo file accanto a questo.</>} Serve un browser con WebGPU o WebGL2.
      </EditorHint>
    </div>
  )
}
