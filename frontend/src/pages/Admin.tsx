import AdminSafeBrowser from './AdminSafeBrowser'
import AdminChildRules from './AdminChildRules'
import PanelMenu from '../components/PanelMenu'
import '../panel-menu.css'
import { useBranding } from '../lib/branding'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { del, get, patch, post, put, type User } from '../lib/api'
import AdminProviders from './AdminProviders'
import AdminBackup from './AdminBackup'
import AdminMemory from './AdminMemory'
import { useAuth } from '../lib/auth'
import { StorageBar } from './Settings'
import { fmtBytes } from '../lib/api'
import AdminMcp from './AdminMcp'
import AdminCosts from './AdminCosts'
import AdminApps from './AdminApps'
import AdminUserModels from './AdminUserModels'
import AdminLog from './AdminLog'

type Tab = 'browser-rules' | 'child-rules' | 'members' | 'apps' | 'models' | 'providers' | 'costs' | 'supervision' | 'usage' | 'devices' | 'audit' | 'mcp' | 'backup' | 'memory'
const TOOLS = ['web_search', 'generate_image', 'read_document', 'artifacts', 'run_code', 'mcp', 'ask_model', 'memory']
const TOOL_IT: Record<string, string> = { web_search: 'Ricerca web', generate_image: 'Immagini', read_document: 'Documenti (lettura + ricerca)', artifacts: 'Artifact (pagine/app/codice con anteprima)', run_code: 'Esecuzione codice (sandbox)', mcp: 'Server MCP', ask_model: 'Consultare altri modelli AI (ask_model)', memory: 'Memoria (ricordati che… / dimentica…)' }
const PROVIDERS = ['openai', 'ollama']
const DAYS = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


const DEPT_LABEL: Record<string, string> = { tecnico: 'Tecnico', commerciale: 'Commerciale', amministrativo: 'Amministrativo' }
const APP_SHORT: Record<string, string> = { wordcraft: 'Word', gridcraft: 'Fogli', deckcraft: 'Presentazioni', ideacraft: 'Mappe', pdfcraft: 'PDF', mailcraft: 'Posta', archivio: 'Archivio' }
interface AppInfo { id: string; label: string; app: string; glyph: string; desc: string; admin?: string; active: boolean; opt_in?: boolean }

export default function Admin() {
  const { brand } = useBranding()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<Tab>((params.get('tab') as Tab) || 'members')
  const [version, setVersion] = useState('')
  const { me, refresh } = useAuth()
  const org = !!me?.features?.org_mode
  useEffect(() => {
    get('/health').then((h) => setVersion(h.version)).catch(() => {})
  }, [])
  const groups = [
    { label: 'Famiglia', items: [{ id: 'members', label: 'Membri', icon: '♧' }, { id: 'apps', label: 'App e permessi', icon: '▦' }, { id: 'browser-rules', label: 'Navigazione protetta', icon: '◎' }, { id: 'child-rules', label: 'Regole AI bambini', icon: '✎' }, { id: 'supervision', label: 'Supervisione bambini', icon: '♡' }] },
    { label: 'Assistente Emily', items: [{ id: 'providers', label: 'Provider AI', icon: '✳' }, { id: 'models', label: 'Modelli', icon: '◉' }, { id: 'memory', label: 'Memoria', icon: '◇' }, { id: 'usage', label: 'Utilizzo', icon: '▥' }, { id: 'costs', label: 'Costi', icon: '€' }] },
    { label: 'Sicurezza e dati', items: [{ id: 'devices', label: 'Dispositivi', icon: '▣' }, { id: 'audit', label: 'Registro attività', icon: '≡' }, { id: 'backup', label: 'Backup', icon: '↥' }] },
    { label: 'Avanzate', items: [{ id: 'mcp', label: 'Connessioni MCP', icon: '⌘' }] },
  ]
  return (
    <div className="page system-panel">
      <header className="system-panel-heading"><img src={brand.logo_url} alt="" /><div><p>IL VOSTRO SPAZIO</p><h1>{me?.family_name || 'Famiglia'}</h1><span className="muted">{brand.name} · v{version}</span></div></header>
      <div className="panel-shell"><div><PanelMenu groups={groups} active={tab} onChange={id => { setTab(id as Tab); setParams({ tab: id }) }} label="Sezioni famiglia" /><button className="ghost panel-install-link" onClick={() => navigate('/installation')}>⚙ Installazione del sistema ↗</button></div>
      <main className="panel-content">
      {tab === 'members' && <Members />}
      {tab === 'child-rules' && <AdminChildRules />}
      {tab === 'browser-rules' && <AdminSafeBrowser />}
      {tab === 'apps' && <AdminApps />}
      {tab === 'models' && <AdminUserModels />}
      {tab === 'providers' && <AdminProviders />}
      {tab === 'backup' && <AdminBackup />}
      {tab === 'memory' && <AdminMemory />}
      {tab === 'costs' && <AdminCosts />}
      {tab === 'supervision' && <Supervision />}
      {tab === 'usage' && <Usage />}
      {tab === 'devices' && <Devices />}
      {tab === 'audit' && <AdminLog />}
      {tab === 'mcp' && <AdminMcp />}
      </main></div>
    </div>
  )
}

// ---------------- Membri + policy ----------------
function Members() {
  const { me } = useAuth()
  const [members, setMembers] = useState<User[]>([])
  const [defaults, setDefaults] = useState<Record<string, any>>({})
  const [edit, setEdit] = useState<User | null>(null)
  const [adding, setAdding] = useState(false)
  const [err, setErr] = useState('')
  const { refresh } = useAuth()
  const org = !!me?.features?.org_mode
  const load = () => get<User[]>('/admin/members').then(setMembers)
  useEffect(() => {
    load()
    get('/admin/policy-defaults').then(setDefaults)
  }, [])
  async function run2(fn: () => Promise<any>) { try { await fn(); await refresh() } catch (e: any) { setErr(e.message) } }

  return (
    <div className="stack">
      {err && <div className="error">{err}</div>}
      <DefaultQuota members={members} onSaved={() => { load(); refresh() }} />
      <label className="row card" style={{ gap: 10, alignItems: 'flex-start' }}>
        <input type="checkbox" checked={me?.features?.passkeys === false} onChange={(e) => run2(() => put('/admin/providers', { values: { passkeys: e.target.checked ? '0' : '' } }))} />
        <span><b>📱 Solo app authenticator</b><br /><small className="muted">Disattiva le passkey: i nuovi utenti attivano il secondo fattore con l'app (codice a 6 cifre), utile su un server locale dove le passkey sono legate all'indirizzo. Chi ha già una passkey continua a poterla usare.</small></span>
      </label>
      <label className="row card" style={{ gap: 10, alignItems: 'flex-start' }}>
        <input type="checkbox" checked={me?.features?.mfa_optional !== false} onChange={(e) => run2(() => put('/admin/providers', { values: { mfa_optional: e.target.checked ? '1' : '0' } }))} />
        <span><b>🔐 Secondo fattore a scelta dell'utente</b><br /><small className="muted">Al primo accesso ognuno decide se attivare il 2FA (app authenticator) o entrare senza; può attivarlo dopo dalle impostazioni. Spento = obbligatorio per tutti gli adulti.</small></span>
      </label>
      <table className="table">
        <thead><tr><th>Membro</th><th>Ruolo</th><th>{org ? 'Stato' : 'Fascia'}</th><th>2FA</th><th>Accessi</th><th>Spazio</th><th></th></tr></thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.id} className={m.is_active ? '' : 'muted'}>
              <td>{m.avatar} {m.display_name} <small className="muted">{m.email ?? m.username}</small></td>
              <td>{m.role === 'admin' ? '👑 Amministratore' : m.role === 'child' ? 'Bambino/a' : (m.admin_apps?.length ? '🛠 Amministratore app' : (org ? 'Utente' : 'Adulto'))}
                {m.role === 'adult' && <><br /><small className="muted">{m.apps === null || m.apps === undefined ? 'tutti i software' : m.apps.length === 0 ? 'nessun software' : `${m.apps.length} software`}{m.admin_apps?.length ? ` · amministra: ${m.admin_apps.map((a) => APP_SHORT[a] || a).join(', ')}` : ''}</small></>}
                {org && m.role !== 'child' && <><br /><small className="muted">{m.role === 'admin' ? 'tutti i reparti' : (m.departments?.length ? m.departments.map((d) => DEPT_LABEL[d] || d).join(', ') : 'nessun reparto')}</small></>}</td>
              <td>{org ? (m.is_active ? '🟢 attivo' : '⛔ disattivato') : m.role === 'child' ? defaults[m.age_group]?.label ?? m.age_group : '—'}</td>
              <td>{m.role !== 'child' ? ((m.has_passkey || m.has_totp) ? (m.has_passkey && m.has_totp ? '🔑📱' : m.has_passkey ? '🔑' : '📱') : m.mfa_choice === 'skip' ? <span title="Ha scelto di entrare senza secondo fattore">— (scelta)</span> : '⚠️ no') : '—'}</td>
              <td><small>
                {m.last_login ? <>↪ {new Date(m.last_login).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}<br /></> : <span className="muted">mai entrato<br /></span>}
                {m.last_logout && <span className="muted">↩ {new Date(m.last_logout).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}<br /></span>}
                {(m.active_sessions ?? 0) > 0 ? <span style={{ color: '#1e8e4b' }}>● {m.active_sessions} sessione{m.active_sessions === 1 ? '' : 'i'} aperta{m.active_sessions === 1 ? '' : 'e'}</span> : <span className="muted">○ nessuna sessione</span>}
              </small></td>
              <td style={{ minWidth: 150 }}><StorageBar used={m.storage_used || 0} quota={m.quota_effective || 0} compact /><small className="muted">{m.quota_bytes === null || m.quota_bytes === undefined ? 'quota predefinita' : 'quota personale'}</small></td>
              <td className="row wrap">
                {m.id !== me?.user.id && (m.is_active
                  ? <button className="ghost" title="Blocca l'accesso: le sessioni aperte vengono chiuse subito" onClick={() => { if (confirm(`Disattivare ${m.display_name}? Verrà scollegato da tutti i dispositivi.`)) patch(`/admin/members/${m.id}`, { is_active: false }).then(load).catch((e) => setErr(e.message)) }}>⛔ Disattiva</button>
                  : <button className="ghost" onClick={() => patch(`/admin/members/${m.id}`, { is_active: true }).then(load).catch((e) => setErr(e.message))}>✅ Attiva</button>)}
                <button className="ghost" onClick={() => setEdit(m)}>Modifica</button>
                {m.role !== 'child' && m.email && <button className="ghost" title="Invia l'email con il link per impostare la password" onClick={() => post(`/admin/members/${m.id}/invite`).then(() => alert(`Invito inviato a ${m.email}`)).catch((e) => setErr(e.message))}>✉️ Invito</button>}
                <button className="ghost danger" disabled={m.id === me?.user.id} title={m.id === me?.user.id ? "Non puoi eliminare l'account con cui sei collegato" : 'Elimina definitivamente il membro e tutte le sue chat, file e progetti'}
                  onClick={() => { if (confirm(`Eliminare definitivamente ${m.display_name}? Verranno cancellate anche le sue chat, i file e i progetti.`)) del(`/admin/members/${m.id}`).then(load).catch((e) => setErr(e.message)) }}>🗑</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div><button onClick={() => setAdding(true)}>＋ Aggiungi membro</button></div>
      {adding && <MemberForm defaults={defaults} onClose={() => { setAdding(false); load() }} onError={setErr} />}
      {edit && <MemberForm user={edit} defaults={defaults} onClose={() => { setEdit(null); load() }} onError={setErr} />}
    </div>
  )
}

function MemberForm({ user, defaults, onClose, onError }: { user?: User; defaults: Record<string, any>; onClose: () => void; onError: (s: string) => void }) {
  const { me } = useAuth()
  const org = !!me?.features?.org_mode
  const [f, setF] = useState<any>({
    username: user?.username ?? '', display_name: user?.display_name ?? '', avatar: user?.avatar ?? '🙂',
    role: user?.role ?? (org ? 'adult' : 'child'), age_group: user?.age_group ?? 'kid', birth_year: user?.birth_year ?? '',
    email: user?.email ?? '', password: '', pin: '', is_active: user?.is_active ?? true, departments: user?.departments ?? [],
    apps: user ? (user.apps ?? null) : null, admin_apps: user?.admin_apps ?? [],   // apps null = tutti i software
    quota_gb: user && user.quota_bytes !== null && user.quota_bytes !== undefined ? String(Math.round(user.quota_bytes / 1024 ** 3 * 10) / 10) : '',
  })
  const [catalog, setCatalog] = useState<AppInfo[]>([])
  useEffect(() => { get<AppInfo[]>('/admin/apps').then(setCatalog).catch(() => {}) }, [])
  const [policy, setPolicy] = useState<any>(user ? { ...user.effective_policy } : { ...(defaults[org ? 'adult' : 'kid'] ?? {}) })
  const [advanced, setAdvanced] = useState(!!user)
  const [active, setActive] = useState<{ id: string; label: string; models: { id: string; label: string }[] }[]>([])
  useEffect(() => {
    get('/admin/providers').then((r) => setActive(r.providers.filter((p: any) => p.active && p.model_key)))
  }, [])
  const isChild = f.role === 'child'
  useEffect(() => {
    if (!user) setPolicy({ ...(defaults[isChild ? f.age_group : 'adult'] ?? {}) })
  }, [f.role, f.age_group])

  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const setP = (k: string, v: any) => setPolicy({ ...policy, [k]: v })
  const toggleList = (k: string, v: string) => {
    const l: string[] = policy[k] ?? []
    setP(k, l.includes(v) ? l.filter((x) => x !== v) : [...l, v])
  }
  const win = policy.time_windows?.[0] ?? null

  async function save() {
    onError('')
    try {
      const cleanPolicy = { ...policy }
      delete cleanPolicy.label
      delete cleanPolicy.age_group
      if (user) {
        const body: any = { display_name: f.display_name, avatar: f.avatar, role: f.role, is_active: f.is_active, policy: cleanPolicy, departments: f.departments, apps: f.apps === null ? ['*'] : f.apps, admin_apps: f.admin_apps, quota_gb: f.quota_gb === '' ? -1 : Number(f.quota_gb) }
        if (isChild) body.age_group = f.age_group
        if (f.birth_year) body.birth_year = Number(f.birth_year)
        if (f.password) body.password = f.password
        if (f.pin) body.pin = f.pin
        await patch(`/admin/members/${user.id}`, body)
      } else {
        const body: any = { ...f, birth_year: f.birth_year ? Number(f.birth_year) : null }
        if (!body.email) delete body.email
        if (!body.password) delete body.password
        if (!body.pin) delete body.pin
        delete body.departments; delete body.apps; delete body.admin_apps; delete body.quota_gb
        const u = await post<User>('/admin/members', body)
        await patch(`/admin/members/${u.id}`, { policy: cleanPolicy, departments: f.departments, apps: f.apps === null ? ['*'] : f.apps, admin_apps: f.admin_apps, quota_gb: f.quota_gb === '' ? -1 : Number(f.quota_gb) })
      }
      onClose()
    } catch (e: any) {
      onError(e.message)
    }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide stack" onClick={(e) => e.stopPropagation()}>
        <h3>{user ? `Modifica ${user.display_name}` : 'Nuovo membro'}</h3>
        <div className="grid2">
          <label>Nome visualizzato<input value={f.display_name} onChange={(e) => set('display_name', e.target.value)} /></label>
          <label>Avatar (emoji)<input value={f.avatar} onChange={(e) => set('avatar', e.target.value)} maxLength={4} /></label>
          {!user && <label>Username<input value={f.username} onChange={(e) => set('username', e.target.value)} /></label>}
          <label>Ruolo
            <select value={f.role} onChange={(e) => set('role', e.target.value)}>
              {!org && <option value="child">Bambino/a</option>}<option value="adult">{org ? 'Utente (o amministratore di app)' : 'Adulto'}</option><option value="admin">Amministratore</option>
            </select>
          </label>
          {isChild && (
            <label>Fascia d'età
              <select value={f.age_group} onChange={(e) => set('age_group', e.target.value)}>
                <option value="young">5–8 anni</option><option value="kid">9–12 anni</option><option value="teen">13–17 anni</option>
              </select>
            </label>
          )}
          <label>Anno di nascita<input type="number" value={f.birth_year} onChange={(e) => set('birth_year', e.target.value)} /></label>
          {isChild ? (
            <label>PIN {user && '(lascia vuoto per non cambiare)'}<input value={f.pin} onChange={(e) => set('pin', e.target.value)} inputMode="numeric" /></label>
          ) : (
            <>
              {!user && <label>Email<input type="email" value={f.email} onChange={(e) => set('email', e.target.value)} /></label>}
              <label>Password {user ? '(vuota = invariata)' : '(vuota = invia un invito via email per sceglierla)'}<input type="password" value={f.password} onChange={(e) => set('password', e.target.value)} /></label>
            </>
          )}
          {user && <label className="row"><input type="checkbox" checked={f.is_active} onChange={(e) => set('is_active', e.target.checked)} /> Attivo</label>}
          <label>Quota spazio (GB) <small className="muted">(vuoto = predefinita, 0 = illimitata{user?.storage_used !== undefined ? ` · usati ${fmtBytes(user.storage_used)}` : ''})</small><input type="number" min={0} step={1} value={f.quota_gb} placeholder="predefinita" onChange={(e) => set('quota_gb', e.target.value)} /></label>
        </div>
        {org && !isChild && (
          <fieldset className="stack" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px' }}>
            <legend>Reparti</legend>
            <small className="muted">Decidono quali cartelle di <b>{me?.family_name || 'famiglia'}</b> (Documenti) l'utente vede e quali documenti il modello usa come base nelle sue chat: carta intestata e modelli di preventivo (Commerciale), relazioni e procedure (Tecnico), dati amministrativi. {f.role === 'admin' ? "L'amministratore vede tutto e carica i documenti." : ''}</small>
            <div className="row wrap" style={{ gap: 14 }}>
              {Object.entries(DEPT_LABEL).map(([k, label]) => (
                <label key={k} className="row"><input type="checkbox" checked={(f.departments as string[]).includes(k)} onChange={(e) => set('departments', e.target.checked ? [...f.departments, k] : (f.departments as string[]).filter((x) => x !== k))} /> {label}</label>
              ))}
            </div>
          </fieldset>
        )}

        {!isChild && f.role !== 'admin' && (
          <fieldset className="stack" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px' }}>
            <legend>Software</legend>
            <small className="muted">Quali software del framework l'utente vede e può usare (pagine, icone nella cartella APP, tool in chat). «Amministra» dà, solo in quella app, i poteri di configurazione riservati all'amministratore. Il amministratore ha tutto.</small>
            <label className="row"><input type="checkbox" checked={f.apps === null} onChange={(e) => set('apps', e.target.checked ? null : catalog.filter((a) => !a.opt_in).map((a) => a.id))} /> Tutti i software (anche quelli attivati in futuro){catalog.some((a) => a.opt_in) ? <small className="muted"> · tranne {catalog.filter((a) => a.opt_in).map((a) => a.label).join(', ')}, da abilitare a parte</small> : null}</label>
            {f.apps !== null && (
              <table className="table small">
                <thead><tr><th>Software</th><th>Usa</th><th>Amministra</th></tr></thead>
                <tbody>{catalog.map((a) => {
                  const on = (f.apps as string[]).includes(a.id)
                  const adm = (f.admin_apps as string[]).includes(a.id)
                  return (
                    <tr key={a.id} className={a.active ? '' : 'muted'}>
                      <td title={a.desc}>{a.glyph} <b>{a.label}</b> <small className="muted">{a.app}{a.active ? '' : ' · non attivo sul server'}</small></td>
                      <td><input type="checkbox" checked={on} onChange={(e) => { const next = e.target.checked ? [...f.apps, a.id] : (f.apps as string[]).filter((x) => x !== a.id); setF({ ...f, apps: next, admin_apps: e.target.checked ? f.admin_apps : (f.admin_apps as string[]).filter((x) => x !== a.id) }) }} /></td>
                      <td>{a.admin ? <label className="row" title={a.admin}><input type="checkbox" disabled={!on} checked={adm} onChange={(e) => set('admin_apps', e.target.checked ? [...f.admin_apps, a.id] : (f.admin_apps as string[]).filter((x) => x !== a.id))} /> <small className="muted">{a.admin}</small></label> : <small className="muted">—</small>}</td>
                    </tr>
                  )
                })}</tbody>
              </table>
            )}
            {f.apps === null && catalog.some((a) => a.admin) && (
              <div className="row wrap" style={{ gap: 14 }}>
                <small className="muted">Amministra:</small>
                {catalog.filter((a) => a.admin).map((a) => <label key={a.id} className="row" title={a.admin}><input type="checkbox" checked={(f.admin_apps as string[]).includes(a.id)} onChange={(e) => set('admin_apps', e.target.checked ? [...f.admin_apps, a.id] : (f.admin_apps as string[]).filter((x) => x !== a.id))} /> {a.glyph} {a.label}</label>)}
              </div>
            )}
          </fieldset>
        )}
        <details open={advanced} onToggle={(e) => setAdvanced((e.target as any).open)}>
          <summary>Permessi e limiti {isChild ? '(controllo genitori)' : ''}</summary>
          <div className="stack">
            <div><b>Strumenti consentiti:</b> {TOOLS.map((t) => (
              <label key={t} className="tool-toggle"><input type="checkbox" checked={(policy.allowed_tools ?? []).includes(t)} onChange={() => toggleList('allowed_tools', t)} /> {TOOL_IT[t]}</label>
            ))}</div>
            {isChild ? (
              <div className="grid2">
                <label>Provider AI (deciso dal genitore)
                  <select value={policy.provider ?? ''} onChange={(e) => setPolicy({ ...policy, provider: e.target.value, model: '', allowed_providers: e.target.value ? [e.target.value] : PROVIDERS })}>
                    <option value="">— primo disponibile —</option>
                    {active.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                </label>
                <label>Compiti per la scuola <small className="muted">(richieste tipo "risolvi l'esercizio", "scrivi il tema")</small>
                  <select value={policy.homework_mode ?? 'ask_parent'} onChange={(e) => setP('homework_mode', e.target.value)}>
                    <option value="ask_parent">chiedi il permesso a un genitore (notifica nel campanello)</option>
                    <option value="tutor">aiuto guidato: spiega e fa domande, mai la soluzione pronta</option>
                    <option value="block">blocca con un messaggio gentile</option>
                    <option value="allow">consenti sempre</option>
                  </select>
                </label>
                <label>Modello
                  <select value={policy.model ?? ''} onChange={(e) => setP('model', e.target.value)} disabled={!policy.provider}>
                    <option value="">— default del provider —</option>
                    {(active.find((p) => p.id === policy.provider)?.models ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                </label>
              </div>
            ) : (
              <div><b>Provider consentiti:</b> {PROVIDERS.map((p) => (
                <label key={p} className="tool-toggle"><input type="checkbox" checked={(policy.allowed_providers ?? []).includes(p)} onChange={() => toggleList('allowed_providers', p)} /> {p}</label>
              ))}</div>
            )}
            <div className="grid3">
              <label>Messaggi/giorno (0=∞)<input type="number" value={policy.daily_message_limit ?? 0} onChange={(e) => setP('daily_message_limit', Number(e.target.value))} /></label>
              <label>Immagini/giorno<input type="number" value={policy.daily_image_limit ?? 0} onChange={(e) => setP('daily_image_limit', Number(e.target.value))} /></label>
              <label>Video/giorno<input type="number" value={policy.daily_video_limit ?? 0} onChange={(e) => setP('daily_video_limit', Number(e.target.value))} /></label>
              <label>Upload max (MB)<input type="number" value={policy.max_upload_mb ?? 20} onChange={(e) => setP('max_upload_mb', Number(e.target.value))} /></label>
            </div>
            <div className="stack">
              <label className="row"><input type="checkbox" checked={!!win} onChange={(e) => setP('time_windows', e.target.checked ? [{ days: [0, 1, 2, 3, 4, 5, 6], start: '08:00', end: '20:00' }] : [])} /> Limita gli orari di utilizzo</label>
              {win && (
                <div className="row wrap">
                  {DAYS.map((d, i) => (
                    <label key={d} className="tool-toggle"><input type="checkbox" checked={win.days.includes(i)} onChange={() => setP('time_windows', [{ ...win, days: win.days.includes(i) ? win.days.filter((x: number) => x !== i) : [...win.days, i] }])} /> {d}</label>
                  ))}
                  <input type="time" value={win.start} onChange={(e) => setP('time_windows', [{ ...win, start: e.target.value }])} />
                  <span>→</span>
                  <input type="time" value={win.end} onChange={(e) => setP('time_windows', [{ ...win, end: e.target.value }])} />
                </div>
              )}
            </div>
            <div className="row wrap">
              <label className="tool-toggle"><input type="checkbox" checked={!!policy.moderation} onChange={(e) => setP('moderation', e.target.checked)} /> Filtro contenuti</label>
              <label className="tool-toggle"><input type="checkbox" checked={!!policy.safe_search} onChange={(e) => setP('safe_search', e.target.checked)} /> Ricerca sicura</label>
              <label className="tool-toggle"><input type="checkbox" checked={!!policy.parent_can_view} onChange={(e) => setP('parent_can_view', e.target.checked)} /> I genitori possono vedere le chat</label>
              <label className="tool-toggle"><input type="checkbox" checked={policy.can_share !== false} onChange={(e) => setP('can_share', e.target.checked)} /> Può condividere</label>
              <label className="tool-toggle"><input type="checkbox" checked={policy.can_create_projects !== false} onChange={(e) => setP('can_create_projects', e.target.checked)} /> Può creare progetti</label>
            </div>
            <label>Parole bloccate aggiuntive (separate da virgola)
              <input value={(policy.blocked_words ?? []).join(', ')} onChange={(e) => setP('blocked_words', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
            </label>
            <label>Istruzioni personalizzate per l'assistente
              <textarea rows={3} value={policy.custom_instructions ?? ''} onChange={(e) => setP('custom_instructions', e.target.value)} placeholder="Es. Lorenzo fa la seconda elementare e adora i dinosauri." />
            </label>
          </div>
        </details>
        <div className="row">
          <button onClick={save}>Salva</button>
          <button className="ghost" onClick={onClose}>Annulla</button>
        </div>
      </div>
    </div>
  )
}

// ---------------- Supervisione ----------------
function Supervision() {
  const nav = useNavigate()
  const [kids, setKids] = useState<User[]>([])
  const [sel, setSel] = useState<string>('')
  const [convs, setConvs] = useState<any[]>([])
  const [flagged, setFlagged] = useState<any[]>([])
  const [err, setErr] = useState('')
  useEffect(() => {
    get<User[]>('/admin/members').then((m) => setKids(m.filter((x) => x.role === 'child')))
    get('/admin/supervision/flagged').then(setFlagged)
  }, [])
  useEffect(() => {
    if (!sel) return
    setErr('')
    get(`/admin/supervision/${sel}/conversations`).then(setConvs).catch((e) => setErr(e.message))
  }, [sel])
  return (
    <div className="stack">
      <h3>⚑ Messaggi segnalati (ultimi 7 giorni)</h3>
      {flagged.length === 0 ? <p className="muted">Nessuna segnalazione.</p> : (
        <table className="table"><thead><tr><th>Quando</th><th>Chi</th><th>Motivo</th><th>Testo</th></tr></thead>
          <tbody>{flagged.map((f) => (
            <tr key={f.message_id}><td>{new Date(f.created_at).toLocaleString()}</td><td>{f.user}</td><td>{f.reason}</td>
              <td><Link to={`/c/${f.conversation_id}`}>{f.text.slice(0, 120)}</Link></td></tr>
          ))}</tbody></table>
      )}
      <h3>Conversazioni dei bambini</h3>
      <select value={sel} onChange={(e) => setSel(e.target.value)}>
        <option value="">— scegli —</option>
        {kids.map((k) => <option key={k.id} value={k.id}>{k.avatar} {k.display_name}</option>)}
      </select>
      {err && <div className="error">{err}</div>}
      <ul className="file-list">
        {convs.map((c) => (
          <li key={c.id}><a href={`/c/${c.id}`} onClick={(e) => { e.preventDefault(); nav(`/c/${c.id}`) }}>{c.title}</a>
            <small className="muted"> · {new Date(c.updated_at).toLocaleString()} · {c.provider}{c.flagged ? ` · ⚑ ${c.flagged}` : ''}</small></li>
        ))}
      </ul>
    </div>
  )
}

// ---------------- Utilizzo ----------------
function Usage() {
  const [rows, setRows] = useState<any[]>([])
  useEffect(() => {
    get('/admin/usage?days=30').then(setRows)
  }, [])
  const byUser: Record<string, any> = {}
  for (const r of rows) {
    const u = (byUser[r.user] ||= { message: 0, image: 0, video: 0, web_search: 0, tool: 0, tokens: 0 })
    u[r.kind] = (u[r.kind] ?? 0) + r.count
    u.tokens += r.input_tokens + r.output_tokens
  }
  return (
    <table className="table">
      <thead><tr><th>Membro (30 gg)</th><th>Messaggi</th><th>Immagini</th><th>Video</th><th>Ricerche</th><th>Altri tool</th><th>Token</th></tr></thead>
      <tbody>{Object.entries(byUser).map(([u, v]) => (
        <tr key={u}><td>{u}</td><td>{v.message}</td><td>{v.image}</td><td>{v.video}</td><td>{v.web_search}</td><td>{v.tool}</td><td>{v.tokens.toLocaleString()}</td></tr>
      ))}</tbody>
    </table>
  )
}

// ---------------- Dispositivi ----------------
function Devices() {
  const [rows, setRows] = useState<any[]>([])
  const load = () => get('/admin/devices').then(setRows)
  useEffect(() => {
    load()
  }, [])
  return (
    <div className="stack">
      <p className="muted">I bambini possono accedere con il PIN solo dai dispositivi approvati da un adulto (Impostazioni → Dispositivo per i bambini).</p>
      <table className="table">
        <thead><tr><th>Nome</th><th>Approvato</th><th>Ultimo uso</th><th></th></tr></thead>
        <tbody>{rows.map((d) => (
          <tr key={d.full_id} className={d.revoked ? 'muted' : ''}>
            <td>{d.name}</td><td>{new Date(d.created_at).toLocaleString()}</td><td>{d.last_seen_at ? new Date(d.last_seen_at).toLocaleString() : '—'}</td>
            <td>{d.revoked ? 'revocato' : <button className="ghost danger" onClick={() => post(`/admin/devices/${d.full_id}/revoke`).then(load)}>Revoca</button>}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

/** Quota di spazio predefinita per tutti i membri senza quota personale, con il totale usato dalla famiglia. */
function DefaultQuota({ members, onSaved }: { members: User[]; onSaved: () => void }) {
  const [cur, setCur] = useState<string>('')
  const [draft, setDraft] = useState<string>('')
  const [err, setErr] = useState('')
  useEffect(() => { get('/admin/providers').then((r) => { const v = r.settings?.default_quota_gb?.value ?? '50'; setCur(v); setDraft(v) }).catch(() => {}) }, [])
  const used = members.reduce((a, m) => a + (m.storage_used || 0), 0)
  const over = members.filter((m) => (m.quota_effective || 0) > 0 && (m.storage_used || 0) >= 0.9 * (m.quota_effective || 1))
  async function save() {
    setErr('')
    try { await put('/admin/providers', { values: { default_quota_gb: draft } }); setCur(draft); onSaved() } catch (e: any) { setErr(e.message) }
  }
  return (
    <div className="card stack" style={{ gap: 6 }}>
      <div className="row between wrap">
        <span><b>💾 Spazio</b><br /><small className="muted">Quota predefinita per ogni utente (file e versioni di cui è proprietario); nella scheda del membro si può dare una quota personale. In totale la famiglia usa <b>{fmtBytes(used)}</b>.</small></span>
        <span className="row" style={{ gap: 6 }}>
          <input type="number" min={0} step={1} value={draft} onChange={(e) => setDraft(e.target.value)} style={{ width: 90 }} /> <span className="muted">GB</span>
          <button disabled={draft === cur} onClick={save}>Salva</button>
        </span>
      </div>
      {over.length > 0 && <small style={{ color: 'var(--danger)' }}>⚠️ Vicini al limite: {over.map((m) => `${m.display_name} (${fmtBytes(m.storage_used || 0)})`).join(', ')}</small>}
      {err && <div className="error">{err}</div>}
    </div>
  )
}
