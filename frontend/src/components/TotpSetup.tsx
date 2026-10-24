import { useEffect, useState } from 'react'
import { post } from '../lib/api'

/** Configurazione dell'app authenticator: mostra il QR (o il segreto da digitare) e chiede il primo codice. */
export default function TotpSetup({ onDone, onCancel }: { onDone: (recoveryCodes: string[]) => void; onCancel?: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr_svg: string | null } | null>(null)
  const [code, setCode] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    post('/auth/totp/setup').then(setSetup).catch((e) => setErr(e.message))
  }, [])

  async function confirm() {
    setErr('')
    setBusy(true)
    try {
      const r = await post('/auth/totp/confirm', { code })
      onDone(r.recovery_codes || [])
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack totp-setup">
      <p>Apri la tua app authenticator (Google Authenticator, Microsoft Authenticator, Authy, 1Password…) e inquadra il codice:</p>
      {setup ? (
        <>
          {setup.qr_svg ? (
            <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          ) : (
            <p className="muted">Aggiungi un account manualmente nell'app.</p>
          )}
          <details>
            <summary className="muted small">Non riesci a inquadrare? Inserisci il codice a mano</summary>
            <p className="muted small">Account: <b>Emily</b> · Tipo: basato sul tempo (TOTP)</p>
            <code className="secret">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</code>
          </details>
          <input placeholder="Codice a 6 cifre mostrato dall'app" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" autoFocus />
          {err && <div className="error">{err}</div>}
          <div className="row">
            <button onClick={confirm} disabled={busy || code.replace(/\D/g, '').length < 6}>{busy ? '…' : 'Attiva'}</button>
            {onCancel && <button className="ghost" type="button" onClick={onCancel}>Annulla</button>}
          </div>
        </>
      ) : err ? (
        <div className="error">{err}</div>
      ) : (
        <p className="muted">Preparo il codice…</p>
      )}
    </div>
  )
}
