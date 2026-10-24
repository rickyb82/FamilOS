import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import '../folder-dialog.css'

export default function FolderDialog({ parentName, onCreate, onClose }: {
  parentName: string; onCreate: (name: string) => Promise<void>; onClose: () => void
}) {
  const [name, setName] = useState('Nuova cartella')
  const id = useId()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const form = useRef<HTMLFormElement>(null)
  const submitting = useRef(false)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    input.current?.focus(); input.current?.select()
    return () => { if (opener?.isConnected) opener.focus({ preventScroll: true }) }
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    const value = name.trim()
    if (submitting.current || !value) return
    if (/[\\/\x00-\x1f]/.test(value)) { setError('Il nome non può contenere / o \\ o caratteri di controllo.'); return }
    submitting.current = true; setBusy(true); setError('')
    try { await onCreate(value); onClose() }
    catch (e: any) { setError(e.message || 'Non riesco a creare la cartella. Riprova.'); submitting.current = false; setBusy(false); input.current?.focus() }
  }
  function keys(e: KeyboardEvent) {
    if (e.key === 'Escape' && !busy) { e.stopPropagation(); onClose() }
    if (e.key !== 'Tab') return
    const controls = Array.from(form.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)') || [])
    const first = controls[0], last = controls[controls.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
  }

  return <div className="folder-dialog-backdrop" onClick={e => e.stopPropagation()}>
    <form ref={form} className="folder-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} onSubmit={submit} onKeyDown={keys}>
      <div className="folder-dialog-titlebar"><button type="button" className="folder-dialog-close" aria-label="Chiudi nuova cartella" disabled={busy} onClick={onClose}>×</button><span>File</span></div>
      <div className="folder-dialog-content">
        <svg className="folder-dialog-icon" viewBox="0 0 80 64" aria-hidden="true"><defs><linearGradient id="folder-blue" x2="0" y2="1"><stop stopColor="#9bdcff"/><stop offset="1" stopColor="#61b5ee"/></linearGradient></defs><path d="M4 15a6 6 0 0 1 6-6h21l8 8h31a6 6 0 0 1 6 6v32a6 6 0 0 1-6 6H10a6 6 0 0 1-6-6Z" fill="#55a6df"/><rect x="4" y="21" width="72" height="40" rx="6" fill="url(#folder-blue)"/><path d="M10 22h60" stroke="#c4ebff" strokeWidth="2" strokeLinecap="round"/></svg>
        <h2 id={`${id}-title`}>Nuova cartella</h2>
        <p id={`${id}-description`}>Scegli un nome per la cartella in <strong>{parentName}</strong>.</p>
        <label htmlFor={`${id}-name`}>Nome</label>
        <input id={`${id}-name`} ref={input} value={name} onChange={e => { setName(e.target.value); setError('') }} maxLength={120} autoComplete="off" spellCheck={false} disabled={busy} aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} />
        {error && <p id={`${id}-error`} className="folder-dialog-error" role="alert">{error}</p>}
      </div>
      <footer className="folder-dialog-actions"><button type="button" disabled={busy} onClick={onClose}>Annulla</button><button type="submit" className="folder-dialog-create" disabled={busy || !name.trim()}>{busy ? 'Creazione…' : 'Crea cartella'}</button></footer>
    </form>
  </div>
}
