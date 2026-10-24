import { useEffect, useState, type FormEvent } from 'react'
import Brand from '../components/Brand'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { post } from '../lib/api'

/** Password dimenticata (invio codice) e impostazione nuova password (da codice o da link di invito). */
export default function Reset() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const [email, setEmail] = useState(params.get('email') ?? '')
  const [code, setCode] = useState(params.get('code') ?? '')
  const [password, setPassword] = useState('')
  const [step, setStep] = useState<'ask' | 'code' | 'done'>(params.get('code') ? 'code' : 'ask')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const invite = !!params.get('code')

  useEffect(() => {
    document.title = invite ? 'Imposta la password — Emily' : 'Password dimenticata — Emily'
  }, [invite])

  async function sendCode(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await post('/auth/forgot', { email })
      setStep('code')
    } catch (ex: any) {
      setErr(ex.message)
    } finally {
      setBusy(false)
    }
  }

  async function reset(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await post('/auth/reset', { email, code, new_password: password })
      setStep('done')
    } catch (ex: any) {
      setErr(ex.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1><Brand size="large" /></h1>
        {step === 'ask' && (
          <form onSubmit={sendCode} className="stack">
            <p>Inserisci la tua email: ti mandiamo un codice per impostare una nuova password.</p>
            <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            {err && <div className="error">{err}</div>}
            <button disabled={busy}>{busy ? 'Invio…' : 'Invia codice'}</button>
            <Link to="/login" className="link-btn">Torna al login</Link>
          </form>
        )}
        {step === 'code' && (
          <form onSubmit={reset} className="stack">
            <p>{invite ? 'Benvenuto/a! Scegli la password per il tuo account.' : `Se l'indirizzo è registrato, riceverai un codice via email. Inseriscilo qui sotto con la nuova password.`}</p>
            {!params.get('email') && <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />}
            {!invite && <input placeholder="Codice a 6 cifre" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" required autoFocus />}
            <input type="password" placeholder="Nuova password (min 10 caratteri)" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} required autoFocus={invite} />
            {err && <div className="error">{err}</div>}
            <button disabled={busy}>{busy ? '…' : 'Imposta password'}</button>
            {!invite && <button type="button" className="ghost" onClick={() => setStep('ask')}>Non ho ricevuto il codice</button>}
          </form>
        )}
        {step === 'done' && (
          <div className="stack">
            <p>✅ Password impostata. Ora puoi accedere{invite ? ': al primo accesso ti verrà chiesto di registrare una passkey' : ''}.</p>
            <button onClick={() => nav('/login')}>Vai al login</button>
          </div>
        )}
      </div>
    </div>
  )
}
