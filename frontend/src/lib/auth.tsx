import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { get, post, type Me } from './api'

interface AuthCtx {
  me: Me | null
  loading: boolean
  refresh: () => Promise<Me | null>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthCtx>({ me: null, loading: true, refresh: async () => null, logout: async () => {} })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const m = await get<Me>('/auth/me')
      setMe(m)
      return m
    } catch {
      setMe(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const lock = () => setMe(current => current ? { ...current, session_locked: true } : current)
    const sync = (event: StorageEvent) => { if (event.key === 'familos:session') void refresh() }
    window.addEventListener('storage',sync)
    window.addEventListener('familos:locked', lock)
    return () => { window.removeEventListener('familos:locked', lock); window.removeEventListener('storage',sync) }
  }, [])

  const logout = useCallback(async () => {
    try {
      await post('/auth/logout')
    } finally {
      setMe(null)
    }
  }, [])

  return <Ctx.Provider value={{ me, loading, refresh, logout }}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
