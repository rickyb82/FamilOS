import { zoneStyle, snapZone, zones } from "../lib/windowLayout"
import AppIcon from "./AppIcon"
import { lockSession } from './LockScreen'
import DesktopCalls from './DesktopCalls'
import AppLauncher from './AppLauncher'
import { useLiveEvents } from '../lib/events'
import { useBranding } from '../lib/branding'
import { useCallback, useEffect, useRef, useState, type PointerEvent } from 'react'
import { useAuth } from '../lib/auth'
import { get, type Conversation, type FileOut } from '../lib/api'
import { appForPath, desktopApps, restoreWindows, windowAppId, WindowActivity, WindowCommands, type AppEntry, type DesktopWindow } from '../lib/desktop'
import { editorFor, editorPath } from '../lib/artcraft'
import { wireNative } from '../lib/native'
import WindowRouter from './WindowRouter'
import Notifications from './Notifications'
import Approvals from './Approvals'
import Guide from '../pages/Guide'
import { WorkspaceRoutes } from '../WorkspaceRoutes'
import '../desktop.css'

const uid = () => crypto.randomUUID()
const initialPath = () => window.location.pathname + window.location.search


export default function Desktop() {
  const { brand } = useBranding()
  const { me, logout } = useAuth()
  const storageKey = `familos:desktop:v1:${me!.user.id}`
  const [windows, setWindows] = useState<DesktopWindow[]>(() => restoreWindows(storageKey).map(w => ({ ...w, minimized: true })))
  const [active, setActive] = useState<string | null>(null)
  const [showHome, setShowHome] = useState(true)
  const [launcherOpen, setLauncherOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [switcher, setSwitcher] = useState(false)
  const [guide, setGuide] = useState(false)
  const [files, setFiles] = useState<FileOut[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [recentError, setRecentError] = useState('')
  const [online, setOnline] = useState(navigator.onLine)
  const [now, setNow] = useState(new Date())
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight })
  const state = useRef(windows)
  state.current = windows
  const board = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const booted = useRef(false)
  const osFullscreen = useRef(false)
  const [osFull,setOsFull] = useState(false)
  const [layoutMenu,setLayoutMenu] = useState(false)
  const [snap,setSnap] = useState<DesktopWindow['mode']|null>(null)
  const maximizedFrom=useRef(new Map<string,DesktopWindow['mode']>())
  async function toggleOSFullscreen(){
    if(document.fullscreenElement){osFullscreen.current=false;setOsFull(false);await document.exitFullscreen().catch(()=>{});return}
    try{await document.documentElement.requestFullscreen();osFullscreen.current=true;setOsFull(true)}catch{setFullscreenError('Il browser non consente lo schermo intero. Usa il comando schermo intero del browser.')}
  }
  const [fullscreenError,setFullscreenError]=useState('')
  function maximize(w:DesktopWindow){
    if(fullscreen.current?.id===w.id)return
    const mode=w.mode==='full'?(maximizedFrom.current.get(w.id)||'float'):'full'
    if(w.mode!=='full')maximizedFrom.current.set(w.id,w.mode)
    arrange(w.id,mode)
  }
  function tile(count:2|4){
    if(fullscreen.current)leaveFullscreen()
    const visible=state.current.filter(w=>!w.minimized).slice(-count)
    setWindows(ws=>ws.map(w=>{const i=visible.findIndex(v=>v.id===w.id);return i<0?w:{...w,mode:count===2?(i===0?'left':'right'):zones[i]}}))
    if(visible.length){setShowHome(false);setActive(visible[visible.length-1].id)}
    setLayoutMenu(false)
  }
  const fullscreen = useRef<{id:string; previous:DesktopWindow['mode']} | null>(null)
  const [fullId, setFullId] = useState<string | null>(null)
  function leaveFullscreen() {
    const saved = fullscreen.current
    fullscreen.current = null; setFullId(null)
    if (saved) setWindows(ws=>ws.map(w=>w.id===saved.id?{...w,mode:saved.previous}:w))
    if (document.fullscreenElement && !osFullscreen.current) document.exitFullscreen().catch(()=>{})
  }
  function toggleFullscreen(w:DesktopWindow) {
    if (fullscreen.current?.id===w.id) {leaveFullscreen();return}
    const previous=fullscreen.current
    if(previous)setWindows(ws=>ws.map(x=>x.id===previous.id?{...x,mode:previous.previous}:x))
    fullscreen.current={id:w.id,previous:w.mode};setFullId(w.id)
    setWindows(ws=>ws.map(x=>x.id===w.id?{...x,mode:'full',minimized:false}:x));setActive(w.id);setShowHome(false)
    // Request from the click gesture; CSS fills the viewport if the browser declines.
    document.documentElement.requestFullscreen?.().catch(()=>{})
  }
  useEffect(()=>{
    const changed=()=>{if (!document.fullscreenElement){osFullscreen.current=false;setOsFull(false);if(fullscreen.current)leaveFullscreen()}}
    const escaped=(e:KeyboardEvent)=>{if(e.key==='Escape' && !document.fullscreenElement && fullscreen.current) leaveFullscreen()}
    const locked=()=>{osFullscreen.current=false;setOsFull(false);leaveFullscreen()}
    document.addEventListener('fullscreenchange',changed);window.addEventListener('keydown',escaped);window.addEventListener('familos:locked',locked)
    return ()=>{document.removeEventListener('fullscreenchange',changed);window.removeEventListener('keydown',escaped);window.removeEventListener('familos:locked',locked);if(document.fullscreenElement)document.exitFullscreen().catch(()=>{})}
  },[])
  useEffect(()=>{if(me?.session_locked){osFullscreen.current=false;setOsFull(false);leaveFullscreen()}},[me?.session_locked])
  const [installed, setInstalled] = useState<AppEntry[]>([])
  useEffect(() => {
    let alive = true
    const refresh = () => get<Array<{id:string;name:string;enabled:boolean;description:string;icon:string}>>('/installed-apps').then(items => {
      if (alive) setInstalled(items.filter(a=>a.enabled).map(a=>({id:'installed-'+a.id,title:a.name,icon:'▦',iconUrl:'data:image/png;base64,'+a.icon,color:'lavender',path:'/installed/'+a.id,description:a.description})))
    }).catch(()=>{})
    refresh(); const timer = window.setInterval(refresh, 10000)
    return () => {alive=false;clearInterval(timer)}
  }, [])
  const apps = [...desktopApps(me!), ...installed]

  const focus = useCallback((id: string) => {
    setWindows(ws => {
      const target = ws.find(w => w.id === id)
      if (!target) return ws
      return [...ws.filter(w => w.id !== id), { ...target, minimized: false }]
    })
    setActive(id); setShowHome(false); setSwitcher(false); setLauncherOpen(false)
  }, [])

  const open = useCallback((path: string, fresh = false) => {
    if (!path.startsWith('/') || path.startsWith('//')) return
    if (path === '/app') { setShowHome(true); return }
    // Authentication notification links are handled by the session gate, not an app window.
    if (['/login', '/enroll', '/reset', '/forgot'].includes(path.split('?')[0])) {
      window.location.assign(path); return
    }
    setSearchOpen(false); setLauncherOpen(false)
    const app = appForPath(path)
    const existing = state.current.find(w => app.id === 'editor' ? w.path === path || (appForPath(w.path).id !== 'editor' && windowAppId(w.path) === windowAppId(path)) : windowAppId(w.path) === app.id)
    if (existing && !fresh) {
      if (path !== app.path || app.id === 'editor') setWindows(ws => ws.map(w => w.id === existing.id ? { ...w, path } : w))
      if(fullscreen.current && fullscreen.current.id!==existing.id)toggleFullscreen(existing)
      focus(existing.id); return
    }
    const offset = (state.current.length % 5) * 28
    const id = uid()
    const previousFullscreen=fullscreen.current
    if(previousFullscreen){fullscreen.current={id,previous:'float'};setFullId(id)}
    setWindows(ws => [...ws.map(w=>previousFullscreen?.id===w.id?{...w,mode:previousFullscreen.previous}:w), { id, path, x: 36 + offset, y: 26 + offset,
      width: Math.min(1000, window.innerWidth - 80), height: Math.min(700, window.innerHeight - 185), minimized: false, mode: previousFullscreen?'full':'float' }])
    setActive(id); setShowHome(false); setSwitcher(false); setLauncherOpen(false)
  }, [focus])

  const navigateWindow = useCallback((id: string, path: string) => {
    const current = state.current.find(w => w.id === id)
    if (!current) return
    if (path === '/app') { setShowHome(true); return }
    if (windowAppId(path) !== windowAppId(current.path)) { open(path); return }
    setWindows(ws => ws.map(w => w.id === id ? { ...w, path } : w))
  }, [open])

  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(windows)) } catch { /* private browsing / quota */ }
  }, [windows, storageKey])
  useEffect(() => {
    const path = initialPath()
    if (!booted.current) {
      booted.current = true
      if (path !== '/app' && path !== '/login' && path !== '/kids' && path !== '/') open(path)
      wireNative(open)
    }
    const pop = () => open(initialPath())
    window.addEventListener('popstate', pop)
    return () => window.removeEventListener('popstate', pop)
  }, [open])
  useEffect(() => {
    const path = showHome ? '/app' : windows.find(w => w.id === active)?.path || '/app'
    if (initialPath() !== path) window.history.replaceState(null, '', path)
    document.title = showHome ? `${brand.name} · Casa` : `${appForPath(path).title} · ${brand.name}`
  }, [windows, active, showHome, brand.name])
  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight })
    const connectivity = () => setOnline(navigator.onLine)
    const help = () => setGuide(true)
    window.addEventListener('resize', resize); window.addEventListener('online', connectivity); window.addEventListener('offline', connectivity)
    window.addEventListener('familai:guide', help)
    const timer = window.setInterval(() => setNow(new Date()), 30000)
    return () => { clearInterval(timer); window.removeEventListener('resize', resize); window.removeEventListener('online', connectivity); window.removeEventListener('offline', connectivity); window.removeEventListener('familai:guide', help) }
  }, [])
  useEffect(() => {
    const load = () => {
      get<FileOut[]>('/files').then(f => { setFiles(f); setRecentError('') }).catch(() => setRecentError('Non riesco a caricare i file. Riprova aprendo File.'))
      get<Conversation[]>('/chat/conversations').then(setConversations).catch(() => {})
    }
    load()
    window.addEventListener('familai:conversations', load)
    return () => window.removeEventListener('familai:conversations', load)
  }, [])
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearchOpen(s => !s) }
      if (e.key === 'Escape') { setLayoutMenu(false); setSearchOpen(false); setSwitcher(false); setLauncherOpen(false) }
    }
    const leaving = (e: BeforeUnloadEvent) => {
      if (state.current.some(w => appForPath(w.path).id === 'editor')) { e.preventDefault(); e.returnValue = '' }
    }
    window.addEventListener('keydown', key); window.addEventListener('beforeunload', leaving)
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('beforeunload', leaving) }
  }, [])
  useEffect(() => { if (searchOpen) searchRef.current?.focus() }, [searchOpen])

  function close(w: DesktopWindow) {
    if (appForPath(w.path).id === 'editor' && !confirm('Chiudere il documento? Verifica di avere salvato le modifiche. In caso di conflitto, esporta prima una copia.')) return
    if(fullscreen.current?.id===w.id)leaveFullscreen()
    setWindows(ws => ws.filter(x => x.id !== w.id))
    activateRemaining(w.id)
  }
  function activateRemaining(id: string) {
    if (active !== id) return
    const next = [...state.current].reverse().find(w => w.id !== id && !w.minimized)
    setActive(next?.id ?? null)
    setShowHome(!next)
  }
  function minimize(w: DesktopWindow) {
    if (fullscreen.current?.id === w.id) leaveFullscreen()
    setWindows(ws => ws.map(x => x.id === w.id ? { ...x, minimized: true } : x))
    activateRemaining(w.id)
  }
  function arrange(id: string, mode: DesktopWindow['mode']) { if(fullscreen.current?.id===id)leaveFullscreen(); setWindows(ws => ws.map(w => w.id === id ? { ...w, mode } : w)) }
  function drag(e: PointerEvent<HTMLElement>, w: DesktopWindow, resize = false) {
    if (e.button !== 0 || (!resize && (e.target as HTMLElement).closest('button,select,input'))) return
    if (viewport.width <= 720 || w.mode !== 'float') return
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId)
    const startX = e.clientX, startY = e.clientY
    const surface = board.current!
    surface.classList.add('os-dragging')
    const target = e.currentTarget
    let pending:DesktopWindow['mode']|null=null
    const move = (ev: globalThis.PointerEvent) => {
      const dx = ev.clientX - startX, dy = ev.clientY - startY
      if(!resize){const rect=surface.getBoundingClientRect();pending=snapZone(ev.clientX-rect.left,ev.clientY-rect.top,rect.width,rect.height);setSnap(pending)}
      setWindows(ws => ws.map(x => x.id !== w.id ? x : resize
        ? { ...x, width: Math.max(360, Math.min(w.width + dx, surface.clientWidth - w.x)), height: Math.max(260, Math.min(w.height + dy, surface.clientHeight - w.y)) }
        : { ...x, x: Math.max(0, Math.min(w.x + dx, surface.clientWidth - Math.min(w.width, surface.clientWidth))), y: Math.max(0, Math.min(w.y + dy, surface.clientHeight - 100)) }))
    }
    const end = (ev:globalThis.PointerEvent) => { if(pending&&ev.type!=='pointercancel')arrange(w.id,pending);setSnap(null);surface.classList.remove('os-dragging'); target.removeEventListener('pointermove', move); target.removeEventListener('pointerup', end); target.removeEventListener('pointercancel', end) }
    target.addEventListener('pointermove', move); target.addEventListener('pointerup', end); target.addEventListener('pointercancel', end)
  }

  function openFile(f: FileOut) {
    if (editorFor(f.filename)) open(editorPath(f.id, f.filename))
    else window.open(`/files/${f.id}`, '_blank', 'noopener,noreferrer')
  }

  useLiveEvents(e => {
    if (e.kind === 'files' || e.kind === 'sync') get<FileOut[]>('/files').then(setFiles).catch(() => {})
  })
  const q = search.trim().toLocaleLowerCase()
  const matches = apps.filter(a => a.title.toLocaleLowerCase().includes(q))
  const firstName = me!.user.display_name.split(' ')[0]
  const today = now.toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long' })
  return <div className={`familos ${fullId ? "os-fullscreen" : ""}`}>
    {me?.features?.messaggi && <DesktopCalls userId={me.user.id} open={open} />}
    <header className="os-topbar">
      <button className="os-wordmark" onClick={() => setShowHome(true)} aria-label={`Home ${brand.name}`}><img src={brand.logo_url} alt="" /> {brand.name}</button>
      <div className="os-arrange-menu"><button aria-expanded={layoutMenu} onClick={()=>setLayoutMenu(!layoutMenu)}>Disponi</button>{layoutMenu&&<div role="group" aria-label="Disponi finestre"><button onClick={()=>tile(2)}>◫ Due zone</button><button onClick={()=>tile(4)}>⊞ Quattro zone</button><button onClick={()=>{setWindows(ws=>ws.map(w=>({...w,mode:'float'})));setLayoutMenu(false)}}>▢ Finestre libere</button></div>}</div>
      <span className="os-household">{me!.family_name || 'La nostra casa'}</span>
      <div className="os-status"><button className="os-screen-button" onClick={toggleOSFullscreen} aria-label={osFull?"Esci da schermo intero OS":"OS a schermo intero"} title={osFull?"Esci da schermo intero OS":"OS a schermo intero"}>⛶</button><span className={`os-online ${online ? '' : 'offline'}`} title={online ? 'Connesso' : 'Connessione assente'} />
        <time>{now.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}</time>
        <WindowRouter path="/app" navigate={open}>{me!.user.role !== 'child' && <Approvals />}<Notifications /></WindowRouter>
        <button className="os-lock-button" onClick={() => lockSession()} aria-label="Blocca schermo" title="Blocca schermo">▣</button>
        <button className="os-avatar" onClick={() => open('/settings')} title={`Profilo di ${me!.user.display_name}`}>{me!.user.avatar || firstName[0]}</button>
        <button className="os-logout" onClick={() => { if (!state.current.some(w => appForPath(w.path).id === 'editor') || confirm('Uscire? Salva prima i documenti aperti.')) logout() }} title="Esci">↪</button>
      </div>
    </header>
    {fullscreenError&&<div role="alert">{fullscreenError}<button onClick={()=>setFullscreenError('')}>Chiudi</button></div>}
    {!online && <div className="os-offline" role="status">Sei offline. Le app aperte restano visibili; lettura e salvataggi richiedono la connessione.</div>}
    <div className="os-board" ref={board}>
      <main className="os-home" style={{ visibility: showHome ? undefined : 'hidden' }} aria-hidden={!showHome}>
        <div className="os-home-heading"><div><p className="os-eyebrow">IL TUO SPAZIO, INSIEME</p><h1>Ciao, {firstName}<span>.</span></h1><p className="os-date">{today}</p></div>
          <button className="os-search-button" onClick={() => setSearchOpen(true)}>⌕ <span>Cerca in {brand.name}</span><kbd>⌘ K</kbd></button></div>
        <div className="os-widgets">
          <section className="os-widget"><button className="ghost" onClick={()=>open('/games')}><AppIcon app={appForPath('/games')} /><h2>Giochi</h2><p>I tuoi mondi e le stanze della famiglia</p></button></section>
          <section className="os-welcome"><div className="os-welcome-symbol">✳</div><p className="os-eyebrow">UN AIUTO QUOTIDIANO</p><h2>Facciamo spazio<br /> alle tue idee.</h2><p>Organizza i documenti, riprendi un progetto o chiedi una mano a Emily.</p><button onClick={() => open('/')}>Parla con Emily <span>↗</span></button></section>
          <section className="os-widget os-recents"><div className="os-widget-heading"><h2>I tuoi file</h2><button onClick={() => open('/documents')}>Apri File ↗</button></div>
            {recentError && <p className="os-empty" role="status">{recentError}</p>}
            {!recentError && !files.length && <div className="os-empty"><span>▰</span><p>Una casa per i tuoi documenti.</p><button onClick={() => open('/documents')}>Carica il primo file</button></div>}
            {files.slice(0, 4).map(f => <button className="os-recent-file" key={f.id} onClick={() => openFile(f)}><span>▤</span><span><b>{f.filename}</b><small>{new Date(f.created_at).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })}</small></span><span>↗</span></button>)}
          </section>
          <section className="os-widget os-continue"><div className="os-widget-heading"><h2>Riprendi da qui</h2><button onClick={() => open('/chats')}>Tutte le chat ↗</button></div>
            {conversations.slice(0, 3).map(c => <button key={c.id} onClick={() => open(`/c/${c.id}`)}><span>✳</span><span>{c.title}</span><span>→</span></button>)}
            {!conversations.length && <p className="os-empty">Le tue conversazioni con Emily compariranno qui.</p>}
            <div className="os-window-tip"><b>Tutto al suo posto.</b><p>Apri più app, affiancale e ritrovale nel dock.</p></div>
          </section>
        </div>
        <footer className="os-home-footer"><span>La tua famiglia. Il vostro spazio.</span><button onClick={() => setGuide(true)}>Come funziona ↗</button></footer>
      </main>
      {snap&&<div className="os-snap-preview" style={zoneStyle(snap)} aria-hidden="true"/>}
      {windows.map((w, i) => {
        const app = appForPath(w.path)
        const hidden = w.minimized || showHome || viewport.width <= 720 && active !== w.id
        const full = w.mode === 'full' || viewport.width <= 720
        const tiled = w.mode !== 'float'
        const availableHeight = Math.max(260, viewport.height - (online ? 156 : 190))
        const top = Math.max(0, Math.min(w.y, availableHeight - 260))
        const style = full ? zoneStyle('full') : tiled ? zoneStyle(w.mode)
          : { left: Math.max(0, Math.min(w.x, viewport.width - Math.min(w.width, viewport.width - 24) - 16)), top, width: Math.min(w.width, viewport.width - 24), height: Math.min(w.height, availableHeight - top - 8) }
        return <section key={w.id} className={`os-window ${active === w.id ? 'active' : ''} ${fullId===w.id?'os-window-fullscreen':''}`} style={{ ...style, zIndex: i + 1, display: hidden ? 'none' : undefined }} aria-label={`${app.title}, finestra`} onPointerDownCapture={() => { if (active !== w.id) focus(w.id) }}>
          <header className="os-window-bar" onPointerDown={e => drag(e, w)} onDoubleClick={e => {if(!(e.target as HTMLElement).closest('button,select,input'))maximize(w)}}>
            <div className="os-window-controls"><button title="Chiudi finestra" aria-label={`Chiudi ${app.title}`} onClick={() => close(w)}>×</button><button title="Riduci nel dock" aria-label={`Riduci ${app.title}`} onClick={() => minimize(w)}>−</button><button title={fullId===w.id?"Torna in finestra":"Schermo intero"} aria-label={fullId===w.id?"Torna in finestra":"Schermo intero"} onClick={() => toggleFullscreen(w)}>↗</button></div>
            <span className="os-window-title"><AppIcon app={app}/> {app.id === 'editor' ? new URLSearchParams(w.path.split('?')[1]).get('name') || 'Documento' : app.title}</span>
            <div className="os-window-layout"><select aria-label="Posizione finestra" value={w.mode} onChange={e=>arrange(w.id,e.target.value as DesktopWindow['mode'])}><option value="float">Libera</option><option value="full">Ingrandita nell’OS</option><option value="left">Metà sinistra</option><option value="right">Metà destra</option><option value="top-left">Alto sinistra</option><option value="top-right">Alto destra</option><option value="bottom-left">Basso sinistra</option><option value="bottom-right">Basso destra</option></select><button title="Affianca a sinistra" aria-label="Affianca a sinistra" onClick={() => arrange(w.id, 'left')}>▏</button><button title="Affianca a destra" aria-label="Affianca a destra" onClick={() => arrange(w.id, 'right')}>▕</button><button title="Finestra libera" aria-label="Finestra libera" onClick={() => arrange(w.id, 'float')}>▢</button></div>
          </header>
          <div className="os-window-body"><WindowCommands.Provider value={{ close: () => close(w) }}><WindowActivity.Provider value={!hidden && active === w.id}><WindowRouter path={w.path} navigate={path => navigateWindow(w.id, path)}><WorkspaceRoutes /></WindowRouter></WindowActivity.Provider></WindowCommands.Provider></div>
          {!full && !tiled && <button className="os-resize" aria-label={`Ridimensiona ${app.title}`} title="Trascina per ridimensionare" onPointerDown={e => drag(e, w, true)} onKeyDown={e => { const delta = e.shiftKey ? 80 : 20; if (['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); setWindows(ws => ws.map(x => x.id !== w.id ? x : { ...x, width: Math.max(360, x.width + (e.key === 'ArrowRight' ? delta : e.key === 'ArrowLeft' ? -delta : 0)), height: Math.max(260, x.height + (e.key === 'ArrowDown' ? delta : e.key === 'ArrowUp' ? -delta : 0)) })) } }}>◢</button>}
        </section>
      })}
    </div>
    <nav className="os-dock" aria-label="Dock delle applicazioni"><button className={showHome ? 'selected' : ''} onClick={() => setShowHome(true)} title="Home"><span className="os-dock-home">◈</span><small>Home</small></button><button className={launcherOpen ? "selected" : ""} aria-label="Applicazioni" aria-expanded={launcherOpen} aria-haspopup="dialog" onClick={() => { setLauncherOpen(value => !value); setSearchOpen(false); setSwitcher(false) }}><span className="os-dock-apps" aria-hidden="true">▦</span><small>App</small></button><span className="os-dock-divider" />
      {apps.filter(a => ['chat', 'files', 'messages', 'projects'].includes(a.id)).map(app => <button key={app.id} onClick={e => open(app.path, e.shiftKey)} title={`${app.title} · Maiusc+clic per una nuova finestra`}><AppIcon app={app} /><small>{app.title}</small><i className={windows.some(w => appForPath(w.path).id === app.id) ? 'running' : ''} /></button>)}
      {Array.from(new Map(windows.filter(w => !['chat', 'files', 'projects', 'messages'].includes(windowAppId(w.path))).map(w => [windowAppId(w.path), w])).values()).map(w => <button key={windowAppId(w.path)} onClick={() => focus(w.id)} title={appForPath(w.path).title}><AppIcon app={appForPath(w.path)} /><small>{appForPath(w.path).title}</small><i className="running" /></button>)}
      <span className="os-dock-divider" /><button onClick={() => setSwitcher(s => !s)} title="Finestre aperte"><span className="os-dock-switch">▣</span><small>Finestre</small><i className={windows.length ? 'running' : ''} /></button>
    </nav>
    <AppLauncher open={launcherOpen} apps={apps} onClose={() => setLauncherOpen(false)} onOpen={(path, fresh) => { setLauncherOpen(false); open(path, fresh) }} />
    {switcher && <div className="os-switcher" role="dialog" aria-label="Finestre aperte"><div className="os-widget-heading"><h2>Finestre aperte</h2><button onClick={() => setSwitcher(false)} aria-label="Chiudi selettore">×</button></div>{!windows.length && <p>Nessuna finestra aperta. Apri Applicazioni nella barra.</p>}{windows.map(w => <div className="os-switch-row" key={w.id}><button onClick={() => focus(w.id)}><AppIcon app={appForPath(w.path)} /><span>{appForPath(w.path).title}<small>{w.minimized ? 'Nel dock' : 'Aperta'}</small></span></button><button onClick={() => close(w)} aria-label={`Chiudi ${appForPath(w.path).title}`}>×</button></div>)}</div>}
    {searchOpen && <div className="os-overlay" onClick={() => setSearchOpen(false)}><section className="os-spotlight" role="dialog" aria-modal="true" aria-label="Cerca in FamilOS" onClick={e => e.stopPropagation()}><div className="os-search-field"><span>⌕</span><input ref={searchRef} placeholder="Cerca app, file e conversazioni…" value={search} onChange={e => setSearch(e.target.value)} /><button onClick={() => setSearchOpen(false)}>Esc</button></div><div className="os-search-results"><h3>App</h3>{matches.map(app => <button key={app.id} onClick={e => open(app.path, e.shiftKey)}><AppIcon app={app} />{app.title}<span>↵</span></button>)}{q && <><h3>File</h3>{files.filter(f => f.filename.toLocaleLowerCase().includes(q)).slice(0, 8).map(f => <button key={f.id} onClick={() => openFile(f)}>▤ {f.filename}</button>)}<h3>Conversazioni</h3>{conversations.filter(c => c.title.toLocaleLowerCase().includes(q)).slice(0, 5).map(c => <button key={c.id} onClick={() => open(`/c/${c.id}`)}>✳ {c.title}</button>)}</>}</div></section></div>}
    {guide && <div className="os-overlay" onClick={() => setGuide(false)}><div className="os-guide" onClick={e => e.stopPropagation()}><Guide onClose={() => setGuide(false)} /></div></div>}
  </div>
}
