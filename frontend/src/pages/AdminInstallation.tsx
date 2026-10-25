import { useSearchParams } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api, get, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useBranding } from '../lib/branding'
import AdminBranding from './AdminBranding'
import AdminUpdates from './AdminUpdates'
import PanelMenu from '../components/PanelMenu'
import '../panel-menu.css'

interface Certificate { domains: string[]; issuer: string; expires_at: string; days_remaining: number }
interface Installation {
  public_url: string; tls_mode: string; acme_email: string; acme_staging: boolean; rp_id: string
  google_origin: string; google_redirect_uri: string; gmail_redirect_uri: string; google_client_id: string
  google_secret_configured: boolean; manual_certificate: Certificate | null; proxy_pending: boolean; apply_command: string
}
const groups = [{ label: 'Sistema', items: [
  { id: 'brand', label: 'Nome e logo', icon: '◈' }, { id: 'domain', label: 'Dominio e passkey', icon: '◎' },
  { id: 'google', label: 'Accesso con Google', icon: 'G' }, { id: 'tls', label: 'HTTPS e certificati', icon: '▣' },
  { id: 'updates', label: 'Aggiornamenti e app', icon: '↻' },
] }]

export default function AdminInstallation() {
  const { me } = useAuth()
  const { brand } = useBranding()
  const [params] = useSearchParams()
  const [section, setSection] = useState(params.get('section') === 'updates' ? 'updates' : 'brand')
  useEffect(() => { if (params.get('section') === 'updates') setSection('updates') }, [params])
  const [config, setConfig] = useState<Installation | null>(null)
  const [url, setUrl] = useState('')
  const [mode, setMode] = useState('acme')
  const [email, setEmail] = useState('')
  const [staging, setStaging] = useState(false)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [cert, setCert] = useState<File | null>(null)
  const [key, setKey] = useState<File | null>(null)
  const [tls, setTls] = useState<(Partial<Certificate> & { ok: boolean; error?: string }) | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  async function load() {
    const c = await get<Installation>('/admin/installation')
    setConfig(c); setUrl(c.public_url); setMode(c.tls_mode); setEmail(c.acme_email); setStaging(c.acme_staging); setClientId(c.google_client_id)
  }
  useEffect(() => { if (me?.user.role === 'admin') load().catch(e => setError(e.message)) }, [me?.user.id])
  async function run(action: () => Promise<unknown>, success = '') {
    setBusy(true); setError(''); setMessage('')
    try { await action(); if (success) setMessage(success) }
    catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  async function saveDomain() {
    const c = await put<Installation>('/admin/installation', { public_url: url, tls_mode: mode, acme_email: email, acme_staging: staging })
    setConfig(c); setUrl(c.public_url)
    setMessage('Dominio e identità di accesso salvati. Applica ora la configurazione sul server.')
  }
  let previewOrigin = url.trim().replace(/\/$/, '')
  if (previewOrigin && !previewOrigin.includes('://')) previewOrigin = 'https://' + previewOrigin
  if (me?.user.role !== 'admin') return <div className="page"><h2>Accesso riservato</h2><p>Questa app è disponibile solo agli amministratori.</p></div>
  return <div className="page system-panel">
    <header className="system-panel-heading"><img src={brand.logo_url} alt="" /><div><p>AMMINISTRAZIONE DEL SERVER</p><h1>Installazione</h1><span className="muted">{brand.name} · dominio, identità e sicurezza</span></div></header>
    <div className="panel-shell"><PanelMenu groups={groups} active={section} onChange={id => { setSection(id); setMessage(''); setError('') }} label="Sezioni installazione" />
    <main className="panel-content">
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="ok" role="status">{message}</p>}
      {section === 'brand' && <AdminBranding />}
      {section === 'updates' && <AdminUpdates />}
      {section !== 'brand' && section !== 'updates' && !config && <p>Caricamento configurazione…</p>}
      {config && section === 'domain' && <section className="card stack">
        <h2>Il tuo indirizzo pubblico</h2><p className="muted">Un solo indirizzo per raggiungere il sistema, completare il login Google e registrare le passkey.</p>
        <label>Dominio o URL pubblico<input placeholder="https://casa.example.it" value={url} onChange={e => setUrl(e.target.value)} autoComplete="url" /></label>
        <div className="installation-urls"><small>Indirizzo previsto</small><code>{previewOrigin || 'https://casa.example.it'}</code><small>Dominio passkey (RP ID)</small><code>{config.rp_id}</code></div>
        <p className="muted small">Le passkey sono legate al dominio: dopo un cambio di dominio dovranno essere registrate sul nuovo indirizzo. Mantieni disponibile l’accesso con password o Google durante il passaggio.</p>
        <button disabled={busy || !url.trim()} onClick={() => run(saveDomain)}>Salva dominio</button>
        <div className="installation-deployment"><h3>Raggiungibilità del server</h3><ol><li>Fai puntare il DNS al server e configura il proxy o le porte pubbliche.</li><li>Scegli la modalità HTTPS nella sezione Certificati.</li><li>Dal progetto sul server esegui <code>{config.apply_command}</code>.</li></ol><p>Il comando applica dominio e TLS a Caddy. Il salvataggio nel pannello aggiorna subito gli URL di login e passkey.</p></div>
      </section>}
      {config && section === 'google' && <section className="card stack">
        <h2>Accesso con Google</h2><p className="muted">Crea un client OAuth di tipo “Applicazione web” nella console Google. Possono accedere soltanto gli adulti già presenti nella famiglia con la stessa email.</p>
        <div className="installation-urls"><small>Origine JavaScript autorizzata</small><code>{config.google_origin}</code><small>URI di reindirizzamento autorizzato per il login</small><code>{config.google_redirect_uri}</code><small>URI opzionale per l’email di sistema Gmail</small><code>{config.gmail_redirect_uri}</code></div>
        <label>Client ID Google<input value={clientId} onChange={e => setClientId(e.target.value)} autoComplete="off" /></label>
        <label>Client secret<input type="password" value={secret} onChange={e => setSecret(e.target.value)} autoComplete="new-password" placeholder={config.google_secret_configured ? 'Già configurato: lascia vuoto per conservarlo' : 'Client secret Google'} /></label>
        <button disabled={busy || !clientId.trim()} onClick={() => run(async () => { await put('/admin/providers', { values: { google_client_id: clientId.trim(), ...(secret ? { google_client_secret: secret } : {}) } }); setSecret(''); await load() }, 'Configurazione Google salvata.')}>Salva accesso Google</button>
        <details className="installation-deployment"><summary>Guida: abilita tutti gli account Google</summary><ol><li>In Google Cloud, configura Google Auth Platform → Branding con nome e contatto dell’installazione.</li><li>In Audience scegli <b>External / Esterno</b>, quindi <b>In production / In produzione</b>. Completa le verifiche richieste da Google.</li><li>Crea un client <b>Applicazione web</b> e registra esattamente origine e callback mostrati qui sopra.</li><li>Incolla Client ID e secret in questa pagina e salva.</li><li>In Famiglia aggiungi l’adulto con la stessa email Google: ora potrà usare “Continua con Google”.</li></ol><p>Gli account Workspace possono essere soggetti alle policy della loro famiglia. FamilOS non crea nuovi membri automaticamente e non abilita il login Google per i bambini.</p><p>Ogni installazione registra il proprio callback. Google non ammette URL jolly per domini arbitrari; nome e logo del consenso si impostano separatamente nella console Google.</p><a href="https://support.google.com/cloud/answer/15549945" target="_blank" rel="noreferrer">Guida ufficiale Google ↗</a></details>
        <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Apri la console Google ↗</a>
      </section>}
      {config && section === 'tls' && <section className="card stack">
        <h2>HTTPS e certificati</h2><p className="muted">Caddy gestisce emissione e rinnovo dei certificati automatici.</p>
        <label>Modalità HTTPS<select value={mode} onChange={e => setMode(e.target.value)}><option value="acme">Let’s Encrypt automatico</option><option value="internal">CA interna · rete locale</option><option value="manual">Certificato personale PEM</option></select></label>
        {mode === 'acme' && <><label>Email per Let’s Encrypt<input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="admin@example.it" /></label><label className="row"><input type="checkbox" checked={staging} onChange={e => setStaging(e.target.checked)} />Ambiente di prova Let’s Encrypt (certificato non attendibile)</label><p className="muted small">Il dominio pubblico deve risolvere sul server e le challenge devono raggiungerlo sulle porte 80/443. Il rinnovo è automatico finché queste condizioni restano valide.</p></>}
        {mode === 'internal' && <p className="muted">Per usare la CA locale, esportala con <code>./familai.sh ca-cert</code> e installala sui dispositivi della famiglia.</p>}
        {mode === 'manual' && <><p className="muted">Carica la catena del certificato e la chiave privata PEM non cifrata. La chiave resta sul server e non viene restituita al browser.</p><label>Dominio del certificato<input value={url} onChange={e => setUrl(e.target.value)} /></label><label>Certificato / catena PEM<input type="file" accept=".pem,.crt,.cer" onChange={e => setCert(e.target.files?.[0] || null)} /></label><label>Chiave privata PEM<input type="file" accept=".pem,.key" onChange={e => setKey(e.target.files?.[0] || null)} /></label><button disabled={busy || !cert || !key} onClick={() => run(async () => { const chosenUrl = url; const data = new FormData(); data.set('public_url', url); data.set('certificate', cert!); data.set('private_key', key!); await api('/admin/installation/certificate', { method: 'POST', body: data }); setCert(null); setKey(null); await load(); setUrl(chosenUrl); setMode('manual') }, 'Certificato verificato e caricato. Salva la modalità HTTPS per usarlo.')}>Verifica e carica certificato</button>{config.manual_certificate && <p>Certificato caricato: {config.manual_certificate.domains.join(', ')} · scade il {new Date(config.manual_certificate.expires_at).toLocaleDateString('it-IT')}.</p>}<p className="muted small">I certificati personali richiedono un nuovo caricamento prima della scadenza.</p></>}
        <button disabled={busy} onClick={() => run(saveDomain)}>Salva configurazione HTTPS</button>
        <div className="installation-deployment"><p>Applica sul server con <code>{config.apply_command}</code>. {config.proxy_pending ? 'Sono presenti modifiche da applicare al proxy.' : ''}</p></div>
        <button className="ghost" disabled={busy} onClick={() => run(async () => setTls(await get('/admin/installation/certificate/status')))}>Controlla certificato esposto</button>
        {tls && (tls.ok ? <p role="status">HTTPS verificato · {tls.domains?.join(', ')} · scadenza {tls.expires_at && new Date(tls.expires_at).toLocaleDateString('it-IT')} ({tls.days_remaining} giorni).</p> : <p role="status" className="muted">Verifica non riuscita: {tls.error}</p>)}
      </section>}
    </main></div>
  </div>
}
