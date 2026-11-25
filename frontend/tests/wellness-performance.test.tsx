// @vitest-environment jsdom
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import Wellness from '../src/pages/Wellness'
import { LinePlot } from '../src/pages/WellnessCharts'
import * as model from '../src/lib/wellness'

vi.mock('../src/lib/auth', () => ({ useAuth: () => ({ me: { user: { id: 'synthetic-adult', role: 'adult', display_name: 'Ada' } } }) }))
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

it('bounds dense plotting and preserves real extrema, timestamps and the full export', () => {
  // More points than Math.min(...values) can safely accept on common engines.
  const points = Array.from({ length: 200000 }, (_, i) => ({ t: i, v: 90 + i % 7 }))
  points[50321].v = 210; points[50322].v = 40
  const chart = model.chartEnvelope(points, 0, 199999)
  expect(chart.points.length).toBeLessThanOrEqual(640)
  expect(chart.min).toBe(40); expect(chart.max).toBe(210)
  expect(chart.points).toContain(points[50321]); expect(chart.points).toContain(points[50322])
  expect(chart.points[0]).toBe(points[0]); expect(chart.points.at(-1)).toBe(points.at(-1))
  expect(chart.segments).toHaveLength(1) // Decimation is not a signal dropout.
  expect(chart.points.every(p => points[p.t] === p)).toBe(true)
  expect(points).toHaveLength(200000)
  const analysis = { ...model.demoAnalysis(200000), heart_rate: points }
  expect(model.analysisCsv(analysis).split('\n').some(row => row.split(',')[1] === 'heart_rate' && row.split(',')[2] === '210')).toBe(true)
  const { container } = render(<LinePlot points={points} start={0} end={199999} color="green" kind="heart" label="Synthetic dense signal" />)
  expect(container.querySelectorAll('circle').length).toBeLessThanOrEqual(640)
  expect(container.querySelectorAll('polyline')).toHaveLength(1)
})

it('never connects over original gaps or warning boundaries, including gaps within one bucket', () => {
  const points = [{ t: 0, v: 90 }, { t: 1, v: 91 }, { t: 12, v: 200 }, { t: 13, v: 201 },
    { t: 14, v: 50, warnings: ['contact'] }, { t: 15, v: 70 }, { t: 16, v: 71 }]
  const chart = model.chartEnvelope(points, 0, 100, 1)
  for (const segment of chart.segments) {
    for (let i = 1; i < segment.length; i++) {
      const first = points.indexOf(segment[i - 1]), last = points.indexOf(segment[i])
      const original = model.chartSegments(points.slice(first, last + 1))
      expect(original).toHaveLength(1)
    }
  }
  expect(chart.points).toContain(points[3]); expect(chart.points).toContain(points[4])
  expect(chart.segments.every(segment => segment.length === 1)).toBe(true)
  const unordered = [...points].reverse()
  expect(model.chartEnvelope(unordered, 0, 100, 1)).toEqual(chart)
  expect(unordered[0]).toBe(points[6])
})

it('keeps archived chart calculations out of the one-second live refresh', async () => {
  vi.useFakeTimers()
  const analysis = model.demoAnalysis(Date.now() / 1000)
  vi.spyOn(model.wellness, 'analysis').mockResolvedValue(analysis)
  vi.spyOn(model.wellness, 'state').mockImplementation(async () => model.demoState(Date.now() / 1000))
  const envelope = vi.spyOn(model, 'chartEnvelope')
  render(<Wellness />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  const initial = envelope.mock.calls.length
  expect(initial).toBeGreaterThan(0)
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(model.wellness.state).toHaveBeenCalledTimes(4)
  expect(envelope).toHaveBeenCalledTimes(initial)
})

it('fetches history only on the dashboard and resumes live requests after visibility returns', async () => {
  vi.useFakeTimers()
  const analysis = vi.spyOn(model.wellness, 'analysis').mockResolvedValue(model.demoAnalysis(Date.now() / 1000))
  const state = vi.spyOn(model.wellness, 'state').mockImplementation(async () => model.demoState(Date.now() / 1000))
  render(<Wellness />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(analysis).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('tab', { name: 'Profilo' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(analysis).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('tab', { name: 'Panoramica' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(analysis).toHaveBeenCalledTimes(2)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  fireEvent(document, new Event('visibilitychange'))
  const stateCount = state.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(state).toHaveBeenCalledTimes(stateCount)
  expect(analysis).toHaveBeenCalledTimes(2)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(state).toHaveBeenCalledTimes(stateCount + 1)
  expect(analysis).toHaveBeenCalledTimes(3)
})
