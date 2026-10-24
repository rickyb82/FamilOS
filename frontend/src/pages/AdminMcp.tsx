import { useEffect, useState } from 'react'
import { del, get, post, put } from '../lib/api'

interface Srv {
  id: string; name: string; type: 'stdio' | 'http'; command: string; args: string[]; url: string
  min_role: 'child' | 'adult' | 'admin'; enabled: boolean; description: string
  headers_keys: string[]; env_keys: string[]; connected: boolean; error: string; tools: string[]; builtin?: boolean
}

const EMPTY = { name: '', type: 'stdio' as 'stdio' | 'http', command: 'npx', args: '', url: '', headers: '', env: '', min_role: 'adult' as Srv['min_role'], enabled: true, description: '' }

const PRESETS: { label: string; v: Partial<typeof EMPTY> }[] = [
  { label: 'Filesystem (cartella /data/shared)', v: { name: 'filesystem', type: 'stdio', command: 'npx', args: '-y @modelcontextprotocol/server-filesystem /data/shared', description: 'Legge/scrive file in una cartella del server' } },
  { label: 'Fetch (pagine web)', v: { name: 'fetch', type: 'stdio', command: 'npx', args: '-y @modelcontextprotocol/server-fetch', description: 'Scarica e legge pagine web' } },
  { label: 'Memory (note persistenti)', v: { name: 'memory', type: 'stdio', command: 'npx', args: '-y @modelcontextprotocol/server-memory', description: 'Grafo di conoscenza condiviso' } },
  { label: 'Home Assistant (HTTP)', v: { name: 'homeassistant', type: 'http', url: 'http://homeassistant.local:8123/mcp_server/sse', headers: '{"Authorization": "Bearer <token>"}', min_role: 'admin', description: 'Controllo domotica' } },
  { label: 'Server HTTP generico', v: { name: 'remoto', type: 'http', url: 'http://192.168.1.50:8080/mcp', headers: '{}' } },
]

function parseJson(s: string, what: string): Record<string, string> | undefined {
  if (!s.trim()) return undefined
  try {
    const v = JSON.parse(s)
    if (typeof v !== 'object' || Array.isArray(v)) throw new Error()
    return v
  } catch {
    throw new Error(`${what}: JSON non valido (es. {"Authorization": "Bearer xyz"})`)
  }
}

export default function AdminMcp() {
  const [rows, setRows] = useState<Srv[]>([])
  const [edit, setEdit] = useState<(typeof EMPTY & { id?: string }) | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [testOut, setTestOut] = useState<Record<string, string>>({})
  const load = () => get<Srv[]>('/admin/mcp').then(setRows)
  useEffect(() => {
    load()
  }, [])

  const run = async (key: string, fn: () => Promise<any>) => {
    setBusy(key)
    setErr('')
    try {
      await fn()
      await load()
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  async function save() {
    if (!edit) return
    const body: any = {
      name: edit.name, type: edit.type, command: edit.command, url: edit.url, min_role: edit.min_role, enabled: edit.enabled,
      description: edit.description,
      args: edit.args.trim() ? edit.args.trim().split(/\s+/) : [],
    }
    try {
      const h = parseJson(edit.headers, 'Header')
      const e = parseJson(edit.env, 'Variabili ambiente')
      if (h !== undefined || !edit.id) body.headers = h ?? {}
      if (e !== undefined || !edit.id) body.env = e ?? {}
    } catch (ex: any) {
      setErr(ex.message)
      return
    }
    await run('save', async () => {
      if (edit.id) await put(`/admin/mcp/${edit.id}`, body)
      else await post('/admin/mcp', body)
      setEdit(null)
    })
  }

  function openEdit(s: Srv) {
    setEdit({ id: s.id, name: s.name, type: s.type, command: s.command, args: s.args.join(' '), url: s.url,
      headers: '', env: '', min_role: s.min_role, enabled: s.enabled, description: s.description })
  }

  return (
    <div className="stack">
      <p className="muted">I server MCP aggiungono strumenti all'assistente (file, domotica, database, servizi esterni). Si connettono e scollegano a caldo, senza riavviare. <b>stdio</b> = programma avviato nel container backend (es. <code>npx -y pacchetto</code>, richiede internet per il primo download); <b>http</b> = server già in esecuzione altrove (Streamable HTTP). Il <b>ruolo minimo</b> decide chi può usarli.</p>
      {err && <div className="error">{err}</div>}

      <table className="table">
        <thead><tr><th>Server</th><th>Stato</th><th>Tool</th><th>Ruolo min.</th><th></th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td><b>{s.name}</b><br /><small className="muted">{s.type === 'stdio' ? `${s.command} ${s.args.join(' ')}` : s.url}{s.description ? ` — ${s.description}` : ''}</small></td>
              <td>{s.connected ? '🟢 connesso' : s.enabled ? <span className="error" style={{ padding: '2px 6px' }}>🔴 {s.error || 'non connesso'}</span> : '⚪ disattivo'}</td>
              <td>
                {s.tools.length === 0 ? <span className="muted">—</span> : (
                  <details><summary>{s.tools.length} tool</summary>
                    <div className="stack" style={{ marginTop: 6 }}>
                      {s.tools.map((t) => (
                        <div key={t} className="row wrap">
                          <code>{t}</code>
                          <button className="ghost small" disabled={busy !== ''} onClick={() => {
                            const args = prompt(`Argomenti JSON per ${t}`, '{}')
                            if (args === null) return
                            run('call', async () => {
                              const r = await post(`/admin/mcp/${s.id}/call`, { tool: t, args: JSON.parse(args || '{}') })
                              setTestOut((o) => ({ ...o, [s.id]: `${t}: ${r.ok ? '✓' : '✗'}\n${r.content}` }))
                            })
                          }}>prova</button>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </td>
              <td>{s.min_role}</td>
              <td className="row wrap">
                {s.builtin ? (
                  <button className="ghost" disabled={busy !== ''} onClick={() => run('c' + s.id, () => post(`/admin/mcp/${s.id}/connect`))}>{busy === 'c' + s.id ? 'Connessione…' : 'Riconnetti'}</button>
                ) : <>
                  {s.connected
                    ? <button className="ghost" disabled={busy !== ''} onClick={() => run('d' + s.id, () => post(`/admin/mcp/${s.id}/disconnect`))}>Scollega</button>
                    : <button disabled={busy !== ''} onClick={() => run('c' + s.id, () => post(`/admin/mcp/${s.id}/connect`))}>{busy === 'c' + s.id ? 'Connessione…' : 'Connetti'}</button>}
                  <button className="ghost" onClick={() => openEdit(s)}>Modifica</button>
                  <button className="ghost danger" disabled={busy !== ''} onClick={() => confirm(`Eliminare ${s.name}?`) && run('x', () => del(`/admin/mcp/${s.id}`))}>Elimina</button>
                </>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={5} className="muted">Nessun server configurato.</td></tr>}
        </tbody>
      </table>
      {Object.entries(testOut).map(([id, out]) => <pre key={id}>{out}</pre>)}

      <div className="row wrap">
        <button onClick={() => setEdit({ ...EMPTY })}>＋ Aggiungi server</button>
        <select value="" onChange={(e) => { const p = PRESETS[Number(e.target.value)]; if (p) setEdit({ ...EMPTY, ...p.v }) }}>
          <option value="">Parti da un esempio…</option>
          {PRESETS.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
        </select>
      </div>

      {edit && (
        <div className="modal-bg" onClick={() => setEdit(null)}>
          <div className="modal wide stack" onClick={(e) => e.stopPropagation()}>
            <h3>{edit.id ? `Modifica ${edit.name}` : 'Nuovo server MCP'}</h3>
            <div className="grid2">
              <label>Nome (senza spazi)<input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
              <label>Tipo
                <select value={edit.type} onChange={(e) => setEdit({ ...edit, type: e.target.value as any })}>
                  <option value="stdio">stdio (comando locale)</option><option value="http">http (Streamable HTTP)</option>
                </select>
              </label>
              {edit.type === 'stdio' ? (
                <>
                  <label>Comando<input value={edit.command} onChange={(e) => setEdit({ ...edit, command: e.target.value })} placeholder="npx" /></label>
                  <label>Argomenti (separati da spazio)<input value={edit.args} onChange={(e) => setEdit({ ...edit, args: e.target.value })} placeholder="-y @modelcontextprotocol/server-filesystem /data/shared" /></label>
                  <label>Variabili d'ambiente (JSON, cifrate){edit.id && <small className="muted"> vuoto = invariate</small>}
                    <input value={edit.env} onChange={(e) => setEdit({ ...edit, env: e.target.value })} placeholder='{"API_KEY": "…"}' />
                  </label>
                </>
              ) : (
                <>
                  <label>URL<input value={edit.url} onChange={(e) => setEdit({ ...edit, url: e.target.value })} placeholder="http://host:porta/mcp" /></label>
                  <label>Header HTTP (JSON, cifrati){edit.id && <small className="muted"> vuoto = invariati</small>}
                    <input value={edit.headers} onChange={(e) => setEdit({ ...edit, headers: e.target.value })} placeholder='{"Authorization": "Bearer …"}' />
                  </label>
                </>
              )}
              <label>Ruolo minimo
                <select value={edit.min_role} onChange={(e) => setEdit({ ...edit, min_role: e.target.value as any })}>
                  <option value="child">Bambini e adulti</option><option value="adult">Solo adulti</option><option value="admin">Solo amministratori</option>
                </select>
              </label>
              <label>Descrizione<input value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
              <label className="row"><input type="checkbox" checked={edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} /> Attivo (connetti subito)</label>
            </div>
            <div className="row">
              <button onClick={save} disabled={busy !== ''}>{busy === 'save' ? 'Salvataggio e connessione…' : 'Salva'}</button>
              <button className="ghost" onClick={() => setEdit(null)}>Annulla</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
