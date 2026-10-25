import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { del, get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useLiveEvents } from '../lib/events'

interface Notice { id: string; kind: string; text: string; link: string; actor: string; read: boolean; created_at: string }

/** 🔔 Campanella: notifiche (condivisioni ricevute…) con contatore, elenco a tendina e avviso a comparsa in tempo reale. */
export default function Notifications() {
  const nav = useNavigate()
  const { me } = useAuth()
  const admin = me?.user.role === 'admin'
  const [update, setUpdate] = useState<any>(null)
  const [updateError, setUpdateError] = useState('')
  const [checking, setChecking] = useState(false)
  const available = admin && update?.offers?.core?.newer
  async function checkUpdates() {
    setChecking(true); setUpdateError('')
    try {
      const current = await get('/admin/updates')
      setUpdate(current)
      if (!current.connected) throw new Error(current.message || 'Agente aggiornamenti non disponibile')
      if (['running', 'interrupted', 'recovery_required'].includes(current.job?.status)) return
      await post('/admin/updates/check', { target: 'core', channel: current.channel })
      setUpdate(await get('/admin/updates'))
    } catch (e: any) { setUpdateError(e.message) }
    finally { setChecking(false) }
  }
  useEffect(() => {
    if (!admin) return
    let alive = true
    const refresh = () => get('/admin/updates').then(s => { if (alive) setUpdate(s) }).catch(() => {})
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [admin])
  const [items, setItems] = useState<Notice[]>([])
  const [unread, setUnread] = useState(0)
  const [open, setOpen] = useState(false)
  const [toasts, setToasts] = useState<(Notice & { leaving?: boolean })[]>([])
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number }>({ left: 8, bottom: 56 })
  const btn = useRef<HTMLButtonElement>(null)
  const seen = useRef<Set<string>>(new Set())
  // il menu sta sopra tutta la pagina (portal su body): lo ancoriamo al pulsante, dentro lo schermo
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = btn.current?.getBoundingClientRect()
      if (!r) return
      const w = Math.min(360, window.innerWidth - 16)
      const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8))
      setPos(r.top > window.innerHeight / 2 ? { left, bottom: window.innerHeight - r.top + 8 } : { left, top: r.bottom + 8 })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])
  const dismiss = (id: string) => {
    setToasts((t) => t.map((x) => (x.id === id ? { ...x, leaving: true } : x)))
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 260)
  }
  const load = () => get<{ unread: number; items: Notice[] }>('/notifications').then((r) => { r.items.forEach((n) => seen.current.add(n.id)); setItems(r.items); setUnread(r.unread) }).catch(() => {})
  useEffect(() => { load() }, [])
  useLiveEvents((e) => {
    if (e.kind === 'hello') { load(); return }
    if (e.kind !== 'notification') return
    const n: Notice = { id: e.id, kind: e.nkind, text: e.text, link: e.link, actor: e.actor, read: false, created_at: new Date().toISOString() }
    if (seen.current.has(n.id)) return   // la stessa notifica arrivata due volte (riconnessione del flusso)
    seen.current.add(n.id)
    setItems((l) => [n, ...l]); setUnread((u) => u + 1)
    setToasts((t) => [...t.filter((x) => !x.leaving).slice(-3), n])
    setTimeout(() => dismiss(n.id), 8000)
    try { if ('Notification' in window && Notification.permission === 'granted') new Notification(n.actor || 'Emily', { body: n.text, tag:n.kind }) } catch {}
  })
  // avvisi a comparsa di altre parti dell'app (es. un messaggio nuovo): stessi toast, senza finire nella campanella
  useEffect(() => {
    const h = (ev: Event) => {
      const d = (ev as CustomEvent).detail || {}
      if (!d.id || seen.current.has(d.id)) return
      seen.current.add(d.id)
      const n: Notice = { id: d.id, kind: d.kind || 'info', text: String(d.text || ''), link: d.link || '', actor: d.actor || '', read: true, created_at: new Date().toISOString() }
      setToasts((t) => [...t.filter((x) => !x.leaving).slice(-3), n])
      setTimeout(() => dismiss(n.id), 8000)
    }
    window.addEventListener('familai:toast', h)
    return () => window.removeEventListener('familai:toast', h)
  }, [])
  useEffect(() => { if (!open) return; const close = () => setOpen(false); window.addEventListener('click', close); return () => window.removeEventListener('click', close) }, [open])
  async function toggle(e: React.MouseEvent) {
    e.stopPropagation()
    const next = !open; setOpen(next)
    if (next && unread > 0) { try { await post('/notifications/read'); setUnread(0); setItems((l) => l.map((n) => ({ ...n, read: true }))) } catch {} }
  }
  // una notifica cliccata (o chiusa con la ✕) sparisce dalla campanella, anche sul server
  const remove = (id: string) => {
    if (items.some((x) => x.id === id && !x.read)) setUnread((u) => Math.max(0, u - 1))
    setItems((l) => l.filter((x) => x.id !== id))
    del(`/notifications/${encodeURIComponent(id)}`).catch(() => {})
  }
  const clearAll = () => { setItems([]); setUnread(0); del('/notifications').catch(() => {}) }
  const go = (n: Notice) => {
    setOpen(false)
    if (items.some((x) => x.id === n.id)) remove(n.id)
    if (n.link) nav(n.link)
  }
  const icon = (k: string) => k.startsWith('share_file') ? '📄' : k.startsWith('share_conversation') ? '💬' : k.startsWith('share_project') ? '📁' : k === 'starlink' ? '🛰' : k === 'radiocraft' ? '📡' : k === 'netcraft' ? '📶' : '🔔'
  return (
    <>
      <button ref={btn} className={`icon bell ${unread || available ? 'has' : ''}`} onClick={toggle} title={available ? "Notifiche · Aggiornamento disponibile" : "Notifiche"}>🔔{available && <span aria-label="Aggiornamento disponibile">↻</span>}{unread > 0 && <span className="bell-count">{unread > 9 ? '9+' : unread}</span>}</button>
      {open && createPortal(
        <div className={`bell-menu ${pos.bottom != null ? 'up' : 'down'}`} style={pos} onClick={(e) => e.stopPropagation()}>
          <div className="row between"><b>Notifiche</b>
            <span className="row">
              {'Notification' in window && Notification.permission === 'default' && <button className="ghost small" onClick={() => Notification.requestPermission()}>attiva avvisi del browser</button>}
              {items.length > 0 && <button className="ghost small" onClick={clearAll} title="Cancella tutte le notifiche">cancella tutte</button>}
            </span>
          </div>
          {admin && <section className="card stack" aria-label="Aggiornamenti FamilOS">
            <b>{available ? '↻ Aggiornamento FamilOS disponibile' : 'Aggiornamenti FamilOS'}</b>
            {available && <span>{update.offers.core.tag}</span>}
            <button disabled={checking || update?.job?.status === 'running'} onClick={checkUpdates}>{checking || update?.job?.status === 'running' ? 'Operazione in corso…' : 'Cerca aggiornamenti'}</button>
            {available && <button onClick={() => { setOpen(false); nav('/installation?section=updates') }}>Vedi e installa aggiornamento</button>}
            {updateError && <p role="alert">{updateError}</p>}
            {update?.job?.status === 'failed' && <p role="alert">{update.job.message}</p>}
          </section>}
          {items.length === 0 && <p className="muted small">Nessuna notifica.</p>}
          <ul>
            {items.slice(0, 30).map((n) => (
              <li key={n.id} className={n.read ? '' : 'unread'} onClick={() => go(n)}>
                {n.kind !== 'starlink' && n.kind !== 'msg' && <span className="bell-ico">{icon(n.kind)}</span>}
                <span className="bell-text"><span>{n.text}</span><br /><small className="muted">{new Date(n.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</small></span>
                <button className="icon bell-x" title="Cancella" onClick={(e) => { e.stopPropagation(); remove(n.id) }}>✕</button>
              </li>
            ))}
          </ul>
        </div>,
        document.body,
      )}
      {createPortal(
        <div className="toasts" aria-live="polite">
          {toasts.map((n) => (
            <div key={n.id} className={`toast ${n.leaving ? 'leaving' : ''} ${n.kind === 'starlink' && n.text.startsWith('🛰') ? 'alert' : ''}`} onClick={() => { dismiss(n.id); go(n) }}>
              {n.kind !== 'starlink' && n.kind !== 'msg' && <span className="bell-ico">{icon(n.kind)}</span>}
              <span className="toast-text">{n.actor && <small className="muted">{n.actor}<br /></small>}{n.text}</span>
              <button className="icon toast-x" title="Chiudi" onClick={(e) => { e.stopPropagation(); dismiss(n.id) }}>✕</button>
              <i className="toast-bar" />
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
