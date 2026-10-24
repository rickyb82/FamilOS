import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

// Match the production Caddy gate; preview ports remain bound to loopback.
function appGate(app: string): ProxyOptions['bypass'] {
  return async (req, res) => {
    if (!res) return false
    try {
      const headers: Record<string, string> = { 'X-Forwarded-Method': req.method || 'GET' }
      for (const name of ['cookie', 'origin', 'sec-fetch-site']) {
        const value = req.headers[name]
        if (typeof value === 'string') headers[name] = value
      }
      const result = await fetch(`http://127.0.0.1:8000/api/auth/apps/${app}`, { headers, signal: AbortSignal.timeout(5000) })
      if (result.ok) return
      res.writeHead(result.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(await result.text())
    } catch {
      res.writeHead(503); res.end('Verifica accesso non disponibile')
    }
    return req.url || '/'
  }
}

// In sviluppo il backend gira su :8000 (uvicorn) e Vite fa da proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: false },
      '/files': { target: 'http://localhost:8000', changeOrigin: false },
      ...Object.fromEntries(['wordcraft', 'gridcraft', 'pdfcraft', 'deckcraft', 'ideacraft'].map((app, i) => [
        `/${app}`, { target: `http://127.0.0.1:${19781 + i}`, changeOrigin: false, bypass: appGate(app),
          rewrite: (path: string) => path.replace(new RegExp(`^/${app}`), '') || '/' },
      ])),
      '/mailcraft': { target: 'http://127.0.0.1:17878', changeOrigin: false, bypass: appGate('mailcraft'),
        rewrite: (path: string) => path.replace(/^\/mailcraft/, '') || '/' },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
