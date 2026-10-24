import { useEffect, useRef, useState } from 'react'
import type { AppEntry } from '../lib/desktop'

export default function AppLauncher({ open, apps, onClose, onOpen }: { open: boolean; apps: AppEntry[]; onClose: () => void; onOpen: (path: string, fresh: boolean) => void }) {
  const [present, setPresent] = useState(open)
  const [search, setSearch] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const panel = useRef<HTMLElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (open) {
      setPresent(true); setSearch('')
      const opener = document.activeElement as HTMLElement | null
      input.current?.focus()
      return () => opener?.focus()
    }
    const timer = window.setTimeout(() => setPresent(false), 180)
    return () => clearTimeout(timer)
  }, [open])
  useEffect(() => {
    if (!open) return
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRef.current() }
      if (event.key === 'Tab') {
        const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('input,button') || [])
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [open])
  const filtered = apps.filter(app => app.id !== 'apps' && (app.title + ' ' + (app.description || '')).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  if (!open && !present) return null
  return <div className={`os-launchpad-overlay ${open ? 'is-open' : 'is-closing'}`} aria-hidden={!open} data-testid="launchpad-backdrop" onClick={() => closeRef.current()}>
    <section ref={panel} className="os-launchpad" role="dialog" aria-modal="true" aria-label="Applicazioni" onClick={e => e.stopPropagation()}>
      <header><div><p>IL TUO SPAZIO DI LAVORO</p><h2>Applicazioni</h2></div><button className="os-launchpad-close" onClick={onClose} aria-label="Chiudi applicazioni">×</button></header>
      <div className="os-launchpad-search"><span aria-hidden="true">⌕</span><input ref={input} placeholder="Cerca un’app…" aria-label="Cerca applicazioni" value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && filtered[0]) onOpen(filtered[0].path, e.shiftKey) }} /></div>
      <div className="os-launchpad-grid">{filtered.map((app, index) => <button key={app.id} style={{ animationDelay: `${Math.min(index,12)*16}ms` }} onClick={e => onOpen(app.path,e.shiftKey)}><span className={`os-icon ${app.color}`} aria-hidden="true">{app.icon}</span><span>{app.title}</span></button>)}</div>
      {!filtered.length && <p className="os-launchpad-empty">Nessuna app trovata.</p>}
      <footer>Seleziona un’app per aprirla · Esc o clic fuori per chiudere</footer>
    </section>
  </div>
}
