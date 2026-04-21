import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3001,
    host: "0.0.0.0",
    // ".ts.net" allows Tailscale MagicDNS hostnames; add machine FQDNs if needed
    allowedHosts: ['mothernode', 'localhost', '100.120.60.40', '.ts.net'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true
      },
      '/automation': {
        target: 'http://localhost:8001',
        changeOrigin: true
      }
      // Phase 5c: Grafana embeds now go directly to
      // VITE_GRAFANA_BASE_URL (http://iskraprojectcea:3001) from the
      // SPA, so no dev-server proxy is needed. The Pi-local Grafana
      // on :3000 was decommissioned 2026-04-19.
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets'
  }
})
