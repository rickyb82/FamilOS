import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { photoForUpload } from '../lib/photo'
import { api, ApiError, del, get, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useLiveEvents } from '../lib/events'
import { calls } from '../lib/calls'
import { canEdit, editorPath } from '../lib/artcraft'

interface Member { id: string; name: string; avatar: string; admin: boolean; online: boolean; last_read_at: string | null }
interface FileRef { id: string | null; name: string; mime: string; size: number; saved: boolean }
interface Msg { id: string; room_id: string; sender_id: string | null; sender_name: string; created_at: string; kind: 'text' | 'file' | 'system' | 'call'; text: string; deleted: boolean; reply_to: string | null; file: FileRef | null }
interface Room { id: string; kind: 'direct' | 'group'; name: string; group_name: string; admin: boolean; muted: boolean; last_at: string | null; unread: number; last: Msg | null; members: Member[]; call: { video: boolean; participants: string[] } | null }
interface Contact { id: string; name: string; avatar: string; online: boolean }

const time = (s: string) => new Date(s).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
function when(s: string | null) {
  if (!s) return ''
  const d = new Date(s), today = new Date()
  if (d.toDateString() === today.toDateString()) return time(s)
  const y = new Date(today); y.setDate(today.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return 'ieri'
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit' })
}
function dayLabel(s: string) {
  const d = new Date(s), today = new Date(), y = new Date(today); y.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return 'Oggi'
  if (d.toDateString() === y.toDateString()) return 'Ieri'
  return d.toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' })
}
const size = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`)
function preview(m: Msg | null, me: string) {
  if (!m) return ''
  const who = m.sender_id === me ? 'Tu: ' : ''
  if (m.deleted) return 'Messaggio eliminato'
  if (m.kind === 'file') return `${who}${m.file?.mime.startsWith('image/') ? '📷 Foto' : `📎 ${m.file?.name}`}${m.text ? ' · ' + m.text : ''}`
  if (m.kind === 'call') return `📞 ${m.text}`
  return who + m.text
}

/** 💬 Messaggi tra colleghi: conversazioni dirette e gruppi, foto e documenti, chiamate e videochiamate. */
export default function Messages() {
  const { me } = useAuth()
  const meId = me?.user.id || ''
  const nav = useNavigate()
  const [params] = useSearchParams()
  const roomId = params.get('room')
  const [rooms, setRooms] = useState<Room[]>([])
  const [contacts, setContacts] = useState<Contact[]>([])
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [more, setMore] = useState(false)
  const [q, setQ] = useState('')
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [typing, setTyping] = useState<Record<string, { name: string; at: number }>>({})
  const [picker, setPicker] = useState(false)
  const [info, setInfo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const list = useRef<HTMLDivElement>(null)
  const fileIn = useRef<HTMLInputElement>(null)
  const camIn = useRef<HTMLInputElement>(null)
  const lastTyping = useRef(0)
  const room = rooms.find((r) => r.id === roomId) || null
  const open = (id: string | null) => nav(id ? `/messages?room=${id}` : '/messages')

  const loadRooms = () => get<Room[]>('/msg/rooms').then(setRooms).catch((e) => setErr(e.message))
  useEffect(() => { loadRooms(); get<Contact[]>('/msg/contacts').then(setContacts).catch(() => {}) }, [])
  useEffect(() => {
    setMsgs([]); setInfo(false); setText('')
    if (!roomId) return
    get<{ messages: Msg[]; more: boolean }>(`/msg/rooms/${roomId}/messages`).then((r) => { setMsgs(r.messages); setMore(r.more); scrollEnd(false) }).catch((e) => setErr(e.message))
    post(`/msg/rooms/${roomId}/read`).then(() => setRooms((rs) => rs.map((r) => (r.id === roomId ? { ...r, unread: 0 } : r)))).catch(() => {})
  }, [roomId])

  function scrollEnd(smooth = true) { requestAnimationFrame(() => list.current?.scrollTo({ top: 1e9, behavior: smooth ? 'smooth' : 'auto' })) }
  async function loadOlder() {
    if (!roomId || !msgs.length) return
    const el = list.current, h = el?.scrollHeight || 0
    const r = await get<{ messages: Msg[]; more: boolean }>(`/msg/rooms/${roomId}/messages?before=${msgs[0].id}`)
    setMsgs((m) => [...r.messages, ...m]); setMore(r.more)
    requestAnimationFrame(() => { if (el) el.scrollTop = el.scrollHeight - h })
  }

  useLiveEvents((e) => {
    if (e.kind === 'msg') {
      const m: Msg = e.message
      if (m.room_id === roomId) {
        setMsgs((cur) => (e.update ? cur.map((x) => (x.id === m.id ? m : x)) : cur.some((x) => x.id === m.id) ? cur : [...cur, m]))
        if (!e.update) {
          const atEnd = list.current ? list.current.scrollHeight - list.current.scrollTop - list.current.clientHeight < 160 : true
          if (atEnd || m.sender_id === meId) scrollEnd()
          if (m.sender_id !== meId && document.visibilityState === 'visible') post(`/msg/rooms/${m.room_id}/read`).catch(() => {})
          setTyping((t) => { const n = { ...t }; if (m.sender_id) delete n[m.sender_id]; return n })
        }
      }
      setRooms((rs) => {
        const r = rs.find((x) => x.id === m.room_id)
        if (!r) { loadRooms(); return rs }
        const unread = m.room_id === roomId || m.sender_id === meId || e.update ? r.unread : r.unread + 1
        const nr = { ...r, last: e.update && r.last?.id !== m.id ? r.last : m, last_at: e.update ? r.last_at : m.created_at, unread }
        return e.update ? rs.map((x) => (x.id === r.id ? nr : x)) : [nr, ...rs.filter((x) => x.id !== r.id)]
      })
    } else if (e.kind === 'msg_read') {
      setRooms((rs) => rs.map((r) => (r.id === e.room_id ? { ...r, members: r.members.map((mm) => (mm.id === e.user_id ? { ...mm, last_read_at: e.at } : mm)) } : r)))
    } else if (e.kind === 'msg_typing' && e.room_id === roomId) {
      setTyping((t) => ({ ...t, [e.user_id]: { name: e.name, at: Date.now() } }))
    } else if (e.kind === 'msg_room' || e.kind === 'call') {
      if (e.kind === 'msg_room' && e.removed && e.room_id === roomId) open(null)
      if (e.kind === 'msg_room' || ['ring', 'joined', 'left', 'ended', 'update'].includes(e.type)) loadRooms()
    }
  })
  useEffect(() => { const t = setInterval(() => setTyping((ty) => Object.fromEntries(Object.entries(ty).filter(([, v]) => Date.now() - v.at < 4500))), 1500); return () => clearInterval(t) }, [])
  useEffect(() => {
    const v = () => { if (document.visibilityState === 'visible' && roomId) post(`/msg/rooms/${roomId}/read`).then(() => setRooms((rs) => rs.map((r) => (r.id === roomId ? { ...r, unread: 0 } : r)))).catch(() => {}) }
    document.addEventListener('visibilitychange', v); return () => document.removeEventListener('visibilitychange', v)
  }, [roomId])

  async function send() {
    const t = text.trim()
    if (!t || !roomId) return
    setText(''); setErr('')
    try { await post(`/msg/rooms/${roomId}/messages`, { text: t }) } catch (e: any) { setErr(e.message); setText(t) }
  }
  async function sendFiles(files: FileList | File[]) {
    if (!roomId) return
    setBusy(true); setErr('')
    for (const raw of Array.from(files).slice(0, 10)) {
      const f = await photoForUpload(raw)   // foto del telefono: dritte, JPEG, leggere
      const fd = new FormData(); fd.append('file', f)
      try { await api(`/msg/rooms/${roomId}/files`, { method: 'POST', body: fd }) } catch (e: any) { setErr(`${f.name}: ${e.message}`) }
    }
    setBusy(false)
  }
  function onType(v: string) {
    setText(v)
    if (roomId && v && Date.now() - lastTyping.current > 2500) { lastTyping.current = Date.now(); post(`/msg/rooms/${roomId}/typing`).catch(() => {}) }
  }

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return s ? rooms.filter((r) => r.name.toLowerCase().includes(s) || r.members.some((m) => m.name.toLowerCase().includes(s))) : rooms
  }, [rooms, q])
  const others = room ? room.members.filter((m) => m.id !== meId) : []
  const nameOf = useMemo(() => Object.fromEntries([...contacts.map((c) => [c.id, c.name]), ...(room?.members || []).map((m) => [m.id, m.name])]), [contacts, room])
  const typingNames = Object.entries(typing).filter(([id]) => id !== meId).map(([, v]) => v.name)
  const lastRead = (m: Msg) => others.length > 0 && others.every((o) => o.last_read_at && new Date(o.last_read_at) >= new Date(m.created_at))

  return (
    <div className={`msg-page ${roomId ? 'has-room' : ''}`}>
      <aside className="msg-side">
        <div className="msg-side-head">
          <h2>💬 Messaggi</h2>
          <button className="primary msg-new" onClick={() => setPicker(true)} title="Nuova conversazione o gruppo">＋</button>
        </div>
        <input placeholder="Cerca una conversazione…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="msg-rooms">
          {shown.map((r) => {
            const online = r.kind === 'direct' && r.members.some((m) => m.id !== meId && m.online)
            return (
              <button key={r.id} className={`msg-room ${r.id === roomId ? 'active' : ''}`} onClick={() => open(r.id)}>
                <span className={`msg-avatar ${online ? 'online' : ''}`}>{r.kind === 'group' ? '👥' : r.members.find((m) => m.id !== meId)?.avatar || '🙂'}</span>
                <span className="msg-room-main">
                  <span className="row between"><b>{r.name}</b><small className={r.unread ? 'msg-unread-time' : 'muted'}>{when(r.last?.created_at || r.last_at)}</small></span>
                  <span className="row between"><small className="muted msg-preview">{r.call ? '🟢 Chiamata in corso' : preview(r.last, meId)}</small>{r.unread > 0 && <span className="msg-badge">{r.unread}</span>}</span>
                </span>
              </button>
            )
          })}
          {rooms.length === 0 && <p className="muted small" style={{ padding: 12 }}>Nessuna conversazione. Premi ＋ per scrivere a un collega o creare un gruppo.</p>}
        </div>
      </aside>

      <section className="msg-main" onDragOver={(e) => { if (room) { e.preventDefault(); setDrag(true) } }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) sendFiles(e.dataTransfer.files) }}>
        {!room ? (
          <div className="msg-empty"><div>💬</div><p>Scegli una conversazione o premi ＋ per iniziarne una.</p><small className="muted">Foto e documenti finiscono anche in Documenti › Messaggi.</small></div>
        ) : (
          <>
            <header className="msg-head">
              <button className="icon msg-back" onClick={() => open(null)} title="Indietro">←</button>
              <span className="msg-avatar">{room.kind === 'group' ? '👥' : others[0]?.avatar || '🙂'}</span>
              <span className="msg-head-main" onClick={() => setInfo(!info)}>
                <b>{room.name}</b>
                <small className="muted">{typingNames.length ? `${typingNames.join(', ')} sta scrivendo…` : room.kind === 'group' ? room.members.map((m) => (m.id === meId ? 'tu' : m.name)).join(', ') : others[0]?.online ? 'online' : 'non collegato'}</small>
              </span>
              {room.call && !calls.state.roomId && <button className="primary small" onClick={() => calls.join(room.id, room.call!.video, room.name)}>Entra nella chiamata</button>}
              <button className="icon msg-call" title="Chiamata" onClick={() => calls.join(room.id, false, room.name)}>📞</button>
              <button className="icon msg-call" title="Videochiamata" onClick={() => calls.join(room.id, true, room.name)}>🎥</button>
              <button className="icon" title="Informazioni" onClick={() => setInfo(!info)}>ⓘ</button>
            </header>
            <div className="msg-body">
              <div className="msg-list" ref={list}>
                {more && <div className="center"><button className="ghost small" onClick={loadOlder}>Messaggi precedenti</button></div>}
                {msgs.map((m, i) => {
                  const prev = msgs[i - 1]
                  const newDay = !prev || new Date(prev.created_at).toDateString() !== new Date(m.created_at).toDateString()
                  const mine = m.sender_id === meId
                  const sameAuthor = prev && prev.sender_id === m.sender_id && !newDay && prev.kind !== 'system'
                  return (
                    <div key={m.id}>
                      {newDay && <div className="msg-day"><span>{dayLabel(m.created_at)}</span></div>}
                      {m.kind === 'system' ? <div className="msg-system">{m.text}</div>
                        : m.kind === 'call' ? <div className="msg-system">📞 {m.text} · {time(m.created_at)}</div>
                        : (
                          <div className={`msg-row ${mine ? 'mine' : ''}`}>
                            <div className={`msg-bubble ${m.deleted ? 'deleted' : ''}`} onContextMenu={(e) => { if (mine && !m.deleted) { e.preventDefault(); if (confirm('Eliminare il messaggio per tutti?')) del(`/msg/messages/${m.id}`).catch((er) => setErr(er.message)) } }}>
                              {room.kind === 'group' && !mine && !sameAuthor && <div className="msg-author">{m.sender_name}</div>}
                              {m.deleted ? <i className="muted">🚫 Messaggio eliminato</i> : (
                                <>
                                  {m.file && <FileBubble f={m.file} features={me?.features} />}
                                  {m.text && <div className="msg-text">{m.text}</div>}
                                </>
                              )}
                              <span className="msg-meta">{time(m.created_at)}{mine && !m.deleted && <span className={`msg-tick ${lastRead(m) ? 'read' : ''}`} title={lastRead(m) ? 'Letto' : 'Inviato'}>{lastRead(m) ? ' ✓✓' : ' ✓'}</span>}</span>
                            </div>
                          </div>
                        )}
                    </div>
                  )
                })}
              </div>
              {info && <RoomInfo room={room} meId={meId} contacts={contacts} onClose={() => setInfo(false)} onLeft={() => { open(null); loadRooms() }} onChanged={loadRooms} />}
            </div>
            {err && <div className="error" style={{ margin: '0 12px' }}>{err}</div>}
            {drag && <div className="msg-drop">Rilascia per inviare</div>}
            <footer className="msg-compose">
              <div className="msg-pill">
                <button className="icon" title="Allega foto o documenti" disabled={busy} onClick={() => fileIn.current?.click()}>{busy ? '⏳' : '📎'}</button>
                <input ref={fileIn} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) sendFiles(e.target.files); e.target.value = '' }} />
                <input ref={camIn} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { if (e.target.files?.length) sendFiles(e.target.files); e.target.value = '' }} />
                <textarea rows={1} placeholder="Messaggio" value={text} onChange={(e) => onType(e.target.value)}
                  onPaste={(e) => { const fs = Array.from(e.clipboardData.files || []); if (fs.length) { e.preventDefault(); sendFiles(fs) } }}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !window.matchMedia('(pointer: coarse)').matches) { e.preventDefault(); send() } }} />
                <button className="icon touch-only" title="Scatta una foto" disabled={busy} onClick={() => camIn.current?.click()}>📷</button>
              </div>
              <button className="primary msg-send" disabled={!text.trim()} onClick={send} title="Invia">➤</button>
            </footer>
          </>
        )}
      </section>
      {picker && <NewRoomDialog contacts={contacts} onClose={() => setPicker(false)} onCreated={(r) => { setPicker(false); setRooms((rs) => [r, ...rs.filter((x) => x.id !== r.id)]); open(r.id) }} />}
    </div>
  )
}

function FileBubble({ f, features }: { f: FileRef; features: any }) {
  if (!f.saved || !f.id) return <div className="msg-file muted">📎 {f.name} · non salvato (spazio esaurito)</div>
  const url = `/files/${f.id}`
  if (f.mime.startsWith('image/')) return <a href={url} target="_blank" rel="noreferrer"><img className="msg-img" src={url} alt={f.name} loading="lazy" /></a>
  if (f.mime.startsWith('video/')) return <video className="msg-img" src={url} controls preload="metadata" />
  return (
    <div className="msg-file">
      <span className="msg-file-icon">📄</span>
      <span className="msg-file-main"><b>{f.name}</b><small className="muted">{size(f.size)}</small></span>
      {canEdit(f.name, features) ? <a className="ghost small btn-link" href={editorPath(f.id, f.name)}>Apri</a> : <a className="ghost small btn-link" href={url} target="_blank" rel="noreferrer">Apri</a>}
      <a className="ghost small btn-link" href={url} download={f.name} title="Scarica">⬇</a>
    </div>
  )
}

function NewRoomDialog({ contacts, onClose, onCreated }: { contacts: Contact[]; onClose: () => void; onCreated: (r: Room) => void }) {
  const [sel, setSel] = useState<string[]>([])
  const [name, setName] = useState('')
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  const group = sel.length > 1
  const shown = contacts.filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()))
  async function create(ids = sel) {
    try { onCreated(await post<Room>('/msg/rooms', { members: ids, name, group: ids.length > 1 })) } catch (e: any) { setErr(e instanceof ApiError ? e.message : String(e)) }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal stack" onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: 'calc(100vw - 24px)' }}>
        <div className="row between"><h3 style={{ margin: 0 }}>Nuova conversazione</h3><button className="icon" onClick={onClose}>✕</button></div>
        <small className="muted">Clicca una persona per scriverle subito, oppure spunta più persone per creare un gruppo.</small>
        <input placeholder="Cerca un collega…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <div className="msg-contacts">
          {shown.map((c) => (
            <div key={c.id} className="msg-contact">
              <input type="checkbox" checked={sel.includes(c.id)} onChange={() => setSel((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id]))} />
              <button className="ghost msg-contact-btn" onClick={() => (sel.length ? setSel((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id])) : create([c.id]))}>
                <span className={`msg-avatar small ${c.online ? 'online' : ''}`}>{c.avatar || '🙂'}</span>{c.name}
              </button>
            </div>
          ))}
          {contacts.length === 0 && <p className="muted small">Nessun collega con i Messaggi abilitati.</p>}
        </div>
        {group && <input placeholder="Nome del gruppo" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />}
        {err && <div className="error">{err}</div>}
        {sel.length > 0 && <button className="primary" onClick={() => create()}>{group ? `Crea il gruppo (${sel.length + 1} persone)` : 'Scrivi'}</button>}
      </div>
    </div>
  )
}

function RoomInfo({ room, meId, contacts, onClose, onLeft, onChanged }: { room: Room; meId: string; contacts: Contact[]; onClose: () => void; onLeft: () => void; onChanged: () => void }) {
  const [name, setName] = useState(room.group_name)
  const [add, setAdd] = useState('')
  const [err, setErr] = useState('')
  const run = async (fn: () => Promise<any>) => { setErr(''); try { await fn(); onChanged() } catch (e: any) { setErr(e.message) } }
  const notIn = contacts.filter((c) => !room.members.some((m) => m.id === c.id))
  return (
    <aside className="msg-info">
      <div className="row between"><b>{room.kind === 'group' ? 'Info gruppo' : 'Info contatto'}</b><button className="icon" onClick={onClose}>✕</button></div>
      {room.kind === 'group' && room.admin && (
        <div className="row" style={{ gap: 6 }}><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /><button className="ghost" disabled={!name.trim() || name === room.group_name} onClick={() => run(() => patch(`/msg/rooms/${room.id}`, { name }))}>Rinomina</button></div>
      )}
      <small className="muted">{room.members.length} {room.members.length === 1 ? 'persona' : 'persone'}</small>
      <ul className="msg-members">
        {room.members.map((m) => (
          <li key={m.id}>
            <span className={`msg-avatar small ${m.online ? 'online' : ''}`}>{m.avatar || '🙂'}</span>
            <span style={{ flex: 1 }}>{m.id === meId ? 'Tu' : m.name}{m.admin && room.kind === 'group' ? <small className="muted"> · amministratore</small> : null}</span>
            {room.kind === 'group' && room.admin && (m.admin
              ? (room.members.filter((x) => x.admin).length > 1 && <button className="ghost small" title={m.id === meId ? 'Lascia il ruolo di amministratore' : 'Togli il ruolo di amministratore'} onClick={() => confirm(m.id === meId ? 'Lasciare il ruolo di amministratore del gruppo?' : `Togliere a ${m.name} il ruolo di amministratore?`) && run(() => patch(`/msg/rooms/${room.id}`, { revoke_admin: [m.id] }))}>– admin</button>)
              : <button className="ghost small" title="Rendi amministratore del gruppo" onClick={() => run(() => patch(`/msg/rooms/${room.id}`, { make_admin: [m.id] }))}>+ admin</button>)}
            {room.kind === 'group' && room.admin && m.id !== meId && <button className="icon" title="Togli dal gruppo" onClick={() => confirm(`Togliere ${m.name} dal gruppo?`) && run(() => patch(`/msg/rooms/${room.id}`, { remove: [m.id] }))}>✕</button>}
          </li>
        ))}
      </ul>
      {room.kind === 'group' && room.admin && notIn.length > 0 && (
        <div className="row" style={{ gap: 6 }}>
          <select value={add} onChange={(e) => setAdd(e.target.value)}><option value="">Aggiungi una persona…</option>{notIn.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <button className="ghost" disabled={!add} onClick={() => run(async () => { await patch(`/msg/rooms/${room.id}`, { add: [add] }); setAdd('') })}>Aggiungi</button>
        </div>
      )}
      <label className="row"><input type="checkbox" checked={room.muted} onChange={(e) => run(() => patch(`/msg/rooms/${room.id}`, { muted: e.target.checked }))} /> Silenzia gli avvisi</label>
      <small className="muted">Foto e documenti di questa conversazione sono anche in Documenti › Messaggi › {room.name}.</small>
      {room.kind === 'group' && <button className="ghost danger" onClick={() => confirm('Uscire dal gruppo?') && run(async () => { await post(`/msg/rooms/${room.id}/leave`); onLeft() })}>Esci dal gruppo</button>}
      {err && <div className="error">{err}</div>}
    </aside>
  )
}
