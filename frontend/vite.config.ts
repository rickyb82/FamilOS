import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In sviluppo il backend gira su :8000 (uvicorn) e Vite fa da proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: false },
      '/files': { target: 'http://localhost:8000', changeOrigin: false },
      ...Object.fromEntries(['wordcraft', 'gridcraft', 'pdfcraft', 'deckcraft', 'ideacraft'].map((app, i) => [
        `/${app}`, { target: `http://127.0.0.1:${19781 + i}`, changeOrigin: false,
          rewrite: (path: string) => path.replace(new RegExp(`^/${app}`), '') || '/' },
      ])),
      '/mailcraft': { target: 'http://127.0.0.1:17878', changeOrigin: false,
        rewrite: (path: string) => path.replace(/^\/mailcraft/, '') || '/' },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
