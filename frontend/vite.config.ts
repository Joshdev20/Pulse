import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000'

export default defineConfig({
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
})
