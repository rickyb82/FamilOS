import { del, get, post, put } from './api'

export const WELLNESS_API = '/wellness/v1'
export const WELLNESS_TIMEZONE = 'Europe/Rome'
export const WEEKDAYS = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']
export type WellnessDate = number | string
export type Profile = {
  resting_hr?: number | null; weight_kg: number | null; height_cm: number | null; age: number | null; body_fat_pct: number | null
  reported_lean_pct: number | null; reported_retention_pct: number | null; composition_source: string | null; goal: string | null
}
export type Phase = { name: string; duration: number; target_min: number | null; target_max: number | null; met: number | null }
export type WorkoutConfig = { work: number; rest: number; rounds: number; hrmax: number | null; plan: Phase[]; activity_name: string | null; activity_id?: number | null }
export type WeeklySlot = { id: string; weekday: number; start: string; end: string; timezone: string; label: string }
export type AutoDetection = {
  enabled: boolean; gym: { id: string; name: string; address: string; latitude?: number | null; longitude?: number | null; radius_m: number }
  heart_rate_delta_bpm: number; sustain_seconds: number; cooldown_seconds: number
}
export type WellnessSettings = { profile: Profile; config: WorkoutConfig; weekly_schedule: WeeklySlot[]; auto_detection: AutoDetection }
export type SessionStats = { samples?: number; estimated_seconds?: number; unestimated_seconds?: number; average_bpm?: number | null; max_bpm?: number | null; net_kcal?: number | null; gross_kcal?: number | null }
export type WellnessSession = {
  id: string; activity_name: string; started_at: WellnessDate; ended_at?: WellnessDate | null; active_seconds: number
  status: string; auto_detected?: boolean; provisional?: boolean; stats: SessionStats; config?: WorkoutConfig
}
export type BridgeDevice = { id: string; name: string; last_seen_at?: WellnessDate | null; revoked?: boolean; queue?: number | Record<string, unknown> }
export type WellnessState = {
  server_time: WellnessDate; settings: WellnessSettings
  latest: {
    heart_rate?: { bpm: number; captured_at: WellnessDate; source: string; warnings?: string[]; flags_raw?: number; ibi_validity_raw?: number } | null
    heart_rate_outside_display?: { value: number; captured_at: WellnessDate; utc_ms?: number } | null
    respiratory_rate?: { value: number; utc_ms: number } | null
    spo2?: { value: number; utc_ms: number; quality?: number | null } | null
    temperature?: { value: number; utc_ms: number; sensor_index?: number | null } | null
    motion?: { value: number; utc_ms: number } | null; steps?: { value: number; utc_ms: number } | null
    exercise?: Record<string, unknown> | null
  }
  devices: BridgeDevice[]; sessions: WellnessSession[]
  weekly_occurrences: Array<WeeklySlot & { date: string; starts_at: WellnessDate; ends_at: WellnessDate }>
  archive: { events: number; history_records: number; decode_errors: number; processing_status: string }
  active_session?: WellnessSession | null
}
export type TimePoint = { t: number; v: number; warnings?: string[]; flags_raw?: number }
export type WellnessAnalysis = {
  window_start: number; window_end: number; workout_end?: number | null; heart_rate: TimePoint[]
  respiratory_rate?: TimePoint[]; heart_rate_outside_display?: TimePoint[]; spo2: TimePoint[]; temperature: TimePoint[]; motion: TimePoint[]; step_increments: TimePoint[]
  steps_in_records?: number | null; quality_events?: unknown[]; activity_name?: string; stats?: SessionStats
}
export type Pairing = { pairing_code: string; expires_at: WellnessDate; server_url: string; enrollment_uri: string; qr_png_data_url?: string | null }

export const wellness = {
  state: () => get<WellnessState>(`${WELLNESS_API}/state`),
  analysis: (sessionId?: string) => get<WellnessAnalysis>(`${WELLNESS_API}/analysis${sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''}`),
  settings: (settings: Partial<WellnessSettings>) => put<WellnessState>(`${WELLNESS_API}/settings`, settings),
  session: (action: 'start' | 'pause' | 'resume' | 'end' | 'activity', values: Record<string, unknown> = {}) => post<WellnessState>(`${WELLNESS_API}/sessions`, { action, ...values }),
  pair: (device_name?: string) => post<Pairing>(`${WELLNESS_API}/bridges/pair`, device_name ? { device_name } : {}),
  revoke: (id: string) => del(`${WELLNESS_API}/bridges/${encodeURIComponent(id)}`),
  bindRing: (owner_key_hex: string, logical_ring_id: string) => post(`${WELLNESS_API}/ring-binding`, { owner_key_hex, logical_ring_id }),
}

export function epochSeconds(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : null
  if (typeof value !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return null
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms / 1000 : null
}
export function timeLabel(epoch: number | null | undefined, seconds = false): string {
  if (epoch == null || !Number.isFinite(epoch)) return '—'
  return new Intl.DateTimeFormat('it-IT', { timeZone: WELLNESS_TIMEZONE, hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) }).format(new Date(epoch * 1000))
}
export function dateLabel(value: WellnessDate | null | undefined): string {
  const epoch = epochSeconds(value)
  return epoch == null ? 'Data non disponibile' : new Intl.DateTimeFormat('it-IT', { timeZone: WELLNESS_TIMEZONE, day: 'numeric', month: 'short' }).format(new Date(epoch * 1000))
}
export function ageLabel(value: WellnessDate | null | undefined, now: number): string {
  const epoch = epochSeconds(value)
  if (epoch == null) return 'Mai collegato'
  const age = Math.max(0, now - epoch)
  if (age < 60) return `${Math.floor(age)} s fa`
  if (age < 3600) return `${Math.floor(age / 60)} min fa`
  return `${dateLabel(value)} · ${timeLabel(epoch)}`
}
export function durationLabel(seconds: number): string {
  const total = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0))
  return `${Math.floor(total / 60).toString().padStart(2, '0')}:${(total % 60).toString().padStart(2, '0')}`
}
export function valueLabel(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('it-IT', { maximumFractionDigits: digits })
}
/** Unknown coverage is deliberately unestimated, even if the API aggregate is zero. */
export function energyValue(stats: SessionStats | null | undefined, kind: 'net_kcal' | 'gross_kcal'): number | null {
  return stats != null && (stats.estimated_seconds || 0) > 0 ? stats[kind] ?? null : null
}
export function weeklyLabel(slot: WeeklySlot): string {
  return `${WEEKDAYS[slot.weekday - 1] || 'Giorno non valido'} · ${slot.start}–${slot.end}`
}
export function emptyProfile(): Profile {
  return { resting_hr: null, weight_kg: null, height_cm: null, age: null, body_fat_pct: null, reported_lean_pct: null, reported_retention_pct: null, composition_source: null, goal: null }
}
export function defaultConfig(): WorkoutConfig {
  return { work: 40, rest: 20, rounds: 1, hrmax: null, plan: [], activity_name: 'Allenamento' }
}
export function defaultDetection(): AutoDetection {
  return { enabled: false, gym: { id: '', name: '', address: '', radius_m: 150 }, heart_rate_delta_bpm: 15, sustain_seconds: 30, cooldown_seconds: 3600 }
}
export function profileComposition(profile: Profile) {
  const weight = profile.weight_kg, fat = profile.body_fat_pct, lean = profile.reported_lean_pct
  const conflict = fat != null && lean != null && Math.abs(fat + lean - 100) > 1
  const fatKg = weight != null && fat != null ? weight * fat / 100 : null
  const leanKg = weight != null && fatKg != null && !conflict ? weight - fatKg : null
  return { conflict, fatKg, leanKg, reportedLeanKg: weight != null && lean != null ? weight * lean / 100 : null, basalKcal: leanKg == null ? null : 500 + 22 * leanKg }
}
export function activePhase(config: WorkoutConfig, elapsed: number) {
  const plan = config.plan || []
  if (!plan.length) return null
  const cycle = plan.reduce((sum, phase) => sum + phase.duration, 0)
  if (!(cycle > 0) || elapsed >= cycle * config.rounds) return null
  let position = Math.max(0, elapsed) % cycle
  for (let index = 0; index < plan.length; index++) {
    const phase = plan[index]
    if (position < phase.duration) return { ...phase, index, round: Math.floor(Math.max(0, elapsed) / cycle) + 1, remaining: Math.ceil(phase.duration - position), total: cycle * config.rounds }
    position -= phase.duration
  }
  return null
}
export function heartQuality(heart: WellnessState['latest']['heart_rate'], now: number) {
  const captured = epochSeconds(heart?.captured_at)
  const valid = heart != null && Number.isFinite(heart.bpm) && heart.bpm >= 20 && heart.bpm <= 250 && captured != null
  const fresh = valid && now - captured! <= 8 && now - captured! >= -2
  const flags = heart?.flags_raw
  const flagged = flags != null && !!(flags & (2 | 4 | 8 | 16 | 32))
  const invalidIbi = heart?.ibi_validity_raw != null && heart.ibi_validity_raw !== 1
  return { fresh: !!fresh, reliable: !!fresh && !flagged && !invalidIbi && !(heart?.warnings?.length), captured, warnings: heart?.warnings || [] }
}
export function chartSegments(values: TimePoint[], maxGap = 8): TimePoint[][] {
  const sorted = [...values].filter(p => Number.isFinite(p.t) && Number.isFinite(p.v)).sort((a, b) => a.t - b.t)
  const segments: TimePoint[][] = []
  for (const point of sorted) {
    const last = segments[segments.length - 1]
    if (!last || point.t - last[last.length - 1].t > maxGap || point.warnings?.length || last[last.length - 1].warnings?.length) segments.push([point])
    else last.push(point)
  }
  return segments
}
export function analysisCsv(analysis: WellnessAnalysis): string {
  const lines = ['timestamp_utc,serie,valore']
  const rows = (['heart_rate', 'spo2', 'temperature', 'motion', 'step_increments', 'respiratory_rate'] as const)
    .flatMap(key => (analysis[key] || []).filter(point => Number.isFinite(point.t) && Number.isFinite(point.v)).map(point => ({ key, ...point })))
    .sort((a, b) => a.t - b.t)
  for (const row of rows) lines.push(`${new Date(row.t * 1000).toISOString()},${row.key},${row.v}`)
  return lines.join('\n') + '\n'
}

/** Frontend-only demo. No real state/profile/device data enters this fixture. */
export function demoState(now: number): WellnessState {
  return {
    server_time: now,
    settings: { profile: emptyProfile(), config: { ...defaultConfig(), activity_name: 'Circuito di esempio', rounds: 4, plan: [{ name: 'Fase dimostrativa', duration: 60, target_min: null, target_max: null, met: null }, { name: 'Recupero di esempio', duration: 30, target_min: null, target_max: null, met: null }] }, weekly_schedule: [], auto_detection: defaultDetection() },
    latest: { heart_rate: { bpm: Math.round(128 + 11 * Math.sin(now / 16)), captured_at: now, source: 'demo', warnings: [] }, spo2: { value: 97, utc_ms: (now - 140) * 1000, quality: 1 }, temperature: { value: 34.8, utc_ms: (now - 160) * 1000, sensor_index: 1 }, motion: { value: 19, utc_ms: (now - 30) * 1000 }, steps: { value: 24, utc_ms: (now - 60) * 1000 } },
    devices: [], sessions: [], weekly_occurrences: [], archive: { events: 0, history_records: 0, decode_errors: 0, processing_status: 'demo' },
    active_session: { id: 'demo-only', activity_name: 'Circuito di esempio', started_at: now - 120, active_seconds: 120, status: 'active', stats: { average_bpm: 127, max_bpm: 143, gross_kcal: null, net_kcal: null } },
  }
}
export function demoAnalysis(now: number): WellnessAnalysis {
  const start = now - 300
  return { window_start: start, window_end: now, workout_end: null, activity_name: 'Dati sintetici',
    heart_rate: Array.from({ length: 151 }, (_, index) => ({ t: start + index * 2, v: 121 + 14 * Math.sin(index / 18) + 4 * Math.sin(index / 4) })),
    spo2: [30, 130, 230].map((offset, index) => ({ t: start + offset, v: 97 + (index % 2) })),
    temperature: [20, 100, 180, 260].map(offset => ({ t: start + offset, v: 34.6 + offset / 1000 })),
    motion: [10, 40, 70, 100, 130, 160, 190, 220, 250, 280].map((offset, index) => ({ t: start + offset, v: 12 + (index % 4) * 5 })),
    step_increments: [], steps_in_records: null, quality_events: [] }
}
