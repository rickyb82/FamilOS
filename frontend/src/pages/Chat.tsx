import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { del, get, patch, post, type Conversation, type FileOut, type Message, type Project } from '../lib/api'
import { useAuth } from '../lib/auth'
import MessageView, { ActivityGroup } from '../components/MessageView'
import { abortTurn, clearTurn, getTurn, startTurn, subscribe, type TurnState } from '../lib/turns'
import ShareDialog from '../components/ShareDialog'
import { photoForUpload } from '../lib/photo'
import ArtifactPanel, { type ArtifactData } from '../components/ArtifactPanel'
import { newFilePath } from '../lib/artcraft'
import { useWindowActive } from '../lib/desktop'

interface ToolInfo { name: string; description: string; kind: string; group: string }

export default function Chat() {
  const windowActive = useWindowActive()
  const { id } = useParams()
  const nav = useNavigate()
  const { me } = useAuth()
  const [conv, setConv] = useState<Conversation | null>(null)
  const [access, setAccess] = useState('owner')
  const [ownerName, setOwnerName] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [text, setText] = useState('')
  const [files, setFiles] = useState<FileOut[]>([])
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [gotText, setGotText] = useState(false)   // true appena arriva il primo token
  const [approval, setApproval] = useState<{ id: string; text: string; file_ids: string[]; status: string } | null>(null)
  const [err, setErr] = useState('')
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [enabledTools, setEnabledTools] = useState<Set<string>>(new Set())
  const [share, setShare] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [artifacts, setArtifacts] = useState<ArtifactData[]>([])
  const [openArtifact, setOpenArtifact] = useState<ArtifactData | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  function upsertArtifact(a: ArtifactData | any, open = true) {
    if (a?.type === 'artifact_deleted') return removeArtifact(a.id)
    setArtifacts((l) => (l.some((x) => x.id === a.id) ? l.map((x) => (x.id === a.id ? a : x)) : [...l, a]))
    if (open) setOpenArtifact(a)
  }
  function removeArtifact(aid: string) {
    setArtifacts((l) => l.filter((x) => x.id !== aid))
    setOpenArtifact((o) => (o && o.id === aid ? null : o))
  }
  const messagesRef = useRef<Message[]>([])
  messagesRef.current = messages
  const appliedArtifacts = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (!windowActive) return; const t = setTimeout(() => composerRef.current?.focus(), 50); return () => clearTimeout(t) }, [id, windowActive])

  useEffect(() => {
    get('/chat/providers').then((r) => {
      setTools(r.tools)
    })
  }, [])
  // strumenti accesi: tutti quelli consentiti tranne quelli spenti in Impostazioni › Strumenti dell'assistente
  useEffect(() => {
    const off = new Set(me?.prefs?.tools_off || [])
    setEnabledTools(new Set<string>(tools.map((t) => t.group).filter((g) => !off.has(g))))
  }, [tools, me?.prefs?.tools_off?.join(',')])

  useEffect(() => {
    setErr('')
    setOpenArtifact(null)
    setArtifacts([])
    if (!id) {
      setConv(null)
      setMessages([])
      return
    }
    get<ArtifactData[]>(`/artifacts?conversation_id=${id}`).then(setArtifacts).catch(() => {})
    get(`/chat/conversations/${id}`)
      .then((r) => {
        setConv(r.conversation)
        setAccess(r.access)
        setOwnerName(r.owner_name)
        const t = getTurn(id)
        if (t && t.busy) {
          // turno ancora in corso (l'utente era andato su un'altra chat): riprendi dallo stato del gestore
          setMessages(t.messages)
        } else {
          if (t) clearTurn(id)   // finito mentre eravamo altrove: il server ha gia' tutto
          setMessages(r.messages)
        }
      })
      .catch((e) => setErr(e.message))
  }, [id])

  // iscrizione al gestore dei turni: lo streaming vive fuori dal componente e sopravvive ai cambi di chat
  useEffect(() => {
    if (!id) return
    appliedArtifacts.current = 0
    const apply = (t: TurnState) => {
      setMessages(t.messages)
      setBusy(t.busy)
      setStatus(t.status)
      setGotText(t.gotText)
      if (t.err) setErr(t.err)
      if (t.approval && (!approval || approval.id !== t.approval.id)) setApproval({ ...t.approval, status: 'pending' })
      if (t.provider) setConv((cv) => (cv && (cv.provider !== t.provider || cv.model !== t.model) ? { ...cv, provider: t.provider!, model: t.model! } : cv))
      while (appliedArtifacts.current < t.artifacts.length) upsertArtifact(t.artifacts[appliedArtifacts.current++])
    }
    const t = getTurn(id)
    if (t?.busy) apply(t)
    else setBusy(false)
    return subscribe(id, apply)
  }, [id])

  // bambino: aspetta la decisione del genitore e, se approvata, rimanda la domanda da solo
  useEffect(() => {
    if (!approval || approval.status !== 'pending' || !conv) return
    const t = setInterval(async () => {
      try {
        const r = await get(`/approvals/mine/${approval.id}`)
        if (r.status === 'approved') {
          clearInterval(t)
          setApproval(null)
          await startTurn(conv.id, 'messages', { text: approval.text, file_ids: approval.file_ids, provider: conv.provider, model: conv.model,
            tools: Array.from(enabledTools), approval_id: approval.id }, messagesRef.current)
        } else if (r.status === 'denied' || r.status === 'expired') {
          clearInterval(t)
          setApproval({ ...approval, status: r.status })
        }
      } catch {}
    }, 5000)
    return () => clearInterval(t)
  }, [approval?.id, approval?.status, conv?.id])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, status, busy])

  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail
      setConv((c) => (c && d && c.id === d.id ? { ...c, title: d.title } : c))
    }
    window.addEventListener('familai:renamed', h)
    return () => window.removeEventListener('familai:renamed', h)
  }, [])

  async function ensureConversation(): Promise<Conversation> {
    if (conv) return conv
    const c = await post<Conversation>('/chat/conversations', {})
    setConv(c)
    window.dispatchEvent(new Event('familai:conversations'))
    nav(`/c/${c.id}`, { replace: true })
    return c
  }

  async function send(e?: FormEvent) {
    e?.preventDefault()
    if (busy || (!text.trim() && files.length === 0)) return
    const c = await ensureConversation()
    const body = { text, file_ids: files.map((f) => f.id), provider: c.provider, model: c.model, tools: Array.from(enabledTools) }
    setText('')
    setFiles([])
    await runTurn(c, 'messages', body)
  }

  /** Rigenera l'ultima risposta, eventualmente con un altro provider/modello. */
  async function regenerate(provider?: string, model?: string) {
    if (!conv || busy) return
    await runTurn(conv, 'regenerate', { provider: provider ?? conv.provider, model: model ?? conv.model, tools: Array.from(enabledTools) })
  }

  async function runTurn(c: Conversation, action: 'messages' | 'regenerate', body: any) {
    setErr('')
    // il turno gira nel gestore globale: se cambi chat continua, e al ritorno lo ritrovi
    await startTurn(c.id, action, body, messagesRef.current)
  }

  async function upload(list: FileList | null) {
    if (!list) return
    for (const raw of Array.from(list)) {
      const f = await photoForUpload(raw)
      const fd = new FormData()
      fd.append('file', f)
      if (conv) fd.append('conversation_id', conv.id)
      try {
        const out = await post<FileOut>('/files', fd)
        setFiles((x) => [...x, out])
      } catch (e: any) {
        setErr(e.message)
      }
    }
    if (fileInput.current) fileInput.current.value = ''
    if (cameraInput.current) cameraInput.current.value = ''
  }

  useEffect(() => {
    if (!me || me.user.role === 'child') return
    get<Project[]>('/projects').then(setProjects).catch(() => {})
  }, [me?.user.id])

  async function moveToProject(projectId: string | null) {
    if (!conv) return
    const target = projects.find((p) => p.id === projectId)
    const msg = target
      ? `Spostare la chat nel progetto "${target.name}"? Gli allegati e gli artifact di questa chat diventano visibili a chi partecipa al progetto.`
      : 'Togliere la chat dal progetto? I suoi allegati torneranno visibili solo in questa chat.'
    if (!confirm(msg)) return
    try {
      const c = await patch<Conversation>(`/chat/conversations/${conv.id}`, { project_id: projectId })
      setConv(c)
      window.dispatchEvent(new Event('familai:conversations'))
    } catch (e: any) {
      setErr(e.message)
    }
  }

  async function changeModel(provider: string, model: string) {
    if (!conv) {
      const c = await post<Conversation>('/chat/conversations', { provider, model })
      setConv(c)
      nav(`/c/${c.id}`, { replace: true })
      window.dispatchEvent(new Event('familai:conversations'))
      return
    }
    const c = await patch<Conversation>(`/chat/conversations/${conv.id}`, { provider, model })
    setConv(c)
    const hasAnswer = messages.some((m) => m.role === 'assistant')
    if (hasAnswer && confirm(`Modello cambiato in ${model}. Vuoi rigenerare l'ultima risposta con il nuovo modello? La cronologia della chat viene comunque passata al nuovo modello dai prossimi messaggi.`)) {
      await regenerate(c.provider, c.model)
    }
  }

  const provider = me?.providers.find((p) => p.id === (conv?.provider ?? me.providers[0]?.id))
  const canWrite = access === 'owner' || access === 'write'
  const isChild = me?.user.role === 'child'

  const grouped = groupActivity(messages)
  // mentre il modello lavora dopo le chiamate ai tool (o tra una e l'altra) lo stato vive nella riga Attività, non in un secondo indicatore
  const activityLive = busy && !gotText && grouped.length > 0 && grouped[grouped.length - 1].kind === 'activity'
  return (
    <div className={`chat-layout ${openArtifact ? 'with-panel' : ''}`}>
    <div className="chat">
      <header className="chat-header">
        <div className="title" title={canWrite && conv ? 'Clicca per rinominare' : undefined} style={canWrite && conv ? { cursor: 'text' } : undefined}
          onClick={async () => {
            if (!conv || !canWrite) return
            const title = prompt('Nuovo nome della chat', conv.title)
            if (!title || title.trim() === conv.title) return
            const c = await patch<Conversation>(`/chat/conversations/${conv.id}`, { title: title.trim().slice(0, 200) })
            setConv(c)
            window.dispatchEvent(new Event('familai:conversations'))
          }}>
          {conv?.title ?? 'Nuova chat'} {ownerName && access !== 'owner' && <small className="muted">· di {ownerName}</small>}
        </div>
        <div className="row">
          {me?.features?.debug_llm && <span className="debug-badge" title="Debug LLM attivo: sotto ogni risposta c'è il pannello 🐞 con parametri, tempi e ragionamento nascosto">🐞 debug</span>}
          {artifacts.length === 1 && !openArtifact && (
            <button className="ghost" onClick={() => setOpenArtifact(artifacts[0])} title={artifacts[0].title}>📦 {artifacts[0].title.slice(0, 24)}</button>
          )}
          {artifacts.length > 1 && !openArtifact && (
            <select className="ghost-select" value="" title="Apri un artifact" onChange={(e) => { const a = artifacts.find((x) => x.id === e.target.value); if (a) setOpenArtifact(a) }}>
              <option value="" disabled>📦 {artifacts.length} artifact…</option>
              {artifacts.map((a, i) => <option key={a.id} value={a.id}>{i + 1}. {a.title}</option>)}
            </select>
          )}
          {conv && access === 'owner' && !isChild && projects.length > 0 && (
            <select className="ghost-select" value={conv.project_id ?? ''} title="Sposta la chat in un progetto (allegati e artifact condivisi con i membri)"
              disabled={busy} onChange={(e) => moveToProject(e.target.value || null)}>
              <option value="">📁 Nessun progetto</option>
              {projects.map((p) => <option key={p.id} value={p.id}>📁 {p.name}</option>)}
            </select>
          )}
          {conv && canWrite && !isChild && me?.features?.wordcraft_web && (
            <button className="ghost" title="Nuovo documento Word collegato a questa chat (editor WordCraft)" onClick={() => nav(newFilePath('wordcraft', { conversationId: conv.id, projectId: conv.project_id }))}>📝</button>
          )}
          {conv && canWrite && !isChild && me?.features?.gridcraft_web && (
            <button className="ghost" title="Nuovo foglio di calcolo collegato a questa chat (editor GridCraft)" onClick={() => nav(newFilePath('gridcraft', { conversationId: conv.id, projectId: conv.project_id }))}>📊</button>
          )}
          {conv && access === 'owner' && <button className="ghost" onClick={() => setShare(true)}>🔗 Condividi</button>}
        </div>
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <div className="big">{isChild ? '👋' : '🏠'}</div>
            <h2>{isChild ? `Ciao ${me?.user.display_name}! Cosa vuoi scoprire oggi?` : 'Come posso aiutarti?'}</h2>
            <p className="muted">{isChild ? "Sono Emily, un’AI che ti aiuta a imparare. Posso sbagliare: ragioniamo insieme e chiediamo a un adulto quando serve." : "Chiedi, allega documenti, genera immagini, cerca sul web."}</p>
          </div>
        )}
        {grouped.map((g, i) =>
          g.kind === 'activity' ? (
            <ActivityGroup key={g.key} steps={g.steps} live={activityLive && i === grouped.length - 1}
              onOpenArtifact={(a) => upsertArtifact(artifacts.find((x) => x.id === a.id) ?? a)} />
          ) : (
            <MessageView key={g.m.id} m={g.m} onOpenArtifact={(a) => upsertArtifact(artifacts.find((x) => x.id === a.id) ?? a)} />
          ),
        )}
        {busy && (!gotText || status) && !activityLive && <Working status={status} />}
        {approval && approval.status === 'pending' && (
          <div className="working approval-wait"><span className="dots"><i /><i /><i /></span><span>📚 Aspetto il permesso di mamma o papà…</span></div>
        )}
        {approval && approval.status !== 'pending' && (
          <div className="status">{approval.status === 'denied' ? '🚫 Il genitore ha detto di no: i compiti si fanno da soli! Posso però spiegarti come si fa.' : '⌛ Nessuna risposta dal genitore: riprova più tardi.'}
            <button className="ghost small" style={{ marginLeft: 8 }} onClick={() => setApproval(null)}>OK</button></div>
        )}
        {err && <div className="error">{err}</div>}
        {!busy && canWrite && messages.some((m) => m.role === 'assistant') && (
          <div className="row" style={{ justifyContent: 'flex-start' }}>
            <button className="ghost small" onClick={() => regenerate()} title="Rigenera l'ultima risposta con il modello corrente">↻ Rigenera risposta</button>
            {(() => {
              const last = [...messages].reverse().find((m) => m.role === 'assistant')
              const isLocal = last?.usage?.provider === 'ollama'
              const cloud = me?.providers.find((p) => p.id === 'openai')
              return isLocal && cloud ? (
                <button className="ghost small" onClick={() => regenerate(cloud.id, cloud.default_model)} title={`Rifai l'ultima risposta con ${cloud.label}`}>☁️ Rifai con {cloud.label.split(' ')[0]}</button>
              ) : null
            })()}
          </div>
        )}
        <div ref={bottom} />
      </div>

      {canWrite ? (
        <form className="composer" onSubmit={send}>
          {files.length > 0 && (
            <div className="chips">
              {files.map((f) => (
                <span key={f.id} className="chip">📎 {f.filename} <button type="button" className="icon" onClick={() => setFiles(files.filter((x) => x.id !== f.id))}>✕</button></span>
              ))}
            </div>
          )}
          <div className="composer-box">
            <button type="button" className="ghost small" onClick={() => nav('/chats')}>◷ Tutte le chat</button>
            <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
            <input ref={cameraInput} type="file" accept="image/*" capture="environment" hidden onChange={(e) => upload(e.target.files)} />
            <button type="button" className="ghost small" disabled={busy} onClick={() => setText(current => current || 'Crea un mini videogioco giocabile come artifact di tipo game, con più file e le librerie grafiche adatte. La mia idea è: ')}>🎮 Crea un gioco</button>
            <textarea
              ref={composerRef}
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={isChild ? 'Scrivi qui…' : 'Scrivi a Emily…'}
              title="Invio per inviare, Shift+Invio per andare a capo"
              rows={1}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !window.matchMedia('(pointer: coarse)').matches) {
                  e.preventDefault()
                  send()
                }
              }}
            />
            <div className="composer-tools">
              <button type="button" className="icon" onClick={() => fileInput.current?.click()} title="Allega file o foto">📎</button>
              <button type="button" className="icon touch-only" onClick={() => cameraInput.current?.click()} title="Scatta una foto e falla analizzare">📷</button>
              <span style={{ flex: 1 }} />
              {!isChild && me && (
              <select
                className="model-select"
                value={provider ? `${provider.id}|${conv?.model || provider.default_model}` : ''}
                onChange={(e) => {
                  const [p, m] = e.target.value.split('|')
                  changeModel(p, m)
                }}
                disabled={busy || !!me.model_lock}
                title={me.model_lock ? 'Modello bloccato da te (Amministrazione › Provider AI › Il mio modello)' : me.user.role !== 'admin' ? 'Modelli messi a disposizione dall\'amministratore' : ''}
              >
                {me.providers.map((p) => (
                  <optgroup key={p.id} label={p.label}>
                    {p.models.map((m) => (
                      <option key={m.id} value={`${p.id}|${m.id}`}>{m.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
              )}
              {busy ? (
                <button type="button" className="send-btn stop" onClick={() => conv && abortTurn(conv.id)} title="Ferma">■</button>
              ) : (
                <button className="send-btn" disabled={!text.trim() && files.length === 0} title="Invia">➤</button>
              )}
            </div>
          </div>
        </form>
      ) : (
        <div className="composer muted center-text">Conversazione in sola lettura</div>
      )}
      {share && conv && <ShareDialog type="conversation" id={conv.id} onClose={() => setShare(false)} />}
    </div>
    {openArtifact && (
      <ArtifactPanel onAsk={prompt => setText(current => current ? current + "\n\n" + prompt : prompt)} artifact={openArtifact} all={artifacts} canEdit={canWrite} onClose={() => setOpenArtifact(null)} onChange={(a) => upsertArtifact(a)} onSelect={(a) => setOpenArtifact(a)}
        onDelete={async (a) => { await del(`/artifacts/${a.id}`); removeArtifact(a.id) }} />
    )}
    </div>
  )
}


/** Indicatore animato mostrato mentre il modello lavora: puntini, testo di stato e tempo trascorso. */
function Working({ status }: { status: string }) {
  const [secs, setSecs] = useState(0)
  useEffect(() => {
    const t0 = Date.now()
    const t = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(t)
  }, [])
  const fallback = secs < 6 ? 'Sto pensando' : secs < 20 ? 'Ci sto lavorando, ancora un momento' : secs < 60 ? 'Sto preparando una risposta lunga' : 'Sto ancora lavorando, la connessione è attiva'
  const label = status ? status.replace(/…+$/, '') : fallback
  return (
    <div className="working" role="status" aria-live="polite">
      <span className="dots"><i /><i /><i /></span>
      <span>{label}</span>
      {secs >= 3 && <span className="muted small">{secs}s</span>}
    </div>
  )
}

type Grouped = { kind: 'msg'; m: Message } | { kind: 'activity'; key: string; steps: Message[] }

/** Come lavora l'agente si legge in un solo blocco Attività: chiamate ai tool E testo intermedio dell'assistente (quello scritto
 * prima di una chiamata) finiscono nella timeline ripiegabile; fuori resta solo la risposta finale del turno. Un messaggio
 * dell'assistente e' intermedio se contiene chiamate a tool o se subito dopo arriva un messaggio di tool. */
function groupActivity(messages: Message[]): Grouped[] {
  const out: Grouped[] = []
  let cur: Message[] | null = null
  const flush = () => { if (cur && cur.length) out.push({ kind: 'activity', key: 'act-' + cur[0].id, steps: cur }); cur = null }
  messages.forEach((m, i) => {
    const next = messages[i + 1]
    const intermediate = m.role === 'assistant' && !m.usage?.debug &&
      (m.content.some((b: any) => b.type === 'tool_use') || (!!next && next.role === 'tool'))
    if (m.role === 'tool' || intermediate) {
      if (!cur) cur = []
      cur.push(m)
    } else {
      flush()
      out.push({ kind: 'msg', m })
    }
  })
  flush()
  return out
}
