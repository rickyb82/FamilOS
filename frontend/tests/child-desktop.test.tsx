// @vitest-environment jsdom
import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import App from '../src/App'
import { desktopApps } from '../src/lib/desktop'
import type { Me } from '../src/lib/api'

vi.mock('../src/lib/auth', () => ({ useAuth: () => ({ loading: false, me: { user: { id: 'kid', role: 'child' }, mfa_required: false } }) }))
vi.mock('../src/components/Desktop', () => ({ default: () => <div>Desktop a finestre</div> }))
afterEach(cleanup)

it('uses the shared desktop for an authenticated child', () => {
  render(<App />)
  expect(screen.getByText('Desktop a finestre')).toBeTruthy()
})

it('shows child apps and settings without parental or admin controls', () => {
  const me = { user: { role: 'child' }, features: { apps: ['wordcraft', 'gridcraft', 'pdfcraft', 'deckcraft', 'ideacraft'] } } as Me
  const ids = desktopApps(me).map(app => app.id)
  for (const id of ['files', 'projects', 'settings', 'browser', ...me.features!.apps!]) expect(ids).toContain(id)
  for (const id of ['parental', 'family', 'installation', 'mailcraft']) expect(ids).not.toContain(id)
})
