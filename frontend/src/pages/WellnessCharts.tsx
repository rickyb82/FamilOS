import { memo, useMemo, useState } from 'react'
import { chartEnvelope, dateLabel, timeLabel, valueLabel, type TimePoint, type WellnessAnalysis } from '../lib/wellness'

export const EMPTY_POINTS: TimePoint[] = []

/** Bounds affect presentation only; original samples and gaps stay unchanged. */
export function plotScale(min: number, max: number, kind: 'heart' | 'dots' | 'bars', unit = '') {
  const spread = Math.max(kind === 'heart' ? 20 : 1, max - min)
  let bottom = kind === 'bars' ? 0 : min - spread * .1
  let top = max + spread * .1
  if (min >= 0) bottom = Math.max(0, bottom)
  if (unit === '%' && min >= 0 && max <= 100) top = Math.min(100, top)
  const target = Math.max(.01, (top - bottom) / 3)
  const power = 10 ** Math.floor(Math.log10(target))
  const factor = [1, 2, 5, 10].find(value => value * power >= target) ?? 10
  const step = Math.max(kind === 'heart' || kind === 'bars' || unit === '%' ? 1 : .01, factor * power)
  const clean = (value: number) => Number(value.toPrecision(12))
  const low = clean(Math.floor(bottom / step) * step)
  let high = clean(Math.ceil(top / step) * step)
  if (unit === '%' && max <= 100) high = Math.min(100, high)
  if (high <= low) high = clean(low + step)
  const ticks: number[] = []
  for (let value = high; value >= low - step * .001; value -= step) ticks.push(clean(value))
  if (ticks[ticks.length - 1] !== low) ticks.push(low)
  return { low, high, ticks }
}
const fullDate = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'short', year: 'numeric' })
const rangeDate = (t: number) => fullDate.format(new Date(t * 1000))
function TimeAxis({ start, end }: { start: number; end: number }) {
  return <div className={`wl-time-axis${end - start >= 86400 ? ' wl-time-axis-long' : ''}`} aria-label="Orari di acquisizione, Roma">{Array.from({ length: 5 }, (_, index) => {
    const t = start + (end - start) * index / 4
    return <span key={index} style={{ left: `${index / 4 * 100}%` }}>{end - start >= 86400 && <small>{dateLabel(t)}</small>}{timeLabel(t)}</span>
  })}</div>
}
export const MiniHeart = memo(function MiniHeart({ points, now, historical = false }: { points: TimePoint[]; now: number; historical?: boolean }) {
  const start = now - 300
  return <div className="wl-mini-heart"><div className="wl-chart-window"><strong>{historical ? 'Ultimi 5 minuti del periodo selezionato' : 'Ultimi 5 minuti'}</strong><span>{rangeDate(start)} · {timeLabel(start)} → {rangeDate(now)} · {timeLabel(now)} · Roma</span></div><LinePlot points={points} start={start} end={now} color="#d6ed98" kind="heart" unit="BPM" label="Battito negli ultimi cinque minuti" /><TimeAxis start={start} end={now} /></div>
})
export const LinePlot = memo(function LinePlot({ points, start, end, color, kind, label, unit = '', markerAt }: { points: TimePoint[]; start: number; end: number; color: string; kind: 'heart' | 'dots' | 'bars'; label: string; unit?: string; markerAt?: number | null }) {
  const { points: valid, segments, min, max } = useMemo(() => chartEnvelope(points, start, end), [points, start, end])
  const { low, high, ticks } = plotScale(min, max, kind, unit)
  const [detail, setDetail] = useState<TimePoint | null>(null)
  const x = (t: number) => Math.min(1, Math.max(0, (t - start) / Math.max(1, end - start))) * 900
  const y = (v: number) => 96 - (v - low) / Math.max(.01, high - low) * 80
  const digits = kind === 'heart' || kind === 'bars' ? 0 : 1
  const pointLabel = (point: TimePoint) => `${dateLabel(point.t)} · ${timeLabel(point.t, true)} · ${valueLabel(point.v, digits)} ${unit}`
  const current = detail && valid.includes(detail) ? detail : null
  const axis = (side: 'left' | 'right') => <div className={`wl-y-axis wl-y-axis-${side}`} aria-label={`Scala ${side === 'left' ? 'sinistra' : 'destra'} · ${label}${unit ? ` in ${unit}` : ''}`}>
    {!!valid.length && ticks.map(tick => <span key={tick} style={{ top: `${y(tick) / 112 * 100}%` }}>{valueLabel(tick, Number.isInteger(tick) ? 0 : 2)}</span>)}
  </div>
  return <div className="wl-plot">
    {axis('left')}
    <div className="wl-plot-surface" onPointerLeave={() => setDetail(null)}>
      <svg viewBox="0 0 900 112" preserveAspectRatio="none" role="img" aria-label={`${label}${unit ? ` · ${unit}` : ''}`}>
        <g stroke="currentColor" className="wl-grid-lines">{ticks.map(tick => <path d={`M0 ${y(tick)}H900`} key={tick} />)}{[0, 225, 450, 675, 900].map(at => <path d={`M${at} 0V112`} key={at} />)}</g>
        {kind === 'heart' && segments.map((segment, index) => segment.length > 1 && <polyline key={index} fill="none" stroke={color} strokeWidth="2.4" vectorEffect="non-scaling-stroke" points={segment.map(point => `${x(point.t)},${y(point.v)}`).join(' ')} />)}
        {valid.map((point, index) => kind === 'bars' ? <path key={index} d={`M${x(point.t)} ${y(0)}V${y(point.v)}`} stroke={color} strokeWidth="4" vectorEffect="non-scaling-stroke" onPointerEnter={() => setDetail(point)} onClick={() => setDetail(point)}><title>{pointLabel(point)}</title></path> : <circle key={index} cx={x(point.t)} cy={y(point.v)} r={kind === 'dots' ? 3.4 : 2} fill={point.warnings?.length ? '#f0aa81' : color} onPointerEnter={() => setDetail(point)} onClick={() => setDetail(point)}><title>{pointLabel(point)}</title></circle>)}
      </svg>
      {!valid.length && <span className="wl-plot-empty">Nessuna rilevazione in questo periodo</span>}
      {current && <output className="wl-point-detail">{pointLabel(current)}</output>}
      {markerAt != null && markerAt > start && markerAt < end && <span className="wl-workout-end" style={{ left: `${(markerAt - start) / (end - start) * 100}%` }} title={`Fine allenamento ${timeLabel(markerAt)}`} />}
    </div>
    {axis('right')}
  </div>
})
export const SharedTimeline = memo(function SharedTimeline({ analysis }: { analysis: WellnessAnalysis | null }) {
  const now = Date.now() / 1000, start = analysis?.window_start ?? now - 3600, end = Math.max(start + 1, analysis?.window_end ?? now)
  const series = [{ key: 'heart_rate', label: 'Battito', unit: 'BPM', color: '#d6ed98', kind: 'heart' }, { key: 'spo2', label: 'SpO₂', unit: '%', color: '#85d4cc', kind: 'dots' }, { key: 'temperature', label: 'Temperatura', unit: '°C', color: '#eda58c', kind: 'dots' }, { key: 'motion', label: 'Movimento', unit: 's / record', color: '#b5a9e6', kind: 'bars' }, { key: 'step_increments', label: 'Passi', unit: 'passi / record', color: '#c6d8a0', kind: 'bars' }, { key: 'respiratory_rate', label: 'Respiro', unit: 'resp/min', color: '#a3d4eb', kind: 'dots' }] as const
  return <div className="wl-shared-chart"><div className="wl-chart-window"><strong>Periodo visualizzato</strong><span>{rangeDate(start)} · {timeLabel(start)} → {rangeDate(end)} · {timeLabel(end)} · Roma</span></div>{series.map(row => <div className="wl-series-row" key={row.key}><div className="wl-series-label"><span style={{ background: row.color }} /><strong>{row.label}</strong><small>{row.unit}</small></div><div className="wl-series-plot"><LinePlot points={analysis?.[row.key] || EMPTY_POINTS} start={start} end={end} color={row.color} kind={row.kind} unit={row.unit} label={`${row.label}, istanti di acquisizione`} markerAt={analysis?.workout_end} /></div></div>)}<div className="wl-shared-axis"><span /><TimeAxis start={start} end={end} /></div><p className="wl-scale-note">Ogni serie ha la propria scala verticale, adattata ai dati disponibili. Orari di Roma; passa sui punti o toccali per leggere valore e unità.</p></div>
})
