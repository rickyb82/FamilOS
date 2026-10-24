import { useEffect, useState } from 'react'
import Brand from '../components/Brand'
import { Link } from 'react-router-dom'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'

interface Kid { id: string; display_name: string; avatar: string }

export default function KidLogin() {
  const { refresh } = useAuth()
  const [trusted, setTrusted] = useState<boolean | null>(null)
  const [kids, setKids] = useState<Kid[]>([])
  const [sel, setSel] = useState<Kid | null>(null)
  const [pin, setPin] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    get('/auth/device').then((d) => {
      setTrusted(d.trusted)
      setKids(d.children)
    })
  }, [])

  async function login() {
    if (!sel) return
    setErr('')
    try {
      await post('/auth/pin-login', { user_id: sel.id, pin })
      await refresh()
    } catch (e: any) {
      setErr(e.message)
      setPin('')
    }
  }

  if (trusted === null) return <div className="center muted">…</div>
  if (!trusted)
    return (
      <div className="auth-page">
        <div className="auth-card stack">
          <h1><Brand size="large" /></h1>
          <p>Questo dispositivo non è ancora stato approvato da mamma o papà.</p>
          <p className="muted">Un genitore deve accedere da qui e, nelle Impostazioni, scegliere “Approva questo dispositivo per i bambini”.</p>
          <Link to="/login" className="link-btn">Accesso genitori</Link>
        </div>
      </div>
    )

  return (
    <div className="auth-page kids">
      <div className="auth-card stack">
        <h1>🏠 Ciao! Chi sei?</h1>
        {!sel ? (
          <div className="kid-grid">
            {kids.map((k) => (
              <button key={k.id} className="kid-tile" onClick={() => setSel(k)}>
                <span className="kid-avatar">{k.avatar}</span>
                <span>{k.display_name}</span>
              </button>
            ))}
            {kids.length === 0 && <p className="muted">Nessun bambino configurato.</p>}
          </div>
        ) : (
          <div className="stack center-text">
            <div className="kid-avatar big">{sel.avatar}</div>
            <h2>{sel.display_name}, scrivi il tuo PIN</h2>
            <div className="pin-dots">{'●'.repeat(pin.length).padEnd(4, '○')}</div>
            <div className="pin-pad">
              {['1', '2', '3', '4', '5', '6', '7', '8', '9', '⌫', '0', 'OK'].map((d) => (
                <button
                  key={d}
                  className={d === 'OK' ? 'primary' : ''}
                  onClick={() => {
                    if (d === '⌫') setPin(pin.slice(0, -1))
                    else if (d === 'OK') login()
                    else if (pin.length < 8) setPin(pin + d)
                  }}
                >
                  {d}
                </button>
              ))}
            </div>
            {err && <div className="error">{err}</div>}
            <button className="ghost" onClick={() => { setSel(null); setPin(''); setErr('') }}>← Non sono io</button>
          </div>
        )}
        <Link to="/login" className="link-btn small">Accesso genitori</Link>
      </div>
    </div>
  )
}
