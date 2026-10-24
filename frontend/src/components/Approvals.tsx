import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { get, post } from '../lib/api'

interface Req { id: string; child: string; text: string; reason: string; subject: string; status: string; decision: string | null; created_at: string; decided_by: string | null; valid_until: string | null }

/** Campanello degli adulti: richieste di permesso dei bambini (compiti) con decisione al volo. Polling ogni 15 s;
 *  notifica del browser (se concessa) quando arriva una richiesta nuova. */
export default function Approvals() {
  const [data, setData] = useState<{ pending: Req[]; recent: Req[]; count: number } | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const btn = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 8, top: 48 })
  // pannello sopra tutta la pagina (portal su body), ancorato al pulsante e dentro lo schermo
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = btn.current?.getBoundingClientRect()
      if (!r) return
      const w = Math.min(380, window.innerWidth - 16)
      setPos({ left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)), top: r.bottom + 8 })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])
  const seen = useRef<Set<string>>(new Set())
  const first = useRef(true)

  async function load() {
    try {
      const d = await get('/approvals/notifications')
      setData(d)
      const fresh = d.pending.filter((r: Req) => !seen.current.has(r.id))
      d.pending.forEach((r: Req) => seen.current.add(r.id))
      if (!first.current && fresh.length) {
        setOpen(true)
        try {
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification(`${fresh[0].child} chiede il permesso`, { body: fresh[0].text.slice(0, 120) })
          }
        } catch {}
      }
      first.current = false
    } catch {}
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  async function decide(id: string, decision: string, scope: string) {
    setBusy(id)
    try { await post(`/approvals/${id}/decide`, { decision, scope }); await load() } finally { setBusy('') }
  }
  function askNotifications() {
    try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission() } catch {}
  }

  const count = data?.count ?? 0
  return (
    <div className="bell-wrap">
      <button ref={btn} className={`icon bell ${count ? 'has' : ''}`} title="Richieste di permesso" onClick={() => { setOpen((o) => !o); askNotifications() }}>
        🔔{count > 0 && <span className="badge">{count}</span>}
      </button>
      {open && data && createPortal(
        <div className="bell-panel" style={pos}>
          <div className="row between"><b>Richieste dei bambini</b><button className="icon" onClick={() => setOpen(false)}>✕</button></div>
          {data.pending.length === 0 && <p className="muted small">Nessuna richiesta in attesa.</p>}
          {data.pending.map((r) => (
            <div key={r.id} className="req">
              <div><b>{r.child}</b> <small className="muted">· {new Date(r.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}{r.subject && ` · ${r.subject}`}</small></div>
              <div className="req-text">“{r.text}”</div>
              {r.reason && <small className="muted">Motivo: {r.reason}</small>}
              <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
                <button className="small" disabled={busy === r.id} onClick={() => decide(r.id, 'approve', 'once')} title="Risponde con la soluzione completa, solo per questa richiesta">✓ Approva</button>
                <button className="small" disabled={busy === r.id} onClick={() => decide(r.id, 'approve_tutor', 'once')} title="Aiuta a capire senza dare la soluzione">🎓 Aiuto guidato</button>
                <button className="ghost small" disabled={busy === r.id} onClick={() => decide(r.id, 'approve', 'today')} title="Compiti liberi per il resto della giornata">Per oggi</button>
                <button className="ghost small danger" disabled={busy === r.id} onClick={() => decide(r.id, 'deny', 'once')}>Nega</button>
              </div>
            </div>
          ))}
          {data.recent.length > 0 && (
            <details><summary className="muted small">Ultime decisioni</summary>
              {data.recent.map((r) => (
                <div key={r.id} className="small muted" style={{ padding: '4px 0' }}>
                  {r.decision === 'deny' ? '🚫' : r.decision === 'approve_tutor' ? '🎓' : r.status === 'expired' ? '⌛' : '✓'} {r.child}: “{r.text.slice(0, 70)}{r.text.length > 70 ? '…' : ''}”{r.decided_by && ` · ${r.decided_by}`}{r.valid_until && ' · per oggi'}
                </div>
              ))}
            </details>
          )}
        </div>,
        document.body,
      )}
    </div>
  )
}
