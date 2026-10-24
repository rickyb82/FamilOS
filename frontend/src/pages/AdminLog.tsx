import { Fragment, useEffect, useState } from 'react'
import { get, type User } from '../lib/api'

/** Registro accessi e attività della famiglia (Emily + ConnectCraft): solo amministratori. */
interface Ev { id: number | string; at: string; app: string; event: string; user_id: string | null; user_name: string; target: string; detail: string; ip: string; ok: boolean }
interface Page { events: Ev[]; next_before: string | null; apps: string[]; warning: string }

const APP_LABEL: Record<string, string> = {
  emily: 'Sicurezza', chat: 'Chat', projects: 'Progetti', files: 'Documenti', folders: 'Cartelle', shares: 'Condivisioni',
  artifacts: 'Artifact', approvals: 'Approvazioni', admin: 'Amministrazione', messaging: 'Messaggi', mail: 'Posta',
  remote: 'Assistenza', covercraft: 'Copertura', quotecraft: 'Preventivi', intranet: 'Intranet', reseller: 'Partner+',
  starlink: 'Starlink', connectcraft: 'Dispositivi (ConnectCraft)', devices: 'Dispositivi', netcraft: 'Monitoraggio (NetCraft)',
}

// Eventi di sicurezza scritti dai router: nomi in chiaro.
const EVENT_LABEL: Record<string, string> = {
  login_password: 'Accesso con password', login_google: 'Accesso con Google', login_pin: 'Accesso con PIN', login_failed: 'Accesso fallito (password errata)', login_unknown: 'Accesso con email sconosciuta', login_refused: 'Accesso rifiutato',
  google_login_unknown: 'Accesso Google con indirizzo sconosciuto', logout: 'Uscita', pin_failed: 'PIN errato',
  mfa_passkey_ok: 'Secondo fattore: passkey', mfa_totp_ok: 'Secondo fattore: app authenticator', mfa_email_ok: 'Secondo fattore: email',
  mfa_failed: 'Secondo fattore fallito', mfa_totp_failed: 'Codice authenticator errato', mfa_email_failed: 'Codice email errato',
  passkey_registered: 'Passkey registrata', passkey_deleted: 'Passkey eliminata', totp_enabled: 'Authenticator attivato', totp_disabled: 'Authenticator disattivato',
  recovery_ok: 'Codice di recupero usato', recovery_failed: 'Codice di recupero errato', recovery_regenerated: 'Codici di recupero rigenerati',
  password_changed: 'Password cambiata', password_reset: 'Password reimpostata', reset_code_sent: 'Codice di reimpostazione inviato', reset_failed: 'Reimpostazione fallita',
  member_created: 'Membro creato', member_updated: 'Membro modificato', member_deleted: 'Membro eliminato', invite_sent: 'Invito inviato',
  device_trusted: 'Dispositivo approvato', device_revoked: 'Dispositivo revocato', settings_changed: 'Impostazioni cambiate', family_renamed: 'Famiglia rinominata',
  user_models_changed: 'Modelli consentiti cambiati', mcp_created: 'Server MCP aggiunto', mcp_updated: 'Server MCP modificato', mcp_deleted: 'Server MCP eliminato',
  backup_exported: 'Backup scaricato', backup_import_scheduled: 'Ripristino programmato', memory_added: 'Memoria aggiunta', memory_self_deleted: 'Memoria eliminata',
  starlink_connected: 'Starlink collegato', starlink_disconnected: 'Starlink scollegato', gmail_oauth_linked: 'Gmail collegato', gmail_oauth_unlinked: 'Gmail scollegato',
  audit_exported: 'Log esportato',
  'connectcraft.session.open': 'Sessione richiesta', 'connectcraft.session.start': 'Sessione aperta', 'connectcraft.session.end': 'Sessione chiusa',
  'connectcraft.session.denied': 'Sessione negata', 'connectcraft.secret.set': 'Credenziale impostata', 'connectcraft.secret.clear': 'Credenziale rimossa',
  'connectcraft.connection.create': 'Dispositivo creato', 'connectcraft.connection.update': 'Dispositivo modificato', 'connectcraft.connection.delete': 'Dispositivo eliminato',
  'connectcraft.folder.create': 'Cartella creata', 'connectcraft.folder.update': 'Cartella modificata', 'connectcraft.folder.delete': 'Cartella eliminata',
  'connectcraft.grant.set': 'Permesso concesso', 'connectcraft.grant.delete': 'Permesso revocato',
}
const VERB: Record<string, string> = { create: 'creazione', update: 'modifica', delete: 'eliminazione', opened: 'app aperta', denied: 'accesso negato' }

/** `files.upload`, `projects.delete`, `quotecraft.opened` → testo leggibile. */
function label(e: Ev): string {
  if (EVENT_LABEL[e.event]) return EVENT_LABEL[e.event]
  const parts = e.event.split('.')
  const verb = parts[parts.length - 1]
  const what = parts.slice(1, VERB[verb] ? -1 : undefined).join(' ')
  return [VERB[verb] ? VERB[verb][0].toUpperCase() + VERB[verb].slice(1) : '', what].filter(Boolean).join(' · ') || e.event
}

// Gruppi pronti nel menu "Tipo di evento" (prefissi: finiscono con . o _)
const KINDS: [string, string][] = [
  ['', 'Tutti gli eventi'], ['login_', 'Accessi'], ['mfa_', 'Secondo fattore'], ['member_', 'Membri'],
  ['connectcraft.session.', 'Sessioni remote'], ['connectcraft.secret.', 'Credenziali (vault)'], ['connectcraft.grant.', 'Permessi dispositivi'],
]

const SEL = { width: 'auto', flex: '0 1 auto' }

interface Filter { q: string; user_id: string; app: string; event: string; ok: string; since: string; until: string }
const EMPTY: Filter = { q: '', user_id: '', app: '', event: '', ok: '', since: '', until: '' }

function query(f: Filter, before?: string | null) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v)
  if (before) p.set('before', before)
  return p.toString()
}

export default function AdminLog() {
  const [f, setF] = useState<Filter>(EMPTY)
  const [page, setPage] = useState<Page | null>(null)
  const [members, setMembers] = useState<User[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string | number | null>(null)

  async function load(filter = f, more = false) {
    setBusy(true)
    setErr('')
    try {
      const r = await get<Page>(`/admin/audit?${query(filter, more ? page?.next_before : null)}`)
      setPage(more && page ? { ...r, events: [...page.events, ...r.events] } : r)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    load()
    get<User[]>('/admin/members').then(setMembers).catch(() => {})
  }, [])

  const set = (k: keyof Filter) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  const apply = (next: Filter) => { setF(next); load(next) }

  return (
    <div className="stack">
      <p className="muted">Chi è entrato, da dove e cosa ha fatto: accessi, secondo fattore, membri e impostazioni, ogni modifica a documenti,
        progetti, chat e app, le app aperte e, da ConnectCraft, le sessioni remote e le credenziali usate. Mai i contenuti. Il registro non si può modificare né cancellare.</p>
      <form className="card row wrap" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); load() }}>
        <input placeholder="Cerca (evento, oggetto, IP, nome…)" value={f.q} onChange={set('q')} style={{ flex: '1 1 220px' }} />
        <select style={SEL} value={f.user_id} onChange={(e) => apply({ ...f, user_id: e.target.value })}>
          <option value="">Tutti gli utenti</option>
          {members.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
        </select>
        <select style={SEL} value={f.app} onChange={(e) => apply({ ...f, app: e.target.value })}>
          <option value="">Tutte le sezioni</option>
          {(page?.apps || []).map((a) => <option key={a} value={a}>{APP_LABEL[a] || a}</option>)}
        </select>
        <select style={SEL} value={f.event} onChange={(e) => apply({ ...f, event: e.target.value })}>
          {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select style={SEL} value={f.ok} onChange={(e) => apply({ ...f, ok: e.target.value })}>
          <option value="">Tutti gli esiti</option><option value="true">Riusciti</option><option value="false">Falliti o negati</option>
        </select>
        <label className="row" style={{ gap: 4 }}><span className="muted">dal</span><input type="date" style={SEL} value={f.since} onChange={(e) => apply({ ...f, since: e.target.value })} /></label>
        <label className="row" style={{ gap: 4 }}><span className="muted">al</span><input type="date" style={SEL} value={f.until} onChange={(e) => apply({ ...f, until: e.target.value })} /></label>
        <button type="submit" disabled={busy}>🔎 Filtra</button>
        <button type="button" onClick={() => apply(EMPTY)}>Azzera</button>
        <button type="button" title="Scarica il registro filtrato (fino a 5000 righe)" onClick={() => { window.location.href = `/api/admin/audit.csv?${query(f)}` }}>⬇ CSV</button>
      </form>
      {err && <div className="error">{err}</div>}
      {page?.warning && <div className="error">{page.warning}</div>}
      {!page ? <p className="muted">…</p> : page.events.length === 0 ? <p className="muted">Nessun evento con questi filtri.</p> : (
        <table className="table small">
          <thead><tr><th>Quando</th><th>Utente</th><th>Sezione</th><th>Evento</th><th>Oggetto</th><th>Esito</th><th>IP</th></tr></thead>
          <tbody>{page.events.map((e) => (
            <Fragment key={e.id}>
              <tr onClick={() => setOpen(open === e.id ? null : e.id)} style={{ cursor: 'pointer' }} title="Clicca per il dettaglio">
                <td style={{ whiteSpace: 'nowrap' }}>{new Date(e.at).toLocaleString('it-IT')}</td>
                <td>{e.user_name || (e.user_id ? e.user_id.slice(0, 8) : <span className="muted">—</span>)}</td>
                <td><span className="badge">{APP_LABEL[e.app] || e.app}</span></td>
                <td title={e.event}>{label(e)}</td>
                <td className="muted" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.target}</td>
                <td><span className={`badge ${e.ok ? 'ok' : 'err'}`}>{e.ok ? 'ok' : 'fallito'}</span></td>
                <td className="muted">{e.ip}</td>
              </tr>
              {open === e.id && (
                <tr><td colSpan={7}><code style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{e.event}{e.detail ? ` · ${e.detail}` : ''}{e.target ? ` · oggetto ${e.target}` : ''}</code></td></tr>
              )}
            </Fragment>
          ))}</tbody>
        </table>
      )}
      {page?.next_before && <button disabled={busy} onClick={() => load(f, true)}>Carica altri</button>}
    </div>
  )
}
