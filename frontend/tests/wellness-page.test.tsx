// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import Wellness from '../src/pages/Wellness'
import { desktopApps } from '../src/lib/desktop'
import { demoAnalysis, demoState, wellness } from '../src/lib/wellness'
import type { Me } from '../src/lib/api'

const auth = vi.hoisted(() => ({ role: 'adult' }))
vi.mock('../src/lib/auth', () => ({ useAuth: () => ({ me: { user: { id: 'own-user', role: auth.role, display_name: 'Ada' } } }) }))

function fixture() {
  const state = demoState(Date.now() / 1000)
  state.settings.profile.weight_kg = 84
  state.settings.profile.goal = 'Obiettivo personale riservato'
  state.latest.heart_rate!.source = 'gen3'
  state.active_session = null
  state.sessions = []
  return state
}

beforeEach(() => {
  auth.role = 'adult'
  vi.spyOn(wellness, 'state').mockImplementation(async () => fixture())
  vi.spyOn(wellness, 'analysis').mockImplementation(async () => demoAnalysis(Date.now() / 1000))
  vi.spyOn(wellness, 'session').mockImplementation(async () => fixture())
  vi.spyOn(wellness, 'settings').mockImplementation(async () => fixture())
  vi.spyOn(wellness, 'pair').mockResolvedValue({ pairing_code: 'JOIN-4321', expires_at: Date.now() / 1000 + 300, server_url: 'https://familos.example', enrollment_uri: 'familos-wellness://enroll?code=JOIN-4321', qr_png_data_url: 'data:image/png;base64,aGVsbG8=' })
  vi.spyOn(wellness, 'revoke').mockResolvedValue(undefined)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('integrates the app for adults and blocks child rendering and requests', async () => {
  expect(desktopApps({ user: { role: 'adult' }, features: {wellness:true} } as Me).some(app => app.id === 'wellness')).toBe(true)
  expect(desktopApps({ user: { role: 'child' } } as Me).some(app => app.id === 'wellness')).toBe(false)
  auth.role = 'child'
  render(<Wellness />)
  expect(screen.getByText('L’app Allenamento è disponibile nei profili adulti.')).toBeTruthy()
  expect(wellness.state).not.toHaveBeenCalled()
  expect(wellness.analysis).not.toHaveBeenCalled()
})

it('shows server-owned profile data and does not seed another user calendar', async () => {
  render(<Wellness />)
  await waitFor(() => expect(wellness.state).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('tab', { name: 'Profilo' }))
  expect(await screen.findByLabelText('Peso · kg')).toHaveProperty('value', '84')
  expect(screen.getByLabelText('Il tuo obiettivo')).toHaveProperty('value', 'Obiettivo personale riservato')
  fireEvent.click(screen.getByRole('tab', { name: 'Calendario' }))
  expect(screen.queryAllByLabelText('Dalle')).toHaveLength(0)
  expect(screen.getByLabelText('Latitudine · opzionale')).toHaveProperty('value', '')
})

it('keeps demo profiles blank and blocks mutations and pairing', async () => {
  render(<Wellness />)
  await screen.findByText('Ada, ritrova il tuo ritmo.')
  fireEvent.click(screen.getByRole('button', { name: 'Esplora demo' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Profilo' }))
  expect(screen.getByLabelText('Peso · kg')).toHaveProperty('value', '')
  expect(screen.getByRole('button', { name: 'Salva profilo' })).toHaveProperty('disabled', true)
  fireEvent.click(screen.getByRole('tab', { name: 'iPhone e anello' }))
  expect(screen.getByRole('button', { name: 'Collega iPhone' })).toHaveProperty('disabled', true)
  expect(wellness.settings).not.toHaveBeenCalled()
  expect(wellness.session).not.toHaveBeenCalled()
  expect(wellness.pair).not.toHaveBeenCalled()
})

it('renders backend PNG QR and keeps the one-time pairing code out of the server URL', async () => {
  render(<Wellness />)
  fireEvent.click(screen.getByRole('tab', { name: 'iPhone e anello' }))
  fireEvent.click(screen.getByRole('button', { name: 'Collega iPhone' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('JOIN-4321')).toBeTruthy()
  expect(within(dialog).getByRole('img').getAttribute('src')).toBe('data:image/png;base64,aGVsbG8=')
  expect(within(dialog).getByLabelText('Indirizzo server FamilOS')).toHaveProperty('value', 'https://familos.example')
  expect(dialog.querySelector('a')).toBeNull()
  expect(localStorage.length).toBe(0)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Ho finito' }))
  expect(screen.queryByText('JOIN-4321')).toBeNull()
  expect(wellness.pair).toHaveBeenCalledTimes(1)
})

it('hides an expired pairing QR and disables copying its code', async () => {
  vi.mocked(wellness.pair).mockResolvedValue({ pairing_code: 'OLD-4321', expires_at: Date.now() / 1000 - 1, server_url: 'https://familos.example', enrollment_uri: '', qr_png_data_url: 'data:image/png;base64,aGVsbG8=' })
  render(<Wellness />)
  fireEvent.click(screen.getByRole('tab', { name: 'iPhone e anello' }))
  fireEvent.click(screen.getByRole('button', { name: 'Collega iPhone' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('SCADUTO')).toBeTruthy()
  expect(within(dialog).queryByRole('img')).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Copia codice' })).toHaveProperty('disabled', true)
})

it('marks calorie aggregates unavailable without estimated duration and shows steps as an increment', async () => {
  const state = fixture()
  state.active_session = { id: 'active', activity_name: 'Libero', started_at: Date.now() / 1000 - 60, active_seconds: 60, status: 'active', stats: { gross_kcal: 0, net_kcal: 0, estimated_seconds: 0, unestimated_seconds: 60 } }
  vi.mocked(wellness.state).mockResolvedValue(state)
  render(<Wellness />)
  await screen.findByText(/Nessun intervallo stimabile per ora/)
  const card = screen.getByText('Energia della sessione').closest('section')!
  expect([...card.querySelectorAll('.wl-energy strong')].map(node => node.textContent)).toEqual(['—', '—'])
  expect(screen.getByText('Incremento nell’ultimo record')).toBeTruthy()
})

it('suppresses a recent but invalid contact reading and preserves the historical plot', async () => {
  const state = fixture()
  state.latest.heart_rate = { bpm: 160, captured_at: Date.now() / 1000, source: 'gen3', flags_raw: 2, warnings: ['contact'] }
  vi.mocked(wellness.state).mockResolvedValue(state)
  const { container } = render(<Wellness />)
  await screen.findByText('Il segnale richiede una verifica')
  expect(container.querySelector('.wl-heart-value strong')?.textContent).toBe('—')
  expect(screen.getByRole('img', { name: 'Battito, istanti di acquisizione' })).toBeTruthy()
})

it('can resume or finish a paused session even when active_session is null', async () => {
  const state = fixture()
  state.sessions = [{ id: 'paused-session', activity_name: 'Circuito', started_at: Date.now() / 1000 - 100, active_seconds: 30, status: 'paused', stats: {} }]
  vi.mocked(wellness.state).mockResolvedValue(state)
  render(<Wellness />)
  const resume = await screen.findByRole('button', { name: 'Riprendi' })
  expect(screen.getByRole('button', { name: 'Termina' })).toBeTruthy()
  fireEvent.click(resume)
  await waitFor(() => expect(wellness.session).toHaveBeenCalledWith('resume', expect.objectContaining({ session_id: 'paused-session' })))
})

it('confirms an existing provisional session through activity instead of starting it twice', async () => {
  const state = fixture()
  state.active_session = { id: 'provisional-session', activity_name: 'Allenamento', started_at: Date.now() / 1000 - 20, active_seconds: 20, status: 'active', provisional: true, stats: {} }
  vi.mocked(wellness.state).mockResolvedValue(state)
  render(<Wellness />)
  fireEvent.click(await screen.findByRole('button', { name: 'Conferma e inizia' }))
  await waitFor(() => expect(wellness.session).toHaveBeenCalledWith('activity', expect.objectContaining({ session_id: 'provisional-session' })))
})

it('keeps the active session phase on its captured plan when settings have since changed', async () => {
  const state = fixture()
  state.settings.config.plan = [{ name: 'Piano futuro', duration: 90, target_min: null, target_max: null, met: null }]
  state.active_session = { id: 'captured-plan', activity_name: 'Circuito', started_at: Date.now() / 1000 - 20, active_seconds: 20, status: 'active', stats: {}, config: { ...state.settings.config, rounds: 1, plan: [{ name: 'Fase della sessione', duration: 60, target_min: 100, target_max: 120, met: 4 }] } }
  vi.mocked(wellness.state).mockResolvedValue(state)
  render(<Wellness />)
  expect(await screen.findByText('Fase della sessione')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'La mia scheda' }))
  expect(screen.getByLabelText('Fase o esercizio')).toHaveProperty('value', 'Piano futuro')
})

it('leaves breathing empty without its own historical records even with a live heart signal', async () => {
  render(<Wellness />)
  expect(await screen.findByText('Nessun record del respiro disponibile')).toBeTruthy()
  const card = screen.getByText('Frequenza respiratoria').closest('.wl-sensor')!
  expect(card.querySelector('strong')?.textContent?.trim()).toBe('—')
  const chart = screen.getByRole('img', { name: 'Respiro, istanti di acquisizione' })
  expect(chart.querySelectorAll('circle')).toHaveLength(0)
})

it('plots breathing from its source UTC timestamp and shows its historical rate', async () => {
  const now = Date.now() / 1000, state = fixture(), analysis = demoAnalysis(now)
  state.latest.respiratory_rate = { value: 15.5, utc_ms: (now - 100) * 1000 }
  analysis.respiratory_rate = [{ t: now - 100, v: 15.5 }]
  vi.mocked(wellness.state).mockResolvedValue(state)
  vi.mocked(wellness.analysis).mockResolvedValue(analysis)
  render(<Wellness />)
  await waitFor(() => expect(screen.getByText('Frequenza respiratoria').closest('.wl-sensor')?.textContent).toContain('15,5'))
  const chart = screen.getByRole('img', { name: 'Respiro, istanti di acquisizione' })
  expect(chart.querySelectorAll('circle')).toHaveLength(1)
  expect(Number(chart.querySelector('circle')?.getAttribute('cx'))).toBeCloseTo(8 + 872 * 2 / 3)
})

it('signals a newer outside-display heart reading without presenting its value as the primary BPM', async () => {
  const now = Date.now() / 1000, state = fixture()
  state.latest.heart_rate = { bpm: 90, captured_at: now - 2, source: 'gen3' }
  state.latest.heart_rate_outside_display = { value: 600, captured_at: now - 1 }
  vi.mocked(wellness.state).mockResolvedValue(state)
  const { container } = render(<Wellness />)
  await screen.findByText(/Ultima lettura fuori dall’intervallo di visualizzazione/)
  expect(container.querySelector('.wl-heart-value strong')?.textContent).toBe('—')
  expect(screen.queryByText('600')).toBeNull()
})

it('restores the primary heart reading when it is newer than the separately preserved value', async () => {
  const now = Date.now() / 1000, state = fixture()
  state.latest.heart_rate = { bpm: 90, captured_at: now - 1, source: 'gen3' }
  state.latest.heart_rate_outside_display = { value: 600, captured_at: now - 2 }
  vi.mocked(wellness.state).mockResolvedValue(state)
  const { container } = render(<Wellness />)
  await waitFor(() => expect(container.querySelector('.wl-heart-value strong')?.textContent).toBe('90'))
  expect(screen.queryByText(/Ultima lettura fuori dall’intervallo di visualizzazione/)).toBeNull()
})
