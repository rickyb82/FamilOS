import { useEffect, useState } from 'react'
import { del, get, patch, post } from '../lib/api'

interface Item {
  id: string; text: string; category: string; scope: string; status: string; pinned: boolean; confidence: number
  created_at: string; used_count: number; author: string; subject: string | null; subject_user_id: string | null; source_conversation_id: string | null
}
interface Data { items: Item[]; counts: Record<string, number>; enabled: boolean; members: { id: string; name: string }[]; categories: string[] }

const CAT_IT: Record<string, string> = { famiglia: '👪 Famiglia', preferenze: '💡 Preferenze', scuola: '🎒 Scuola', casa: '🏠 Casa', lavoro: '💼 Lavoro', hobby: '🎨 Hobby', progetti: '🧩 Progetti', regole: '📏 Regole', altro: '📝 Altro' }

/** Memoria di famiglia: coda di approvazione ("firewall" umano), voci approvate, aggiunta manuale. */
export default function AdminMemory() {
  const [tab, setTab] = useState<'pending' | 'approved' | 'rejected'>('pending')
  const [data, setData] = useState<Data | null>(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [newText, setNewText] = useState('')
  const [newCat, setNewCat] = useState('altro')
  const [newScope, setNewScope] = useState('family')
  const [newSubject, setNewSubject] = useState('')

  async function load() {
    try {
      setData(await get(`/admin/memory?status_=${tab}&q=${encodeURIComponent(q)}`))
      setSel(new Set())
    } catch (e: any) { setErr(e.message) }
  }
  useEffect(() => { load() }, [tab])

  const run = async (fn: () => Promise<any>, ok = 'Fatto') => {
    setErr(''); setMsg('')
    try { await fn(); setMsg(ok); await load() } catch (e: any) { setErr(e.message) }
  }
  const act = (id: string, action: string) => run(() => post(`/admin/memory/${id}/${action}`), '')
  const bulk = (action: string) => run(() => post(`/admin/memory/bulk/${action}`, { ids: Array.from(sel) }), `${sel.size} voci: ${action}`)
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  if (!data) return <div className="muted">Carico…</div>
  return (
    <div className="stack">
      {!data.enabled && <div className="error">La memoria di famiglia è disattivata (Provider AI → Altre impostazioni → Memoria).</div>}
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
      <p className="muted">Fatti durevoli estratti dalle chat (ogni pochi messaggi, con un modello economico) e filtrati: niente salute, finanze, credenziali, politica/religione, cose temporanee. Quello che passa il filtro finisce qui <b>in attesa della tua approvazione</b>; solo le voci approvate vengono usate nelle risposte, scegliendo le più pertinenti a ogni messaggio. Le voci "fissate" sono sempre presenti.</p>

      <div className="tabs small">
        {(['pending', 'approved', 'rejected'] as const).map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {{ pending: '⏳ Da approvare', approved: '✅ Approvate', rejected: '🚫 Scartate' }[t]} ({data.counts[t] ?? 0})
          </button>
        ))}
      </div>
      <div className="row wrap">
        <input placeholder="Cerca…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} style={{ maxWidth: 320 }} />
        <button className="ghost" onClick={load}>Cerca</button>
        {sel.size > 0 && (
          <>
            <span className="muted">{sel.size} selezionate:</span>
            {tab !== 'approved' && <button onClick={() => bulk('approve')}>✓ Approva</button>}
            {tab !== 'rejected' && <button className="ghost" onClick={() => bulk('reject')}>Scarta</button>}
            <button className="ghost danger" onClick={() => confirm('Eliminare definitivamente le voci selezionate?') && bulk('delete')}>🗑 Elimina</button>
          </>
        )}
        {tab === 'pending' && data.items.length > 0 && <button className="ghost small" onClick={() => setSel(new Set(data.items.map((i) => i.id)))}>Seleziona tutte</button>}
        <button className="ghost small" title="Estrae i fatti da tutte le chat passate non ancora analizzate (in background)" onClick={() => run(async () => { const r = await post('/admin/memory/extract-all'); setMsg(`Analisi avviata su ${r.conversations} chat: le nuove voci compariranno tra qualche minuto in "Da approvare"`) }, '')}>🔍 Analizza le chat passate</button>
      </div>

      {data.items.length === 0 && <p className="muted">Nessuna voce.</p>}
      <div className="stack" style={{ gap: 6 }}>
        {data.items.map((it) => (
          <div key={it.id} className={`mem-item ${it.pinned ? 'pinned' : ''}`}>
            <input type="checkbox" checked={sel.has(it.id)} onChange={() => toggle(it.id)} />
            <div className="mem-body">
              {editing === it.id ? (
                <div className="row">
                  <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus />
                  <button onClick={() => run(async () => { await patch(`/admin/memory/${it.id}`, { text: draft }); setEditing(null) }, 'Modificata')}>Salva</button>
                  <button className="ghost" onClick={() => setEditing(null)}>Annulla</button>
                </div>
              ) : (
                <div className="mem-text" onDoubleClick={() => { setEditing(it.id); setDraft(it.text) }} title="Doppio clic per modificare">{it.pinned && '📌 '}{it.text}</div>
              )}
              <small className="muted">
                {CAT_IT[it.category] ?? it.category} · {it.scope === 'family' ? 'tutta la famiglia' : `solo ${it.subject ?? it.author}`} · da {it.author} · fiducia {it.confidence}/5 · {new Date(it.created_at).toLocaleDateString()}
                {it.used_count > 0 && <> · usata {it.used_count}×</>}
                {it.source_conversation_id && <> · <a href={`/c/${it.source_conversation_id}`} target="_blank" rel="noreferrer">chat</a></>}
              </small>
            </div>
            <div className="row" style={{ gap: 4 }}>
              <select value={it.category} onChange={(e) => run(() => patch(`/admin/memory/${it.id}`, { category: e.target.value }), '')} title="Categoria">
                {data.categories.map((c) => <option key={c} value={c}>{CAT_IT[c] ?? c}</option>)}
              </select>
              {it.status !== 'approved' && <button className="small" onClick={() => act(it.id, 'approve')} title="Approva">✓</button>}
              {it.status !== 'rejected' && <button className="ghost small" onClick={() => act(it.id, 'reject')} title="Scarta">✕</button>}
              <button className="ghost small" onClick={() => act(it.id, it.pinned ? 'unpin' : 'pin')} title={it.pinned ? 'Togli dal prompt fisso' : 'Fissa: sempre nel prompt'}>📌</button>
              <button className="ghost small danger" onClick={() => confirm('Eliminare definitivamente?') && run(() => del(`/admin/memory/${it.id}`), 'Eliminata')} title="Elimina">🗑</button>
            </div>
          </div>
        ))}
      </div>

      <section className="card stack">
        <h3>＋ Aggiungi una voce a mano</h3>
        <div className="grid2">
          <label className="wide">Fatto <small className="muted">(frase breve in terza persona)</small><input placeholder="es. La famiglia va al mare a Stintino ogni agosto" value={newText} onChange={(e) => setNewText(e.target.value)} /></label>
          <label>Categoria<select value={newCat} onChange={(e) => setNewCat(e.target.value)}>{data.categories.map((c) => <option key={c} value={c}>{CAT_IT[c] ?? c}</option>)}</select></label>
          <label>Riguarda<select value={newScope === 'family' ? 'family' : newSubject} onChange={(e) => { if (e.target.value === 'family') { setNewScope('family'); setNewSubject('') } else { setNewScope('user'); setNewSubject(e.target.value) } }}>
            <option value="family">tutta la famiglia</option>{data.members.map((m) => <option key={m.id} value={m.id}>solo {m.name}</option>)}</select></label>
        </div>
        <div><button disabled={!newText.trim()} onClick={() => run(async () => { await post('/admin/memory', { text: newText, category: newCat, scope: newScope, subject_user_id: newSubject || null }); setNewText('') }, 'Voce aggiunta e approvata')}>Aggiungi</button></div>
      </section>
    </div>
  )
}
