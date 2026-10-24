import { streamMessage, type Message } from './api'
import { toolLabel } from '../components/MessageView'

/**
 * Gestore dei turni di chat fuori da React: lo streaming continua anche se l'utente cambia chat,
 * e quando torna ritrova la risposta in corso (o finita) invece di una chat "interrotta".
 * Uno stato per conversazione; la pagina Chat si iscrive con subscribe(convId, listener).
 */
export interface TurnState {
  convId: string
  busy: boolean
  status: string
  gotText: boolean
  err: string
  messages: Message[]                 // messaggi completi della chat (snapshot iniziale + eventi)
  artifacts: any[]                    // artifact creati/aggiornati durante il turno (in ordine)
  provider?: string                   // aggiornati da un evento 'regenerating'
  model?: string
  approval?: { id: string; text: string; file_ids: string[] }   // richiesta di permesso al genitore in attesa
  finishedAt?: number
}

type Listener = (s: TurnState) => void
const turns = new Map<string, TurnState>()
const listeners = new Map<string, Set<Listener>>()
const controllers = new Map<string, AbortController>()

function emit(convId: string) {
  const s = turns.get(convId)
  if (!s) return
  for (const l of listeners.get(convId) ?? []) l(s)
}

export function getTurn(convId: string): TurnState | undefined {
  return turns.get(convId)
}

export function subscribe(convId: string, l: Listener): () => void {
  if (!listeners.has(convId)) listeners.set(convId, new Set())
  listeners.get(convId)!.add(l)
  return () => { listeners.get(convId)?.delete(l) }
}

export function abortTurn(convId: string) {
  controllers.get(convId)?.abort()
}

/** Dimentica un turno concluso (la chat ha ricaricato i messaggi dal server). */
export function clearTurn(convId: string) {
  const s = turns.get(convId)
  if (s && !s.busy) turns.delete(convId)
}

export async function startTurn(convId: string, action: 'messages' | 'regenerate', body: any, initialMessages: Message[]) {
  if (turns.get(convId)?.busy) return
  const s: TurnState = { convId, busy: true, status: '', gotText: false, err: '', messages: [...initialMessages], artifacts: [] }
  turns.set(convId, s)
  const ctrl = new AbortController()
  controllers.set(convId, ctrl)
  const set = (patch: Partial<TurnState>) => { Object.assign(s, patch); emit(convId) }
  let current: Message | null = null
  let optimisticUserId: string | null = null
  if (action === 'messages' && !body?.approval_id && (body?.text || body?.file_ids?.length)) {
    // la domanda compare subito in chat, prima che il server risponda
    optimisticUserId = 'tmp-user-' + Date.now()
    const att = (body.file_ids || []).length
    s.messages = [...s.messages, { id: optimisticUserId, role: 'user', author_id: null, created_at: '', flagged: false, usage: {},
      content: [{ type: 'text', text: String(body.text || '') + (att ? `\n📎 ${att} allegat${att === 1 ? 'o' : 'i'}` : '') }] }]
    s.status = 'Invio…'
  }
  emit(convId)
  try {
    await streamMessage(convId, body, (ev) => {
      if (ev.type === 'regenerating') {
        const i = s.messages.map((x) => x.role).lastIndexOf('user')
        set({ messages: i >= 0 ? s.messages.slice(0, i + 1) : s.messages, provider: ev.provider, model: ev.model, status: `↻ Rigenero con ${ev.model}…` })
      } else if (ev.type === 'user_message') {
        const oid = optimisticUserId
        optimisticUserId = null
        set({ status: '', messages: oid && s.messages.some((x) => x.id === oid) ? s.messages.map((x) => (x.id === oid ? ev.message : x)) : [...s.messages, ev.message] })
      } else if (ev.type === 'approval_resumed') {
        const rm = new Set<string>(ev.removed || [])
        set({ messages: s.messages.filter((x) => !rm.has(x.id)), approval: undefined })
      } else if (ev.type === 'approval_required') {
        set({ approval: { id: ev.approval_id, text: ev.message_text, file_ids: ev.file_ids || [] } })
      } else if (ev.type === 'route') {
        set({ status: ev.local ? `🏠 Risponde il modello locale (${ev.model})…` : `☁️ Inoltro a ${ev.provider_label} (${ev.model})…` })
      } else if (ev.type === 'status') {
        set({ status: ev.text || '' })
      } else if (ev.type === 'thinking_delta') {
        if (!current) {
          current = { id: 'tmp-' + Date.now(), role: 'assistant', author_id: null, content: [{ type: 'text', text: '' }], created_at: '', flagged: false, usage: {}, streaming: true }
          s.messages = [...s.messages, current]
        }
        ;(current as any).thinking = ((current as any).thinking || '') + ev.text
        if (ev.hidden) (current as any).thinkingHidden = true   // debug: ragionamento che normalmente non si vedrebbe
        const snap = { ...current, content: [{ ...current.content[0] }] }
        set({ status: ev.hidden ? '🐞 Ragiona (nascosto)…' : '💭 Sto ragionando…', messages: s.messages.map((x) => (x.id === snap.id ? snap : x)) })
      } else if (ev.type === 'text_delta') {
        if (!current) {
          current = { id: 'tmp-' + Date.now(), role: 'assistant', author_id: null, content: [{ type: 'text', text: '' }], created_at: '', flagged: false, usage: {}, streaming: true }
          s.messages = [...s.messages, current]
        }
        current.content[0].text += ev.text
        const snapshot = { ...current, content: [{ ...current.content[0] }] }
        set({ status: '', gotText: true, messages: s.messages.map((x) => (x.id === snapshot.id ? snapshot : x)) })
      } else if (ev.type === 'rag') {
        const names = Array.from(new Set(ev.hits.map((h: any) => h.filename)))
        set({ status: `📚 Trovati ${ev.hits.length} passaggi in: ${names.join(', ')}` })
      } else if (ev.type === 'tool_start') {
        let msgs = s.messages
        if (current) {
          const finished = { ...current, streaming: false }
          msgs = msgs.map((x) => (x.id === finished.id ? finished : x))
          current = null
        }
        const step: Message = { id: 'tool-' + ev.id, role: 'tool', author_id: null, created_at: '', flagged: false, usage: {},
          content: [{ type: 'tool_result', tool_use_id: ev.id, name: ev.name, input: ev.input, status: 'running', content: '', is_error: false, artifacts: [], started_at: Date.now() }] }
        set({ status: `${toolLabel(ev.name)}…`, gotText: false, messages: [...msgs, step] })
      } else if (ev.type === 'tool_result') {
        const arts = (ev.artifacts || []).filter((a: any) => a.type === 'artifact' || a.type === 'artifact_deleted')
        const id = 'tool-' + ev.tool_use_id
        const exists = s.messages.some((x) => x.id === id)
        const done: Message = { id, role: 'tool', author_id: null, created_at: '', flagged: false, usage: {},
          content: [{ ...ev, input: (s.messages.find((x) => x.id === id)?.content?.[0] as any)?.input, status: 'done',
            started_at: (s.messages.find((x) => x.id === id)?.content?.[0] as any)?.started_at, ended_at: Date.now() }] }
        set({ status: '', messages: exists ? s.messages.map((x) => (x.id === id ? done : x)) : [...s.messages, done], artifacts: [...s.artifacts, ...arts] })
      } else if (ev.type === 'error') {
        set({ err: ev.error })
      } else if (ev.type === 'done') {
        let msgs = s.messages
        if (ev.message) {
          const done = ev.message
          const tmpId = current?.id
          msgs = tmpId && msgs.some((x) => x.id === tmpId) ? msgs.map((x) => (x.id === tmpId ? done : x)) : [...msgs, done]
          current = null
        }
        set({ status: '', messages: msgs })
        window.dispatchEvent(new Event('familai:conversations'))
      }
    }, ctrl.signal, action)
  } catch (e: any) {
    if (e.name !== 'AbortError') set({ err: e.message })
    else if (current) {
      // interrotto dall'utente: chiudi il messaggio parziale
      const finished: Message = { ...(current as Message), streaming: false }
      set({ messages: s.messages.map((x) => (x.id === finished.id ? finished : x)) })
    }
  } finally {
    controllers.delete(convId)
    set({ busy: false, status: '', gotText: false, finishedAt: Date.now() })
  }
}
