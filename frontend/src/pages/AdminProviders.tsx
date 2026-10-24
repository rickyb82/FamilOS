import { useEffect, useState } from 'react'
import { get, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'

type State = { label: string; secret: boolean; configured: boolean; source: string; value: string }
type Provider = { id: string; label: string; key: string; model_key: string; active: boolean; key_state: State; model_state: State; models: { id: string; label: string }[] }
type Status = { providers: Provider[]; settings: Record<string, State>; mock_active: boolean }
const GROUPS: { title: string; keys: string[] }[] = [
  { title: 'Ollama locale', keys: ['ollama_temperature', 'ollama_top_p', 'ollama_seed', 'ollama_think'] },
  { title: 'Conversazioni', keys: ['openai_cache_mode', 'openai_reasoning_effort', 'thinking_mode', 'compaction_enabled', 'auto_continue', 'debug_llm'] },
  { title: 'Documenti e memoria', keys: ['embedding_provider', 'embedding_model', 'memory_enabled', 'memory_auto_approve', 'memory_model', 'default_quota_gb'] },
  { title: 'Immagini e voce OpenAI', keys: ['image_model', 'tts_provider', 'tts_model_openai', 'tts_voice_openai', 'tts_style'] },
  { title: 'Ricerca web', keys: ['web_search_provider', 'searxng_url', 'web_search_summarize'] },
  { title: 'Email di sistema', keys: ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_from_name', 'smtp_security', 'mail_transport', 'security_login_alerts'] },
]
const OPTIONS: Record<string, string[]> = {
  security_login_alerts: ['true', 'false'],
  openai_cache_mode: ['auto', 'stable'],
  embedding_provider: ['auto', 'ollama', 'openai', 'none'], tts_provider: ['auto', 'openai', 'none'],
  web_search_provider: ['auto', 'searxng', 'openai', 'duckduckgo'], web_search_summarize: ['auto', 'none'],
  openai_reasoning_effort: ['auto', 'none', 'low', 'medium', 'high'], thinking_mode: ['off', 'cloud', 'all'],
  memory_enabled: ['true', 'false'], memory_auto_approve: ['false', 'true'], compaction_enabled: ['true', 'false'],
  auto_continue: ['true', 'false'], debug_llm: ['off', 'admins', 'all'], ollama_think: ['auto', 'on', 'off'],
  smtp_security: ['starttls', 'ssl', 'none'], mail_transport: ['auto', 'smtp', 'gmail_oauth'],
}
export default function AdminProviders() {
  const { refresh } = useAuth()
  const [status, setStatus] = useState<Status | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({})
  useEffect(() => { get<Status>('/admin/providers').then(setStatus).catch(e => setError(e.message)) }, [])
  const edit = (key: string, value: string) => setDraft(d => ({ ...d, [key]: value }))
  async function save() {
    setBusy('save'); setError(''); setMessage('')
    try { setStatus(await put<Status>('/admin/providers', { values: draft })); setDraft({}); await refresh(); setMessage('Configurazione salvata. Modelli aggiornati.') }
    catch (e: any) { setError(e.message) }
    finally { setBusy('') }
  }
  async function test(id: string) {
    setBusy(id)
    try { const result = await post('/admin/providers/' + id + '/test'); setTests(t => ({ ...t, [id]: result })) }
    catch (e: any) { setTests(t => ({ ...t, [id]: { ok: false, detail: e.message } })) }
    finally { setBusy('') }
  }
  if (!status) return <p role="status">{error || 'Caricamento provider…'}</p>
  return <div className="stack">
    <p className="muted">FamilOS usa OpenAI nel cloud oppure un modello locale su Ollama. Le chiavi restano cifrate sul server e non vengono inviate al browser.</p>
    <p className="muted small">Gli avvisi di sicurezza usano le Email di sistema e raggiungono gli amministratori attivi. Configura SMTP prima di pubblicare il sistema. I tentativi ripetuti sono raggruppati per 5 minuti, con un massimo di 20 avvisi per esito ogni ora; tutti i tentativi registrati restano nel registro accessi.</p>
    {status.mock_active && <p className="muted small">La preview usa anche un simulatore di sviluppo. Non è un provider di produzione.</p>}
    {error && <div className="error" role="alert">{error}</div>}
    {message && <div className="ok" role="status">{message}</div>}
    {status.providers.map(p => <section className="card stack" key={p.id}><div className="row between"><h3 style={{ margin: 0 }}>{p.id === 'openai' ? 'OpenAI' : 'Ollama locale'}</h3><small className="muted">{p.active ? 'Configurato' : 'Da configurare'}</small></div>
      <p className="muted small">{p.id === 'openai' ? 'Usa la tua chiave API OpenAI. I modelli disponibili vengono letti dal tuo account.' : 'Indica il server Ollama e un modello già scaricato. Per Docker sul Mac usa host.docker.internal:11434; su Ubuntu il servizio ollama:11434.'}</p>
      <div className="grid2"><label>{p.id === 'openai' ? 'Chiave API' : 'Indirizzo Ollama'}<input type={p.id === 'openai' ? 'password' : 'url'} autoComplete="off" value={draft[p.key] ?? (p.id === 'ollama' ? p.key_state.value : '')} placeholder={p.key_state.configured ? 'Già configurato; lascia vuoto per conservarlo' : p.id === 'openai' ? 'Incolla la chiave API' : 'http://host.docker.internal:11434'} onChange={e => edit(p.key, e.target.value)} /></label>
      <label>Modello predefinito{p.models.length ? <select value={draft[p.model_key] ?? p.model_state.value} onChange={e => edit(p.model_key, e.target.value)}>{!p.models.some(m => m.id === (draft[p.model_key] ?? p.model_state.value)) && <option value={p.model_state.value}>{p.model_state.value}</option>}{p.models.map(m => <option key={m.id} value={m.id}>{m.label || m.id}</option>)}</select> : <input value={draft[p.model_key] ?? p.model_state.value} onChange={e => edit(p.model_key, e.target.value)} placeholder="ID del modello" />}</label></div>
      <div className="row wrap"><button className="ghost" disabled={!!busy || !p.key_state.configured} onClick={() => test(p.id)}>{busy === p.id ? 'Verifica…' : 'Prova connessione'}</button>{p.key_state.source === 'admin' && <button className="ghost" disabled={!!busy} onClick={() => edit(p.key, '')}>Rimuovi dal pannello</button>}{tests[p.id] && <span className={tests[p.id].ok ? 'ok' : 'error'}>{tests[p.id].detail}</span>}</div>
    </section>)}
    {GROUPS.map(group => <details className="card settings-group" key={group.title}><summary>{group.title}</summary><div className="grid2" style={{ marginTop: 16 }}>{group.keys.filter(k => status.settings[k]).map(key => { const field = status.settings[key]; const value = draft[key] ?? (field.secret ? '' : field.value); return <label key={key}>{field.label}{OPTIONS[key] ? <select value={value} onChange={e => edit(key, e.target.value)}>{OPTIONS[key].map(option => <option key={option} value={option}>{option}</option>)}</select> : <input type={field.secret ? 'password' : 'text'} value={value} autoComplete="off" onChange={e => edit(key, e.target.value)} placeholder={field.secret && field.configured ? 'Già impostato' : ''} />}</label> })}</div></details>)}
    <div className="row"><button className="primary" disabled={!!busy} onClick={save}>{busy === 'save' ? 'Salvataggio…' : Object.keys(draft).length ? 'Salva configurazione' : 'Aggiorna modelli'}</button><small className="muted">Le modifiche si applicano senza riavvio.</small></div>
  </div>
}
