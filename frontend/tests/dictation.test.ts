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

it('replaces provisional words and commits each confirmed segment once', () => {
  const speech = fakeBrowser()
  const append = vi.fn()
  const { result } = renderHook(() => useDictation('chat', true, false, append))
  act(() => result.current.toggle())
  const segment = (transcript: string, isFinal = false) => ({ isFinal, 0: { transcript } })
  const emit = (...results: ReturnType<typeof segment>[]) =>
    act(() => speech().onresult?.({ resultIndex: 0, results }))
  emit(segment('buon'))
  expect(result.current.interim).toBe('buon')
  emit(segment('buongiorno'), segment('Emily'))
  expect(result.current.interim).toBe('buongiorno Emily')
  expect(append).not.toHaveBeenCalled()
  emit(segment('buongiorno', true), segment('Emily'))
  expect(result.current.interim).toBe('Emily')
  emit(segment('buongiorno', true), segment('Emily', true))
  expect(result.current.interim).toBe('')
  expect(append.mock.calls).toEqual([['buongiorno'], ['Emily']])
})
