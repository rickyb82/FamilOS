import { useLiveEvents } from '../lib/events'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { get, post, type FileOut } from '../lib/api'
import { editorFor, editorPath, newFilePath, type ArtApp } from '../lib/artcraft'
import { PRODUCTIVITY_APPS } from '../lib/desktop'
import Mail from './Mail'
import '../productivity.css'

export default function Productivity() {
  const { me } = useAuth()
  const { appId } = useParams()
  const navigate = useNavigate()
  const [files, setFiles] = useState<FileOut[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const app = PRODUCTIVITY_APPS.find(a => a.id === appId)
  const allowed = (a: typeof PRODUCTIVITY_APPS[number]) => me?.user.role === 'admin' || !!me?.features?.apps?.includes(a.id)
  const active = (a: typeof PRODUCTIVITY_APPS[number]) => !!(me?.features as any)?.[a.feature!]
  useEffect(() => {
    if (!app || app.id === 'mailcraft') return
    let cancelled = false
    get<FileOut[]>('/files').then(items => { if (!cancelled) setFiles(items) }).catch(e => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [appId])

  useLiveEvents(e => { if ((e.kind === 'files' || e.kind === 'sync') && app && app.id !== 'mailcraft') get<FileOut[]>('/files').then(setFiles).catch(() => {}) })

  async function create() {
    if (!app || busy) return
    setBusy(true); setError('')
    try {
      if (app.id === 'pdfcraft') {
        const file = await post<FileOut>('/files/blank-pdf', { filename: 'documento.pdf' })
        navigate(editorPath(file.id, file.filename))
      } else navigate(newFilePath(app.id as ArtApp))
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  if (!appId) return <div className="page productivity-library"><p className="productivity-eyebrow">LA TUA SUITE</p><h1>Spazio alle cose che fai.</h1><p className="muted">Documenti, fogli, presentazioni, mappe e posta. Ogni app nella sua finestra.</p>
    <div className="productivity-cards">{PRODUCTIVITY_APPS.filter(allowed).map(a => <button key={a.id} onClick={() => navigate(a.path)}><span className={`os-icon ${a.color}`}>{a.icon}</span><h2>{a.title}</h2><p>{a.description}</p><small className={active(a) ? 'productivity-connected' : ''}>{active(a) ? 'Collegata' : 'Da attivare sul server'} <span>↗</span></small></button>)}</div>
    {!!me?.features?.messaggi && <button className="productivity-messages" onClick={() => navigate('/messages')}>☏ <span><b>Messaggi</b><small>Conversazioni e chiamate con la famiglia.</small></span><span>↗</span></button>}
  </div>
  if (!app || !allowed(app)) return <div className="page"><h2>App non disponibile</h2><p>Questa app non è abilitata per il tuo profilo.</p><button onClick={() => navigate('/apps')}>Torna alla suite</button></div>
  if (!active(app)) return <div className="page productivity-start"><span className={`os-icon ${app.color}`}>{app.icon}</span><h1>{app.title}</h1><p>{app.description}</p><div className="productivity-setup"><h2>Pronta da collegare</h2><p>L'integrazione è inclusa in FamilOS. L'app deve essere attivata sul server prima di aprire i documenti.</p>{me?.user.role === 'admin' ? <details><summary>Attivazione sul server</summary><p>Dal progetto FamilOS:</p><code>./familai.sh {app.id} up</code><p>Per l'intera suite: <code>./familos.sh productivity up</code>. Poi ricarica FamilOS.</p></details> : <p>Chiedi a chi gestisce FamilOS di attivarla.</p>}<a href={`https://github.com/rickyb82/${app.id}`} target="_blank" rel="noreferrer">Repository {app.title} ↗</a></div></div>
  if (app.id === 'mailcraft') return <Mail />
  const recent = files.filter(f => editorFor(f.filename) === app.id).slice(0, 12)
  return <div className="page productivity-start"><span className={`os-icon ${app.color}`}>{app.icon}</span><p className="productivity-eyebrow">IL TUO SPAZIO DI LAVORO</p><h1>{app.title}</h1><p>{app.description}</p>
    <div className="productivity-actions"><button className="primary" disabled={busy} onClick={create}>＋ {busy ? 'Apertura…' : app.id === 'gridcraft' ? 'Nuovo foglio' : app.id === 'deckcraft' ? 'Nuova presentazione' : app.id === 'ideacraft' ? 'Nuova mappa' : app.id === 'pdfcraft' ? 'Nuovo PDF' : 'Nuovo documento'}</button><button onClick={() => navigate('/documents')}>Apri da File…</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="productivity-recents"><h2>Riprendi un documento</h2>{recent.length ? recent.map(f => <button key={f.id} onClick={() => navigate(editorPath(f.id, f.filename))}><span className={`os-icon ${app.color}`}>{app.icon}</span><span>{f.filename}<small>{new Date(f.created_at).toLocaleDateString('it-IT')}</small></span><span>↗</span></button>) : <p className="muted">I file di {app.title} compariranno qui. Puoi iniziare da un nuovo documento o caricarne uno da File.</p>}</section>
  </div>
}
