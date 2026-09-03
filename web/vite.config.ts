import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8010',
      '/healthz': 'http://127.0.0.1:8010',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
