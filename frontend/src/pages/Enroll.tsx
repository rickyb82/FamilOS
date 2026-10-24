import { useEffect, useState } from 'react'
import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import TotpSetup from '../components/TotpSetup'

/** Pagina mostrata a un adulto che deve attivare (o verificare) il secondo fattore prima di usare l'app. */
export default function Enroll() {
  const { me, refresh, logout } = useAuth()
  const [codes, setCodes] = useState<string[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const hasPasskey = !!me?.user.has_passkey
  const hasTotp = !!me?.user.has_totp
  const needsVerify = me && (hasPasskey || hasTotp) && !me.mfa_verified
  const [emailMfa, setEmailMfa] = useState(false)
  const [code, setCode] = useState('')
  const [sent, setSent] = useState('')
  const passkeys = me?.features?.passkeys !== false
  const optional = !!me?.features?.mfa_optional
  const [mode, setMode] = useState<'choose' | 'totp' | 'recovery'>(passkeys || optional ? 'choose' : 'totp')
  const skip = () => run(async () => { await post('/auth/mfa/choice', { choice: 'skip' }); await refresh() })
  useEffect(() => {
    get('/auth/features').then((f) => setEmailMfa(f.email_mfa)).catch(() => {})
  }, [])

  const run = async (fn: () => Promise<void>, fallbackMsg = 'Operazione annullata') => {
    setErr('')
    setBusy(true)
    try {
      await fn()
    } catch (e: any) {
      setErr(e.message || fallbackMsg)
    } finally {
      setBusy(false)
    }
  }

  const emailFlow = () => run(async () => {
    if (!sent) setSent((await post('/auth/mfa/email/send')).hint)
    else { await post('/auth/mfa/email/verify', { code }); await refresh() }
  })

  const register = () => run(async () => {
    const options = await post('/auth/webauthn/register/options')
    const credential = await startRegistration({ optionsJSON: options })
    const r = await post('/auth/webauthn/register/verify', { credential, name: navigator.platform || 'Passkey' })
    if (r.recovery_codes?.length) setCodes(r.recovery_codes)
    else await refresh()
  }, 'Registrazione annullata')

  const verifyPasskey = () => run(async () => {
    const options = await post('/auth/webauthn/authenticate/options')
    const credential = await startAuthentication({ optionsJSON: options })
    await post('/auth/webauthn/authenticate/verify', { credential })
    await refresh()
  }, 'Verifica annullata')

  const verifyTotp = () => run(async () => { await post('/auth/totp/verify', { code }); await refresh() })
  const verifyRecovery = () => run(async () => { await post('/auth/recovery', { code }); await refresh() })

  return (
    <div className="auth-page">
      <div className="auth-card stack">
        <h1>🔐 Sicurezza account</h1>
        {codes ? (
          <>
            <p><b>Salva questi codici di recupero</b> in un posto sicuro: servono se perdi il telefono o la passkey. Non verranno mostrati di nuovo.</p>
            <pre className="codes">{codes.join('\n')}</pre>
            <button onClick={() => refresh()}>Ho salvato i codici, continua</button>
          </>
        ) : needsVerify ? (
          <>
            <p>Conferma l'accesso con il tuo secondo fattore.</p>
            {err && <div className="error">{err}</div>}
            {hasPasskey && <button onClick={verifyPasskey} disabled={busy}>Usa la passkey</button>}
            {hasTotp && mode !== 'recovery' && (
              <div className="row">
                <input placeholder="Codice app authenticator" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" />
                <button onClick={verifyTotp} disabled={busy || code.replace(/\D/g, '').length < 6}>Verifica</button>
              </div>
            )}
            {emailMfa && (
              <>
                {sent && <input placeholder={`Codice inviato a ${sent}`} value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />}
                <button className="ghost" onClick={emailFlow} disabled={busy}>{sent ? 'Verifica codice email' : 'Ricevi un codice via email'}</button>
              </>
            )}
            {mode === 'recovery' ? (
              <div className="row">
                <input placeholder="xxxx-xxxx-xxxx" value={code} onChange={(e) => setCode(e.target.value)} />
                <button onClick={verifyRecovery} disabled={busy}>Usa il codice</button>
              </div>
            ) : (
              <button className="ghost small" onClick={() => { setCode(''); setMode('recovery') }}>Usa un codice di recupero</button>
            )}
          </>
        ) : mode === 'totp' ? (
          <TotpSetup onDone={(rc) => { if (rc.length) setCodes(rc); else refresh() }} onCancel={passkeys || optional ? () => setMode('choose') : undefined} />
        ) : (
          <>
            {optional
              ? <p>Vuoi proteggere il tuo account con un <b>secondo fattore</b>? È facoltativo: puoi attivarlo ora o più avanti dalle impostazioni.</p>
              : <p>Per proteggere l'accesso da remoto, Emily richiede {me?.features?.org_mode ? 'agli utenti' : 'agli adulti'} un <b>secondo fattore</b>. Scegli quello che preferisci (potrai aggiungere anche l'altro dalle impostazioni):</p>}
            {err && <div className="error">{err}</div>}
            {passkeys && <button onClick={register} disabled={busy}>{busy ? 'In attesa…' : '🔑 Passkey (Face ID, Touch ID, Windows Hello, chiave di sicurezza)'}</button>}
            <button onClick={() => setMode('totp')} disabled={busy}>📱 App authenticator (codice a 6 cifre)</button>
            {optional && <button className="ghost" onClick={skip} disabled={busy}>Entra senza secondo fattore</button>}
            {passkeys && <p className="muted small">La passkey è legata all'indirizzo del sito: se usi Emily da più indirizzi (es. localhost e il dominio di casa) l'app authenticator funziona ovunque.</p>}
          </>
        )}
        <button className="ghost" onClick={logout}>Esci</button>
      </div>
    </div>
  )
}
