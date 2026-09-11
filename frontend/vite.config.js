import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/tournaments': 'http://localhost:8000',
      '/rules': 'http://localhost:8000',
      '/games': 'http://localhost:8000'
    }
  }
})
