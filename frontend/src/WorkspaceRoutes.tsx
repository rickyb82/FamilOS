const Games = lazy(() => import("./pages/Games"))
const InstalledApp = lazy(() => import("./pages/InstalledApp"))
const SafeBrowser = lazy(() => import('./pages/SafeBrowser'))
const ParentalControls = lazy(() => import('./pages/ParentalControls'))
import { lazy, Suspense } from 'react'
import { Route, Routes, useSearchParams } from 'react-router-dom'
import { useAuth } from './lib/auth'
const Chat = lazy(() => import('./pages/Chat'))
const Projects = lazy(() => import('./pages/Projects'))
const AdminInstallation = lazy(() => import('./pages/AdminInstallation'))
const Admin = lazy(() => import('./pages/Admin'))
const Settings = lazy(() => import('./pages/Settings'))
const Documents = lazy(() => import('./pages/Documents'))
const Editor = lazy(() => import('./pages/Editor'))
const Mail = lazy(() => import('./pages/Mail'))
const Messages = lazy(() => import('./pages/Messages'))
const Productivity = lazy(() => import('./pages/Productivity'))
function EditorRoute() {
  const [params] = useSearchParams()
  return <Editor key={params.get('file') || ''} />
}
export function WorkspaceRoutes() {
  const { me } = useAuth()
  if (!me) return null
  return (
      <Suspense fallback={<div className="center muted">Apertura app…</div>}><Routes>
        <Route path="/" element={<Chat />} />
        <Route path="/c/:id" element={<Chat />} />
        <Route path="/games" element={<Games />} />
        <Route path="/browser" element={<SafeBrowser />} />
        {me.user.role !== "child" && <Route path="/parental" element={<ParentalControls />} />}
        <Route path="/installed/:appId" element={<InstalledApp />} />
        <Route path="/apps" element={<Productivity />} />
        <Route path="/productivity/:appId" element={<Productivity />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:id" element={<Projects />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/editor" element={<EditorRoute />} />
        {me.user.role !== 'child' && <Route path="/mail" element={<Mail />} />}
        {me.features?.messaggi && <Route path="/messages" element={<Messages />} />}
        {me.user.role === 'admin' && <Route path="/admin/*" element={<Admin />} />}
        {me.user.role === 'admin' && <Route path="/installation" element={<AdminInstallation />} />}
        {/* dopo l'accesso: la pagina da cui si era partiti (link di una notifica…), sul telefono la home delle app */}

        <Route path="*" element={<div className="center muted" role="status">Questa app non è disponibile per il tuo profilo. Puoi chiudere la finestra.</div>} />
      </Routes></Suspense>
  )
}
