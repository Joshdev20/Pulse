import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBase = (env.VITE_API_BASE ?? process.env.VITE_API_BASE ?? '').trim()

  // A Vercel build with no VITE_API_BASE silently ships a bundle that calls its
  // own origin, where the SPA catch-all rewrite answers every /api/* request
  // with index.html. The result is a site that renders and looks healthy but
  // cannot log in or register. Fail the build instead of shipping that.
  if (process.env.VERCEL && !apiBase && env.VITE_SAME_ORIGIN !== '1') {
    throw new Error(
      'VITE_API_BASE is not set, so this bundle would call its own origin for ' +
        '/api/* and receive index.html back. Set VITE_API_BASE to your backend ' +
        'origin (e.g. https://pulse-api.onrender.com) in the Vercel project ' +
        'settings and redeploy. If the API really is served from this same ' +
        'origin, set VITE_SAME_ORIGIN=1 to opt out of this check.',
    )
  }

  const backend = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      host: true,
      port: Number(process.env.PORT ?? 5173),
      // Sandbox/dev hosts are proxied through generated hostnames, so accept any Host header.
      allowedHosts: true,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
          // Realtime notifications and messaging ride on /api/ws.
          ws: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
  }
})
