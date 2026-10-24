import { useMemo, useRef, type ReactNode } from 'react'
import { Router, createPath, type To } from 'react-router-dom'

/** Each window owns its location. Switching windows never unmounts another app. */
export default function WindowRouter({ path, navigate, children }: { path: string; navigate: (path: string) => void; children: ReactNode }) {
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate
  const navigator = useMemo(() => ({
    createHref: (to: To) => typeof to === 'string' ? to : createPath(to),
    encodeLocation: (to: To) => new URL(typeof to === 'string' ? to : createPath(to), window.location.origin),
    push: (to: To) => navigateRef.current(typeof to === 'string' ? to : createPath(to)),
    replace: (to: To) => navigateRef.current(typeof to === 'string' ? to : createPath(to)),
    go: () => {},
  }), [])
  return <Router location={path} navigator={navigator}>{children}</Router>
}
