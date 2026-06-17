import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/auth': 'http://localhost:8000',
      '/api': {
        target: 'http://localhost:8000',
        ws: true,
      },
      '/health': 'http://localhost:8000',
    },
  },
})
