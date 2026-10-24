import { lockSession } from './LockScreen'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { get, post, type Conversation } from '../lib/api'
import { useAuth } from '../lib/auth'
import Brand from './Brand'
import Guide from '../pages/Guide'

/** Small, protected navigation for the child interface. Adult apps use Desktop. */
export default function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth()
  const navigate = useNavigate()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [open, setOpen] = useState(false)
  const [guide, setGuide] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const load = () => get<Conversation[]>('/chat/conversations').then(setConversations).catch(() => {})
    load()
    window.addEventListener('familai:conversations', load)
    return () => window.removeEventListener('familai:conversations', load)
  }, [])
  async function newChat() {
    try { const conversation = await post<Conversation>('/chat/conversations', {}); navigate('/c/' + conversation.id); setOpen(false) }
    catch (e: any) { setError(e.message) }
  }
  return <div className={`shell ${open ? 'open' : ''}`}><aside className="sidebar"><div className="brand"><Brand /><button className="icon" onClick={() => setOpen(false)} aria-label="Chiudi menu">×</button></div>
    <button className="primary wide" onClick={newChat}>＋ Nuova chat</button><nav className="navlinks"><NavLink to="/projects" onClick={() => setOpen(false)}>◈ Progetti</NavLink><NavLink to="/browser" onClick={() => setOpen(false)}>◎ Esplora</NavLink><NavLink to="/settings" onClick={() => setOpen(false)}>⚙ Il mio profilo</NavLink></nav>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="conv-list">{conversations.map(c => <NavLink className="conv" key={c.id} to={'/c/' + c.id} onClick={() => setOpen(false)}>{c.title}</NavLink>)}</div>
    <button className="ghost" onClick={() => setGuide(true)}>Come funziona</button><button className="ghost" onClick={()=>lockSession()}>Blocca schermo</button><div className="userbox"><span>{me?.user.avatar}</span><span>{me?.user.display_name}</span><button className="icon" onClick={logout} title="Esci">↪</button></div>
  </aside><main className="main"><button className="icon menu-btn" onClick={() => setOpen(true)} aria-label="Apri menu">☰</button>{children}</main>
    {guide && <div className="guide-modal-bg" onClick={() => setGuide(false)}><div className="guide-modal" onClick={e => e.stopPropagation()}><Guide onClose={() => setGuide(false)} /></div></div>}
  </div>
}
