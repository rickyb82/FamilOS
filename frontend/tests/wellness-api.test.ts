// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { wellness } from '../src/lib/wellness'

afterEach(() => vi.unstubAllGlobals())

it('sends the owner key only in an authenticated anti-CSRF POST body', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetch)
  const key = 'ab'.repeat(16)
  await wellness.bindRing(key, 'logical-ring')
  const [url, options] = fetch.mock.calls[0]
  expect(url).toBe('/api/wellness/v1/ring-binding')
  expect(url).not.toContain(key)
  expect(options).toMatchObject({ method: 'POST', credentials: 'include', headers: { 'X-Requested-With': 'FamilAI', 'Content-Type': 'application/json' } })
  expect(JSON.parse(options.body)).toEqual({ owner_key_hex: key, logical_ring_id: 'logical-ring' })
  expect(localStorage.length).toBe(0)
})

it('requests one-time pairing and encodes identifiers when revoking or selecting history', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  await wellness.pair('Telefono di casa')
  expect(fetch.mock.calls[0][0]).toBe('/api/wellness/v1/bridges/pair')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ device_name: 'Telefono di casa' })
  await wellness.revoke('device/a?')
  expect(fetch.mock.calls[1][0]).toBe('/api/wellness/v1/bridges/device%2Fa%3F')
  expect(fetch.mock.calls[1][1].method).toBe('DELETE')
  await wellness.analysis('session&a=other')
  expect(fetch.mock.calls[2][0]).toBe('/api/wellness/v1/analysis?session_id=session%26a%3Dother')
})
