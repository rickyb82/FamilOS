import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { calls, type CallState } from '../lib/calls'

/** Chiamata in corso e squillo in arrivo, sopra tutta l'app (portal su body). Riducibile a una finestrella. */
export default function CallOverlay({ names }: { names: Record<string, string> }) {
  const [s, setS] = useState<CallState>(calls.state)
  const [mini, setMini] = useState(false)
  const [now, setNow] = useState(Date.now())
  useEffect(() => calls.subscribe(setS), [])
  useEffect(() => { if (s.status !== 'active') return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [s.status])
  useEffect(() => { if (s.status === 'idle') setMini(false) }, [s.status])
  useEffect(() => { if (!s.error) return; const t = setTimeout(() => calls.clearError(), 5000); return () => clearTimeout(t) }, [s.error])

  const secs = s.startedAt ? Math.floor((now - s.startedAt) / 1000) : 0
  const dur = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`
  const nm = (id: string) => names[id] || 'Partecipante'

  return createPortal(
    <>
      {s.incoming && s.status === 'idle' && (
        <div className="call-ring" role="dialog" aria-label="Chiamata in arrivo">
          <div className="call-ring-avatar">{s.incoming.video ? '🎥' : '📞'}</div>
          <div className="call-ring-text">
            <b>{s.incoming.fromName}</b>
            <small>{s.incoming.video ? 'Videochiamata' : 'Chiamata'}{s.incoming.roomName ? ` · ${s.incoming.roomName}` : ''}</small>
          </div>
          <button className="call-btn red" title="Rifiuta" onClick={() => calls.decline()}>✕</button>
          <button className="call-btn green" title="Rispondi" onClick={() => calls.accept()}>{s.incoming.video ? '🎥' : '📞'}</button>
        </div>
      )}
      {s.error && s.status === 'idle' && <div className="call-error" onClick={() => calls.clearError()}>{s.error}</div>}
      {s.status !== 'idle' && (
        <div className={`call-overlay ${mini ? 'mini' : ''}`}>
          <div className="call-head">
            <span>{s.video ? '🎥' : '📞'} <b>{s.roomName || (s.peers[0] ? nm(s.peers[0].id) : 'Chiamata')}</b></span>
            <small>{s.status === 'starting' ? 'Avvio…' : s.status === 'calling' ? 'Squilla…' : dur}</small>
            {s.notice && <small className="call-notice">{s.notice}</small>}
            <span style={{ flex: 1 }} />
            <button className="call-icon" title={mini ? 'Ingrandisci' : 'Riduci'} onClick={() => setMini(!mini)}>{mini ? '⤢' : '—'}</button>
          </div>
          <div className={`call-grid n${Math.min(s.peers.length + 1, 6)}`}>
            {s.peers.map((p) => <Tile key={p.id} stream={p.stream} label={nm(p.id)} video={s.video} state={p.state} />)}
            {s.peers.length === 0 && <div className="call-wait">{s.status === 'calling' ? 'Sto chiamando…' : 'In attesa degli altri…'}</div>}
            <Tile stream={s.local} label="Tu" video={s.video && !s.camOff} self muted={s.muted} />
          </div>
          <div className="call-bar">
            <button className={`call-btn ${s.muted ? 'off' : ''}`} title={s.muted ? 'Riattiva il microfono' : 'Disattiva il microfono'} onClick={() => calls.toggleMute()}>{s.muted ? '🔇' : '🎙'}</button>
            {s.video && <button className={`call-btn ${s.camOff ? 'off' : ''}`} title={s.camOff ? 'Accendi la videocamera' : 'Spegni la videocamera'} onClick={() => calls.toggleCam()}>{s.camOff ? '🚫' : '📷'}</button>}
            {s.video && 'getDisplayMedia' in (navigator.mediaDevices || {}) && <button className={`call-btn ${s.sharing ? 'on' : ''}`} title={s.sharing ? 'Smetti di condividere lo schermo' : 'Condividi lo schermo'} onClick={() => calls.toggleShare()}>🖥</button>}
            <button className="call-btn red" title="Riattacca" onClick={() => calls.hangup()}>📞</button>
          </div>
        </div>
      )}
    </>,
    document.body,
  )
}

function Tile({ stream, label, video, self, muted, state }: { stream: MediaStream | null; label: string; video: boolean; self?: boolean; muted?: boolean; state?: string }) {
  const v = useRef<HTMLVideoElement>(null)
  const a = useRef<HTMLAudioElement>(null)
  useEffect(() => {
    if (v.current && v.current.srcObject !== stream) v.current.srcObject = stream
    if (a.current && a.current.srcObject !== stream) a.current.srcObject = stream
  }, [stream, video])
  const hasVideo = video && !!stream?.getVideoTracks().some((t) => t.enabled && t.readyState === 'live')
  return (
    <div className={`call-tile ${self ? 'self' : ''}`}>
      {hasVideo ? <video ref={v} autoPlay playsInline muted={self} className={self ? 'mirror' : ''} /> : <div className="call-noface">{label.slice(0, 1).toUpperCase()}</div>}
      {!self && !hasVideo && <audio ref={a} autoPlay />}
      <span className="call-name">{label}{muted ? ' 🔇' : ''}{state && state !== 'connected' && state !== 'new' ? ` · ${state === 'connecting' ? 'collegamento…' : state}` : ''}</span>
    </div>
  )
}
