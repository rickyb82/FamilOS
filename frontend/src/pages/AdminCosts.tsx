import { Fragment, useEffect, useState } from 'react'
import { get, put } from '../lib/api'

interface Prov {
  provider: string; configured: boolean; estimated_usd: number; budget_usd: number; remaining_usd: number | null
  percent: number | null; real: { ok: boolean; usd?: number; error?: string; via?: string } | null
  stats: { messages?: number; input_tokens?: number; output_tokens?: number; images?: number; videos?: number; searches?: number; cache_read_tokens?: number; cache_write_tokens?: number; cache_saved?: number; image_tokens?: number }
}
interface Report {
  local?: { configured: boolean; model: string; messages: number; input_tokens: number; output_tokens: number; cloud_messages: number; avoided_usd: number; reference: string }
  since: string; providers: Prov[]; users: { name: string; cost: number; messages: number; images: number; videos: number; tokens: number }[]
  pricing: Record<string, any>; settings: Record<string, { value: string; source: string; configured: boolean; secret: boolean }>
}
const LABEL: Record<string, string> = { openai: 'OpenAI', anthropic: 'Claude (Anthropic)', gemini: 'Gemini (Google)', ollama: 'Ollama locale', custom: 'Compatibile OpenAI' }
const usd = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `$${v.toFixed(2)}`)

export default function AdminCosts() {
  const [rep, setRep] = useState<Report | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [showPricing, setShowPricing] = useState(false)
  const load = () => get<Report>('/admin/costs').then(setRep).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
  }, [])

  async function save(keys: string[]) {
    const values: Record<string, string> = {}
    for (const k of keys) if (k in draft) values[k] = draft[k]
    if (!Object.keys(values).length) return
    setBusy(true)
    setErr('')
    try {
      await put('/admin/providers', { values })
      setDraft((d) => { const n = { ...d }; keys.forEach((k) => delete n[k]); return n })
      setMsg('Salvato')
      await load()
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!rep) return <p className="muted">{err || '…'}</p>
  const s = rep.settings
  return (
    <div className="stack">
      <p className="muted">Mese corrente dal {new Date(rep.since).toLocaleDateString('it-IT')}. I provider non espongono il credito residuo alle normali chiavi API: la <b>stima</b> viene dai log di FamilOS × listino prezzi; la <b>spesa reale</b> compare se inserisci una chiave Admin OpenAI. Il <b>rimanente</b> è budget − spesa (reale se disponibile, altrimenti stimata).</p>
      {msg && <div className="ok">{msg}</div>}
      {err && <div className="error">{err}</div>}

      <div className="grid3">
        {rep.local?.configured && (
          <section className="card stack">
            <h3 style={{ margin: 0 }}>🏠 Modello locale (Ollama)</h3>
            <small className="muted">{rep.local.model} · gratis, i dati restano in casa</small>
            <div><small className="muted">Token elaborati in locale</small><div style={{ fontSize: 22, fontWeight: 600 }}>{(rep.local.input_tokens + rep.local.output_tokens).toLocaleString()}</div>
              <small className="muted">{rep.local.input_tokens.toLocaleString()} in ingresso · {rep.local.output_tokens.toLocaleString()} generati</small></div>
            <div><small className="muted">Messaggi</small><div><b>{rep.local.messages}</b> in locale · {rep.local.cloud_messages} nel cloud
              {rep.local.messages + rep.local.cloud_messages > 0 && <> ({Math.round(100 * rep.local.messages / (rep.local.messages + rep.local.cloud_messages))}% locale)</>}</div></div>
            <div><small className="muted">Costo evitato (stima, come se fosse {rep.local.reference})</small><div style={{ fontSize: 22, fontWeight: 600, color: 'var(--ok)' }}>{usd(rep.local.avoided_usd)}</div></div>
          </section>
        )}
        {rep.providers.map((p) => (
          <section key={p.provider} className="card stack" style={{ opacity: p.configured ? 1 : 0.55 }}>
            <h3 style={{ margin: 0 }}>{LABEL[p.provider]}</h3>
            {!p.configured && <small className="muted">chiave non configurata</small>}
            <div><small className="muted">Stima Emily</small><div style={{ fontSize: 22, fontWeight: 600 }}>{usd(p.estimated_usd)}</div></div>
            <div><small className="muted">Spesa reale (chiave Admin)</small>
              <div>{p.provider !== 'openai' ? <span className="muted">non disponibile: vale la stima</span> : p.real ? (p.real.ok ? <><b>{usd(p.real.usd)}</b> <small className="muted">({p.real.via === 'api_key' ? 'con la chiave normale' : 'chiave Admin'})</small></> : <span className="error" style={{ padding: '2px 6px' }}>{p.real.error}</span>) : <span className="muted">inserisci la chiave Admin sotto</span>}</div>
            </div>
            <div>
              <small className="muted">Budget mensile → rimanente</small>
              <div><b>{p.budget_usd > 0 ? usd(p.budget_usd) : 'nessun budget'}</b>{p.remaining_usd !== null && <> → <b style={{ color: p.remaining_usd < 0 ? 'var(--danger)' : 'var(--ok)' }}>{usd(p.remaining_usd)}</b></>}</div>
              {p.percent !== null && (
                <div style={{ height: 8, background: 'var(--bg)', borderRadius: 4, overflow: 'hidden', marginTop: 4 }}>
                  <div style={{ width: `${p.percent}%`, height: '100%', background: p.percent >= 90 ? 'var(--danger)' : p.percent >= 70 ? '#f59e0b' : 'var(--ok)' }} />
                </div>
              )}
            </div>
            <div>
              <small className="muted">Token usati questo mese</small>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{((p.stats.input_tokens ?? 0) + (p.stats.output_tokens ?? 0)).toLocaleString()}</div>
              <small className="muted">
                {(p.stats.input_tokens ?? 0).toLocaleString()} in ingresso · {(p.stats.output_tokens ?? 0).toLocaleString()} generati
                {(p.stats.cache_read_tokens ?? 0) > 0 && <> · {(p.stats.cache_read_tokens ?? 0).toLocaleString()} da cache ({Math.round(100 * (p.stats.cache_read_tokens ?? 0) / Math.max(1, p.stats.input_tokens ?? 1))}% dell'ingresso, risparmiati ≈ {usd(p.stats.cache_saved ?? 0)})</>}
              </small>
            </div>
            <small className="muted">{p.stats.messages ?? 0} messaggi · {p.stats.images ?? 0} immagini{(p.stats.image_tokens ?? 0) > 0 && <> ({(p.stats.image_tokens ?? 0).toLocaleString()} token immagine)</>} · {p.stats.videos ?? 0} video · {p.stats.searches ?? 0} ricerche</small>
            <label>Budget mensile (USD)
              <input type="number" min={0} step={1} placeholder={s[`budget_${p.provider}`]?.value || '0'} value={draft[`budget_${p.provider}`] ?? ''} onChange={(e) => setDraft({ ...draft, [`budget_${p.provider}`]: e.target.value })} />
            </label>
            {p.provider === 'openai' && (
              <label>Chiave Admin <small className="muted">({s[`${p.provider}_admin_key`]?.configured ? s[`${p.provider}_admin_key`].value : 'non impostata'})</small>
                <input type="password" autoComplete="off" placeholder={'Chiave Admin OpenAI'} value={draft[`${p.provider}_admin_key`] ?? ''} onChange={(e) => setDraft({ ...draft, [`${p.provider}_admin_key`]: e.target.value })} />
              </label>
            )}
            <button disabled={busy || !(`budget_${p.provider}` in draft || `${p.provider}_admin_key` in draft)} onClick={() => save(p.provider === 'openai' ? [`budget_${p.provider}`, `${p.provider}_admin_key`] : [`budget_${p.provider}`])}>Salva</button>
          </section>
        ))}
      </div>

      <UsersTokens />

      <details open={showPricing} onToggle={(e) => setShowPricing((e.target as any).open)}>
        <summary>Listino prezzi usato per la stima (USD per milione di token; immagini per pezzo; video per secondo)</summary>
        <p className="muted small">Puoi sovrascrivere singole voci con un JSON, es. <code>{'{"openai": {"image": 0.04}}'}</code>. Le voci non indicate restano quelle di default. Controlla i prezzi sui siti dei provider: cambiano nel tempo.</p>
        <textarea rows={6} value={draft.pricing_json ?? s.pricing_json.value} onChange={(e) => setDraft({ ...draft, pricing_json: e.target.value })} placeholder="{}" />
        <div className="row"><button disabled={busy || !('pricing_json' in draft)} onClick={() => save(['pricing_json'])}>Salva listino</button></div>
        <pre>{JSON.stringify(rep.pricing, null, 1)}</pre>
      </details>
    </div>
  )
}

interface PTok { input_tokens: number; output_tokens: number; cache_read_tokens: number; messages: number; images: number; cost: number; models: { model: string; input_tokens: number; output_tokens: number; cache_read_tokens: number; messages: number; cost: number }[] }
interface UTok { id: string; name: string; avatar: string; cost: number; tokens: number; messages: number; local_tokens: number; cloud_tokens: number; providers: Record<string, PTok> }
interface UReport { label: string; since: string; until: string | null; providers: { id: string; label: string; local: boolean }[]; users: UTok[] }
const tk = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)} M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)} k` : String(n))

/** Token per utente: una colonna per ogni provider usato (cloud e locali), clic sulla riga per il dettaglio per modello. */
function UsersTokens() {
  const [period, setPeriod] = useState<'month' | 'prev' | '7d' | '30d'>('month')
  const [rep, setRep] = useState<UReport | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => { setRep(null); get<UReport>(`/admin/costs/users?period=${period}`).then(setRep).catch((e) => setErr(e.message)) }, [period])
  const tot = rep && rep.providers.map((p) => rep.users.reduce((a, u) => a + (u.providers[p.id] ? u.providers[p.id].input_tokens + u.providers[p.id].output_tokens : 0), 0))
  return (
    <section className="stack">
      <div className="row wrap" style={{ alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>Token per utente</h3>
        <div className="tabs small">
          {([['month', 'Mese corrente'], ['prev', 'Mese scorso'], ['7d', '7 giorni'], ['30d', '30 giorni']] as const).map(([k, l]) => (
            <button key={k} className={period === k ? 'active' : ''} onClick={() => setPeriod(k)}>{l}</button>
          ))}
        </div>
        {rep && <small className="muted">dal {new Date(rep.since).toLocaleDateString('it-IT')}{rep.until ? ` al ${new Date(new Date(rep.until).getTime() - 1).toLocaleDateString('it-IT')}` : ''} · clicca un utente per il dettaglio per modello</small>}
      </div>
      {err && <div className="error">{err}</div>}
      {!rep && !err && <div className="muted">Carico…</div>}
      {rep && (
        <div className="um-wrap">
          <table className="table small ct-table">
            <thead>
              <tr>
                <th className="um-sticky">Utente</th>
                {rep.providers.map((p) => <th key={p.id} className="ct-num">{p.local ? '🏠 ' : '☁️ '}{p.label}</th>)}
                <th className="ct-num">Totale</th><th className="ct-num">di cui locale</th><th className="ct-num">Stima costo</th>
              </tr>
            </thead>
            <tbody>
              {rep.users.map((u) => (
                <Fragment key={u.id}>
                  <tr className="sl-row" onClick={() => setOpen(open === u.id ? null : u.id)}>
                    <td className="um-sticky">{u.avatar} {u.name} <span className="muted">{open === u.id ? '▾' : '▸'}</span><br /><small className="muted">{u.messages} messaggi</small></td>
                    {rep.providers.map((p) => {
                      const x = u.providers[p.id]
                      return <td key={p.id} className="ct-num">{x && x.input_tokens + x.output_tokens > 0 ? <><b>{tk(x.input_tokens + x.output_tokens)}</b><br /><small className="muted">{tk(x.input_tokens)} in · {tk(x.output_tokens)} out</small></> : <span className="muted">—</span>}</td>
                    })}
                    <td className="ct-num"><b>{tk(u.tokens)}</b></td>
                    <td className="ct-num">{u.tokens ? `${Math.round((100 * u.local_tokens) / u.tokens)}%` : '—'}</td>
                    <td className="ct-num">{usd(u.cost)}</td>
                  </tr>
                  {open === u.id && (
                    <tr className="sl-detail-row"><td colSpan={rep.providers.length + 4}>
                      <table className="table small">
                        <thead><tr><th>Provider</th><th>Modello</th><th className="ct-num">Messaggi</th><th className="ct-num">Token in</th><th className="ct-num">di cui da cache</th><th className="ct-num">Token out</th><th className="ct-num">Stima</th></tr></thead>
                        <tbody>
                          {Object.entries(u.providers).flatMap(([pid, x]) => {
                            const label = rep.providers.find((p) => p.id === pid)?.label || pid
                            const rows = x.models.map((m) => (
                              <tr key={pid + m.model}><td>{label}</td><td><code>{m.model}</code></td><td className="ct-num">{m.messages}</td><td className="ct-num">{m.input_tokens.toLocaleString('it-IT')}</td><td className="ct-num">{m.cache_read_tokens ? m.cache_read_tokens.toLocaleString('it-IT') : '—'}</td><td className="ct-num">{m.output_tokens.toLocaleString('it-IT')}</td><td className="ct-num">{usd(m.cost)}</td></tr>
                            ))
                            if (x.images) rows.push(<tr key={pid + 'img'}><td>{label}</td><td className="muted">immagini</td><td className="ct-num">{x.images}</td><td colSpan={3} /><td className="ct-num" /></tr>)
                            return rows
                          })}
                        </tbody>
                      </table>
                    </td></tr>
                  )}
                </Fragment>
              ))}
              {rep.users.length === 0 && <tr><td colSpan={rep.providers.length + 4} className="muted">Nessun utilizzo nel periodo.</td></tr>}
            </tbody>
            {rep.users.length > 0 && tot && (
              <tfoot><tr><td className="um-sticky"><b>Totale</b></td>{tot.map((t, i) => <td key={i} className="ct-num"><b>{t ? tk(t) : '—'}</b></td>)}
                <td className="ct-num"><b>{tk(rep.users.reduce((a, u) => a + u.tokens, 0))}</b></td><td className="ct-num" /><td className="ct-num"><b>{usd(rep.users.reduce((a, u) => a + u.cost, 0))}</b></td></tr></tfoot>
            )}
          </table>
        </div>
      )}
      <small className="muted">🏠 = modelli che girano sui vostri server (gratis); ☁️ = cloud. Token in = testo inviato al modello (compresi documenti e cronologia della chat), out = testo generato. La stima usa il listino qui sotto.</small>
    </section>
  )
}
