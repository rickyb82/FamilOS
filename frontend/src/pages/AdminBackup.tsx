import { useEffect, useState } from 'react'
import { HEADERS, get } from '../lib/api'

interface Info { counts: Record<string, number>; db_bytes: number; files_bytes: number; pending_restore: boolean }

const mb = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`)

/** Backup completo cifrato (utenti, chat, file, impostazioni, chiavi API) e ripristino su un'installazione nuova. */
export default function AdminBackup() {
  const [info, setInfo] = useState<Info | null>(null)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [ipw, setIpw] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [restoring, setRestoring] = useState<null | { manifest: any; seconds: number }>(null)

  useEffect(() => {
    get('/admin/backup/info').then(setInfo).catch((e) => setErr(e.message))
  }, [])

  // dopo l'import: aspetta che il backend riparta, poi torna al login
  useEffect(() => {
    if (!restoring) return
    let secs = 0
    const t = setInterval(async () => {
      secs += 2
      setRestoring((r) => (r ? { ...r, seconds: secs } : r))
      try {
        const r = await fetch('/api/health', { cache: 'no-store' })
        if (r.ok && secs >= 6) {
          clearInterval(t)
          window.location.href = '/'
        }
      } catch {}
    }, 2000)
    return () => clearInterval(t)
  }, [!!restoring])

  async function doExport() {
    setErr(''); setMsg('')
    if (pw.length < 8) return setErr('La password del backup deve avere almeno 8 caratteri')
    if (pw !== pw2) return setErr('Le due password non coincidono')
    setBusy('export')
    try {
      const r = await fetch('/api/admin/backup/export', { method: 'POST', credentials: 'include',
        headers: { ...HEADERS, 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText)
      const name = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '')?.[1] || 'familai-backup.fab'
      const blob = await r.blob()
      const u = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = u; a.download = name; a.click(); URL.revokeObjectURL(u)
      setMsg(`Backup scaricato: ${name} (${mb(blob.size)}). Conserva la password: senza non si può ripristinare.`)
      setPw(''); setPw2('')
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  async function doImport() {
    setErr(''); setMsg('')
    if (!file) return setErr('Scegli il file di backup (.fab)')
    if (!ipw) return setErr('Inserisci la password del backup')
    if (!confirm) return setErr('Conferma di voler sovrascrivere i dati attuali')
    setBusy('import')
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('password', ipw)
      const r = await fetch('/api/admin/backup/import', { method: 'POST', credentials: 'include', headers: HEADERS, body: fd })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText)
      const data = await r.json()
      setRestoring({ manifest: data.manifest, seconds: 0 })
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  if (restoring) {
    const c = restoring.manifest?.counts || {}
    return (
      <div className="stack">
        <section className="card stack">
          <h3>♻️ Ripristino in corso…</h3>
          <p>Il backend si sta riavviando e sta applicando il backup del {restoring.manifest?.created_at ? new Date(restoring.manifest.created_at).toLocaleString() : '—'}
            {' '}({c.users ?? '?'} membri, {c.conversations ?? '?'} chat, {c.files ?? '?'} file).</p>
          <div className="working"><span className="dots"><i /><i /><i /></span><span>Attendo che il servizio torni disponibile</span><span className="muted small">{restoring.seconds}s</span></div>
          <p className="muted">Al termine verrai riportato alla pagina di accesso: entra con le credenziali e il secondo fattore dell'installazione da cui proviene il backup (le passkey valgono solo se l'indirizzo del sito è lo stesso; in alternativa usa l'app authenticator o un codice di recupero).</p>
        </section>
      </div>
    )
  }

  return (
    <div className="stack">
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}
      {info?.pending_restore && (
        <div className="error">C'è un ripristino in attesa di essere applicato al prossimo riavvio del backend.
          <button className="ghost small" style={{ marginLeft: 8 }} onClick={async () => { await fetch('/api/admin/backup/pending', { method: 'DELETE', credentials: 'include', headers: HEADERS }); setInfo(await get('/admin/backup/info')) }}>Annulla</button>
        </div>
      )}

      <section className="card stack">
        <h3>💾 Esporta backup completo</h3>
        <p className="muted">Un unico file cifrato con tutto: membri e permessi, chat e messaggi, progetti e documenti, artifact, server MCP, impostazioni e chiavi API dei provider, SMTP, Google, passkey, authenticator e codici di recupero.
          {info && <> Attualmente: {info.counts.users} membri · {info.counts.conversations} chat · {info.counts.messages} messaggi · {info.counts.projects} progetti · {info.counts.files} file · DB {mb(info.db_bytes)} + file {mb(info.files_bytes)}.</>}</p>
        <div className="grid2">
          <label>Password del backup <small className="muted">(min. 8 caratteri; protegge le chiavi API)</small><input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <label>Ripeti password<input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} /></label>
        </div>
        <div><button disabled={busy !== ''} onClick={doExport}>{busy === 'export' ? 'Preparo il file…' : '⬇️ Scarica backup (.fab)'}</button></div>
        <small className="muted">Il file viene salvato anche in <code>data/backups/</code> sul server. Da terminale resta disponibile <code>./familai.sh backup</code> (copia non cifrata della cartella dati, legata alla stessa SECRET_KEY).</small>
      </section>

      <section className="card stack">
        <h3>♻️ Ripristina da backup</h3>
        <p className="muted">Per un'installazione nuova: avvia Emily, entra con l'amministratore iniziale del <code>.env</code>, vieni qui e carica il file. <b>Sostituisce tutti i dati attuali</b> (ne viene tenuta una copia in <code>data/pre-restore-…</code>). Le chiavi vengono ricifrate con la SECRET_KEY di questa installazione, quindi non serve che coincida con quella vecchia. Al termine tutti devono accedere di nuovo.</p>
        <div className="grid2">
          <label>File di backup (.fab)<input type="file" accept=".fab" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label>Password del backup<input type="password" autoComplete="off" value={ipw} onChange={(e) => setIpw(e.target.value)} /></label>
        </div>
        <label className="tool-toggle"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> Ho capito: i dati attuali di questa installazione verranno sostituiti</label>
        <div><button className="danger" disabled={busy !== '' || !file || !confirm} onClick={doImport}>{busy === 'import' ? 'Carico…' : '♻️ Ripristina e riavvia'}</button></div>
      </section>
    </div>
  )
}
