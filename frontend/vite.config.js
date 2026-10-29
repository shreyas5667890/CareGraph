import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to the existing Flask backend so the browser only
// ever talks to one origin (no CORS needed during development).
const backendTarget = process.env.CAREGRAPH_BACKEND_URL || 'http://127.0.0.1:5000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
  },
})
