import { useEffect, useMemo, useState } from 'react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import EditorHint, { HintToggle } from '../components/EditorHint'

/**
 * ✉️ Posta: la web app MailCraft (open source, WebAssembly) incorniciata a tutta pagina. Il server MailCraft gira in un
 * container di FamilAI e tiene una cartella per utente: il token che chiediamo a /api/mail/session vale solo per le
 * caselle di chi e' loggato. L'app parla con il suo server su /mailcraft/api (?server=), non con il backend FamilAI.
 */
export default function Mail() {
  const { me } = useAuth()
  const [token, setToken] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    // prima il server MailCraft dietro il proxy (/mailcraft/api/health), poi il token: cosi' un container fermo o un proxy non
    // ricaricato danno un messaggio chiaro invece di una pagina vuota (o di FamilAI annidato in se stesso)
    fetch('/mailcraft/api/health', { cache: 'no-store' })
      .then(async (r) => {
        const ct = r.headers.get('content-type') || ''
        if (!r.ok || !ct.includes('json')) throw new Error(`il server MailCraft non risponde su /mailcraft/ (HTTP ${r.status}${ct.includes('html') ? ', pagina HTML' : ''}): container attivo? \`./familai.sh mailcraft up\`, poi \`./familai.sh update\` per ricaricare il proxy`)
        return get<{ token: string; path: string; lang: string }>('/mail/session')
      })
      .then((r) => setToken(r.token))
      .catch((e) => setErr(e.message))
  }, [])

  const src = useMemo(() => {
    if (!token) return ''
    const q = new URLSearchParams({ server: `${window.location.origin}/mailcraft`, token, lang: 'it' })
    return `/mailcraft/?${q.toString()}`
  }, [token])

  async function logout() {
    if (!confirm('Scollegare la posta su tutti i dispositivi? Gli account restano configurati: al prossimo accesso rientri automaticamente.')) return
    setBusy(true)
    try { await post('/mail/logout', {}); const r = await get<{ token: string }>('/mail/session'); setToken(r.token) } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!me?.features?.mailcraft_web) {
    return (
      <div className="page">
        <h2>✉️ Posta</h2>
        <p className="muted">La posta non è attiva su questo server. Un amministratore può avviarla con <code>./familai.sh mailcraft up</code>.</p>
      </div>
    )
  }
  return (
    <div className="editor-layout">
      <header className="chat-header">
        <div className="title">✉️ Posta <small className="muted">· MailCraft · solo le tue caselle</small></div>
        <div className="row">
          <HintToggle id="mail" />
          <a className="ghost btn-link" href={src} target="_blank" rel="noreferrer" title="Apri in una scheda a parte">⧉</a>
          <button className="ghost" disabled={busy} onClick={logout} title="Revoca i token di posta di questo utente">Scollega</button>
        </div>
      </header>
      {err && <div className="error">{err}</div>}
      {src ? (
        <iframe className="editor-frame" src={src} title="MailCraft" allow="clipboard-read; clipboard-write" />
      ) : !err && <div className="editor-frame center muted">Caricamento…</div>}
      <EditorHint id="mail">
        Prima volta: <b>Impostazioni › Account</b> dentro la posta, oppure chiedi in chat "configura la mia casella …@…". Puoi aggiungere più caselle;
        password e messaggi restano su questo server, in una cartella solo tua. Serve un browser con WebGPU o WebGL2.
      </EditorHint>
    </div>
  )
}
