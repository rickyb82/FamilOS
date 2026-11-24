import { describe, expect, it } from 'vitest'
import {
  activePhase, analysisCsv, chartSegments, defaultConfig, demoAnalysis, demoState, emptyProfile,
  energyValue, epochSeconds, heartQuality, profileComposition, timeLabel, weeklyLabel,
} from '../src/lib/wellness'

describe('wellness acquisition time and personal data', () => {
  it('shows Rome time across summer time without reinterpreting wall clock dates', () => {
    expect(timeLabel(Date.parse('2026-01-15T12:30:00Z') / 1000)).toBe('13:30')
    expect(timeLabel(Date.parse('2026-07-15T12:30:00Z') / 1000)).toBe('14:30')
    expect(epochSeconds('2026-11-24T13:30:00')).toBeNull()
    expect(epochSeconds('2026-11-24T13:30:00+01:00')).toBe(Date.parse('2026-11-24T12:30:00Z') / 1000)
    expect(epochSeconds(NaN)).toBeNull()
  })
  it('labels ISO weekdays and preserves user calendar times', () => {
    const slot = { id: 'a', weekday: 1, start: '13:30', end: '14:15', timezone: 'Europe/Rome', label: 'Palestra' }
    expect(weeklyLabel(slot)).toBe('Lunedì · 13:30–14:15')
    expect(weeklyLabel({ ...slot, weekday: 3, start: '14:45', end: '15:30' })).toBe('Mercoledì · 14:45–15:30')
    expect(weeklyLabel({ ...slot, weekday: 5, start: '08:45', end: '09:30' })).toBe('Venerdì · 08:45–09:30')
  })
  it('does not join a signal across a dropout or questionable contact', () => {
    const segments = chartSegments([{ t: 100, v: 90 }, { t: 102, v: 91 }, { t: 120, v: 92 }, { t: 121, v: 94, warnings: ['contact'] }, { t: 122, v: 95 }])
    expect(segments.map(s => s.map(p => p.t))).toEqual([[100, 102], [120], [121], [122]])
  })
  it('exports original UTC acquisition instants even when historical data arrive late', () => {
    const analysis = demoAnalysis(1800000000)
    analysis.heart_rate = [{ t: 1700000000, v: 80 }]
    analysis.spo2 = [{ t: 1700000010, v: 97 }]
    const csv = analysisCsv(analysis)
    expect(csv).toContain('2023-11-14T22:13:20.000Z,heart_rate,80')
    expect(csv).toContain('2023-11-14T22:13:30.000Z,spo2,97')
  })
  it('exports historical breathing only when supplied and keeps outside-display heart data separate', () => {
    const analysis = demoAnalysis(1800000000)
    expect(analysisCsv(analysis)).not.toContain(',respiratory_rate,')
    analysis.respiratory_rate = [{ t: 1700000020, v: 15.5 }]
    analysis.heart_rate_outside_display = [{ t: 1700000030, v: 600 }]
    expect(analysisCsv(analysis)).toContain('2023-11-14T22:13:40.000Z,respiratory_rate,15.5')
    expect(analysisCsv(analysis)).not.toContain(',heart_rate_outside_display,')
    expect(analysisCsv(analysis)).not.toContain(',heart_rate,600\n')
  })
  it('requires fresh valid contact before presenting a live heart reading', () => {
    const heart = { bpm: 120, captured_at: 100, source: 'gen3', ibi_validity_raw: 1 }
    expect(heartQuality(heart, 105).reliable).toBe(true)
    expect(heartQuality(heart, 109).reliable).toBe(false)
    expect(heartQuality({ ...heart, flags_raw: 2 }, 105).reliable).toBe(false)
    expect(heartQuality({ ...heart, ibi_validity_raw: 0 }, 105).reliable).toBe(false)
    expect(heartQuality({ ...heart, warnings: ['contact'] }, 105).reliable).toBe(false)
    expect(heartQuality({ ...heart, bpm: 251 }, 105).reliable).toBe(false)
  })
  it('preserves incompatible declared composition while withholding derived lean mass and BMR', () => {
    const composition = profileComposition({ ...emptyProfile(), weight_kg: 100, body_fat_pct: 33, reported_lean_pct: 60, reported_retention_pct: 7 })
    expect(composition.conflict).toBe(true)
    expect(composition.fatKg).toBeCloseTo(33)
    expect(composition.reportedLeanKg).toBeCloseTo(60)
    expect(composition.leanKg).toBeNull()
    expect(composition.basalKcal).toBeNull()
  })
  it('uses no calorie value when no interval has a valid estimate', () => {
    expect(energyValue({ net_kcal: 0, gross_kcal: 0, estimated_seconds: 0 }, 'net_kcal')).toBeNull()
    expect(energyValue({ net_kcal: 70, gross_kcal: 84, estimated_seconds: 600 }, 'net_kcal')).toBe(70)
    expect(energyValue({ net_kcal: 0 }, 'net_kcal')).toBeNull()
  })
  it('has no automatic phases or age-derived cardiac targets without a user plan', () => {
    expect(activePhase(defaultConfig(), 40)).toBeNull()
    const config = { ...defaultConfig(), rounds: 2, plan: [{ name: 'A', duration: 30, target_min: 110, target_max: 130, met: 4 }, { name: 'B', duration: 10, target_min: null, target_max: null, met: null }] }
    expect(activePhase(config, 30)).toMatchObject({ name: 'B', round: 1, remaining: 10 })
    expect(activePhase(config, 40)).toMatchObject({ name: 'A', round: 2, target_min: 110, remaining: 30 })
    expect(activePhase(config, 80)).toBeNull()
  })
  it('keeps synthetic demo independent of personal profiles, paired devices and calendar', () => {
    const first = demoState(1000), second = demoState(1000)
    first.settings.profile.weight_kg = 200
    expect(second.settings.profile).toEqual(emptyProfile())
    expect(second.devices).toEqual([])
    expect(second.settings.weekly_schedule).toEqual([])
    expect(second.latest.heart_rate?.source).toBe('demo')
    expect(second.active_session?.stats.net_kcal).toBeNull()
  })
})
