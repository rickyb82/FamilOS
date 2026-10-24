import SafeBrowser from './pages/SafeBrowser'
import Settings from './pages/Settings'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Login from './pages/Login'
import KidLogin from './pages/KidLogin'
import Enroll from './pages/Enroll'
import Reset from './pages/Reset'
import Shell from './components/Shell'
import Chat from './pages/Chat'
import Projects from './pages/Projects'

/** Authentication and enrollment. Authenticated adults and children share the desktop. */
export default function LegacyApp() {
  const { me } = useAuth()
  const location = useLocation()
  if (!me) return <Routes><Route path="/login" element={<Login />} /><Route path="/kids" element={<KidLogin />} /><Route path="/forgot" element={<Reset />} /><Route path="/reset" element={<Reset />} /><Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} /></Routes>
  if (me.mfa_required) return <Routes><Route path="/reset" element={<Reset />} /><Route path="/enroll" element={<Enroll />} /><Route path="*" element={<Navigate to="/enroll" replace />} /></Routes>
  return <Shell><Routes><Route path="/" element={<Chat />} /><Route path="/c/:id" element={<Chat />} /><Route path="/browser" element={<SafeBrowser />} /><Route path="/settings" element={<Settings />} /><Route path="/projects" element={<Projects />} /><Route path="/projects/:id" element={<Projects />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes></Shell>
}
