// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, it, expect, vi } from 'vitest'
import { useDictation } from '../src/lib/dictation'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function fakeBrowser() {
  let instance: Speech
  class Speech {
    start = vi.fn()
    stop = vi.fn()
    abort = vi.fn()
    onresult?: (e: object) => void
    constructor() { instance = this }
  }
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('SpeechRecognition', Speech)
  return () => instance
}

it('appends final text, then discards late results after switching conversation', () => {
  const speech = fakeBrowser()
  const append = vi.fn()
  const hook = renderHook(({ id }) => useDictation(id, true, false, append), { initialProps: { id: 'one' } })
  act(() => hook.result.current.toggle())
  const event = { resultIndex: 0, results: [{ isFinal: true, 0: { transcript: ' buongiorno ' } }] }
  act(() => speech().onresult?.(event))
  expect(append).toHaveBeenCalledWith('buongiorno')
  hook.rerender({ id: 'two' })
  expect(speech().abort).toHaveBeenCalledOnce()
  act(() => speech().onresult?.(event))
  expect(append).toHaveBeenCalledTimes(1)
})

it('requires local speech support for an account without cloud permission', () => {
  const speech = fakeBrowser()
  const { result } = renderHook(() => useDictation('kid', true, true, vi.fn()))
  act(() => result.current.toggle())
  expect(speech().start).not.toHaveBeenCalled()
  expect(result.current.notice).toContain('locale')
})
