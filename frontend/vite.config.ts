import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// VITE_URL_PREFIX sets the public path the bundle assumes — e.g. "/nvbr"
// when the app is mounted under that path behind NV Tools. Empty/unset =
// served at root. The dev proxy targets the local backend started with
// `DEV_MODE=1 uv run uvicorn app.main:app --port 8000`.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const raw = (env.VITE_URL_PREFIX ?? '').trim().replace(/\/$/, '')
  const prefix = raw && !raw.startsWith('/') ? `/${raw}` : raw
  const base = prefix ? `${prefix}/` : '/'
  return {
    base,
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        [`${prefix}/api`]: 'http://localhost:8000',
        [`${prefix}/healthz`]: 'http://localhost:8000',
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
  }
})
