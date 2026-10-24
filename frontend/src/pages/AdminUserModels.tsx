import { useEffect, useState } from 'react'
import { get, put } from '../lib/api'

interface Group { id: string; label: string; models: { id: string; label: string }[] }
interface UserRow { id: string; display_name: string; avatar: string; role: 'admin' | 'adult' | 'child'; is_active: boolean; models: string[] | null; default: string; effective_default: string }
interface Data { groups: Group[]; default_models: string[]; default_model: string; users: UserRow[] }
type Row = { models: string[] | null; default: string }

/**
 * Modelli per utente, come la griglia Software: righe = persone, colonne = i modelli dei provider attivi (router Auto
 * compreso). "Predefinito" = la persona segue l'elenco di Provider AI › Modelli per gli utenti (anche le sue modifiche);
 * togliendolo parte da una copia di quell'elenco da personalizzare. ⭐ = il modello proposto nelle nuove chat.
 */
export default function AdminUserModels() {
  const [data, setData] = useState<Data | null>(null)
  const [draft, setDraft] = useState<Record<string, Row>>({})
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const d = await get<Data>('/admin/user-models')
      setData(d)
      setDraft(Object.fromEntries(d.users.map((u) => [u.id, { models: u.models, default: u.models ? (u.default || u.effective_default) : '' }])))
    } catch (e: any) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])
  if (!data) return err ? <div className="error">{err}</div> : <div className="muted">Carico…</div>

  const cols = data.groups.flatMap((g) => g.models.map((m) => ({ key: `${g.id}/${m.id}`, label: m.label || m.id, group: g.id })))
  const editable = data.users.filter((u) => u.role === 'adult')
  const fixed = data.users.filter((u) => u.role !== 'adult')
  const orig = (u: UserRow): Row => ({ models: u.models, default: u.models ? (u.default || u.effective_default) : '' })
  const norm = (r: Row) => JSON.stringify([r.models && [...r.models].sort(), r.models ? r.default : ''])
  const changed = editable.filter((u) => draft[u.id] && norm(draft[u.id]) !== norm(orig(u)))
  const list = (r: Row) => r.models ?? data.default_models
  const def = (r: Row) => (r.models ? r.default : data.default_model)
  const set = (uid: string, r: Row) => { setMsg(''); setDraft((d) => ({ ...d, [uid]: r })) }

  function toggle(uid: string, k: string) {
    const r = draft[uid]
    const cur = list(r)
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]
    const d = r.models ? r.default : data!.default_model
    set(uid, { models: next, default: next.includes(d) ? d : next[0] || '' })
  }
  function star(uid: string, k: string) {
    const r = draft[uid]
    const cur = list(r)
    set(uid, { models: cur.includes(k) ? cur : [...cur, k], default: k })
  }
  function inherit(uid: string) {
    const r = draft[uid]
    set(uid, r.models ? { models: null, default: '' } : { models: [...data!.default_models], default: data!.default_model || data!.default_models[0] || '' })
  }
  function column(k: string, on: boolean) {
    setMsg('')
    setDraft((d) => {
      const n = { ...d }
      for (const u of editable) {
        const r = n[u.id]
        const cur = r.models ?? data!.default_models
        if (cur.includes(k) === on) continue
        const next = on ? [...cur, k] : cur.filter((x) => x !== k)
        const dd = r.models ? r.default : data!.default_model
        n[u.id] = { models: next, default: next.includes(dd) ? dd : next[0] || '' }
      }
      return n
    })
  }
  async function save() {
    const empty = changed.find((u) => draft[u.id].models && draft[u.id].models!.length === 0)
    if (empty) { setErr(`${empty.display_name}: abilita almeno un modello, oppure rimetti "Predefinito"`); return }
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await put<{ changed: number }>('/admin/user-models', { users: Object.fromEntries(changed.map((u) => [u.id, draft[u.id]])) })
      await load(); setMsg(`Salvato (${r.changed} ${r.changed === 1 ? 'utente' : 'utenti'})`)
    } catch (e: any) { setErr(e.message) }
    setBusy(false)
  }

  return (
    <div className="stack">
      <p className="muted">Scegli quali modelli trova ogni persona nel menu della chat. <b>Predefinito</b> = segue l'elenco di <i>Provider AI › Modelli per gli utenti</i> (anche quando lo cambi); togliendo la spunta parti da una copia di quell'elenco e la personalizzi. La ⭐ è il modello proposto nelle nuove chat. <b>Auto</b> è il router che sceglie da solo tra il modello locale e il cloud. I titoli delle colonne abilitano o tolgono il modello a tutti. Gli amministratori vedono sempre tutti i modelli; i bambini usano quello scelto dal genitore.</p>
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
      {cols.length === 0 && <div className="muted">Nessun provider attivo: configura una chiave in Provider AI.</div>}
      {cols.length > 0 && (
        <div className="um-wrap">
          <table className="table small um-table">
            <thead>
              <tr>
                <th rowSpan={2} className="um-sticky">Utente</th><th rowSpan={2}>Predefinito</th>
                {data.groups.map((g) => <th key={g.id} colSpan={g.models.length} className="um-group">{g.label}</th>)}
              </tr>
              <tr>
                {cols.map((c) => {
                  const all = editable.length > 0 && editable.every((u) => draft[u.id] && list(draft[u.id]).includes(c.key))
                  return (
                    <th key={c.key} className="um-col" title={`${c.key} — clic: ${all ? 'togli a tutti' : 'abilita a tutti'}${data.default_models.includes(c.key) ? ' · nell\'elenco predefinito' : ''}`}>
                      <button className="ghost um-colbtn" onClick={() => column(c.key, !all)} disabled={!editable.length}>
                        <span>{c.label}</span>{data.default_models.includes(c.key) && <small className="muted"> •</small>}
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {editable.map((u) => {
                const r = draft[u.id]
                if (!r) return null
                const dirty = norm(r) !== norm(orig(u))
                const l = list(r), d = def(r)
                return (
                  <tr key={u.id} style={dirty ? { background: 'var(--bg)' } : undefined}>
                    <td className="um-sticky">{u.avatar} {u.display_name}{!u.is_active && <small className="muted"> · disattivato</small>}{dirty && <small className="muted"> · da salvare</small>}
                      <br /><small className="muted">{r.models ? `${l.length} modelli personali` : 'elenco predefinito'}</small></td>
                    <td style={{ textAlign: 'center' }}><input type="checkbox" checked={r.models === null} onChange={() => inherit(u.id)} title="Segue l'elenco predefinito" /></td>
                    {cols.map((c) => {
                      const on = l.includes(c.key)
                      return (
                        <td key={c.key} className="um-cell" style={{ opacity: r.models === null ? 0.6 : 1 }}>
                          <input type="checkbox" checked={on} onChange={() => toggle(u.id, c.key)} title={`${on ? 'Togli' : 'Abilita'} ${c.label}`} />
                          <button className={`icon um-star ${on && d === c.key ? 'on' : ''}`} disabled={!on} onClick={() => star(u.id, c.key)} title="Proposto nelle nuove chat">{on && d === c.key ? '★' : '☆'}</button>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
              {fixed.map((u) => (
                <tr key={u.id} className="muted">
                  <td className="um-sticky">{u.avatar} {u.display_name}</td>
                  <td colSpan={cols.length + 1}><small>{u.role === 'admin' ? '👑 Amministratore: vede tutti i modelli (può bloccarsene uno in Provider AI)' : 'Bambino/a: usa il modello scelto dal genitore nella sua scheda'}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row wrap">
        <button className="primary" disabled={busy || changed.length === 0} onClick={save}>Salva{changed.length ? ` (${changed.length})` : ''}</button>
        <button className="ghost" disabled={busy || changed.length === 0} onClick={load}>Annulla modifiche</button>
        <small className="muted">• = nell'elenco predefinito · ★ = proposto nelle nuove chat</small>
      </div>
    </div>
  )
}
