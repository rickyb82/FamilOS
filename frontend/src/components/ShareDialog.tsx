import { useEffect, useRef, useState } from 'react'
import { del, get, post } from '../lib/api'
import { useAuth } from '../lib/auth'

interface Member { id: string; display_name: string; avatar: string; role: string; email: string; username: string }
interface Share { id: string; target_user_id: string | null; permission: string }

/**
 * Condivisione: si scrive il nome o l'email della persona e i suggerimenti arrivano in tempo reale (GET /shares/members?q=),
 * come in un client di posta; sotto, l'elenco di chi ha gia' accesso con il permesso modificabile.
 */
export default function ShareDialog({ type, id, onClose }: { type: 'conversation' | 'project' | 'file'; id: string; onClose: () => void }) {
  const { me } = useAuth()
  const org = !!me?.features?.org_mode
  const [shares, setShares] = useState<Share[]>([])
  const [people, setPeople] = useState<Record<string, Member>>({})
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Member[]>([])
  const [sel, setSel] = useState(0)
  const [perm, setPerm] = useState<'read' | 'write'>('read')
  const [err, setErr] = useState('')
  const timer = useRef<number>()

  async function load() {
    const list = await get<Share[]>(`/shares?resource_type=${type}&resource_id=${id}`)
    setShares(list)
    const missing = list.filter((s) => s.target_user_id && !people[s.target_user_id])
    if (missing.length) {
      const all = await get<Member[]>('/shares/members')
      setPeople((p) => ({ ...p, ...Object.fromEntries(all.map((m) => [m.id, m])) }))
    }
  }
  useEffect(() => { load() }, [id])

  // ricerca con piccolo debounce: ogni lettera interroga il server
  useEffect(() => {
    window.clearTimeout(timer.current)
    if (!q.trim()) { setHits([]); return }
    timer.current = window.setTimeout(() => {
      get<Member[]>(`/shares/members?q=${encodeURIComponent(q.trim())}`).then((r) => { setHits(r.filter((m) => !shares.some((s) => s.target_user_id === m.id))); setSel(0) }).catch(() => setHits([]))
    }, 150)
    return () => window.clearTimeout(timer.current)
  }, [q, shares])

  async function share(target: string | null, permission: string) {
    setErr('')
    try {
      await post('/shares', { resource_type: type, resource_id: id, target_user_id: target, permission })
      setQ(''); setHits([])
      await load()
    } catch (e: any) { setErr(e.message) }
  }
  async function unshare(s: Share) { await del(`/shares/${s.id}`); await load() }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, hits.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
    else if (e.key === 'Enter' && hits[sel]) { e.preventDefault(); share(hits[sel].id, perm) }
    else if (e.key === 'Escape') { setQ(''); setHits([]) }
  }
  const everyone = shares.find((s) => s.target_user_id === null)

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal share-modal" onClick={(e) => e.stopPropagation()}>
        <div className="share-head">
          <h3>Condividi {type === 'file' ? 'il file' : type === 'project' ? 'il progetto' : 'la chat'}</h3>
          <button className="icon" onClick={onClose} title="Chiudi">✕</button>
        </div>
        {err && <div className="error">{err}</div>}
        <label className="share-label">Aggiungi persone</label>
        <div className="share-search">
          <div className="share-input">
            <span className="share-icon">🔍</span>
            <input autoFocus placeholder="Scrivi il nome o l'email…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
            <select className="perm" value={perm} onChange={(e) => setPerm(e.target.value as any)} title="Permesso che riceverà">
              <option value="read">Può vedere</option>
              <option value="write">Può modificare</option>
            </select>
          </div>
          {hits.length > 0 && (
            <ul className="suggest static">
              {hits.map((m, i) => (
                <li key={m.id} className={i === sel ? 'active' : ''} onMouseEnter={() => setSel(i)} onMouseDown={(e) => { e.preventDefault(); share(m.id, perm) }}>
                  <span className="share-avatar">{m.avatar}</span>
                  <span className="share-who"><b>{m.display_name}</b><small className="muted">{m.email || m.username}{m.role === 'child' ? ' · bambino/a' : ''}</small></span>
                  <span className="share-add">Aggiungi ↵</span>
                </li>
              ))}
            </ul>
          )}
          {q.trim() && hits.length === 0 && <p className="muted small" style={{ margin: '6px 2px 0' }}>Nessuno corrisponde a «{q}».</p>}
          {!q.trim() && <p className="muted small" style={{ margin: '6px 2px 0' }}>I suggerimenti compaiono mentre scrivi: Invio aggiunge il primo, frecce per scegliere.</p>}
        </div>
        <label className="share-label">Chi ha accesso</label>
        <ul className="share-people">
          <li>
            <span className="share-avatar org">{org ? '🏢' : '👪'}</span>
            <span className="share-who"><b>{org ? 'Tutta la famiglia' : 'Tutta la famiglia'}</b><small className="muted">{everyone ? (everyone.permission === 'write' ? 'tutti possono modificare' : 'tutti possono vedere') : 'solo le persone qui sotto'}</small></span>
            <select className="perm" value={everyone?.permission ?? 'none'} onChange={(e) => (e.target.value === 'none' ? everyone && unshare(everyone) : share(null, e.target.value))}>
              <option value="none">Nessuno</option>
              <option value="read">Può vedere</option>
              <option value="write">Può modificare</option>
            </select>
          </li>
          <li>
            <span className="share-avatar me">{me?.user.avatar}</span>
            <span className="share-who"><b>{me?.user.display_name} (tu)</b><small className="muted">proprietario</small></span>
            <span className="perm-static">Proprietario</span>
          </li>
          {shares.filter((s) => s.target_user_id).map((s) => (
            <li key={s.id}>
              <span className="share-avatar">{s.target_user_id && people[s.target_user_id]?.avatar}</span>
              <span className="share-who"><b>{s.target_user_id && people[s.target_user_id]?.display_name || '…'}</b><small className="muted">{s.target_user_id && (people[s.target_user_id]?.email || people[s.target_user_id]?.username)}</small></span>
              <select className="perm" value={s.permission} onChange={(e) => share(s.target_user_id, e.target.value)}>
                <option value="read">Può vedere</option>
                <option value="write">Può modificare</option>
              </select>
              <button className="icon" title="Togli l'accesso" onClick={() => unshare(s)}>✕</button>
            </li>
          ))}
        </ul>
        <div className="share-foot">
          <small className="muted">{shares.length === 0 ? 'Per ora lo vedi solo tu.' : `${shares.filter((s) => s.target_user_id).length} person${shares.filter((s) => s.target_user_id).length === 1 ? 'a' : 'e'}${everyone ? ` + tutt${org ? 'a la famiglia' : 'a la famiglia'}` : ''}`}</small>
          <button className="primary" onClick={onClose}>Fatto</button>
        </div>
      </div>
    </div>
  )
}
