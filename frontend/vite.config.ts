import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 2000 },
  optimizeDeps: { exclude: ['maplibre-gl'] },
  server: {
    allowedHosts: ['.trycloudflare.com', 'oraclis.jaypee.dpdns.org'],
    proxy: { '/api': 'http://127.0.0.1:8765', '/files': 'http://127.0.0.1:8765' },
  },
})
