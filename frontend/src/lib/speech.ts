import { HEADERS } from './api'

/** Client per la sintesi vocale del backend (/api/tts): cache degli audio per testo e riproduzione singola. */
let current: HTMLAudioElement | null = null
const cache = new Map<string, string>()   // chiave -> blob URL
let enabledFlag: boolean | null = null

export async function ttsEnabled(): Promise<boolean> {
  if (enabledFlag !== null) return enabledFlag
  try {
    const r = await fetch('/api/tts/status', { credentials: 'include', headers: HEADERS })
    enabledFlag = r.ok ? !!(await r.json()).enabled : false
  } catch {
    enabledFlag = false
  }
  return enabledFlag
}

export async function fetchSpeech(text: string, opts: { voice?: string; style?: string } = {}): Promise<{ url: string; blob: Blob }> {
  const key = JSON.stringify([text, opts.voice || '', opts.style || ''])
  const cached = cache.get(key)
  if (cached) return { url: cached, blob: await (await fetch(cached)).blob() }
  const r = await fetch('/api/tts', {
    method: 'POST', credentials: 'include',
    headers: { ...HEADERS, 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice: opts.voice, style: opts.style }),
  })
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail ?? msg } catch {}
    throw new Error(msg)
  }
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  cache.set(key, url)
  return { url, blob }
}

/** Riproduce il testo; interrompe l'eventuale lettura in corso. Risolve a fine riproduzione. */
export async function speak(text: string, opts: { voice?: string; style?: string } = {}): Promise<void> {
  stopSpeaking()
  const { url } = await fetchSpeech(text, opts)
  return new Promise((resolve, reject) => {
    const a = new Audio(url)
    current = a
    a.onended = () => { if (current === a) current = null; resolve() }
    a.onerror = () => { if (current === a) current = null; reject(new Error('Riproduzione audio fallita')) }
    a.play().catch(reject)
  })
}

export function stopSpeaking() {
  if (current) {
    current.pause()
    current.src = ''
    current = null
  }
}

export function isSpeaking() {
  return !!current && !current.paused
}

/** Toglie la sintassi markdown più comune per una lettura pulita. */
export function plainText(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, ' (blocco di codice) ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[*_~>|]+/g, '')
    .replace(/^\s*[-+]\s+/gm, '')
    .replace(/\s+\n/g, '\n')
    .trim()
}
