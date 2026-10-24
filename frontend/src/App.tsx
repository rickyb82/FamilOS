import { lazy, Suspense } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Desktop from './components/Desktop'

const LegacyApp = lazy(() => import('./LegacyApp'))

export default function App() {
  const { me, loading } = useAuth()
  if (loading) return <div className="center muted">Caricamento FamilOS…</div>
  if (me && !me.mfa_required && me.user.role !== 'child') return <Desktop key={me.user.id} />
  return <Suspense fallback={<div className="center muted">Caricamento…</div>}><BrowserRouter><LegacyApp /></BrowserRouter></Suspense>
}
