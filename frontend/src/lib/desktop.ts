import type { WindowMode } from "./windowLayout"
import { createContext, useContext } from 'react'
import type { Me } from './api'
import { APP_ICON, APP_LABEL, editorFor, type ArtApp } from './artcraft'

export const WindowActivity = createContext(true)
export const WindowCommands = createContext<{ close: () => void } | null>(null)
export const useWindowActive = () => useContext(WindowActivity)

export type AppEntry = { id: string; title: string; icon: string; iconUrl?:string; color: string; path: string; feature?: string; admin?: boolean; description?: string; appId?: string }
export const PRODUCTIVITY_APPS: AppEntry[] = [
  { id: 'wordcraft', appId: 'wordcraft', title: 'WordCraft', icon: 'W', color: 'blue', path: '/productivity/wordcraft', feature: 'wordcraft_web', description: 'Documenti, lettere e testi.' },
  { id: 'gridcraft', appId: 'gridcraft', title: 'GridCraft', icon: 'X', color: 'green', path: '/productivity/gridcraft', feature: 'gridcraft_web', description: 'Fogli di calcolo, tabelle e budget.' },
  { id: 'pdfcraft', appId: 'pdfcraft', title: 'PdfCraft', icon: 'PDF', color: 'peach', path: '/productivity/pdfcraft', feature: 'pdfcraft_web', description: 'Leggi, modifica e organizza i PDF.' },
  { id: 'deckcraft', appId: 'deckcraft', title: 'DeckCraft', icon: 'P', color: 'peach', path: '/productivity/deckcraft', feature: 'deckcraft_web', description: 'Presentazioni e storie da condividere.' },
  { id: 'ideacraft', appId: 'ideacraft', title: 'IdeaCraft', icon: '◎', color: 'lavender', path: '/productivity/ideacraft', feature: 'ideacraft_web', description: 'Mappe mentali, diagrammi e idee.' },
  { id: 'mailcraft', appId: 'mailcraft', title: 'MailCraft', icon: '✉', color: 'blue', path: '/productivity/mailcraft', feature: 'mailcraft_web', description: 'Le tue caselle di posta, in una finestra.' },
]
export const DESKTOP_APPS: AppEntry[] = [
  { id: 'chat', title: 'Emily', icon: '✳', color: 'lavender', path: '/' },
  { id: 'files', title: 'File', icon: '▰', color: 'blue', path: '/documents' },
  { id: 'projects', title: 'Progetti', icon: '◈', color: 'peach', path: '/projects' },
  { id: 'messages', title: 'Messaggi', icon: '☏', color: 'green', path: '/messages', feature: 'messaggi' },
  { id: 'browser', title: 'Browser', icon: '◎', color: 'blue', path: '/browser' },
  { id: 'parental', title: 'Genitori', icon: '♡', color: 'green', path: '/parental' },
  { id: 'family', title: 'Famiglia', icon: '♧', color: 'peach', path: '/admin', admin: true },
  { id: 'installation', title: 'Installazione', icon: '⚙', color: 'slate', path: '/installation', admin: true },
  { id: 'settings', title: 'Impostazioni', icon: '⚙', color: 'slate', path: '/settings' },
  { id: 'apps', title: 'Tutte le app', icon: '▦', color: 'lavender', path: '/apps' },
]

export function desktopApps(me: Me): AppEntry[] {
  const core = DESKTOP_APPS.filter(a => (!a.admin || me.user.role === 'admin') && (a.id !== 'parental' || me.user.role !== 'child') && (!a.feature || !!(me.features as any)?.[a.feature]))
  const productivity = PRODUCTIVITY_APPS.filter(a => me.user.role === 'admin' || me.features?.apps?.includes(a.appId!))
  return [...core.slice(0, 4), ...productivity, ...core.slice(4)]
}

export function appForPath(path: string): AppEntry {
  const pathname = path.split('?')[0]
  if (pathname.startsWith('/installed/')) return {id:'installed-'+pathname.split('/')[2],title:'App installata',icon:'▦',color:'lavender',path}
  if (pathname === '/editor') {
    const query = new URLSearchParams(path.split('?')[1])
    const kind = (query.get('app') || editorFor(query.get('name') || '')) as ArtApp | undefined
    return { id: 'editor', appId: kind, title: kind ? APP_LABEL[kind] || 'Documento' : 'Documento', icon: kind ? APP_ICON[kind] || '▤' : '▤', color: PRODUCTIVITY_APPS.find(a => a.id === kind)?.color || 'blue', path }
  }
  if (pathname === '/' || pathname.startsWith('/c/')) return DESKTOP_APPS[0]
  if (pathname === '/mail') return PRODUCTIVITY_APPS.find(a => a.id === 'mailcraft')!
  return [...DESKTOP_APPS, ...PRODUCTIVITY_APPS].find(a => a.path !== '/' && (pathname === a.path || pathname.startsWith(a.path + '/')))
    || { id: pathname.split('/')[1], title: 'App', icon: '▦', color: 'slate', path }
}

export function windowAppId(path: string): string {
  const app = appForPath(path)
  return app.appId || app.id
}

export type DesktopWindow = { id: string; path: string; x: number; y: number; width: number; height: number;
  minimized: boolean; mode: WindowMode }

export function restoreWindows(key: string): DesktopWindow[] {
  try {
    const values: unknown = JSON.parse(localStorage.getItem(key) || '[]')
    if (!Array.isArray(values)) return []
    return values.filter((w): w is DesktopWindow => !!w && typeof w.id === 'string' && typeof w.path === 'string'
      && w.path.startsWith('/') && !w.path.startsWith('//')
      && !(w.path.startsWith('/editor?') && new URLSearchParams(w.path.split('?')[1]).get('new') === '1')
      && [w.x, w.y, w.width, w.height].every(Number.isFinite)
      && ['float', 'full', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'].includes(w.mode) && typeof w.minimized === 'boolean')
      .slice(-12)
  } catch { return [] }
}
