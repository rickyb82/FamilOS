import { useEffect, useState, type ReactNode } from 'react'

/**
 * The hint bar under an app page (Dispositivi, Editor, Posta…): × hides it, the ⓘ button in the page header brings it back.
 * Remembered per page in this browser; with storage blocked it lasts until the page is reloaded.
 */
const KEY = 'emily.hints.hidden'
const EVENT = 'emily-hints'
let memory: string[] = []

function hiddenList(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(v) ? v : memory
  } catch {
    return memory
  }
}

function setHidden(id: string, hidden: boolean) {
  memory = hiddenList().filter((x) => x !== id)
  if (hidden) memory.push(id)
  try { localStorage.setItem(KEY, JSON.stringify(memory)) } catch { /* memory only */ }
  window.dispatchEvent(new Event(EVENT))
}

function useHidden(id: string) {
  const [hidden, set] = useState(() => hiddenList().includes(id))
  useEffect(() => {
    const sync = () => set(hiddenList().includes(id))
    window.addEventListener(EVENT, sync)
    return () => window.removeEventListener(EVENT, sync)
  }, [id])
  return hidden
}

export default function EditorHint({ id, children }: { id: string; children: ReactNode }) {
  const hidden = useHidden(id)
  if (hidden) return null
  return (
    <p className="muted small editor-hint hint-closable">
      {children}
      <button type="button" className="ghost hint-close" title="Nascondi i suggerimenti" aria-label="Nascondi i suggerimenti" onClick={() => setHidden(id, true)}>×</button>
    </p>
  )
}

/** In the page header: shown only while the hint bar is hidden. */
export function HintToggle({ id }: { id: string }) {
  const hidden = useHidden(id)
  if (!hidden) return null
  return <button type="button" className="ghost" title="Mostra i suggerimenti" onClick={() => setHidden(id, false)}>ⓘ</button>
}
