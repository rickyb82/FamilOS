import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { canEdit, editorPath, newFilePath } from '../lib/artcraft'
import { del, get, patch, post, type Conversation, type FileOut, type Project } from '../lib/api'
import { useAuth } from '../lib/auth'
import ShareDialog from '../components/ShareDialog'

export default function Projects() {
  const { id } = useParams()
  const nav = useNavigate()
  const { me } = useAuth()
  const [projects, setProjects] = useState<Project[]>([])
  const [proj, setProj] = useState<Project | null>(null)
  const [files, setFiles] = useState<FileOut[]>([])
  const [convs, setConvs] = useState<Conversation[]>([])
  const [form, setForm] = useState({ name: '', description: '', instructions: '', icon: '📁' })
  const [share, setShare] = useState(false)
  const [err, setErr] = useState('')
  const [rag, setRag] = useState<{ embeddings: boolean; mode: string } | null>(null)
  const [reindexing, setReindexing] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const loadFiles = (pid: string) => get<FileOut[]>(`/files?project_id=${pid}`).then(setFiles)

  const loadList = () => get<Project[]>('/projects').then(setProjects)
  useEffect(() => {
    loadList()
    get('/files/rag-status').then(setRag).catch(() => {})
  }, [])
  useEffect(() => {
    if (!id) {
      setProj(null)
      return
    }
    get<Project>(`/projects/${id}`).then((p) => {
      setProj(p)
      setForm({ name: p.name, description: p.description, instructions: p.instructions, icon: p.icon })
    })
    loadFiles(id)
    get<Conversation[]>(`/chat/conversations?project_id=${id}`).then(setConvs)
  }, [id])

  useEffect(() => {
    if (!id || !files.some((f) => f.index_status === 'pending')) return
    const t = setTimeout(() => loadFiles(id), 3000)
    return () => clearTimeout(t)
  }, [files, id])

  async function reindex() {
    if (!proj) return
    setReindexing(true)
    try {
      const r = await post(`/projects/${proj.id}/reindex`)
      setErr('')
      await loadFiles(proj.id)
      alert(`Indicizzati ${r.files} file, ${r.chunks} frammenti (${r.embeddings ? 'ricerca semantica + parole chiave' : 'solo parole chiave'})`)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setReindexing(false)
    }
  }

  async function create() {
    const name = prompt('Nome del progetto')
    if (!name) return
    try {
      const p = await post<Project>('/projects', { name })
      await loadList()
      nav(`/projects/${p.id}`)
    } catch (e: any) {
      setErr(e.message)
    }
  }
  async function save() {
    if (!proj) return
    const p = await patch<Project>(`/projects/${proj.id}`, form)
    setProj(p)
    await loadList()
  }
  async function remove() {
    if (!proj || !confirm('Eliminare il progetto? Le chat resteranno ma senza progetto.')) return
    await del(`/projects/${proj.id}`)
    await loadList()
    nav('/projects')
  }
  async function upload(list: FileList | null) {
    if (!list || !proj) return
    for (const f of Array.from(list)) {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('project_id', proj.id)
      try {
        await post('/files', fd)
      } catch (e: any) {
        setErr(e.message)
      }
    }
    await loadFiles(proj.id)
  }
  async function newChat() {
    if (!proj) return
    const c = await post<Conversation>('/chat/conversations', { project_id: proj.id })
    window.dispatchEvent(new Event('familai:conversations'))
    nav(`/c/${c.id}`)
  }
  const isOwner = proj && proj.owner_id === me?.user.id

  return (
    <div className="page projects">
      <aside className="subnav">
        <div className="row between">
          <h2>Progetti</h2>
          <button className="primary" onClick={create}>＋</button>
        </div>
        {projects.map((p) => (
          <button key={p.id} className={`subnav-item ${p.id === id ? 'active' : ''}`} onClick={() => nav(`/projects/${p.id}`)}>
            {p.icon} {p.name} {p.shared && <small className="muted">· {p.owner_name}</small>}
          </button>
        ))}
        {projects.length === 0 && <p className="muted">Un progetto raccoglie chat, file e istruzioni su un tema (es. “Ricerca sui dinosauri”, “Ristrutturazione casa”).</p>}
      </aside>
      <section className="content">
        {err && <div className="error">{err}</div>}
        {!proj ? (
          <div className="empty"><div className="big">📁</div><p className="muted">Seleziona o crea un progetto</p></div>
        ) : (
          <div className="stack">
            <div className="row between">
              <h2>{proj.icon} {proj.name}</h2>
              <div className="row">
                <button onClick={newChat}>＋ Chat nel progetto</button>
                {isOwner && <button className="ghost" onClick={() => setShare(true)}>🔗 Condividi</button>}
                {isOwner && <button className="ghost danger" onClick={remove}>Elimina</button>}
              </div>
            </div>
            <label>Icona <input value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })} maxLength={4} style={{ width: 60 }} /></label>
            <label>Nome <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Descrizione <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
            <label>Istruzioni per l'assistente (valgono per tutte le chat del progetto)
              <textarea rows={5} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} placeholder="Es. Stiamo preparando una ricerca di scuola sui vulcani per la quarta elementare. Usa un linguaggio semplice e cita le fonti." />
            </label>
            <button onClick={save} disabled={!isOwner && !proj.shared}>Salva</button>

            <h3>File del progetto</h3>
            <p className="muted small">I documenti vengono indicizzati per la ricerca ({rag?.mode ?? '…'}): l'assistente recupera automaticamente i passaggi pertinenti alle domande e può cercare con lo strumento “Ricerca nei documenti”.</p>
            <div className="row">
              <button className="ghost" onClick={() => fileInput.current?.click()}>📎 Carica file</button>
              <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
              {files.length > 0 && <button className="ghost" onClick={reindex} disabled={reindexing}>{reindexing ? 'Indicizzazione…' : '🔄 Reindicizza tutto'}</button>}
              {me?.features?.wordcraft_web && <button className="ghost" onClick={() => nav(newFilePath('wordcraft', { projectId: proj.id }))}>📝 Nuovo documento</button>}
              {me?.features?.gridcraft_web && <button className="ghost" onClick={() => nav(newFilePath('gridcraft', { projectId: proj.id }))}>📊 Nuovo foglio</button>}
              {me?.features?.deckcraft_web && <button className="ghost" onClick={() => nav(newFilePath('deckcraft', { projectId: proj.id }))}>📽️ Nuova presentazione</button>}
              {me?.features?.ideacraft_web && <button className="ghost" onClick={() => nav(newFilePath('ideacraft', { projectId: proj.id }))}>🧠 Nuova mappa</button>}
            </div>
            <ul className="file-list">
              {files.map((f) => (
                <li key={f.id}>
                  <a href={f.url} target="_blank" rel="noreferrer">{f.filename}</a> <small className="muted">{(f.size / 1024).toFixed(0)} KB</small>{' '}
                  {canEdit(f.filename, me?.features) && <Link to={editorPath(f.id, f.filename)} className="small" title="Apri nell'editor">✏️ Modifica</Link>}{' '}
                  {f.index_status === 'indexed' && <span className="badge ok">📚 {f.chunk_count} parti</span>}
                  {f.index_status === 'pending' && <span className="badge">⏳ indicizzazione…</span>}
                  {f.index_status === 'failed' && <span className="badge err">⚠️ indice fallito</span>}
                </li>
              ))}
            </ul>

            <h3>Chat del progetto</h3>
            <ul className="file-list">
              {convs.map((c) => (
                <li key={c.id}><a href={`/c/${c.id}`} onClick={(e) => { e.preventDefault(); nav(`/c/${c.id}`) }}>{c.title}</a>{c.shared && <small className="muted"> · {c.owner_name}</small>}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
      {share && proj && <ShareDialog type="project" id={proj.id} onClose={() => setShare(false)} />}
    </div>
  )
}
