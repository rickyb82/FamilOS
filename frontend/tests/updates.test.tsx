// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdminUpdates from '../src/pages/AdminUpdates'
import { get, post } from '../src/lib/api'

vi.mock('../src/lib/api', () => ({ get: vi.fn(), post: vi.fn() }))
afterEach(cleanup)
beforeEach(() => vi.resetAllMocks())

it('requires both confirmations before installing an experimental release', async () => {
  vi.mocked(get).mockResolvedValue({ connected: true, channel: 'experimental', version: '1.0.0', offers: { core: { id: 'a'.repeat(32), tag: 'v1.1.0-rc.1', channel: 'experimental', experimental: true, newer: true, notes: '<script>untrusted()</script>' } } })
  vi.mocked(post).mockResolvedValue({ status: 'running' })
  render(<AdminUpdates />)
  const install = await screen.findByRole('button', { name: 'Installa release v1.1.0-rc.1' }) as HTMLButtonElement
  expect(install.disabled).toBe(true)
  fireEvent.click(screen.getByLabelText('Confermo l’interruzione temporanea del servizio. I backup sono gestiti manualmente.'))
  expect(install.disabled).toBe(true)
  fireEvent.click(screen.getByLabelText('Accetto di installare questa release sperimentale.'))
  expect(install.disabled).toBe(false)
  expect(document.querySelector('script')).toBeNull()
  fireEvent.click(install)
  await waitFor(() => expect(post).toHaveBeenCalledWith('/admin/updates/install', expect.objectContaining({ channel: 'experimental', accept_experimental: true, confirm_maintenance: true })))
})

it('invalidates the displayed offer after changing channels', async () => {
  vi.mocked(get).mockResolvedValue({ connected: true, channel: 'experimental', offers: { core: { id: 'a'.repeat(32), tag: 'v1.1.0-rc.1', channel: 'experimental', experimental: true, newer: true, notes: '' } } })
  render(<AdminUpdates />)
  await screen.findByRole('button', { name: 'Installa release v1.1.0-rc.1' })
  fireEvent.change(screen.getByLabelText('Canale aggiornamenti'), { target: { value: 'stable' } })
  expect(screen.queryByRole('button', { name: 'Installa release v1.1.0-rc.1' })).toBeNull()
  expect((screen.getByRole('button', { name: 'Installa release' }) as HTMLButtonElement).disabled).toBe(true)
  expect(post).not.toHaveBeenCalled()
})

it('does not offer installation when the host agent is absent', async () => {
  vi.mocked(get).mockResolvedValue({ connected: false, channel: 'stable', setup_command: './familos.sh updater install' })
  render(<AdminUpdates />)
  await screen.findByText('./familos.sh updater install')
  expect((screen.getByRole('button', { name: 'Controlla release' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByLabelText('Canale aggiornamenti') as HTMLSelectElement).value).toBe('stable')
})
