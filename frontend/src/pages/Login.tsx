import { useEffect, useState, type FormEvent } from 'react'
import { isNativeApp } from '../lib/native'
import Brand from '../components/Brand'
import { Link, useSearchParams } from 'react-router-dom'
import { startAuthentication } from '@simplewebauthn/browser'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'

const PASSKEY_HINT = 'emily.passkey'

/** Aperta dall'icona sulla schermata Home (app installata o app nativa) e non in una scheda del browser. */
export function isStandalone(): boolean {
  return isNativeApp() || window.matchMedia?.('(display-mode: standalone)').matches || (navigator as any).standalone === true
}

function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

/** Icona del volto / impronta, disegnata: sugli iPhone «Face ID», altrove l'impronta. */
function BioIcon() {
  if (isIOS()) {
    return (
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
        <path d="M9 9.5v1M15 9.5v1M12 9v4.2a.8.8 0 0 1-.8.8H11M9 15.2c.8.9 1.8 1.3 3 1.3s2.2-.4 3-1.3" />
      </svg>
    )
  }
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6.5 5.8A8 8 0 0 1 20 12v2M4.4 9.2A8 8 0 0 0 4 12v3.5M7.8 20.5A13 13 0 0 1 7 15.6V12a5 5 0 0 1 9.6-2M17 13.5v2.2c0 1.9-.3 3.6-.9 5.1M10.4 21.3A17 17 0 0 0 11 16v-4a1 1 0 0 1 2 0v3.5c0 1.5-.2 3-.6 4.3" />
    </svg>
  )
}

type Step = 'start' | 'password' | 'passkey' | 'recovery' | 'emailcode' | 'totp'

export default function Login() {
  const { refresh } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [step, setStep] = useState<Step>('start')
  const [methods, setMethods] = useState<{ passkey: boolean; totp: boolean }>({ passkey: true, totp: false })
  const [code, setCode] = useState('')
  const [features, setFeatures] = useState<{ email: boolean; email_mfa: boolean; google: boolean; passkeys?: boolean; app_name?: string } | null>(null)
  const [params] = useSearchParams()
  const GOOGLE_ERRORS: Record<string, string> = {
    google_non_configurato: "L'accesso con Google non è configurato.",
    google_annullato: 'Accesso con Google annullato.',
    google_stato_non_valido: 'Sessione di accesso scaduta, riprova.',
    google_errore_token: 'Google non ha confermato l\'accesso, riprova.',
    google_email_non_verificata: 'Email Google non verificata.',
    google_utente_non_registrato: 'Questa email Google non corrisponde a nessun utente: chiedi all\'amministratore di aggiungerti con lo stesso indirizzo.',
  }
  const [hint, setHint] = useState('')
  const [err, setErr] = useState(GOOGLE_ERRORS[params.get('error') ?? ''] ?? '')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    get('/auth/features').then(setFeatures).catch(() => setFeatures({ email: false, email_mfa: false, google: false, passkeys: true }))
  }, [])
  const passkeys = features?.passkeys !== false
  // Senza passkey la pagina parte direttamente da email e password
  useEffect(() => { if (features && !passkeys && step === 'start') setStep('password') }, [features, passkeys])

  // App installata sul telefono e gia' entrata con la passkey: propone subito Face ID / impronta
  const [autoTried, setAutoTried] = useState(false)
  useEffect(() => {
    if (autoTried || !features || !passkeys) return
    setAutoTried(true)
    let remembered = false
    try { remembered = localStorage.getItem(PASSKEY_HINT) === '1' } catch { /* storage non disponibile */ }
    if (remembered && isStandalone() && !params.get('error')) passkeyLogin(true)
  }, [features, passkeys])

  async function passkeyLogin(silent = false) {
    setErr('')
    setBusy(true)
    try {
      const r = await post<{ key: string; options: any }>('/auth/passkey/options')
      const credential = await startAuthentication({ optionsJSON: r.options })
      await post('/auth/passkey/verify', { key: r.key, credential })
      try { localStorage.setItem(PASSKEY_HINT, '1') } catch { /* ok */ }
      await refresh()
    } catch (e: any) {
      if (!silent) setErr(e?.name === 'NotAllowedError' ? 'Accesso annullato. Se su questo dispositivo non hai ancora una passkey, entra con email e password: la registri dopo.' : (e.message || 'Accesso con la passkey non riuscito'))
    } finally {
      setBusy(false)
    }
  }

  async function sendEmailCode() {
    setErr('')
    setBusy(true)
    try {
      const r = await post('/auth/mfa/email/send')
      setHint(r.hint)
      setStep('emailcode')
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function submitEmailCode(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await post('/auth/mfa/email/verify', { code })
      await refresh()
    } catch (ex: any) {
      setErr(ex.message)
    } finally {
      setBusy(false)
    }
  }

  async function submitPassword(e: FormEvent) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const r = await post('/auth/login', { email, password })
      if (r.status === 'mfa_required') {
        const m = r.methods ?? { passkey: true, totp: false }
        setMethods(m)
        setCode('')
        if (m.passkey) {
          setStep('passkey')
          await doPasskey()
        } else {
          setStep('totp')
        }
      } else {
        await refresh()
      }
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function doPasskey() {
    setErr('')
    setBusy(true)
    try {
      const options = await post('/auth/webauthn/authenticate/options')
      const credential = await startAuthentication({ optionsJSON: options })
      await post('/auth/webauthn/authenticate/verify', { credential })
      try { localStorage.setItem(PASSKEY_HINT, '1') } catch { /* ok */ }
      await refresh()
    } catch (e: any) {
      setErr(e.message || 'Verifica passkey annullata')
    } finally {
      setBusy(false)
    }
  }

  async function submitTotp(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await post('/auth/totp/verify', { code })
      await refresh()
    } catch (ex: any) {
      setErr(ex.message)
    } finally {
      setBusy(false)
    }
  }

  async function submitRecovery(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await post('/auth/recovery', { code })
      await refresh()
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const bioLabel = isIOS() ? 'Accedi con Face ID' : 'Accedi con l\'impronta'
  const back = (to: Step) => <button type="button" className="login-link" onClick={() => { setErr(''); setCode(''); setStep(to) }}>← Indietro</button>

  return (
    <div className="login">
      <div className="login-hero">
        <div className="login-logo"><Brand size="large" /></div>
        <p className="login-claim">Il tuo spazio, insieme. Con Emily.</p>
      </div>

      <div className="login-sheet">
        {step === 'start' && (
          <div className="login-stack">
            <button className="login-primary" onClick={() => passkeyLogin()} disabled={busy}>
              <BioIcon />
              <span>{busy ? 'Accesso in corso…' : bioLabel}</span>
            </button>
            <p className="login-note">Senza password: con la passkey salvata su questo telefono o computer.</p>
            {err && <div className="login-error">{err}</div>}
            <div className="login-divider"><span>oppure</span></div>
            <button type="button" className="login-secondary" onClick={() => { setErr(''); setStep('password') }} disabled={busy}>Accedi con email e password</button>
            {features?.google && (
              <a className="login-secondary" href="/api/auth/google/start">
                <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6C12.3 13.1 17.6 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z"/><path fill="#FBBC05" d="M10.4 28.8A14.5 14.5 0 0 1 9.5 24c0-1.7.3-3.3.8-4.8l-7.8-6A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.8l7.8-6z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.4 0-11.7-3.6-13.6-8.7l-7.8 6C6.5 42.6 14.6 48 24 48z"/></svg>
                Accedi con Google
              </a>
            )}
          </div>
        )}

        {step === 'password' && (
          <form onSubmit={submitPassword} className="login-stack">
            <h2 className="login-title">Email e password</h2>
            <label className="login-field">
              <span>Email</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus autoComplete="username" inputMode="email" placeholder="nome@azienda.it" />
            </label>
            <label className="login-field">
              <span>Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" placeholder="••••••••" />
            </label>
            {err && <div className="login-error">{err}</div>}
            <button className="login-primary" disabled={busy}>{busy ? 'Accesso in corso…' : 'Accedi'}</button>
            <div className="login-row">
              {passkeys ? back('start') : <span />}
              {features?.email && <Link to="/forgot" className="login-link">Password dimenticata?</Link>}
            </div>
            {!passkeys && features?.google && (
              <a className="login-secondary" href="/api/auth/google/start">Accedi con Google</a>
            )}
          </form>
        )}

        {step === 'passkey' && (
          <div className="login-stack">
            <h2 className="login-title">Conferma che sei tu</h2>
            <p className="login-note">Usa il volto, l'impronta o la chiave di sicurezza registrata.</p>
            {err && <div className="login-error">{err}</div>}
            <button className="login-primary" onClick={doPasskey} disabled={busy}><BioIcon /><span>{busy ? 'In attesa…' : 'Usa la passkey'}</span></button>
            {methods.totp && <button className="login-secondary" onClick={() => { setErr(''); setCode(''); setStep('totp') }} disabled={busy}>Usa l'app authenticator</button>}
            {features?.email_mfa && <button className="login-secondary" onClick={sendEmailCode} disabled={busy}>Ricevi un codice via email</button>}
            <div className="login-row">
              {back('password')}
              <button type="button" className="login-link" onClick={() => { setErr(''); setCode(''); setStep('recovery') }}>Codice di recupero</button>
            </div>
          </div>
        )}

        {step === 'totp' && (
          <form onSubmit={submitTotp} className="login-stack">
            <h2 className="login-title">Codice dell'app authenticator</h2>
            <input className="login-code" placeholder="000000" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} required autoFocus />
            {err && <div className="login-error">{err}</div>}
            <button className="login-primary" disabled={busy || code.replace(/\D/g, '').length < 6}>Accedi</button>
            {methods.passkey && <button type="button" className="login-secondary" onClick={() => { setStep('passkey'); doPasskey() }}>Usa la passkey</button>}
            {features?.email_mfa && <button type="button" className="login-secondary" onClick={sendEmailCode} disabled={busy}>Ricevi un codice via email</button>}
            <div className="login-row">
              {back('password')}
              <button type="button" className="login-link" onClick={() => { setErr(''); setCode(''); setStep('recovery') }}>Codice di recupero</button>
            </div>
          </form>
        )}

        {step === 'emailcode' && (
          <form onSubmit={submitEmailCode} className="login-stack">
            <h2 className="login-title">Codice via email</h2>
            <p className="login-note">Inviato a {hint}. Vale 10 minuti.</p>
            <input className="login-code" placeholder="000000" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} required autoFocus />
            {err && <div className="login-error">{err}</div>}
            <button className="login-primary" disabled={busy}>Accedi</button>
            <div className="login-row">{back(methods.passkey ? 'passkey' : 'password')}</div>
          </form>
        )}

        {step === 'recovery' && (
          <form onSubmit={submitRecovery} className="login-stack">
            <h2 className="login-title">Codice di recupero</h2>
            <p className="login-note">Uno dei codici salvati quando hai attivato il secondo fattore.</p>
            <input className="login-code" placeholder="xxxx-xxxx-xxxx" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" required autoFocus />
            {err && <div className="login-error">{err}</div>}
            <button className="login-primary" disabled={busy}>Accedi</button>
            <div className="login-row">{back(methods.passkey ? 'passkey' : 'password')}</div>
          </form>
        )}
      </div>
    </div>
  )
}
