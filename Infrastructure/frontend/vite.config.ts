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
    // Tailscale MagicDNS hostnames resolve under .ts.net; add machine FQDNs
    // as needed. Phase 7.4 stripped a hard-coded Tailscale IPv4 that pinned
    // dev access to one operator machine.
    allowedHosts: ['mothernode', 'localhost', '.ts.net'],
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true
      },
      '/automation': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
      // Phase 5c: Grafana embeds go directly to VITE_GRAFANA_BASE_URL
      // (http://iskraprojectcea:3001) from the SPA; no dev-server proxy
      // needed. The Pi-local Grafana on :3000 was decommissioned 2026-04-19.
      // Phase 7.4: /api, /ws, /automation dev proxies now target Caddy
      // (:8080) rather than the individual service ports so dev traffic
      // goes through the same ingress as prod.
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets'
  }
})
