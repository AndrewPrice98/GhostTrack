import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dashboard calls /api/*; in development Vite forwards that to FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  preview: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
