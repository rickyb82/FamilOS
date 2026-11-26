// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import Wellness from '../src/pages/Wellness'
import SleepPanel, { sleepPhaseRuns } from '../src/pages/SleepPanel'
import { ApiError } from '../src/lib/api'
import { demoAnalysis, demoState, wellness, type SleepPeriod, type WellnessSleep } from '../src/lib/wellness'

const auth = vi.hoisted(() => ({ id: 'adult-a', role: 'adult', enabled: true, acting: false }))
vi.mock('../src/lib/auth', () => ({ useAuth: () => ({ me: { user: { id: auth.id, role: auth.role, display_name: 'Ada' }, features: { wellness: auth.enabled }, acting_as_child: auth.acting } }) }))
const start = Date.UTC(2026, 10, 20, 22) / 1000
function period(overrides: Partial<SleepPeriod> = {}): SleepPeriod {
  return { id: 'native-period-a', started_at: start, ended_at: start + 28800,
    time_zone_minutes: 60, time_in_bed_seconds: 28800, total_sleep_seconds: 27000,
    stage_seconds: { awake: 1800, rem: 5400, light: 12600, deep: 9000 },
    latency_seconds: 600, efficiency: 94, phases_30_seconds: '11223344', phases_5_minutes: '23',
    source: 'oura-ecore', native_version: [3, 6, 2], ...overrides }
}
function response(periods: SleepPeriod[] = [period()]): WellnessSleep {
  return { server_time: start + 86400, window_start: start - 13 * 86400, window_end: start + 86400,
    periods, processing_status: 'Storico elaborato', last_processed_at: start + 32400 }
}
beforeEach(() => {
  Object.assign(auth, { id: 'adult-a', role: 'adult', enabled: true, acting: false })
  vi.spyOn(wellness, 'state').mockImplementation(async () => ({ ...demoState(Date.now() / 1000), active_session: null }))
  vi.spyOn(wellness, 'analysis').mockImplementation(async () => demoAnalysis(Date.now() / 1000))
  vi.spyOn(wellness, 'sleep').mockResolvedValue(response())
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

it('opens Sonno lazily and keeps native summaries separate from partial phase coverage', async () => {
  render(<Wellness />)
  expect(wellness.sleep).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('tab', { name: 'Sonno' }))
  const summary = await screen.findByRole('region', { name: 'Riepilogo del sonno' })
  expect(wellness.sleep).toHaveBeenCalledWith(14)
  expect(within(summary).getByText('8 h')).toBeTruthy()
  expect(within(summary).getByText('7 h 30 min')).toBeTruthy()
  expect(within(summary).getByText('23:00')).toBeTruthy() // Source UTC22:00 in Europe/Rome.
  expect(within(summary).getByText('07:00')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Durate delle fasi' })).getByText('2 h 30 min')).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Fasi del sonno · sequenza relativa' })).toBeTruthy()
  expect(screen.getByText(/8 campioni da 30 secondi · 4 min di sequenza/)).toBeTruthy()
  expect(screen.getByText(/la sua durata può differire dal tempo a letto/)).toBeTruthy()
  expect(screen.queryByText(/punteggio/i)).toBeNull()
  expect(localStorage.length).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: '5 min' }))
  expect(screen.getByText(/2 campioni da 5 minuti · 10 min di sequenza/)).toBeTruthy()
})

it('shows loading and honest empty history without fabricated nights', async () => {
  let resolve!: (value: WellnessSleep) => void
  vi.mocked(wellness.sleep).mockImplementation(() => new Promise(done => { resolve = done }))
  render(<SleepPanel />)
  expect(screen.getByText('Caricamento del sonno…')).toBeTruthy()
  await act(async () => resolve(response([])))
  expect(screen.getByText('Nessun periodo di sonno disponibile')).toBeTruthy()
  expect(screen.queryByRole('img', { name: /Fasi del sonno/ })).toBeNull()
  expect(screen.queryByText('Tempo a letto')).toBeNull()
})

it('selects earlier periods, nullable summaries and the requested history window', async () => {
  const earlier = period({ id: 'earlier', started_at: start - 86400, ended_at: start - 57600,
    total_sleep_seconds: null, stage_seconds: null, latency_seconds: null, efficiency: null,
    phases_30_seconds: null, phases_5_minutes: null })
  vi.mocked(wellness.sleep).mockResolvedValue(response([earlier, period()]))
  render(<SleepPanel />)
  await screen.findByText('7 h 30 min')
  const picker = screen.getByLabelText('Periodo di sonno')
  expect(picker).toHaveProperty('value', 'native-period-a')
  fireEvent.change(picker, { target: { value: 'earlier' } })
  expect(screen.getByText('Riepilogo non disponibile')).toBeTruthy()
  expect(screen.getByText(/Le fasi di questo periodo non sono ancora disponibili/)).toBeTruthy()
  expect(screen.queryByText('7 h 30 min')).toBeNull()
  fireEvent.change(screen.getByLabelText('Storico sonno'), { target: { value: '30' } })
  await waitFor(() => expect(wellness.sleep).toHaveBeenCalledWith(30))
})

it('retains unknown literal positions as gaps and never infers stages for them', async () => {
  const runs = sleepPhaseRuns('10?2', 30)
  expect(runs.map(run => [run.stage?.key ?? null, run.start, run.duration])).toEqual([
    ['deep', 0, 30], [null, 30, 60], ['light', 90, 30],
  ])
  vi.mocked(wellness.sleep).mockResolvedValue(response([period({ phases_30_seconds: '10?2' })]))
  const { container } = render(<SleepPanel />)
  await screen.findByText(/1 min senza fase disponibile/)
  expect(container.querySelectorAll('.wl-sleep-gap')).toHaveLength(1)
  expect(screen.getByText(/4 campioni da 30 secondi · 2 min di sequenza/)).toBeTruthy()
  expect(screen.getByText('7 h 30 min')).toBeTruthy() // Summary remains native, not recomputed from4 samples.
})

it('keeps demo explicit and separate from previously displayed private sleep', async () => {
  render(<Wellness />)
  fireEvent.click(screen.getByRole('tab', { name: 'Sonno' }))
  await screen.findByText('7 h 30 min')
  const count = vi.mocked(wellness.sleep).mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: 'Esplora demo' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Sonno' }))
  expect(screen.getByText('Nessun dato reale nella demo')).toBeTruthy()
  expect(screen.queryByText('7 h 30 min')).toBeNull()
  expect(wellness.sleep).toHaveBeenCalledTimes(count)
})

it('clears prior-owner sleep immediately when the authenticated owner changes', async () => {
  const page = render(<Wellness />)
  fireEvent.click(screen.getByRole('tab', { name: 'Sonno' }))
  await screen.findByText('7 h 30 min')
  auth.id = 'adult-b'
  vi.mocked(wellness.sleep).mockResolvedValue(response([]))
  page.rerender(<Wellness />)
  expect(screen.queryByText('7 h 30 min')).toBeNull()
  await screen.findByText('Nessun periodo di sonno disponibile')
})

it('blocks child, acting-child and disabled-module requests', () => {
  for (const settings of [{ role: 'child' }, { acting: true }, { enabled: false }]) {
    Object.assign(auth, { role: 'adult', enabled: true, acting: false }, settings)
    const page = render(<Wellness />)
    expect(screen.queryByRole('tab', { name: 'Sonno' })).toBeNull()
    page.unmount()
  }
  expect(wellness.sleep).not.toHaveBeenCalled()
  expect(wellness.state).not.toHaveBeenCalled()
  expect(wellness.analysis).not.toHaveBeenCalled()
})

it('removes cached periods when permission expires and stops automatic sleep retries', async () => {
  vi.useFakeTimers()
  vi.mocked(wellness.sleep).mockResolvedValueOnce(response()).mockRejectedValue(new ApiError(403, 'disabled'))
  render(<SleepPanel />)
  await act(async () => { await Promise.resolve() })
  expect(screen.getByText('7 h 30 min')).toBeTruthy()
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.getByRole('alert').textContent).toContain('Sonno non disponibile per questo profilo.')
  expect(screen.queryByText('7 h 30 min')).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  expect(wellness.sleep).toHaveBeenCalledTimes(2)
})

it('handles unavailable routes and can retry a temporary error', async () => {
  vi.mocked(wellness.sleep).mockRejectedValueOnce(new ApiError(404, 'missing'))
  render(<SleepPanel />)
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Il sonno non è ancora disponibile su questo server.Riprova')
  vi.mocked(wellness.sleep).mockResolvedValue(response())
  fireEvent.click(screen.getByRole('button', { name: 'Riprova' }))
  expect(await screen.findByText('7 h 30 min')).toBeTruthy()
})


it('shows the selected sleep SpO2 aggregate, not the current pulse or an invented zero', async () => {
  const spo2 = {average:97.5,display_percentage:98,minimum:96,maximum:99,sample_count:2,catalog_count:3,quality_one_count:1,excluded_range_count:1,started_at:start,ended_at:start+28800,quality_prefilter_applied:false,bias_correction_applied:false,live:false} as const
  vi.mocked(wellness.sleep).mockResolvedValue(response([period({spo2})]))
  render(<SleepPanel />)
  const card=await screen.findByRole('region',{name:'SpO₂ durante il sonno'})
  expect(within(card).getByText('98%')).toBeTruthy()
  expect(within(card).getByText('2 campioni · minimo 96% · massimo 99%')).toBeTruthy()
})


it('distinguishes firmware bedtime windows from unavailable sleep stages', async () => {
  vi.spyOn(wellness, 'sleep').mockResolvedValue(response([period({source:'oura-ring-bedtime',total_sleep_seconds:null,stage_seconds:null,latency_seconds:null,efficiency:null,phases_30_seconds:null,phases_5_minutes:null})]))
  render(<SleepPanel />)
  await screen.findByText(/periodo a letto registrato dal firmware/)
  expect(screen.getByText('SpO₂ · media nel periodo a letto')).toBeTruthy()
  expect(screen.queryByRole('img', {name:'Fasi del sonno · sequenza relativa'})).toBeNull()
})


it('shows respiratory means only for the selected sleep period', async () => {
  vi.spyOn(wellness, 'sleep').mockResolvedValue(response([period({respiratory_rate:{sample_count:10,average:16.2,minimum:15,maximum:18,source:'oura-firmware-sleep-period-history',aggregation:'arithmetic-mean-of-record-means',is_live:false}})]))
  render(<SleepPanel />)
  await screen.findByText('16,2 resp/min')
  expect(screen.getByText(/10 campioni · minimo 15 · massimo 18 resp\/min/)).toBeTruthy()
})
