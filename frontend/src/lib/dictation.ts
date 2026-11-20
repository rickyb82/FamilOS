import { useEffect, useRef, useState } from 'react'

interface Recognition {
  lang: string
  continuous: boolean
  processLocally?: boolean
  interimResults: boolean
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition
  webkitSpeechRecognition?: new () => Recognition
}

export function useDictation(scope: string, enabled: boolean, localOnly: boolean, append: (value: string) => void) {
  const current = useRef<Recognition | null>(null)
  const [listening, setListening] = useState(false)
  const [notice, setNotice] = useState('')
  useEffect(() => {
    setListening(false)
    setNotice('')
    return () => {
      const recognition = current.current
      current.current = null
      recognition?.abort()
    }
  }, [scope, enabled, localOnly])

  function toggle() {
    if (!enabled) return
    if (current.current) {
      current.current.stop()
      return
    }
    const browser = window as SpeechWindow
    const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition
    const fallback = 'Puoi usare la dettatura della tastiera nel campo del messaggio.'
    if (!window.isSecureContext || !Constructor) {
      setNotice('Dettatura del browser non disponibile. ' + fallback)
      return
    }
    const recognition = new Constructor()
    if (localOnly) {
      if (!('processLocally' in recognition)) {
        setNotice('Questo profilo richiede la dettatura locale. Usa una tastiera con dettatura offline.')
        return
      }
      recognition.processLocally = true
    }
    recognition.lang = 'it-IT'
    recognition.continuous = true
    recognition.interimResults = false
    recognition.onresult = event => {
      if (current.current !== recognition) return
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        if (result.isFinal && result[0].transcript.trim()) append(result[0].transcript.trim())
      }
    }
    recognition.onerror = event => {
      if (current.current !== recognition) return
      const messages: Record<string, string> = {
        'not-allowed': 'Consenti al browser di usare il microfono per dettare.',
        'audio-capture': 'Microfono non disponibile.',
        'no-speech': 'Non ho rilevato parole. Puoi riprovare.',
        'language-not-supported': 'Dettatura italiana non disponibile su questo dispositivo.',
      }
      setNotice(messages[event.error] || 'Dettatura interrotta. ' + fallback)
    }
    recognition.onend = () => {
      if (current.current !== recognition) return
      current.current = null
      setListening(false)
    }
    current.current = recognition
    try {
      recognition.start()
      setNotice('')
      setListening(true)
    } catch {
      current.current = null
      setNotice('Avvio del microfono non riuscito. ' + fallback)
    }
  }
  return { listening, notice, toggle }
}
