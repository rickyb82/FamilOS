import { memo, useEffect, useId, useState } from 'react'
import { ApiError } from '../lib/api'
import { dateLabel, timeLabel, valueLabel, wellness, type SleepPeriod, type WellnessSleep } from '../lib/wellness'

const STAGES = [
  { key: 'awake', symbol: '4', label: 'Sveglio', color: '#edbd90', row: 0 },
  { key: 'rem', symbol: '3', label: 'REM', color: '#c3b4ed', row: 1 },
  { key: 'light', symbol: '2', label: 'Leggero', color: '#85d4cc', row: 2 },
  { key: 'deep', symbol: '1', label: 'Profondo', color: '#7b92d7', row: 3 },
] as const

type Stage = typeof STAGES[number]
export function sleepDuration(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '—'
  const seconds = Math.floor(value), hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60
  return [hours ? `${hours} h` : '', minutes ? `${minutes} min` : '', rest ? `${rest} s` : ''].filter(Boolean).join(' ') || '0 min'
}

/** Runs preserve every literal position; unexpected symbols remain visible gaps. */
export function sleepPhaseRuns(phases: string, epochSeconds: number) {
  const runs: Array<{ stage: Stage | null; start: number; duration: number }> = []
  for (let index = 0; index < phases.length; index++) {
    const stage = STAGES.find(item => item.symbol === phases[index]) || null
    const previous = runs[runs.length - 1]
    if (previous && previous.stage === stage) previous.duration += epochSeconds
    else runs.push({ stage, start: index * epochSeconds, duration: epochSeconds })
  }
  return runs
}

function periodLabel(period: SleepPeriod) {
  return `${dateLabel(period.started_at)} · ${timeLabel(period.started_at)}–${timeLabel(period.ended_at)}`
}

function SleepPhases({ period }: { period: SleepPeriod }) {
  const [resolution, setResolution] = useState<30 | 300>(30)
  const has30 = typeof period.phases_30_seconds === 'string' && period.phases_30_seconds.length > 0 && period.phases_30_seconds.length <= 1920
  const has5 = typeof period.phases_5_minutes === 'string' && period.phases_5_minutes.length > 0 && period.phases_5_minutes.length <= 192
  const step = resolution === 30 ? has30 ? 30 : 300 : has5 ? 300 : 30
  const phases = step === 30 ? has30 ? period.phases_30_seconds! : '' : has5 ? period.phases_5_minutes! : ''
  const runs = sleepPhaseRuns(phases, step), total = phases.length * step
  const unknown = runs.filter(run => !run.stage).reduce((sum, run) => sum + run.duration, 0)
  return <section className="wl-card wl-sleep-phases" aria-label="Fasi disponibili">
    <div className="wl-card-heading"><div><h2>Fasi disponibili</h2><p>La sequenza ricevuta dall’anello, nel suo ordine.</p></div>
      {has30 && has5 && <div className="wl-sleep-resolution" aria-label="Dettaglio delle fasi"><button onClick={() => setResolution(30)} aria-pressed={step === 30}>30 s</button><button onClick={() => setResolution(300)} aria-pressed={step === 300}>5 min</button></div>}
    </div>
    {phases ? <>
      <div className="wl-sleep-chart"><div className="wl-sleep-chart-labels" aria-hidden="true">{STAGES.map(stage => <span key={stage.key}>{stage.label}</span>)}</div>
        <div className="wl-sleep-chart-content"><svg viewBox="0 0 900 160" preserveAspectRatio="none" role="img" aria-label="Fasi del sonno · sequenza relativa">
          <g className="wl-sleep-grid">{STAGES.map(stage => <path key={stage.key} d={`M0 ${20 + stage.row * 40}H900`} />)}</g>
          {runs.map((run, index) => <rect key={index} x={run.start / total * 900} width={run.duration / total * 900} y={run.stage ? 8 + run.stage.row * 40 : 3} height={run.stage ? 24 : 154} rx={run.stage ? 2 : 0} fill={run.stage?.color || 'transparent'} className={run.stage ? 'wl-sleep-run' : 'wl-sleep-gap'}><title>{run.stage?.label || 'Dato non disponibile'} · {sleepDuration(run.start)}–{sleepDuration(run.start + run.duration)} nella sequenza</title></rect>)}
        </svg><div className="wl-sleep-axis"><span>0 min</span><span>Tempo relativo alla sequenza</span><span>{sleepDuration(total)}</span></div></div>
      </div>
      <p className="wl-note">{phases.length} campioni da {step === 30 ? '30 secondi' : '5 minuti'} · {sleepDuration(total)} di sequenza{unknown > 0 ? ` · ${sleepDuration(unknown)} senza fase disponibile` : ''}. Il grafico mostra solo i campioni disponibili: la sua durata può differire dal tempo a letto.</p>
    </> : <p className="wl-empty-note">Le fasi di questo periodo non sono ancora disponibili. Gli orari e le durate ricevuti restano visibili.</p>}
  </section>
}

export default memo(function SleepPanel({ demo = false, visible = true }: { demo?: boolean; visible?: boolean }) {
  const [data, setData] = useState<WellnessSleep | null>(null), [selected, setSelected] = useState('')
  const [days, setDays] = useState(14), [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(true), [error, setError] = useState('')
  const selectId = useId(), daysId = useId()
  useEffect(() => {
    if (demo || !visible) return
    let alive = true, timer: ReturnType<typeof setTimeout> | undefined
    setLoading(true)
    const load = async () => {
      let denied = false
      try {
        const next = await wellness.sleep(days)
        if (!alive) return
        setData(next); setError('')
        setSelected(current => next.periods.some(period => period.id === current) ? current : [...next.periods].sort((a, b) => b.started_at - a.started_at)[0]?.id || '')
      } catch (failure) {
        if (!alive) return
        denied = failure instanceof ApiError && [401, 403, 404].includes(failure.status)
        if (denied) { setData(null); setSelected('') }
        setError(failure instanceof ApiError && failure.status === 401 ? 'Accedi di nuovo per vedere il tuo sonno.'
          : failure instanceof ApiError && failure.status === 403 ? 'Sonno non disponibile per questo profilo.'
          : failure instanceof ApiError && failure.status === 404 ? 'Il sonno non è ancora disponibile su questo server.'
          : failure instanceof Error ? failure.message : 'Il sonno non è raggiungibile in questo momento.')
      } finally {
        if (alive) { setLoading(false); if (!denied) timer = setTimeout(load, 15000) }
      }
    }
    void load()
    return () => { alive = false; clearTimeout(timer) }
  }, [demo, days, retry, visible])
  const periods = [...(data?.periods || [])].sort((a, b) => b.started_at - a.started_at)
  const period = periods.find(item => item.id === selected) || periods[0]
  return <div className="wl-sleep-panel">
    <div className="wl-page-title"><div><span className="wl-eyebrow">IL RIPOSO, NEL TEMPO</span><h1>Il tuo sonno.</h1><p>Periodi e fasi del riposo ricevuti dal tuo anello.</p></div>
      {!demo && <label className="wl-sleep-window" htmlFor={daysId}>Storico sonno<select id={daysId} value={days} onChange={event => { setData(null); setSelected(''); setDays(Number(event.target.value)) }}><option value={14}>Ultimi 14 giorni</option><option value={30}>Ultimi 30 giorni</option><option value={90}>Ultimi 90 giorni</option></select></label>}
    </div>
    {demo ? <div className="wl-card wl-sleep-empty" role="status"><span className="wl-eyebrow">DEMO</span><h2>Nessun dato reale nella demo</h2><p>Esci dalla demo per consultare i periodi di sonno del tuo anello.</p></div> : <>
      {error && <div className="wl-banner wl-error" role="alert"><span>{error}{data && ' I periodi già ricevuti restano visibili.'}</span><button className="wl-button" onClick={() => setRetry(value => value + 1)}>Riprova</button></div>}
      {loading && !data && <div className="wl-card wl-sleep-empty" role="status"><h2>Caricamento del sonno…</h2><p>Recupero dei tuoi periodi dal server.</p></div>}
      {!loading && data && !period && <div className="wl-card wl-sleep-empty" role="status"><span className="wl-eyebrow">IN ATTESA DELLA PRIMA NOTTE</span><h2>Nessun periodo di sonno disponibile</h2><p>I periodi appariranno dopo la sincronizzazione e l’elaborazione dello storico dell’anello. Nessun sonno viene stimato dai soli movimenti.</p><small>{data.processing_status}</small></div>}
      {data?.processing_error && <div className="wl-banner wl-error" role="status">{data.processing_error}</div>}
      {period && <>
        <div className="wl-sleep-picker"><label htmlFor={selectId}>Periodo di sonno<select id={selectId} value={period.id} onChange={event => setSelected(event.target.value)}>{periods.map(item => <option key={item.id} value={item.id}>{periodLabel(item)}</option>)}</select></label><span>{periods.length} {periods.length === 1 ? 'periodo disponibile' : 'periodi disponibili'}</span></div>
        <section className="wl-card wl-sleep-overview" aria-label="Riepilogo del sonno"><div><span className="wl-eyebrow">{period.id === periods[0]?.id ? 'ULTIMO PERIODO' : 'DAL TUO STORICO'}</span><h2>{dateLabel(period.started_at)} · {timeLabel(period.started_at)}–{timeLabel(period.ended_at)}</h2><div className="wl-sleep-bedtimes"><div><span>Inizio letto</span><strong>{timeLabel(period.started_at)}</strong><small>{dateLabel(period.started_at)}</small></div><span aria-hidden="true">→</span><div><span>Fine letto</span><strong>{timeLabel(period.ended_at)}</strong><small>{dateLabel(period.ended_at)}</small></div></div><p className="wl-note">Orari di Roma · {period.source === 'oura-ring-bedtime' ? 'periodo a letto registrato dal firmware; fasi da elaborare' : 'dati storici dell’anello'}</p></div>
          <div className="wl-sleep-totals"><div><span>Tempo a letto</span><strong>{sleepDuration(period.time_in_bed_seconds)}</strong></div><div><span>Sonno totale</span><strong>{sleepDuration(period.total_sleep_seconds)}</strong>{period.total_sleep_seconds == null && <small>Riepilogo non disponibile</small>}</div></div>
        </section>
        <section className="wl-sleep-stages" aria-label="Durate delle fasi">{STAGES.map(stage => <div className="wl-card" key={stage.key}><span><i style={{ background: stage.color }} />{stage.label}</span><strong>{sleepDuration(period.stage_seconds?.[stage.key])}</strong><small>Durata nel riepilogo del periodo</small></div>)}</section>
        <section className="wl-card" aria-label="SpO₂ durante il sonno"><h2>SpO₂ · {period.source === 'oura-ring-bedtime' ? 'media nel periodo a letto' : 'media sonno'}</h2>
          <strong>{period.spo2?.display_percentage == null ? '—' : `${period.spo2.display_percentage}%`}</strong>
          {period.spo2?.sample_count ? <p>{period.spo2.sample_count} campioni · minimo {period.spo2.minimum}% · massimo {period.spo2.maximum}%</p> : <p>Nessun campione SpO₂ elaborato in questo periodo.</p>}
          <p className="wl-note">Media dei campioni nel periodo di sonno. La data si riferisce al sonno selezionato.</p>
        </section>
        <SleepPhases key={period.id} period={period} />
        <div className="wl-sleep-foot"><span>Latenza <strong>{sleepDuration(period.latency_seconds)}</strong></span><span>Efficienza <strong>{period.efficiency == null ? '—' : `${valueLabel(period.efficiency)}%`}</strong></span><span>{data?.last_processed_at != null ? `Ultima elaborazione ${dateLabel(data.last_processed_at)} · ${timeLabel(data.last_processed_at)}` : 'Elaborazione in attesa'}</span></div>
      </>}
    </>}
  </div>
})
