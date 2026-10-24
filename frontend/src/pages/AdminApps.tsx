import { useEffect, useState } from 'react'
import { get, patch, type User } from '../lib/api'
import { useAuth } from '../lib/auth'

interface AppInfo { id: string; label: string; app: string; glyph: string; desc: string; admin?: string; active: boolean; opt_in?: boolean }
type Row = { apps: string[] | null; admin_apps: string[] }

/**
 * Software per utente in un colpo d'occhio: righe = membri, colonne = app. Ogni casella abilita l'app (pagina, menu, tool
 * in chat); 🛠 la rende anche amministrabile da quell'utente. "Tutti" = anche le app attivate in futuro.
 * Il amministratore ha sempre tutto; i bambini nessun software.
 */
export default function AdminApps() {
  const { refresh } = useAuth()
  const [members, setMembers] = useState<User[]>([])
  const [catalog, setCatalog] = useState<AppInfo[]>([])
  const [draft, setDraft] = useState<Record<string, Row>>({})
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const [m, c] = await Promise.all([get<User[]>('/admin/members'), get<AppInfo[]>('/admin/apps')])
      setMembers(m); setCatalog(c)
      setDraft(Object.fromEntries(m.map((u) => [u.id, { apps: u.apps ?? null, admin_apps: u.admin_apps ?? [] }])))
    } catch (e: any) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])

  const editable = members.filter((u) => u.role === 'adult')
  const fixed = members.filter((u) => u.role !== 'adult')
  // "tutti i software" non comprende quelli da abilitare a parte (opt_in: l'Intranet, riservata ai super amministratori)
  const ids = catalog.filter((a) => !a.opt_in).map((a) => a.id)
  const optIn = new Set(catalog.filter((a) => a.opt_in).map((a) => a.id))
  const orig = (u: User): Row => ({ apps: u.apps ?? null, admin_apps: u.admin_apps ?? [] })
  const same = (a: Row, b: Row) => JSON.stringify([a.apps && [...a.apps].sort(), [...a.admin_apps].sort()]) === JSON.stringify([b.apps && [...b.apps].sort(), [...b.admin_apps].sort()])
  const changed = editable.filter((u) => draft[u.id] && !same(draft[u.id], orig(u)))

  const uses = (r: Row, app: string) => (r.apps === null ? !optIn.has(app) : r.apps.includes(app))
  const setRow = (uid: string, r: Row) => { setMsg(''); setDraft((d) => ({ ...d, [uid]: r })) }
  function toggleUse(uid: string, app: string) {
    const r = draft[uid]
    const cur = r.apps === null ? [...ids] : r.apps
    const on = cur.includes(app)
    setRow(uid, { apps: on ? cur.filter((x) => x !== app) : [...cur, app], admin_apps: on ? r.admin_apps.filter((x) => x !== app) : r.admin_apps })
  }
  function toggleAdmin(uid: string, app: string) {
    const r = draft[uid]
    setRow(uid, { ...r, admin_apps: r.admin_apps.includes(app) ? r.admin_apps.filter((x) => x !== app) : [...r.admin_apps, app] })
  }
  function toggleAll(uid: string) {
    const r = draft[uid]
    setRow(uid, { ...r, apps: r.apps === null ? [...ids] : null })
  }
  function column(app: string, on: boolean) {
    setMsg('')
    setDraft((d) => {
      const n = { ...d }
      for (const u of editable) {
        const r = n[u.id]
        if (on && uses(r, app)) continue
        if (!on && !uses(r, app)) continue
        const cur = r.apps === null ? [...ids] : r.apps
        n[u.id] = { apps: on ? [...cur, app] : cur.filter((x) => x !== app), admin_apps: on ? r.admin_apps : r.admin_apps.filter((x) => x !== app) }
      }
      return n
    })
  }

  async function save() {
    setBusy(true); setErr(''); setMsg('')
    const failed: string[] = []
    for (const u of changed) {
      const r = draft[u.id]
      try { await patch(`/admin/members/${u.id}`, { apps: r.apps === null ? ['*'] : r.apps, admin_apps: r.admin_apps }) } catch (e: any) { failed.push(`${u.display_name}: ${e.message}`) }
    }
    await load(); await refresh()
    if (failed.length) setErr(failed.join(' · ')); else setMsg(`Salvato (${changed.length} ${changed.length === 1 ? 'utente' : 'utenti'})`)
    setBusy(false)
  }

  if (!catalog.length) return err ? <div className="error">{err}</div> : <div className="muted">Carico…</div>
  return (
    <div className="stack">
      <p className="muted">Scegli quali software può usare ogni utente: abilitare un'app mostra la pagina nel menu, l'icona nella cartella APP e i suoi tool in chat. Con 🛠 l'utente ne diventa anche <b>amministratore</b> (le impostazioni riservate di quell'app). <b>Tutti</b> include anche i software attivati in futuro. I titoli delle colonne abilitano o tolgono l'app a tutti.</p>
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
      <div style={{ overflowX: 'auto' }}>
        <table className="table small">
          <thead>
            <tr>
              <th>Utente</th><th>Tutti</th>
              {catalog.map((a) => {
                const all = editable.length > 0 && editable.every((u) => draft[u.id] && uses(draft[u.id], a.id))
                return (
                  <th key={a.id} style={{ textAlign: 'center' }} title={`${a.app}: ${a.desc}${a.active ? '' : ' — non attivo su questo server'}`}>
                    <button className="ghost" style={{ padding: '2px 6px' }} onClick={() => column(a.id, !all)} disabled={!editable.length}>
                      {a.glyph}<br /><small>{a.label}</small>
                    </button>
                    {!a.active && <><br /><small className="muted">non attivo</small></>}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {editable.map((u) => {
              const r = draft[u.id]
              if (!r) return null
              const dirty = !same(r, orig(u))
              return (
                <tr key={u.id} style={dirty ? { background: 'var(--bg)' } : undefined}>
                  <td>{u.avatar} {u.display_name}{!u.is_active && <small className="muted"> · disattivato</small>}{dirty && <small className="muted"> · da salvare</small>}</td>
                  <td><input type="checkbox" checked={r.apps === null} onChange={() => toggleAll(u.id)} title="Tutti i software, anche quelli futuri" /></td>
                  {catalog.map((a) => {
                    const on = uses(r, a.id)
                    return (
                      <td key={a.id} style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                        <input type="checkbox" style={{ width: 'auto', margin: 0, verticalAlign: 'middle' }} checked={on} onChange={() => toggleUse(u.id, a.id)} title={`Usa ${a.label}`} />
                        {a.admin && (
                          <label className="row" title={`Amministra ${a.label}: ${a.admin}`} style={{ display: 'inline-flex', gap: 2, marginLeft: 8, opacity: on ? 1 : 0.35 }}>
                            <input type="checkbox" style={{ width: 'auto', margin: 0 }} disabled={!on} checked={on && r.admin_apps.includes(a.id)} onChange={() => toggleAdmin(u.id, a.id)} />🛠
                          </label>
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
            {fixed.map((u) => (
              <tr key={u.id} className="muted">
                <td>{u.avatar} {u.display_name}</td>
                <td colSpan={catalog.length + 1}><small>{u.role === 'admin' ? '👑 Amministratore: usa e amministra tutti i software' : 'Bambino/a: nessun software'}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row wrap">
        <button className="primary" disabled={busy || changed.length === 0} onClick={save}>Salva{changed.length ? ` (${changed.length})` : ''}</button>
        <button className="ghost" disabled={busy || changed.length === 0} onClick={load}>Annulla modifiche</button>
      </div>
    </div>
  )
}
