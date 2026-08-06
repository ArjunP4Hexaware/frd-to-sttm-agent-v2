import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Local dev only: proxies API calls to the FastAPI backend so the
    // frontend never needs CORS config, in dev or in the deployed
    // single-origin Databricks App.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
