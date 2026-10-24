import { useEffect, useState } from 'react'
import { get, post, put } from '../lib/api'

const ages = [{ id: 'young', name: '5–8 anni' }, { id: 'kid', name: '9–12 anni' }, { id: 'teen', name: '13–17 anni' }]
export function ParentBrowserReview() {
  const [domain, setDomain] = useState(''), [age, setAge] = useState('kid'), [provider, setProvider] = useState('ollama')
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  async function review() {
    setBusy(true); setMessage(''); setError('')
    try { await post('/parental/browser/review', { domain, age_group: age, provider }); setMessage('Sito autorizzato per la fascia selezionata.') }
    catch (e: any) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="card stack"><h2>Supervisione dei siti</h2>
    <p>Valuta un nuovo sito prima che lo aprano i bambini. Le autorizzazioni vengono salvate per fascia d’età. Solo gli amministratori possono modificare una decisione già registrata.</p>
    <label>Dominio esatto<input value={domain} onChange={e => setDomain(e.target.value)} placeholder="www.nasa.gov" /></label>
    <label>Fascia d’età<select value={age} onChange={e => setAge(e.target.value)}>{ages.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    <label>Valutazione AI<select value={provider} onChange={e => setProvider(e.target.value)}><option value="ollama">Ollama · locale</option><option value="openai">OpenAI</option></select></label>
    <button disabled={busy || !domain.trim()} onClick={review}>{busy ? 'Verifica in corso…' : 'Verifica sito'}</button>
    {message && <p role="status">{message}</p>}{error && <p role="alert" className="error">{error}</p>}
    <small>Serve un modello con capacità visive. Una valutazione della pagina iniziale non garantisce tutti i contenuti futuri del sito.</small>
  </section>
}

interface Schedule { weekday: number; hour: number; minute: number; timezone: 'Europe/Rome'; model: string; last_started_at?: string; last_finished_at?: string; last_error?: string }
interface Approval { id: string; domain: string; age_group: string; status: string; source: string; last_checked_at?: string; last_error: string }
export default function BrowserReviews() {
  const [schedule, setSchedule] = useState<Schedule | null>(null), [rows, setRows] = useState<Approval[]>([])
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  async function load() { setSchedule(await get<Schedule>('/admin/browser/approvals/schedule')); setRows(await get<Approval[]>('/admin/browser/approvals')) }
  useEffect(() => { load().catch(e => setError(e.message)) }, [])
  async function run(fn: () => Promise<void>) { setBusy(true); setError(''); setMessage(''); try { await fn(); await load() } catch (e: any) { setError(e.message) } finally { setBusy(false) } }
  async function decide(row: Approval, status: string) { await put('/admin/browser/approvals', { domain: row.domain, age_group: row.age_group, status }); setMessage('Decisione aggiornata.') }
  return <section className="card stack"><h2>Autorizzazioni e controlli settimanali</h2>
    <p>Primo accesso automatico: OpenAI. Controlli settimanali: solo Ollama, senza passaggio automatico al cloud.</p>
    {schedule && <><label>Giorno<select value={schedule.weekday} onChange={e => setSchedule({ ...schedule, weekday: Number(e.target.value) })}>{['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'].map((d, i) => <option key={i} value={i}>{d}</option>)}</select></label>
      <label>Ora · Europe/Rome<input type="time" value={`${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}`} onChange={e => { const [hour, minute] = e.target.value.split(':').map(Number); if (Number.isFinite(hour) && Number.isFinite(minute)) setSchedule({ ...schedule, hour, minute }) }} /></label>
      <label>Modello Ollama con capacità visive<input value={schedule.model} onChange={e => setSchedule({ ...schedule, model: e.target.value })} placeholder="Vuoto: modello Ollama configurato" /></label>
      <button disabled={busy} onClick={() => run(async () => { await put('/admin/browser/approvals/schedule', schedule); setMessage('Orario salvato. Il prossimo controllo seguirà questa programmazione.') })}>Salva programmazione</button>
      {schedule.last_started_at && <small>Ultimo avvio: {new Date(schedule.last_started_at).toLocaleString('it-IT')}. {schedule.last_finished_at ? 'Ciclo terminato: consulta gli esiti sotto.' : 'Ciclo in corso o interrotto: consulta gli esiti sotto.'}</small>}
      {schedule.last_error && <p className="error">{schedule.last_error}</p>}</>}
    <button className="ghost" disabled={busy} onClick={() => run(load)}>Aggiorna esiti</button>
    {!rows.length && <p>Nessun sito ancora catalogato.</p>}
    {rows.map(row => <section className="card stack" key={row.id}><strong>{row.domain}</strong><span>{ages.find(a => a.id === row.age_group)?.name} · {({ approved: 'Autorizzato', blocked: 'Bloccato', review: 'Da verificare' } as Record<string, string>)[row.status]} · {row.source}</span>
      {row.last_checked_at && <small>Ultimo tentativo: {new Date(row.last_checked_at).toLocaleString('it-IT')}</small>}{row.last_error && <p role="alert" className="error">{row.last_error}</p>}
      <div className="row"><button disabled={busy} onClick={() => run(() => decide(row, 'blocked'))}>Revoca accesso</button><button className="ghost" disabled={busy} onClick={() => run(() => decide(row, 'review'))}>Richiedi nuova verifica</button><button className="ghost" disabled={busy} onClick={() => run(() => decide(row, 'approved'))}>Autorizza come amministratore</button></div>
    </section>)}
    {message && <p role="status">{message}</p>}{error && <p className="error" role="alert">{error}</p>}
    <small>In caso di errore Ollama viene conservata l’ultima decisione, con avviso nel pannello. I siti bloccati restano tali finché interviene un amministratore. La verifica riguarda la pagina iniziale del dominio esatto, non tutte le sue pagine.</small>
  </section>
}
