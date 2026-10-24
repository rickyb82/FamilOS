import { useEffect, useMemo, useRef, useState } from 'react'
import { get } from '../lib/api'

/** Dettaglio di un circuito Starlink: puntamento (anche live), mappa delle ostruzioni, grafici dello storico, dati tecnici. */

export interface Alignment {
  tilt_deg: number | null; azimuth_deg: number | null; elevation_deg: number | null
  desired_azimuth_deg: number | null; desired_elevation_deg: number | null; delta_deg: number | null; attitude: string; uncertainty_deg?: number | null
}
export interface DishStatus {
  latency_ms: number | null; drop_rate: number | null; down_bps: number | null; up_bps: number | null; uptime_s: number | null
  software: string; hardware: string; outage: string; alerts: { code: number; label: string; info: boolean }[]
  obstruction: { fraction: number | null; currently: boolean; time_fraction?: number | null }
  alignment: Alignment
  eth_mbps?: number | null; gps?: { valid: boolean; sats: number | null } | null; snr_ok?: boolean; snr_persistently_low?: boolean
  stowed?: boolean; mobility?: string; service_class?: string; routers?: number; seconds_to_slot?: number | null
}
export interface Network {
  error?: string; wan_ipv4?: string; wan_ipv4_kind?: string; wan_ipv6?: string[]; ipv4_changed_s?: number | null
  router_software?: string; router_latency_ms?: number | null; clients?: { name: string; ip: string; mac: string; ipv6: string[] }[]; clients_count?: number
}
export interface Term {
  id: string; name: string; serial: string; service_line: string; address: string; plan: string
  routers?: string[]; public_ip_plan?: boolean; network?: Network | null; assigned_ips?: string[]
  online: boolean; error: string; last_connected: string | null; status: DishStatus | null
  misaligned: boolean; obstructed: boolean; problems: string[]; read_at?: string
}
interface Point { ts: string; online: boolean; latency_ms: number | null; down_bps: number | null; up_bps: number | null; drop_rate: number | null; obstruction: number | null; delta_deg: number | null }
interface ObsMap { rows: number; cols: number; snr: number[]; max_theta_deg: number | null; frame: string; read_at: string }

const fmtTime = (s: string) => new Date(s).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
const fmtDay = (s: string) => new Date(s).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const n = (x: number | null | undefined, d = 1, unit = '') => (x == null ? '–' : `${Number(x.toFixed(d))}${unit}`)
function uptime(s: number | null | undefined) {
  if (s == null) return '–'
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return d ? `${d} g ${h} h` : h ? `${h} h ${m} min` : `${m} min`
}

export default function StarlinkDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [term, setTerm] = useState<Term | null>(null)
  const [hist, setHist] = useState<Point[]>([])
  const [hours, setHours] = useState(24)
  const [err, setErr] = useState('')
  const [live, setLive] = useState(false)
  const [liveErr, setLiveErr] = useState('')
  const [trail, setTrail] = useState<{ az: number; el: number }[]>([])
  const [limits, setLimits] = useState({ min: 2, max: 900 })
  const liveStart = useRef(0)
  const acc = useAccountData(id)

  useEffect(() => {
    let alive = true
    get(`/starlink/terminal/${encodeURIComponent(id)}?hours=${hours}`)
      .then((r) => { if (!alive) return; setHist(r.history); setLimits({ min: r.live_min_s, max: r.live_max_s }); setTerm((t) => (live && t ? t : r.terminal)); setErr('') })
      .catch((e) => alive && setErr(e.message))
    return () => { alive = false }
  }, [id, hours])  // eslint-disable-line react-hooks/exhaustive-deps

  // live: una lettura alla volta, ogni live_min_s; si ferma da solo dopo live_max_s
  useEffect(() => {
    if (!live) return
    let stop = false
    liveStart.current = Date.now()
    setTrail([])
    async function tick() {
      while (!stop) {
        if (Date.now() - liveStart.current > limits.max * 1000) { setLive(false); setLiveErr('Live fermato dopo 15 minuti: premi di nuovo per continuare'); return }
        const t0 = Date.now()
        try {
          const t: Term = await get(`/starlink/terminal/${encodeURIComponent(id)}/live`)
          if (stop) return
          setTerm(t); setLiveErr('')
          const al = t.status?.alignment
          if (al?.azimuth_deg != null && al.elevation_deg != null) setTrail((tr) => [...tr.slice(-40), { az: al.azimuth_deg!, el: al.elevation_deg! }])
        } catch (e: any) { if (!stop) setLiveErr(e.message) }
        await new Promise((r) => setTimeout(r, Math.max(limits.min * 1000 - (Date.now() - t0), 300)))
      }
    }
    tick()
    return () => { stop = true }
  }, [live, id, limits.min, limits.max])

  if (err) return <div className="error">{err}</div>
  if (!term) return <div className="muted">Carico il dettaglio…</div>
  const st = term.status
  return (
    <div className="sl-detail stack">
      <div className="row wrap" style={{ alignItems: 'center' }}>
        <b style={{ fontSize: 17 }}>{term.name || term.serial}</b>
        <span className={`badge ${term.online && !term.problems.length ? 'ok' : 'err'}`}>{term.online ? (term.problems.length ? 'attenzione' : 'ok') : 'offline'}</span>
        {live && <span className="badge ok">● live</span>}
        <small className="muted">{term.read_at ? `letto alle ${new Date(term.read_at).toLocaleTimeString('it-IT')}` : ''}</small>
        <span style={{ flex: 1 }} />
        <button className={live ? 'primary' : 'ghost'} onClick={() => { setLiveErr(''); setLive(!live) }} title="Rilegge la parabola ogni 2 secondi: per l'installatore che la sta orientando">
          {live ? '■ Ferma il live' : '▶ Puntamento live'}
        </button>
        <button className="ghost" onClick={onClose}>✕ Chiudi</button>
      </div>
      {liveErr && <div className="error">{liveErr}</div>}
      {term.problems.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }}>{term.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}

      <div className="sl-grid">
        <div className="card"><b>🧭 Puntamento</b><Compass al={st?.alignment} trail={trail} live={live} /></div>
        <div className="card"><b>🌳 Mappa delle ostruzioni</b><ObstructionMap id={id} fraction={st?.obstruction.fraction ?? null} /></div>
      </div>

      <div className="sl-grid">
        <Facts title="📶 Connessione" rows={[
          ['Latenza', n(st?.latency_ms, 0, ' ms')], ['Pacchetti persi', st?.drop_rate == null ? '–' : `${(st.drop_rate * 100).toFixed(1)}%`],
          ['Download', st?.down_bps == null ? '–' : `${(st.down_bps / 1e6).toFixed(1)} Mbps`], ['Upload', st?.up_bps == null ? '–' : `${(st.up_bps / 1e6).toFixed(1)} Mbps`],
          ['Ethernet', st?.eth_mbps ? `${st.eth_mbps} Mbps` : '–'], ['Segnale sopra il rumore', st ? (st.snr_ok ? 'sì' : 'no') + (st.snr_persistently_low ? ' · basso da tempo' : '') : '–'],
          ['Interruzione', st?.outage || 'nessuna'],
        ]} />
        <Facts title="🛰 Parabola" rows={[
          ['Hardware', st?.hardware || '–'], ['Software', st?.software || '–'], ['Acceso da', uptime(st?.uptime_s)],
          ['GPS', st?.gps ? `${st.gps.valid ? 'valido' : 'non valido'} · ${st.gps.sats ?? '?'} satelliti` : '–'],
          ['Uso', [st?.mobility, st?.service_class].filter(Boolean).join(' · ') || '–'], ['Router collegati', st?.routers != null ? String(st.routers) : '–'],
          ['Avvisi informativi', st?.alerts.filter((a) => a.info).map((a) => a.label).join(', ') || 'nessuno'],
        ]} />
        <NetworkCard t={term} acc={acc} />
        <UsageCard acc={acc} />
        <Facts title="📍 Circuito" rows={[
          ['Indirizzo', term.address || '–'], ['Linea di servizio', term.service_line || '–'], ['Seriale', term.serial || '–'],
          ['Piano', term.plan || '–'], ['Ultimo contatto', term.last_connected ? fmtDay(term.last_connected) : '–'],
        ]} />
      </div>

      <div className="row wrap" style={{ alignItems: 'center' }}>
        <b>📈 Andamento</b>
        <div className="tabs small">
          {[[6, '6 ore'], [24, '24 ore'], [72, '3 giorni'], [168, '7 giorni']].map(([h, l]) => (
            <button key={h} className={hours === h ? 'active' : ''} onClick={() => setHours(h as number)}>{l}</button>
          ))}
        </div>
        <small className="muted">dalle letture di Emily (una ogni pochi minuti)</small>
      </div>
      {hist.length < 2
        ? <div className="muted">Lo storico si riempie con le prossime letture: torna tra qualche minuto.</div>
        : (
          <div className="sl-charts">
            <OnlineStrip pts={hist} />
            <LineChart title="Latenza" unit="ms" pts={hist} series={[{ key: 'latency_ms', label: 'Latenza', color: 'var(--series-1)' }]} />
            <LineChart title="Velocità" unit="Mbps" pts={hist} scale={1e-6} series={[
              { key: 'down_bps', label: 'Download', color: 'var(--series-1)' }, { key: 'up_bps', label: 'Upload', color: 'var(--series-2)' }]} />
            <LineChart title="Ostruzione" unit="%" pts={hist} scale={100} series={[{ key: 'obstruction', label: 'Ostruzione', color: 'var(--series-1)' }]} />
            <LineChart title="Scarto dal puntamento consigliato" unit="°" pts={hist} series={[{ key: 'delta_deg', label: 'Scarto', color: 'var(--series-1)' }]} />
          </div>
        )}
    </div>
  )
}

function ago(s: number) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return d ? `${d} g ${h} h fa` : h ? `${h} h ${m} min fa` : `${m} min fa`
}
function Copy({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return <button className="icon sl-copy" title="Copia" onClick={() => { navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1200) }).catch(() => {}) }}>{ok ? '✓' : '⧉'}</button>
}
/** IP assegnati: li legge Emily dal router Starlink (WAN IPv4/IPv6, cambio dell'IP pubblico, dispositivi in LAN). */
interface Addr { value: string; version: number; prefix: boolean; kind: string; field: string; source: string }
interface Addrs { ips: Addr[]; usage_summary?: UsageSummary | null; usage?: Record<string, { field: string; value: number }[]>; read_at: string; sources: Record<string, { url: string; status: number | null; ips: number; raw?: string; truncated?: boolean }> }
/** IP assegnati e consumo dati: dati dell'account che il sito mostra, letti da Emily con la stessa sessione. */
function useAccountData(id: string) {
  const [ad, setAd] = useState<Addrs | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    setBusy(true); setErr('')
    get<Addrs>(`/starlink/terminal/${encodeURIComponent(id)}/addresses`).then((r) => alive && setAd(r)).catch((e) => alive && setErr(e.message)).finally(() => alive && setBusy(false))
    return () => { alive = false }
  }, [id])
  return { ad, err, busy }
}

interface UsageSummary { cycle_start?: string | null; cycle_end?: string | null; total_gb?: number | null; download_gb?: number | null; upload_gb?: number | null; limit_gb?: number | null; plan_used_gb?: number | null; overage_gb?: number | null }
const gb = (v: number | null | undefined) => (v == null ? '–' : v >= 100 ? `${v.toFixed(0)} GB` : `${Number(v.toFixed(2))} GB`)
const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' }) : '')
/** Consumo del ciclo di fatturazione in corso (download e upload) e limite del profilo. */
function UsageCard({ acc }: { acc: { ad: Addrs | null; err: string; busy: boolean } }) {
  const u = acc.ad?.usage_summary
  const pct = u?.limit_gb ? Math.min(100, ((u.plan_used_gb ?? 0) / u.limit_gb) * 100) : null
  return (
    <div className="card sl-usage">
      <div className="row between" style={{ alignItems: 'baseline' }}>
        <b>📊 Dati consumati</b>
        {u && (u.cycle_start || u.cycle_end) && <small className="muted">ciclo {day(u.cycle_start)}{u.cycle_end ? ` – ${day(u.cycle_end)}` : ''}</small>}
      </div>
      {acc.err && <div className="error small">{acc.err}</div>}
      {!acc.ad && !acc.err && <div className="muted small">{acc.busy ? 'Leggo i consumi…' : ''}</div>}
      {acc.ad && !u && <p className="muted small">Starlink non ha restituito il consumo per questa linea. Se sul sito lo vedi, mandami il testo «consumo dati» della 🔧 Diagnostica.</p>}
      {u && (
        <>
          <div className="sl-usage-total"><span>{gb(u.total_gb)}</span><small className="muted">nel ciclo in corso</small></div>
          <div className="sl-usage-split">
            <div><small className="muted">↓ Download</small><b>{gb(u.download_gb)}</b></div>
            <div><small className="muted">↑ Upload</small><b>{gb(u.upload_gb)}</b></div>
          </div>
          {u.download_gb == null && u.upload_gb == null && <small className="muted">Starlink non separa download e upload per questa linea.</small>}
          {u.limit_gb != null && (
            <div className="sl-usage-limit">
              <div className="row between"><small className="muted">Limite del profilo</small><small><b>{gb(u.plan_used_gb)}</b> di {gb(u.limit_gb)}</small></div>
              <div className="sl-bar" role="progressbar" aria-valuenow={Math.round(pct ?? 0)} aria-valuemin={0} aria-valuemax={100}>
                <i style={{ width: `${pct ?? 0}%` }} className={(pct ?? 0) >= 90 ? 'hot' : (pct ?? 0) >= 75 ? 'warm' : ''} />
              </div>
              {!!u.overage_gb && <small className="error-text">Oltre il limite: {gb(u.overage_gb)}</small>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function NetworkCard({ t, acc }: { t: Term; acc: { ad: Addrs | null; err: string; busy: boolean } }) {
  const net = t.network
  const [all, setAll] = useState(false)
  const { ad, err: adErr, busy: adBusy } = acc
  const raw = ad && Object.entries(ad.sources).filter(([, v]) => v.raw != null)
  return (
    <div className="card">
      <b>🌐 Rete e IP</b>
      <div className="sl-assigned">
        <small className="muted">IP assegnati (come sul sito Starlink)</small>
        {adErr && <div className="error small">{adErr}</div>}
        {!ad && !adErr && <div className="muted small">{adBusy ? 'Leggo gli IP…' : ''}</div>}
        {ad && (ad.ips.length
          ? ad.ips.map((a) => <div key={a.value} className="sl-ip" title={`da ${a.source}: ${a.field}`}><b>{a.value}</b> <Copy text={a.value} /> <small className={a.kind === 'pubblico' ? 'ok-text' : 'muted'}>{a.kind}</small></div>)
          : <div className="muted small">Starlink non ha restituito IP per questo terminale. {raw?.length ? 'Apri la diagnostica qui sotto e mandami il testo.' : ''}</div>)}
        {raw && raw.length > 0 && (
          <details className="sl-clients">
            <summary className="muted small">🔧 Diagnostica (solo amministratori): risposte grezze di Starlink, senza cookie</summary>
            {raw.map(([name, v]) => (
              <div key={name} className="stack" style={{ gap: 4, marginTop: 6 }}>
                <small><b>{name}</b> · HTTP {v.status ?? '–'} · {v.ips} IP trovati{v.truncated ? ' · troncata' : ''} <Copy text={`${v.url}

${v.raw}`} /></small>
                <pre className="sl-raw">{v.raw}</pre>
              </div>
            ))}
          </details>
        )}
      </div>
      <small className="muted">Dal router Starlink</small>
      {!t.routers?.length ? <p className="muted small">Nessun router Starlink associato al terminale: se usate un router vostro (o la modalità bypass), gli IP li vede il vostro router, non Starlink.</p>
        : !net ? <p className="muted small">IP non ancora letti: compaiono alla prossima lettura (parabola online).</p>
        : net.error ? <p className="muted small">Il router Starlink non ha risposto ({net.error}). Succede se è spento, in bypass o non è un router Starlink.</p>
        : (
          <>
            <table className="sl-facts"><tbody>
              <tr><td className="muted">IPv4 WAN</td><td>{net.wan_ipv4 ? <><b>{net.wan_ipv4}</b> <Copy text={net.wan_ipv4} /><br /><small className={net.wan_ipv4_kind === 'pubblico' ? 'ok-text' : 'muted'}>{net.wan_ipv4_kind || ''}</small></> : '–'}</td></tr>
              <tr><td className="muted">IPv6 WAN</td><td>{net.wan_ipv6?.length ? net.wan_ipv6.map((a) => <div key={a} className="sl-ip">{a} <Copy text={a} /></div>) : '–'}</td></tr>
              <tr><td className="muted">IP pubblico nel piano</td><td>{t.public_ip_plan ? 'sì' : 'no'}</td></tr>
              <tr><td className="muted">Ultimo cambio IPv4</td><td>{net.ipv4_changed_s != null ? ago(net.ipv4_changed_s) : '–'}</td></tr>
              <tr><td className="muted">Router</td><td>{net.router_software || '–'}{net.router_latency_ms != null ? ` · ${net.router_latency_ms} ms` : ''}</td></tr>
              <tr><td className="muted">Dispositivi in LAN</td><td>{net.clients_count ?? 0}</td></tr>
            </tbody></table>
            {!!net.clients?.length && (
              <details className="sl-clients" open={all} onToggle={(e) => setAll((e.target as HTMLDetailsElement).open)}>
                <summary className="muted small">Mostra i dispositivi collegati</summary>
                <table className="sl-facts"><tbody>{net.clients.map((c, i) => (
                  <tr key={c.mac || i}><td>{c.name || <span className="muted">senza nome</span>}<br /><small className="muted">{c.mac}</small></td><td>{c.ip || '–'}{c.ip && <Copy text={c.ip} />}</td></tr>
                ))}</tbody></table>
              </details>
            )}
          </>
        )}
    </div>
  )
}

function Facts({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="card">
      <b>{title}</b>
      <table className="sl-facts"><tbody>{rows.map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td>{v}</td></tr>)}</tbody></table>
    </div>
  )
}

// ------------------------------------------------------------------ bussola del puntamento
/** Cielo visto dall'alto: nord in alto, azimut in senso orario, centro = zenit (90°), bordo = orizzonte (0°). */
function Compass({ al, trail, live }: { al?: Alignment; trail: { az: number; el: number }[]; live: boolean }) {
  const R = 110, C = 130
  const pos = (az: number, el: number) => {
    const r = (Math.max(0, Math.min(90, 90 - el)) / 90) * R
    const a = (az * Math.PI) / 180
    return [C + r * Math.sin(a), C - r * Math.cos(a)]
  }
  if (!al || al.azimuth_deg == null || al.elevation_deg == null) return <div className="muted">Puntamento non disponibile (parabola offline o dato non ancora letto).</div>
  const [cx, cy] = pos(al.azimuth_deg, al.elevation_deg)
  const hasTarget = al.desired_azimuth_deg != null && al.desired_elevation_deg != null && (al.desired_azimuth_deg !== 0 || al.desired_elevation_deg !== 0)
  const [tx, ty] = hasTarget ? pos(al.desired_azimuth_deg!, al.desired_elevation_deg!) : [cx, cy]
  let turn = 0, raise = 0
  if (hasTarget) {
    turn = ((al.desired_azimuth_deg! - al.azimuth_deg + 540) % 360) - 180
    raise = al.desired_elevation_deg! - al.elevation_deg
  }
  const okTurn = Math.abs(turn) < 2, okRaise = Math.abs(raise) < 2
  return (
    <div className="sl-compass">
      <svg viewBox="0 0 260 260" role="img" aria-label={`Puntamento attuale azimut ${al.azimuth_deg}°, elevazione ${al.elevation_deg}°`}>
        {[0, 30, 60].map((e) => <circle key={e} cx={C} cy={C} r={((90 - e) / 90) * R} className="sl-ring" />)}
        <line x1={C - R} y1={C} x2={C + R} y2={C} className="sl-ring" /><line x1={C} y1={C - R} x2={C} y2={C + R} className="sl-ring" />
        {[['N', 0], ['E', 90], ['S', 180], ['O', 270]].map(([l, a]) => {
          const [x, y] = [C + (R + 12) * Math.sin(((a as number) * Math.PI) / 180), C - (R + 12) * Math.cos(((a as number) * Math.PI) / 180)]
          return <text key={l as string} x={x} y={y + 4} textAnchor="middle" className="sl-axis">{l}</text>
        })}
        <text x={C + 4} y={C - ((90 - 30) / 90) * R - 3} className="sl-axis small">30°</text>
        <text x={C + 4} y={C - ((90 - 60) / 90) * R - 3} className="sl-axis small">60°</text>
        {trail.length > 1 && <polyline points={trail.map((p) => pos(p.az, p.el).join(',')).join(' ')} className="sl-trail" />}
        {hasTarget && (
          <>
            <circle cx={tx} cy={ty} r={9} className="sl-target" />
            <line x1={cx} y1={cy} x2={tx} y2={ty} className="sl-arrow" markerEnd="url(#sl-arrowhead)" />
          </>
        )}
        <defs><marker id="sl-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className="sl-arrowhead" /></marker></defs>
        <circle cx={cx} cy={cy} r={6} className={`sl-now ${live ? 'live' : ''}`} />
      </svg>
      {hasTarget && <Crosshair turn={turn} raise={raise} />}
      <div className="sl-compass-text">
        <div>Attuale: <b>az {n(al.azimuth_deg)}°</b> · <b>el {n(al.elevation_deg)}°</b></div>
        {hasTarget && <div>Consigliato: <b>az {n(al.desired_azimuth_deg)}°</b> · <b>el {n(al.desired_elevation_deg)}°</b></div>}
        {al.delta_deg != null && <div>Scarto: <b>{n(al.delta_deg)}°</b></div>}
        <div className="muted">Inclinazione {n(al.tilt_deg)}°{al.attitude ? ` · orientamento ${al.attitude}` : ''}{al.uncertainty_deg != null ? ` · incertezza ±${n(al.uncertainty_deg)}°` : ''}</div>
        {hasTarget && (
          <div className="sl-howto">
            <div className={okTurn ? 'ok-text' : ''}>{okTurn ? '✓ Direzione giusta' : `↻ Ruota di ${Math.abs(turn).toFixed(1)}° in senso ${turn > 0 ? 'orario (verso est)' : 'antiorario (verso ovest)'}`}</div>
            <div className={okRaise ? 'ok-text' : ''}>{okRaise ? '✓ Elevazione giusta' : raise > 0 ? `↑ Alza il puntamento di ${raise.toFixed(1)}° (più verticale)` : `↓ Abbassa il puntamento di ${(-raise).toFixed(1)}° (più verso l'orizzonte)`}</div>
          </div>
        )}
        {!hasTarget && <div className="muted">La parabola non indica un puntamento consigliato: di solito vuol dire che è già allineata o che si orienta da sola.</div>}
        <small className="muted">Le parabole con i motori si orientano da sole; le versioni fisse si spostano a mano: avvia il live e segui le frecce.</small>
      </div>
    </div>
  )
}

/** Mirino ingrandito: il bersaglio (puntamento consigliato) e' al centro, il pallino e' la parabola. Scala adattiva. */
function Crosshair({ turn, raise }: { turn: number; raise: number }) {
  const S = 160, C = S / 2, Rr = 66
  const range = Math.max(5, Math.ceil(Math.max(Math.abs(turn), Math.abs(raise)) * 1.25 / 5) * 5)
  const k = Rr / range
  // ruotare in senso orario (turn > 0) = la parabola sta a sinistra del bersaglio; alzare (raise > 0) = sta sotto
  const x = C - Math.max(-range, Math.min(range, turn)) * k, y = C + Math.max(-range, Math.min(range, raise)) * k
  const ok = Math.abs(turn) < 2 && Math.abs(raise) < 2
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="sl-crosshair" role="img" aria-label={`Mirino: ruota di ${turn.toFixed(1)} gradi, alza di ${raise.toFixed(1)} gradi`}>
      <circle cx={C} cy={C} r={Rr} className="sl-ring" />
      <circle cx={C} cy={C} r={Rr / 2} className="sl-ring" />
      <line x1={C - Rr} y1={C} x2={C + Rr} y2={C} className="sl-ring" /><line x1={C} y1={C - Rr} x2={C} y2={C + Rr} className="sl-ring" />
      <circle cx={C} cy={C} r={Math.max(4, 2 * k)} className={`sl-target ${ok ? 'hit' : ''}`} />
      <line x1={x} y1={y} x2={C} y2={C} className="sl-arrow" markerEnd="url(#sl-arrowhead)" />
      <circle cx={x} cy={y} r={6} className="sl-now" />
      <text x={C} y={12} textAnchor="middle" className="sl-axis small">più alto</text>
      <text x={C} y={S - 4} textAnchor="middle" className="sl-axis small">più basso</text>
      <text x={4} y={C - 6} className="sl-axis small">ovest</text>
      <text x={S - 4} y={C - 6} textAnchor="end" className="sl-axis small">est</text>
      <text x={C + Rr} y={C + Rr} textAnchor="end" className="sl-axis small">±{range}°</text>
    </svg>
  )
}

// ------------------------------------------------------------------ mappa delle ostruzioni
function ObstructionMap({ id, fraction }: { id: string; fraction: number | null }) {
  const [m, setM] = useState<ObsMap | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [hover, setHover] = useState<string>('')
  const canvas = useRef<HTMLCanvasElement>(null)
  const load = async () => {
    setBusy(true); setErr('')
    try { setM(await get(`/starlink/terminal/${encodeURIComponent(id)}/map`)) } catch (e: any) { setErr(e.message) }
    setBusy(false)
  }
  useEffect(() => { load() }, [id])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const cv = canvas.current
    if (!cv || !m) return
    const ctx = cv.getContext('2d')
    if (!ctx) return
    const css = getComputedStyle(cv)
    const clear = css.getPropertyValue('--obs-clear').trim() || '#d7e3f4'
    const blocked = css.getPropertyValue('--obs-blocked').trim() || '#dc2626'
    const unknown = css.getPropertyValue('--obs-unknown').trim() || '#eef0f3'
    cv.width = m.cols; cv.height = m.rows
    ctx.clearRect(0, 0, m.cols, m.rows)
    for (let r = 0; r < m.rows; r++) for (let c = 0; c < m.cols; c++) {
      const v = m.snr[r * m.cols + c]
      ctx.fillStyle = v < 0 ? unknown : v >= 0.999 ? clear : blocked
      if (v >= 0 && v < 0.999) ctx.globalAlpha = 0.35 + 0.65 * (1 - v); else ctx.globalAlpha = 1
      ctx.fillRect(c, r, 1, 1)
    }
    ctx.globalAlpha = 1
  }, [m])
  const blockedPct = useMemo(() => {
    if (!m) return null
    const seen = m.snr.filter((v) => v >= 0)
    return seen.length ? (seen.filter((v) => v < 0.999).length / seen.length) * 100 : null
  }, [m])
  function onMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!m) return
    const rect = e.currentTarget.getBoundingClientRect()
    const c = Math.floor(((e.clientX - rect.left) / rect.width) * m.cols), r = Math.floor(((e.clientY - rect.top) / rect.height) * m.rows)
    const v = m.snr[r * m.cols + c]
    setHover(v == null ? '' : v < 0 ? 'cielo non ancora osservato' : v >= 0.999 ? 'cielo libero' : `ostruito (segnale ${Math.round(v * 100)}%)`)
  }
  return (
    <div className="stack" style={{ gap: 6 }}>
      {err && <div className="error">{err}</div>}
      {!m && !err && <div className="muted">{busy ? 'Scarico la mappa dalla parabola…' : ''}</div>}
      {m && (
        <>
          <div className="sl-map-wrap">
            <canvas ref={canvas} className="sl-map" onMouseMove={onMove} onMouseLeave={() => setHover('')} aria-label="Mappa delle ostruzioni: cielo visto dalla parabola" />
            {m.frame === 'terra' && <span className="sl-map-n">N</span>}
          </div>
          <div className="row wrap" style={{ gap: 12, fontSize: 13 }}>
            <span><i className="sl-key clear" /> libero</span><span><i className="sl-key blocked" /> ostruito</span><span><i className="sl-key unknown" /> non ancora visto</span>
          </div>
          <small className="muted">
            {hover || <>Cielo visto dalla parabola{m.frame === 'terra' ? ' (nord in alto)' : m.frame === 'parabola' ? ' (orientata come la parabola)' : ''}, centro = zenit. {blockedPct != null ? `Ostruito il ${blockedPct.toFixed(1)}% del cielo osservato` : ''}{fraction != null ? ` · ostruzione del servizio ${(fraction * 100).toFixed(1)}%` : ''}. Letta alle {new Date(m.read_at).toLocaleTimeString('it-IT')}.</>}
          </small>
          <div><button className="ghost" disabled={busy} onClick={load}>↻ Rileggi la mappa</button></div>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ grafici
function OnlineStrip({ pts }: { pts: Point[] }) {
  const t0 = new Date(pts[0].ts).getTime(), t1 = new Date(pts[pts.length - 1].ts).getTime()
  const span = Math.max(1, t1 - t0)
  const off = pts.filter((p) => !p.online).length
  return (
    <div className="sl-chart">
      <div className="sl-chart-title"><b>Online</b> <small className="muted">{off ? `${off} letture offline su ${pts.length}` : 'sempre online nel periodo'}</small></div>
      <svg viewBox="0 0 600 18" preserveAspectRatio="none" className="sl-strip">
        <rect x="0" y="2" width="600" height="14" rx="4" className="sl-strip-on" />
        {pts.map((p, i) => {
          if (p.online) return null
          const a = new Date(pts[Math.max(0, i - 1)].ts).getTime(), b = new Date(p.ts).getTime()
          const x = ((a - t0) / span) * 600, w = Math.max(3, ((b - a) / span) * 600)
          return <rect key={p.ts} x={x} y="2" width={w} height="14" className="sl-strip-off"><title>offline · {fmtDay(p.ts)}</title></rect>
        })}
      </svg>
      <div className="row between muted" style={{ fontSize: 12 }}><span>{fmtDay(pts[0].ts)}</span><span>{fmtDay(pts[pts.length - 1].ts)}</span></div>
    </div>
  )
}

type Key = 'latency_ms' | 'down_bps' | 'up_bps' | 'obstruction' | 'delta_deg'
function LineChart({ title, unit, pts, series, scale = 1 }: { title: string; unit: string; pts: Point[]; series: { key: Key; label: string; color: string }[]; scale?: number }) {
  const W = 600, H = 150, L = 44, Rm = 12, T = 10, B = 22
  const [hover, setHover] = useState<number | null>(null)
  const t0 = new Date(pts[0].ts).getTime(), t1 = new Date(pts[pts.length - 1].ts).getTime()
  const span = Math.max(1, t1 - t0)
  const vals = series.flatMap((s) => pts.map((p) => p[s.key]).filter((v): v is number => v != null).map((v) => v * scale))
  if (!vals.length) return <div className="sl-chart"><div className="sl-chart-title"><b>{title}</b></div><div className="muted">Nessun dato nel periodo.</div></div>
  const max = niceMax(Math.max(...vals))
  const x = (ts: string) => L + ((new Date(ts).getTime() - t0) / span) * (W - L - Rm)
  const y = (v: number) => T + (1 - v / max) * (H - T - B)
  const path = (k: Key) => {
    let d = '', pen = false
    pts.forEach((p) => {
      const v = p[k]
      if (v == null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(v * scale).toFixed(1)}`; pen = true
    })
    return d
  }
  const ticks = [0, max / 2, max]
  const fmt = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)).replace(/\.0+$/, '')
  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    let best = 0, bd = Infinity
    pts.forEach((p, i) => { const d = Math.abs(x(p.ts) - px); if (d < bd) { bd = d; best = i } })
    setHover(best)
  }
  const hp = hover != null ? pts[hover] : null
  const last = pts[pts.length - 1]
  return (
    <div className="sl-chart">
      <div className="sl-chart-title">
        <b>{title}</b> <small className="muted">({unit})</small>
        {series.length > 1 && <span className="sl-legend">{series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</span>}
      </div>
      <div style={{ position: 'relative' }}>
        <svg viewBox={`0 0 ${W} ${H}`} className="sl-svg" onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label={`${title} nel periodo`}>
          {ticks.map((v) => <g key={v}><line x1={L} x2={W - Rm} y1={y(v)} y2={y(v)} className="sl-grid-line" /><text x={L - 6} y={y(v) + 4} textAnchor="end" className="sl-axis small">{fmt(v)}</text></g>)}
          <text x={L} y={H - 6} className="sl-axis small">{fmtTime(pts[0].ts)}</text>
          <text x={W - Rm} y={H - 6} textAnchor="end" className="sl-axis small">{fmtTime(last.ts)}</text>
          {series.map((s) => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />)}
          {hp && <line x1={x(hp.ts)} x2={x(hp.ts)} y1={T} y2={H - B} className="sl-cross" />}
          {hp && series.map((s) => hp[s.key] != null && <circle key={s.key} cx={x(hp.ts)} cy={y(hp[s.key]! * scale)} r={4} fill={s.color} className="sl-dot" />)}
        </svg>
        {hp && (
          <div className="sl-tip" style={{ left: `${(x(hp.ts) / W) * 100}%` }}>
            <div className="muted">{fmtDay(hp.ts)}{hp.online ? '' : ' · offline'}</div>
            {series.map((s) => <div key={s.key}><i style={{ background: s.color }} />{s.label}: <b>{hp[s.key] == null ? '–' : fmt(hp[s.key]! * scale)} {unit}</b></div>)}
          </div>
        )}
      </div>
    </div>
  )
}

function niceMax(v: number) {
  if (!(v > 0)) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  const m = v / p
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p
}
