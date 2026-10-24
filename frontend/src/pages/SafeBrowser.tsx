import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { del, get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useWindowActive } from '../lib/desktop'
import ReadingBrowser from './ReadingBrowser'

interface Frame { url: string; title: string; image: string; width: number; height: number; notice: string }
interface Status { ready: boolean; child: boolean; setup_command: string }

export default function SafeBrowser() {
  const { me } = useAuth()
  const active = useWindowActive()
  const child = me?.user.role === 'child'
  const [status, setStatus] = useState<Status | null>(null)
  const [sites, setSites] = useState<string[]>([])
  const [session, setSession] = useState('')
  const [url, setUrl] = useState('')
  const [frame, setFrame] = useState<Frame | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(false)
  const [reader, setReader] = useState(false)
  const [text, setText] = useState('')
  const [masked, setMasked] = useState(false)
  const current = useRef('')
  const mounted = useRef(true)
  const version = useRef(0)
  const pending = useRef(0)
  const chain = useRef<Promise<unknown>>(Promise.resolve())
  const address = useRef<HTMLInputElement>(null)
  const screen = useRef<HTMLDivElement>(null)
  const pointer = useRef<{ x: number; y: number } | null>(null)
  async function loadStatus() {
    try { setStatus(await get<Status>('/browser/status')); setSites((await get<{ sites: string[] }>('/browser/sites')).sites) }
    catch (e: any) { setError(e.message) }
  }
  useEffect(() => {
    mounted.current = true
    loadStatus()
    return () => { mounted.current = false; if (current.current) del('/browser/sessions/' + current.current).catch(() => {}) }
  }, [])
  async function refreshFrame(sid: string) {
    const requested = version.current
    if (child) setChecking(true)
    try {
      const result = await get<Frame>('/browser/sessions/' + sid + '/frame')
      if (!mounted.current || current.current !== sid || version.current !== requested) return
      setFrame(result); setError('')
      if (document.activeElement !== address.current && result.url !== 'about:blank') setUrl(result.url)
    } catch (e: any) {
      if (mounted.current && current.current === sid) { setFrame(null); setError(e.message) }
    } finally { if (mounted.current) setChecking(false) }
  }
  useEffect(() => {
    if (!session || !active || reader) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      if (!pending.current) await refreshFrame(session)
      if (!stopped) timer = setTimeout(poll, child ? 1800 : 800)
    }
    poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [session, active, reader])

  function act(body: Record<string, unknown>) {
    const sid = current.current
    if (!sid || pending.current >= 30) return Promise.resolve()
    version.current++; pending.current++; setBusy(true)
    if (child && body.action === 'navigate') setFrame(null)
    chain.current = chain.current.catch(() => {}).then(async () => {
      try {
        if (sid !== current.current) return
        await post('/browser/sessions/' + sid + '/action', body)
      } catch (e: any) { if (mounted.current) { setError(e.message); if (child) setFrame(null) } }
      finally { pending.current--; if (mounted.current && !pending.current) { setBusy(false); if (sid === current.current) await refreshFrame(sid) } }
    })
    return chain.current
  }
  async function open(value: string) {
    const target = value.trim()
    if (!target) return
    setError(''); setBusy(true)
    try {
      if (!current.current) {
        const result = await post<{ id: string }>('/browser/sessions')
        current.current = result.id; setSession(result.id)
      }
      const destination = !target.includes('.') || /\s/.test(target) ? 'https://www.google.com/search?q=' + encodeURIComponent(target) : target.includes('://') ? target : 'https://' + target
      setUrl(destination)
      await act({ action: 'navigate', url: destination })
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  async function end() {
    const sid = current.current
    current.current = ''; version.current++
    setSession(''); setFrame(null); setUrl(''); setError(''); setBusy(false)
    if (sid) await del('/browser/sessions/' + sid).catch(() => {})
  }
  function point(e: PointerEvent) {
    const rect = screen.current!.getBoundingClientRect()
    return { x: Math.max(0, Math.min(1279, (e.clientX - rect.left) * 1280 / rect.width)), y: Math.max(0, Math.min(799, (e.clientY - rect.top) * 800 / rect.height)) }
  }
  function key(e: KeyboardEvent) {
    if (e.ctrlKey || e.metaKey) {
      if (e.key.toLowerCase() === 'a') { e.preventDefault(); act({ action: 'key', key: 'Control+a' }) }
      return
    }
    if (e.key.length === 1) { e.preventDefault(); act({ action: 'text', text: e.key }) }
    else if (['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) {
      e.preventDefault(); act({ action: 'key', key: e.shiftKey && e.key === 'Tab' ? 'Shift+Tab' : e.key })
    }
  }
  if (reader) return <div className="safe-browser"><button className="ghost" onClick={() => setReader(false)}>← Torna al browser grafico</button><ReadingBrowser /></div>
  return <div className="safe-browser graphical-browser">
    <form className="safe-browser-toolbar" onSubmit={e => { e.preventDefault(); open(url) }}>
      <button type="button" disabled={!session || busy} aria-label="Pagina precedente" onClick={() => act({ action: 'back' })}>←</button>
      <button type="button" disabled={!session || busy} aria-label="Pagina successiva" onClick={() => act({ action: 'forward' })}>→</button>
      <button type="button" disabled={!session || busy} aria-label="Ricarica pagina" onClick={() => act({ action: 'reload' })}>↻</button>
      <input ref={address} aria-label="Indirizzo o ricerca" value={url} onChange={e => setUrl(e.target.value)} placeholder="Indirizzo HTTPS o ricerca…" />
      <button disabled={!status?.ready || busy || !url.trim()}>{busy ? 'Carico…' : 'Apri'}</button>
      {session && <button type="button" onClick={end} title="Chiude la sessione e cancella i cookie del browser remoto">Chiudi</button>}
    </form>
    <div className="remote-browser-status" role="status">{child ? 'Profilo bambino · siti verificati dall’AI e decisioni salvate per fascia d’età' : 'Chromium sul server · sessione personale temporanea'}{checking && ' · Verifica schermata…'}</div>
    {error && <p className="error remote-browser-error" role="alert">{error}</p>}
    {!session && <div className="safe-browser-body"><p className="os-eyebrow">IL WEB, NEL TUO DESKTOP</p><h1>Browser</h1><p>Pagine con grafica, immagini, JavaScript e ricerca. La navigazione avviene in una sessione isolata sul server.</p>
      {!status?.ready && <div className="installation-deployment"><h3>Attivazione del browser grafico</h3>{me?.user.role === 'admin' ? <><p>Esegui una volta sulla VM Ubuntu:</p><code>{status?.setup_command || './familos.sh browser up'}</code></> : <p>Chiedi all’amministratore di attivare il browser sul server.</p>}<button onClick={loadStatus}>Ricontrolla disponibilità</button></div>}
      <div className="safe-browser-sites">{sites.map(site => <button key={site} disabled={!status?.ready || busy} onClick={() => open('https://' + site)}>◎<span>{site}</span>↗</button>)}</div>
      {child && <p className="muted">Alla prima visita l’AI controlla testo e immagine del sito. Le approvazioni restano salvate; solo gli amministratori possono modificarle. Un dominio approvato può cambiare contenuti nel tempo.</p>}
      <p className="muted small">Questa versione supporta pagine e ricerca interattive. Audio, videocamera, download, caricamento file, finestre popup e connessioni WebSocket non sono disponibili. Alcuni siti possono rifiutare un browser remoto. I bambini non possono inviare moduli POST.</p>
      {!child && <button className="ghost" onClick={() => setReader(true)}>Modalità lettura testuale</button>}
    </div>}
    {session && <div className="remote-browser-view">
      {frame && <><div ref={screen} className="remote-browser-screen" tabIndex={0} role="application" aria-label="Pagina web interattiva: clicca e usa la tastiera" onKeyDown={key}
        onPaste={e => { e.preventDefault(); act({ action: 'text', text: e.clipboardData.getData('text/plain').slice(0, 4000) }) }}
        onPointerDown={e => { pointer.current = point(e); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId) }}
        onPointerUp={e => { const start = pointer.current; pointer.current = null; if (!start) return; const end = point(e); if (Math.abs(start.y - end.y) > 15) act({ action: 'scroll', ...start, delta: start.y - end.y }); else act({ action: 'click', ...end }) }}
        onWheel={e => { const rect = e.currentTarget.getBoundingClientRect(); act({ action: 'scroll', x: Math.max(0, Math.min(1279, (e.clientX - rect.left) * 1280 / rect.width)), y: Math.max(0, Math.min(799, (e.clientY - rect.top) * 800 / rect.height)), delta: Math.max(-1600, Math.min(1600, e.deltaY)) }) }}>
        <img draggable={false} src={'data:image/jpeg;base64,' + frame.image} alt={frame.title || 'Pagina web'} />
      </div>{frame.notice && <p className="muted small remote-browser-error">{frame.notice}</p>}</>}
      {!frame && !error && <p role="status">{child ? 'Controllo il sito prima di mostrarlo…' : 'Apertura della pagina…'}</p>}
    </div>}
    {session && <form className="remote-browser-input" onSubmit={e => { e.preventDefault(); if (text) { act({ action: 'text', text }); setText('') } }}>
      <label>Testo da inserire nel campo selezionato<input type={masked ? 'password' : 'text'} autoComplete="off" value={text} onChange={e => setText(e.target.value)} maxLength={4000} placeholder="Prima tocca un campo nella pagina" /></label>
      <label className="row"><input type="checkbox" checked={masked} onChange={e => setMasked(e.target.checked)} />Nascondi</label><button disabled={!frame || !text}>Inserisci</button><button type="button" disabled={!frame} onClick={() => { if (text) { act({ action: 'text', text }); setText('') } act({ action: 'key', key: 'Enter' }) }}>Invio ↵</button>
    </form>}
  </div>
}
