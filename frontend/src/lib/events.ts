import { useEffect, useRef } from 'react'

/** Evento in tempo reale dal server (SSE /api/events): notifiche, cambi nei Documenti. */
export interface LiveEvent { kind: string; ts?: number; [k: string]: any }

type Handler = (e: LiveEvent) => void
const handlers = new Set<Handler>()
let source: EventSource | null = null
let retry = 1000
let heartbeat: ReturnType<typeof setInterval> | null = null
function stopHeartbeat() { if (heartbeat) clearInterval(heartbeat); heartbeat = null }
function beat() {
  if (source?.readyState === EventSource.OPEN) fetch("/api/events/heartbeat", {method:"POST",credentials:"same-origin",headers:{"X-Requested-With":"FamilAI"}}).catch(()=>{})
}

function ensure() {
  if (source) return
  const es = new EventSource('/api/events', { withCredentials: true })
  source = es
  es.onopen = () => { stopHeartbeat(); beat(); heartbeat = setInterval(beat, 20000) }
  es.onmessage = (m) => {
    try { const e = JSON.parse(m.data) as LiveEvent; retry = 1000; handlers.forEach((h) => { try { h(e) } catch {} }) } catch {}
  }
  es.onerror = () => {
    stopHeartbeat(); es.close(); source = null
    // riconnessione con attesa crescente (la sessione potrebbe essere scaduta: si riprova comunque, il login la riapre)
    setTimeout(() => { if (handlers.size) ensure() }, retry); retry = Math.min(retry * 2, 30000)
  }
}

/** Iscrive il componente agli eventi live finché è montato (una sola connessione condivisa per scheda). */
export function useLiveEvents(handler: Handler) {
  const ref = useRef(handler); ref.current = handler
  useEffect(() => {
    const h: Handler = (e) => ref.current(e)
    handlers.add(h); ensure()
    return () => { handlers.delete(h); if (!handlers.size && source) { stopHeartbeat(); source.close(); source = null } }
  }, [])
}

/** Come useLiveEvents, fuori da React (es. il gestore delle chiamate): restituisce la funzione per disiscriversi. */
export function subscribeLive(handler: Handler): () => void {
  handlers.add(handler); ensure()
  return () => { handlers.delete(handler); if (!handlers.size && source) { stopHeartbeat(); source.close(); source = null } }
}
