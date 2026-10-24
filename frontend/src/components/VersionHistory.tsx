import { useEffect, useState } from 'react'
import { get, post, type FileOut } from '../lib/api'

interface Version { id: string; created_at: string; author: string; note: string; size: number; url: string }

/** 🕘 Cronologia delle versioni di un file (stile Google Drive): chi, quando, scarica, ripristina. */
export default function VersionHistory({ file, canWrite, onClose, onRestored }: { file: FileOut; canWrite: boolean; onClose: () => void; onRestored?: (f: FileOut) => void }) {
  const [rows, setRows] = useState<Version[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const load = () => get<Version[]>(`/files/${file.id}/versions`).then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [file.id])
  const note = (n: string) => n === 'editor' ? '✏️ modifica nell\'editor' : n.startsWith('assistente') ? '🤖 modifica dell\'assistente' : n === 'prima del ripristino' ? '↩ prima di un ripristino' : n
  async function restore(v: Version) {
    if (!confirm(`Ripristinare la versione del ${new Date(v.created_at).toLocaleString()}? La versione attuale resta in cronologia.`)) return
    setBusy(v.id)
    try { const f = await post<FileOut>(`/files/${file.id}/versions/${v.id}/restore`); onRestored?.(f); await load() } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal stack" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <h3>🕘 Cronologia di «{file.filename}»</h3>
        {err && <div className="error">{err}</div>}
        {rows === null ? <p className="muted">Caricamento…</p> : rows.length === 0 ? (
          <p className="muted">Nessuna versione precedente: la cronologia parte dal primo salvataggio che sovrascrive il file.</p>
        ) : (
          <ul className="version-list">
            <li className="current"><b>Versione attuale</b><small className="muted"> · {(file.size / 1024).toFixed(0)} KB</small></li>
            {rows.map((v) => (
              <li key={v.id}>
                <span>
                  <b>{new Date(v.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</b>
                  <br /><small className="muted">{v.author || '—'} · {note(v.note)} · {(v.size / 1024).toFixed(0)} KB</small>
                </span>
                <span className="row" style={{ gap: 4 }}>
                  <a className="ghost btn-link small" href={v.url} target="_blank" rel="noreferrer">⬇</a>
                  {canWrite && <button className="ghost small" disabled={busy !== ''} onClick={() => restore(v)}>{busy === v.id ? '…' : '↩ Ripristina'}</button>}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="muted small">I salvataggi ravvicinati della stessa persona (entro 5 minuti) formano una sola versione; si tengono le ultime 30.</p>
        <button className="ghost" onClick={onClose}>Chiudi</button>
      </div>
    </div>
  )
}
