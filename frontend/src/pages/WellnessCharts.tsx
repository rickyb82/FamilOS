import { memo, useMemo } from 'react'
import { chartEnvelope, timeLabel, valueLabel, type TimePoint, type WellnessAnalysis } from '../lib/wellness'

export const EMPTY_POINTS: TimePoint[] = []

export const MiniHeart = memo(function MiniHeart({ points, now }: { points: TimePoint[]; now: number }) {
  return <LinePlot points={points} start={now - 300} end={now} color="#d6ed98" kind="heart" label="Battito negli ultimi cinque minuti" />
})
export const LinePlot = memo(function LinePlot({ points, start, end, color, kind, label }: { points: TimePoint[]; start: number; end: number; color: string; kind: 'heart' | 'dots' | 'bars'; label: string }) {
  const { points: valid, segments, min, max } = useMemo(() => chartEnvelope(points, start, end), [points, start, end])
  const spread = Math.max(kind === 'heart' ? 20 : 1, max - min), low = kind === 'bars' ? 0 : min - spread * .18, high = max + spread * .18
  const x = (t: number) => 8 + Math.min(1, Math.max(0, (t - start) / Math.max(1, end - start))) * 872
  const y = (v: number) => 76 - (v - low) / Math.max(.01, high - low) * 64
  return <div className="wl-plot"><svg viewBox="0 0 900 88" preserveAspectRatio="none" role="img" aria-label={label}><g stroke="currentColor" className="wl-grid-lines">{[15, 46, 77].map(at => <path d={`M0 ${at}H900`} key={at} />)}{[0, 225, 450, 675, 900].map(at => <path d={`M${at} 0V88`} key={at} />)}</g>{kind === 'heart' && segments.map((segment, index) => segment.length > 1 && <polyline key={index} fill="none" stroke={color} strokeWidth="2.4" vectorEffect="non-scaling-stroke" points={segment.map(point => `${x(point.t)},${y(point.v)}`).join(' ')} />)}{valid.map((point, index) => kind === 'bars' ? <path key={index} d={`M${x(point.t)} 80V${y(point.v)}`} stroke={color} strokeWidth="4" vectorEffect="non-scaling-stroke"><title>{timeLabel(point.t, true)} · {valueLabel(point.v, 1)}</title></path> : <circle key={index} cx={x(point.t)} cy={y(point.v)} r={kind === 'dots' ? 3.4 : 2} fill={point.warnings?.length ? '#f0aa81' : color}><title>{timeLabel(point.t, true)} · {valueLabel(point.v, 1)}</title></circle>)}</svg>{!valid.length && <span className="wl-plot-empty">Nessuna rilevazione in questo periodo</span>}</div>
})
export const SharedTimeline = memo(function SharedTimeline({ analysis }: { analysis: WellnessAnalysis | null }) {
  const now = Date.now() / 1000, start = analysis?.window_start ?? now - 3600, end = Math.max(start + 1, analysis?.window_end ?? now)
  const series = [{ key: 'heart_rate', label: 'Battito', unit: 'bpm', color: '#d6ed98', kind: 'heart' }, { key: 'spo2', label: 'SpO₂', unit: '%', color: '#85d4cc', kind: 'dots' }, { key: 'temperature', label: 'Temperatura', unit: '°C', color: '#eda58c', kind: 'dots' }, { key: 'motion', label: 'Movimento', unit: 's / record', color: '#b5a9e6', kind: 'bars' }, { key: 'respiratory_rate', label: 'Respiro', unit: 'resp/min', color: '#a3d4eb', kind: 'dots' }] as const
  return <div className="wl-shared-chart">{series.map(row => <div className="wl-series-row" key={row.key}><div className="wl-series-label"><span style={{ background: row.color }} /><strong>{row.label}</strong><small>{row.unit}</small></div><div className="wl-series-plot"><LinePlot points={analysis?.[row.key] || EMPTY_POINTS} start={start} end={end} color={row.color} kind={row.kind} label={`${row.label}, istanti di acquisizione`} />{analysis?.workout_end != null && analysis.workout_end > start && analysis.workout_end < end && <span className="wl-workout-end" style={{ left: `${(analysis.workout_end - start) / (end - start) * 100}%` }} title={`Fine allenamento ${timeLabel(analysis.workout_end)}`} />}</div></div>)}<div className="wl-shared-axis"><span /><div>{Array.from({ length: 5 }, (_, index) => <span key={index}>{timeLabel(start + (end - start) * index / 4)}</span>)}</div></div></div>
})
