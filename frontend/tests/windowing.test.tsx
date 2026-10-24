// @vitest-environment jsdom
import React, { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useLocation, useNavigate } from 'react-router-dom'
import Desktop from '../src/components/Desktop'
import { restoreWindows } from '../src/lib/desktop'

vi.mock('../src/lib/auth', () => ({ useAuth: () => ({ me: {
  user: { id: 'family-user', display_name: 'Anna', role: 'adult', avatar: 'A' },
  family_name: 'Famiglia', features: { apps: ['gridcraft', 'wordcraft'] },
}, logout: vi.fn() }) }))
vi.mock('../src/lib/api', () => ({ get: vi.fn(async () => []) }))
vi.mock('../src/lib/events', () => ({ useLiveEvents: vi.fn() }))
vi.mock('../src/lib/native', () => ({ wireNative: vi.fn() }))
vi.mock('../src/components/Notifications', () => ({ default: () => null }))
vi.mock('../src/components/Approvals', () => ({ default: () => null }))
vi.mock('../src/pages/Guide', () => ({ default: () => null }))
vi.mock('../src/WorkspaceRoutes', () => ({ WorkspaceRoutes: () => {
  const location = useLocation()
  const navigate = useNavigate()
  return <div><p>{location.pathname}</p><input aria-label="Bozza della finestra" />
    <button onClick={() => navigate('/editor?file=new-grid&name=foglio.xlsx&app=gridcraft&new=1')}>Nuovo foglio di prova</button>
    <button onClick={() => navigate('/editor?file=saved-id&name=prova.docx')}>Apri documento di prova</button></div>
} }))

const dock = () => within(screen.getByRole('navigation', { name: 'Dock delle applicazioni' }))
const app = (name: string) => dock().getByRole('button', { name, exact: true })
const region = (name: string) => screen.getByRole('region', { name: `${name}, finestra` })

beforeEach(() => {
  localStorage.clear()
  window.history.replaceState(null, '', '/app')
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
})
afterEach(cleanup)

describe('window lifecycle', () => {
  it('lands on the desktop after the child PIN login instead of opening a kids window', async () => {
    window.history.replaceState(null, '', '/kids')
    render(<Desktop />)
    await act(async () => {})
    expect(window.location.pathname).toBe('/app')
    expect(screen.queryAllByRole('region', { name: /finestra$/ })).toHaveLength(0)
  })
  it('preserves a draft through app switches, Home and minimization', async () => {
    render(<Desktop />)
    await act(async () => {})
    fireEvent.click(app('Emily'))
    const input = within(region('Emily')).getByLabelText('Bozza della finestra') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Una bozza da conservare' } })
    fireEvent.click(app('File'))
    fireEvent.click(screen.getByRole('button', { name: 'Home FamilOS' }))
    fireEvent.click(app('Emily'))
    expect(within(region('Emily')).getByLabelText('Bozza della finestra')).toBe(input)
    expect(input.value).toBe('Una bozza da conservare')
    fireEvent.click(within(region('Emily')).getByRole('button', { name: 'Riduci Emily' }))
    fireEvent.click(app('Emily'))
    expect(input.value).toBe('Una bozza da conservare')
  })

  it('opens an editor without replacing File or its current state', async () => {
    render(<Desktop />)
    await act(async () => {})
    fireEvent.click(app('File'))
    const input = within(region('File')).getByLabelText('Bozza della finestra') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'cartella corrente' } })
    fireEvent.click(within(region('File')).getByText('Apri documento di prova'))
    expect(region('WordCraft')).toBeTruthy()
    expect(within(region('File')).getByLabelText('Bozza della finestra')).toBe(input)
    expect(input.value).toBe('cartella corrente')
  })

  it('shows one app on a phone while keeping other app drafts mounted', async () => {
    render(<Desktop />)
    await act(async () => {})
    fireEvent.click(app('File'))
    const input = within(region('File')).getByLabelText('Bozza della finestra')
    fireEvent.click(app('Emily'))
    act(() => { window.innerWidth = 390; window.dispatchEvent(new Event('resize')) })
    expect(input.closest('section')!.style.display).toBe('none')
    fireEvent.click(app('File'))
    expect(input.closest('section')!.style.display).not.toBe('none')
    expect(within(region('File')).getByLabelText('Bozza della finestra')).toBe(input)
  })

  it('opens a new GridCraft document in the same app window with one dock icon', async () => {
    render(<Desktop />)
    await act(async () => {})
    fireEvent.click(app('Applicazioni'))
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Applicazioni' })).getByRole('button', { name: 'GridCraft', exact: true }))
    const initial = region('GridCraft')
    fireEvent.click(within(initial).getByText('Nuovo foglio di prova'))
    expect(screen.getAllByRole('region', { name: 'GridCraft, finestra' })).toHaveLength(1)
    expect(region('GridCraft')).toBe(initial)
    expect(within(initial).getByText('/editor')).toBeTruthy()
    expect(dock().getAllByRole('button', { name: 'GridCraft', exact: true })).toHaveLength(1)
  })

  it('keeps apps off the desktop, opens the launcher, and closes it from outside or Escape', async () => {
    render(<Desktop />)
    await act(async () => {})
    expect(screen.queryByText('Le tue app')).toBeNull()
    fireEvent.click(app('Applicazioni'))
    const launcher = screen.getByRole('dialog', { name: 'Applicazioni' })
    expect(within(launcher).getByRole('button', { name: 'GridCraft' })).toBeTruthy()
    expect(within(launcher).queryByRole('button', { name: 'Installazione' })).toBeNull()
    fireEvent.click(screen.getByTestId('launchpad-backdrop'))
    expect(screen.queryByRole('dialog', { name: 'Applicazioni' })).toBeNull()
    fireEvent.click(app('Applicazioni'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Applicazioni' })).toBeNull()
  })

  it('does not open duplicate deep links during React StrictMode startup', async () => {
    window.history.replaceState(null, '', '/documents')
    render(<StrictMode><Desktop /></StrictMode>)
    await act(async () => {})
    expect(screen.getAllByRole('region', { name: 'File, finestra' })).toHaveLength(1)
  })
})

it('rejects corrupt layouts and does not restore an unsaved editor', () => {
  const base = { id: '1', path: '/documents', x: 20, y: 20, width: 600, height: 400, mode: 'float', minimized: false }
  localStorage.setItem('layout', JSON.stringify([base, { ...base, id: '2', path: '//other-host' },
    { ...base, id: '3', width: 'broken' }, { ...base, id: '4', path: '/editor?new=1&file=unsaved' }]))
  expect(restoreWindows('layout')).toEqual([base])
  localStorage.setItem('layout', '{broken')
  expect(restoreWindows('layout')).toEqual([])
})
