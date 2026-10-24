import PersonalAppearance from './PersonalAppearance'
import PanelMenu from '../components/PanelMenu'
import { useBranding } from '../lib/branding'
import '../panel-menu.css'
import { useEffect, useState } from 'react'
import { startRegistration } from '@simplewebauthn/browser'
import { del, fmtBytes, get, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import TotpSetup from '../components/TotpSetup'

export default function Settings() {
  const { brand } = useBranding()
  const [section, setSection] = useState("security")
  const { me, refresh } = useAuth()
  const [creds, setCreds] = useState<any[]>([])
  const [sessions, setSessions] = useState<any[]>([])
  const [codes, setCodes] = useState<string[] | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [pw, setPw] = useState({ current_password: '', new_password: '' })
  const [deviceName, setDeviceName] = useState('')
  const [totpSetup, setTotpSetup] = useState(false)
  const [mem, setMem] = useState<any[]>([])
  const isChild = me?.user.role === 'child'

  async function load() {
    get('/auth/memory/me').then(setMem).catch(() => {})
    if (isChild) return
    setCreds(await get('/auth/webauthn/credentials'))
    setSessions(await get('/auth/sessions'))
  }
  useEffect(() => {
    load()
  }, [])

  const run = async (fn: () => Promise<any>, ok = 'Fatto') => {
    setErr('')
    setMsg('')
    try {
      await fn()
      setMsg(ok)
      await load()
    } catch (e: any) {
      setErr(e.message)
    }
  }

  async function addPasskey() {
    const options = await post('/auth/webauthn/register/options')
    const credential = await startRegistration({ optionsJSON: options })
    const name = prompt('Nome per questa passkey (es. iPhone di Anna)') || 'Passkey'
    const r = await post('/auth/webauthn/register/verify', { credential, name })
    if (r.recovery_codes?.length) setCodes(r.recovery_codes)
    await refresh()
  }

  if (isChild) {
    const pol = me!.user.effective_policy
    return (
      <div className="page stack narrow">
        <h2>⚙️ Le mie impostazioni</h2>
        <p>Ciao {me!.user.display_name}! {me!.user.avatar}</p>
        <p className="muted">Puoi mandare fino a {pol.daily_message_limit || '∞'} messaggi e creare {pol.daily_image_limit || '∞'} immagini al giorno.</p>
        {pol.time_windows?.length > 0 && <p className="muted">Orari in cui puoi usare Emily: {pol.time_windows.map((w: any) => `${w.start}–${w.end}`).join(', ')}</p>}
        <PersonalAppearance />
        <MyUsage simple />
      </div>
    )
  }

  return (
    <div className="page system-panel">
      <header className="system-panel-heading"><img src={brand.logo_url} alt="" /><div><p>IL TUO PROFILO</p><h1>Impostazioni</h1><span className="muted">{me?.user.display_name}</span></div></header>
      <div className="panel-shell"><PanelMenu active={section} onChange={setSection} label="Sezioni impostazioni" groups={[{ label: 'Account', items: [{ id: 'security', label: 'Passkey e sicurezza', icon: '▣' }, { id: 'password', label: 'Password', icon: '✱' }, { id: 'sessions', label: 'Sessioni attive', icon: '◷' }] }, { label: 'Il tuo spazio', items: [{ id: 'appearance', label: 'Aspetto', icon: '◈' }, { id: 'memory', label: 'Memoria personale', icon: '◇' }, { id: 'usage', label: 'Spazio e utilizzo', icon: '▥' }, { id: 'devices', label: 'Dispositivi e Sync', icon: '▦' }, { id: 'tools', label: 'Strumenti Emily', icon: '✳' }, { id: 'guide', label: 'Guida', icon: '?' }] }]} /><main className="panel-content stack">
      {section === 'appearance' && <PersonalAppearance />}
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}

      <section className="card stack" hidden={section !== "security"}>
        <h3>🔐 Secondo fattore</h3>
        <p className="muted small">{me?.features?.passkeys !== false ? 'Serve almeno uno tra passkey e app authenticator. Puoi tenerli entrambi: al login scegli quale usare.' : 'Il secondo fattore è l\'app authenticator (codice a 6 cifre).'}</p>
        <h4>📱 App authenticator</h4>
        {me?.user.has_totp ? (
          <div className="row">
            <span>✅ Attiva</span>
            <button className="ghost" onClick={() => { if (confirm("Disattivare l'app authenticator?" + (creds.length ? '' : ' Resterai senza secondo fattore e dovrai attivarne uno al prossimo accesso.'))) run(async () => { await del('/auth/totp'); await refresh() }, 'App authenticator disattivata') }}>Disattiva</button>
          </div>
        ) : totpSetup ? (
          <TotpSetup onDone={async (rc) => { setTotpSetup(false); if (rc.length) setCodes(rc); await refresh(); setMsg('App authenticator attivata') }} onCancel={() => setTotpSetup(false)} />
        ) : (
          <div className="row"><button onClick={() => setTotpSetup(true)}>＋ Attiva app authenticator</button></div>
        )}
        <h4>🔑 Passkey</h4>
        <ul className="file-list">
          {creds.map((c) => (
            <li key={c.id}>{c.name} <small className="muted">creata {new Date(c.created_at).toLocaleDateString()}</small>
              {(creds.length > 1 || me?.user.has_totp) && <button className="icon" onClick={() => run(() => del(`/auth/webauthn/credentials/${c.id}`))}>🗑</button>}
            </li>
          ))}
        </ul>
        <div className="row">
          {me?.features?.passkeys !== false && <button onClick={() => run(addPasskey, 'Passkey aggiunta')}>＋ Aggiungi passkey</button>}
          <button className="ghost" onClick={() => run(async () => setCodes((await post('/auth/recovery-codes/regenerate')).recovery_codes), 'Nuovi codici generati')}>Rigenera codici di recupero</button>
        </div>
        {codes && <pre className="codes">{codes.join('\n')}</pre>}
      </section>

      <section className="card stack" hidden={section !== "memory"}>
        <h3>🧠 Cosa ricorda di te{me?.features?.org_mode ? '' : ' e della famiglia'}</h3>
        <p className="muted">Fatti che l'assistente usa per personalizzare le risposte. Puoi cancellare quelli che ti riguardano; l'amministratore gestisce tutto dalla pagina Famiglia → Memoria. In chat puoi dire "ricordati che…" o "dimentica…".</p>
        {mem.length === 0 ? <small className="muted">Ancora nulla.</small> : (
          <ul className="file-list">
            {mem.map((m) => (
              <li key={m.id}>{m.text} <small className="muted">({m.scope === 'family' ? (me?.features?.org_mode ? 'tutti' : 'famiglia') : 'solo tu'}{m.status === 'pending' ? ' · in attesa di approvazione' : ''})</small>
                {(m.mine || me?.user.role === 'admin') && <button className="icon" title="Cancella" onClick={() => run(() => del(`/auth/memory/${m.id}`), 'Voce cancellata')}>🗑</button>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {!me?.features?.org_mode && <section className="card stack" hidden={section !== "devices"}>
        <h3>👧 Dispositivo per i bambini</h3>
        <p className="muted">Approvando questo dispositivo (browser), i bambini potranno accedere da qui con il loro PIN. Fallo solo su tablet/PC di casa.</p>
        <div className="row">
          <input placeholder="Nome dispositivo (es. iPad salotto)" value={deviceName} onChange={(e) => setDeviceName(e.target.value)} />
          <button disabled={!deviceName} onClick={() => run(() => post('/auth/device/trust', { name: deviceName }), 'Dispositivo approvato')}>Approva</button>
        </div>
      </section>}

      <section className="card stack" hidden={section !== "guide"}>
        <h3>📘 Guida</h3>
        <p className="muted small">La guida interattiva: cosa sa fare Emily, le app, come si usano chat, documenti, posta e personale.</p>
        <button className="ghost" style={{ alignSelf: 'flex-start' }} onClick={() => window.dispatchEvent(new Event('familai:guide'))}>Apri la guida</button>
      </section>

      {section === 'tools' && <AssistantTools />}

      {section === "usage" && <MyUsage />}

      <section className="card stack" hidden={section !== "usage"}>
        <h3>💾 Il mio spazio</h3>
        <StorageBar used={me!.user.storage_used || 0} quota={me!.user.quota_effective || 0} />
        <small className="muted">Conta i file di cui sei proprietario (caricati, creati dalle app e dalla chat) e le versioni conservate. Eliminando un file si libera spazio; la quota la decide l'amministratore.</small>
      </section>

      <section className="card stack" hidden={section !== "password"}>
        <h3>🔑 Password</h3>
        <input type="password" placeholder="Password attuale" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
        <input type="password" placeholder="Nuova password (min 10 caratteri)" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
        <button onClick={() => run(() => post('/auth/password', pw), 'Password aggiornata')}>Cambia password</button>
      </section>

      <section className="card stack" hidden={section !== "sessions"}>
        <h3>💻 Sessioni attive</h3>
        <ul className="file-list">
          {sessions.map((s) => (
            <li key={s.id}>{s.current ? '● ' : ''}{s.ip} <small className="muted">{s.user_agent.slice(0, 60)} · {new Date(s.created_at).toLocaleString()}</small></li>
          ))}
        </ul>
        <button className="ghost" onClick={() => run(() => post('/auth/sessions/revoke-others'), 'Altre sessioni chiuse')}>Disconnetti gli altri dispositivi</button>
      </section>

      {section === 'devices' && <CraftSync admin={me?.user.role === 'admin'} />}
    </main></div>
    </div>
  )
}


/** 🔄 Craft Sync: i computer che tengono una cartella locale uguale ai Documenti (agente della Suite Craft). Il codice di
 * collegamento vale dieci minuti e una volta sola; il token del computer non passa mai dal browser. L'amministratore può
 * vedere e revocare i computer di tutta la famiglia. */
function CraftSync({ admin }: { admin: boolean }) {
  const [devices, setDevices] = useState<any[]>([])
  const [all, setAll] = useState(false)
  const [pairing, setPairing] = useState<{ code: string; expires_at: string } | null>(null)
  const [left, setLeft] = useState(0)
  const [err, setErr] = useState('')
  const load = () => get(`/sync/devices${all ? '?all=1' : ''}`).then(setDevices).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [all])
  useEffect(() => {
    if (!pairing) return
    const tick = () => {
      const s = Math.max(0, Math.round((new Date(pairing.expires_at).getTime() - Date.now()) / 1000))
      setLeft(s)
      if (s <= 0) setPairing(null)
    }
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [pairing])

  async function newCode() {
    setErr('')
    try { setPairing(await post('/sync/devices/pairing-code')) } catch (e: any) { setErr(e.message) }
  }
  async function revoke(d: any) {
    if (!confirm(`Scollegare "${d.name}"? Il computer smette di sincronizzare finché non lo colleghi di nuovo.`)) return
    try { await del(`/sync/devices/${d.id}`); await load() } catch (e: any) { setErr(e.message) }
  }
  async function rename(d: any) {
    const name = prompt('Nome del computer', d.name)
    if (!name || name === d.name) return
    try { await patch(`/sync/devices/${d.id}`, { name }); await load() } catch (e: any) { setErr(e.message) }
  }
  const icon = (p: string) => (p === 'macos' ? '🍎' : p === 'windows' ? '🪟' : p === 'linux' ? '🐧' : '💻')
  const active = devices.filter((d) => !d.revoked)
  const fmtLeft = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`

  return (
    <section className="card stack">
      <h3>🔄 Craft Sync · computer collegati</h3>
      <small className="muted">I computer con Craft Sync tengono una cartella locale uguale ai tuoi Documenti (e all'archivio del tuo reparto): le modifiche fatte lì, anche senza rete, tornano qui con la cronologia delle versioni.</small>
      {err && <div className="error">{err}</div>}
      {active.length === 0 && <p className="muted">Nessun computer collegato.</p>}
      <ul className="file-list">
        {active.map((d) => (
          <li key={d.id} className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
            <span>{icon(d.platform)} {d.name}{d.user_name ? <small className="muted"> · {d.user_name}</small> : null} <small className="muted">· {d.last_seen_at ? `visto ${new Date(d.last_seen_at).toLocaleString()}` : 'mai collegato'}{d.last_ip ? ` · ${d.last_ip}` : ''}{d.app_version ? ` · v${d.app_version}` : ''}</small></span>
            <span className="row" style={{ gap: 4 }}>
              <button className="ghost" onClick={() => rename(d)} title="Rinomina">✎</button>
              <button className="ghost" onClick={() => revoke(d)} title="Scollega: il token smette di funzionare">Scollega</button>
            </span>
          </li>
        ))}
      </ul>
      {pairing ? (
        <div className="card stack" style={{ alignItems: 'center' }}>
          <div style={{ fontSize: 32, letterSpacing: 4, fontFamily: 'ui-monospace, Menlo, monospace' }}>{pairing.code}</div>
          <small className="muted">Apri Craft Sync sul computer, scrivi l'indirizzo <code>{window.location.origin}</code> e questo codice. Vale ancora {fmtLeft}, una volta sola.</small>
          <button className="ghost" onClick={() => setPairing(null)}>Chiudi</button>
        </div>
      ) : (
        <div className="row" style={{ gap: 8 }}>
          <button onClick={newCode}>Collega un computer</button>
          {admin && <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> tutti i computer della famiglia</label>}
        </div>
      )}
    </section>
  )
}


/** 📊 Il mio utilizzo: token, messaggi e costo stimato dell'utente (solo i propri: /auth/usage filtra sul suo id). */
function MyUsage({ simple = false }: { simple?: boolean }) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { get(`/auth/usage?days=${days}`).then(setData).catch((e) => setErr(e.message)) }, [days])
  const t = data?.totals
  const fmt = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)} M` : n >= 10_000 ? `${(n / 1000).toFixed(1)} k` : n.toLocaleString())
  const eur = (c: number) => `${c.toFixed(c < 1 ? 3 : 2)} $`
  const maxDay = Math.max(1, ...(data?.daily ?? []).map((d: any) => d.input_tokens + d.output_tokens))
  if (simple) {
    return t ? <p className="muted">Negli ultimi {days} giorni hai mandato {t.messages} messaggi e usato {fmt(t.tokens)} token.</p> : null
  }
  return (
    <section className="card stack">
      <div className="row between wrap">
        <h3 style={{ margin: 0 }}>📊 Il mio utilizzo</h3>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 'auto' }}>
          <option value={7}>ultimi 7 giorni</option><option value={30}>ultimi 30 giorni</option><option value={90}>ultimi 90 giorni</option><option value={365}>ultimo anno</option>
        </select>
      </div>
      {err && <div className="error">{err}</div>}
      {t && (
        <>
          <div className="usage-tiles">
            <div className="usage-tile"><b>{t.messages}</b><small>messaggi</small></div>
            <div className="usage-tile"><b>{fmt(t.tokens)}</b><small>token totali</small></div>
            <div className="usage-tile"><b>{fmt(t.input_tokens + t.cache_read_tokens + t.cache_write_tokens)}</b><small>in ingresso{t.cache_read_tokens ? ` (${Math.round(100 * t.cache_read_tokens / Math.max(1, t.input_tokens + t.cache_read_tokens + t.cache_write_tokens))}% dalla cache)` : ''}</small></div>
            <div className="usage-tile"><b>{fmt(t.output_tokens)}</b><small>in uscita</small></div>
            <div className="usage-tile"><b>{eur(t.cost)}</b><small>costo stimato</small></div>
            {(t.images > 0 || t.videos > 0 || t.searches > 0) && <div className="usage-tile"><b>{t.images + t.videos + t.searches}</b><small>{[t.images && `${t.images} immagini`, t.videos && `${t.videos} video`, t.searches && `${t.searches} ricerche`].filter(Boolean).join(' · ')}</small></div>}
          </div>
          {data.daily.length > 0 && (
            <div className="usage-bars" title="Token al giorno (ingresso + uscita)">
              {data.daily.map((d: any) => (
                <div key={d.day} className="usage-bar" title={`${new Date(d.day).toLocaleDateString()}: ${d.messages} messaggi, ${fmt(d.input_tokens + d.output_tokens)} token`}>
                  <span style={{ height: `${Math.max(2, 100 * (d.input_tokens + d.output_tokens) / maxDay)}%` }} />
                </div>
              ))}
            </div>
          )}
          {data.by_model.length > 0 && (
            <table className="table small">
              <thead><tr><th>Modello</th><th>Messaggi</th><th>Token in</th><th>Token out</th><th>Costo</th></tr></thead>
              <tbody>{data.by_model.map((m: any) => (
                <tr key={m.provider + m.model}><td>{m.provider} · {m.model}</td><td>{m.messages}</td><td>{fmt(m.input_tokens + m.cache_read_tokens)}</td><td>{fmt(m.output_tokens)}</td><td>{eur(m.cost)}</td></tr>
              ))}</tbody>
            </table>
          )}
          <small className="muted">{data.pricing_note} I modelli locali (Ollama, ComfyUI) contano zero.</small>
        </>
      )}
    </section>
  )
}


/** Barra dello spazio usato / quota (quota 0 = illimitata). */
export function StorageBar({ used, quota, compact = false }: { used: number; quota: number; compact?: boolean }) {
  const pct = quota > 0 ? Math.min(100, Math.round(100 * used / quota)) : 0
  const cls = pct >= 95 ? 'full' : pct >= 80 ? 'warn' : ''
  return (
    <div className={`storage ${compact ? 'compact' : ''}`} title={quota > 0 ? `${fmtBytes(used)} di ${fmtBytes(quota)} (${pct}%)` : `${fmtBytes(used)} usati, nessun limite`}>
      <div className="storage-bar"><span className={cls} style={{ width: `${quota > 0 ? pct : 3}%` }} /></div>
      <small className="muted">{fmtBytes(used)}{quota > 0 ? ` di ${fmtBytes(quota)} · ${pct}%` : ' · illimitato'}</small>
    </div>
  )
}

const TOOL_LABEL: Record<string, string> = { web_search: '🔎 Ricerca web', generate_image: '🎨 Immagini', generate_video: '🎬 Video', read_document: '📄 Documenti', artifacts: '📦 Artifact (pagine, app e codice con anteprima)', run_code: '⚙️ Esecuzione codice', mcp: '🔌 App e server MCP (Personale, Posta, Preventivi…)', ask_model: '🤝 Consultare altri modelli', memory: '🧠 Memoria' }

/** Gli strumenti che l'assistente può usare in chat: prima erano caselle sotto la chat, ora stanno qui (valgono su ogni dispositivo). */
function AssistantTools() {
  const { me, refresh } = useAuth()
  const [groups, setGroups] = useState<{ group: string; description: string }[]>([])
  const [msg, setMsg] = useState('')
  useEffect(() => { get('/chat/providers').then((r) => setGroups(r.tools || [])).catch(() => {}) }, [])
  const ids = Array.from(new Set(groups.map((t) => t.group)))
  if (!ids.length) return null
  const off = new Set(me?.prefs?.tools_off || [])
  async function toggle(g: string, on: boolean) {
    const next = new Set(off)
    if (on) next.delete(g); else next.add(g)
    try { await put('/auth/prefs', { tools_off: [...next] }); await refresh(); setMsg('Salvato') } catch (e: any) { setMsg(e.message) }
  }
  return (
    <section className="card stack">
      <h3>🧰 Strumenti dell'assistente</h3>
      <p className="muted small">Cosa può usare l'assistente quando gli scrivi. Quelli spenti non li usa in nessuna chat, finché non li riaccendi.</p>
      {ids.map((g) => (
        <label key={g} className="row" title={groups.filter((t) => t.group === g).map((t) => t.description).join('\n')}>
          <input type="checkbox" style={{ width: 'auto' }} checked={!off.has(g)} onChange={(e) => toggle(g, e.target.checked)} /> {TOOL_LABEL[g] ?? g}
        </label>
      ))}
      {msg && <small className="muted">{msg}</small>}
    </section>
  )
}
