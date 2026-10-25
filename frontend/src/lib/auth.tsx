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
    const sync = (event: StorageEvent) => { if (event.key === 'familos:session') window.location.reload() }
    window.addEventListener('storage',sync)
    window.addEventListener('familos:locked', lock)
    return () => { window.removeEventListener('familos:locked', lock); window.removeEventListener('storage',sync) }
  }, [])

  const logout = useCallback(async () => {
    try {
      await post('/auth/logout');sessionStorage.removeItem('familos:acting-child')
    } finally {
      setMe(null)
    }
  }, [])

  const returnAdmin = async () => {try{await post('/auth/acting-child/stop');sessionStorage.removeItem('familos:acting-child');localStorage.setItem('familos:session',String(Date.now()));window.location.assign('/')}catch(e:any){alert(e.message)}}
  return <Ctx.Provider value={{ me, loading, refresh, logout }}>{(me?.acting_as_child || (!me && sessionStorage.getItem('familos:acting-child'))) && <div role="status" style={{position:'fixed',top:0,left:0,right:0,zIndex:2147483647,background:'#78350f',color:'white',padding:'8px 16px',display:'flex',alignItems:'center',justifyContent:'space-between',gap:12}}>Stai operando nel profilo bambino {me?.user.display_name || ''}. Le modifiche sono reali.<button onClick={returnAdmin}>Torna amministratore</button></div>}{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
