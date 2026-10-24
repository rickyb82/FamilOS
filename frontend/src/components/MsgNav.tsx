import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { get } from '../lib/api'
import { calls } from '../lib/calls'
import { useLiveEvents } from '../lib/events'
import CallOverlay from './CallOverlay'

interface RoomLite { id: string; name: string; unread: number; muted: boolean }

/** Voce 💬 Messaggi del menu con il numero dei non letti, avvisi a comparsa per i messaggi nuovi e la finestra delle
 *  chiamate (sempre montata: lo squillo arriva su qualsiasi pagina). */
export default function MsgNav({ meId, onNavigate }: { meId: string; onNavigate: () => void }) {
  const loc = useLocation()
  const [rooms, setRooms] = useState<RoomLite[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const timer = useRef<number | null>(null)
  const load = () => get<RoomLite[]>('/msg/rooms').then(setRooms).catch(() => {})
  const reload = () => { if (timer.current) clearTimeout(timer.current); timer.current = window.setTimeout(load, 400) }
  useEffect(() => {
    load()
    calls.start(meId)
    get<{ id: string; name: string }[]>('/msg/contacts').then((cs) => setNames(Object.fromEntries(cs.map((c) => [c.id, c.name])))).catch(() => {})
  }, [meId])
  useEffect(() => { reload() }, [loc.pathname, loc.search])   // aprendo una conversazione i suoi non letti si azzerano

  useLiveEvents((e) => {
    if (e.kind === 'msg_read' || e.kind === 'msg_room') { reload(); return }
    if (e.kind !== 'msg' || e.update) return
    const m = e.message
    reload()
    if (!m || m.sender_id === meId || m.kind === 'system') return
    const here = loc.pathname === '/messages' && new URLSearchParams(loc.search).get('room') === m.room_id
    if (here && document.visibilityState === 'visible') return
    if (rooms.find((r) => r.id === m.room_id)?.muted) return
    const what = m.kind === 'file' ? (m.file?.mime?.startsWith('image/') ? '📷 Foto' : `📎 ${m.file?.name}`) : m.kind === 'call' ? `📞 ${m.text}` : m.text
    const who = e.room_name ? `${m.sender_name} · ${e.room_name}` : m.sender_name
    window.dispatchEvent(new CustomEvent('familai:toast', { detail: { id: `msg-${m.id}`, kind: 'msg', text: `💬 ${who}: ${String(what).slice(0, 140)}`, link: `/messages?room=${m.room_id}`, actor: '' } }))
    try { if (document.visibilityState !== 'visible' && 'Notification' in window && Notification.permission === 'granted') new Notification(who, { body: String(what).slice(0, 200), tag: m.room_id }) } catch {}
  })

  const unread = rooms.reduce((a, r) => a + (r.muted ? 0 : r.unread), 0)
  return (
    <>
      <NavLink to="/messages" onClick={onNavigate} className="msg-navlink">💬 Messaggi{unread > 0 && <span className="msg-badge">{unread > 99 ? '99+' : unread}</span>}</NavLink>
      <CallOverlay names={names} />
    </>
  )
}
