Task: Add /grafana proxy entry to Vite dev server config and verify build

- File changed: Infrastructure/frontend/vite.config.ts
- Change: Added new proxy entry for '/grafana' pointing to http://localhost:3000 with path rewrite to remove '/grafana' prefix.
- Verification: Ran npm run build in /home/antoine/ProjectCEA-ui/Infrastructure/frontend/; build completed successfully.
- Notes: Did not modify existing proxies (/api, /ws, /automation). Dev server port remains 3001.

Diff excerpt (added block):
  '/grafana': {
    target: 'http://localhost:3000',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/grafana/, ''),
  }

- Next steps: If Grafana is expected to be accessible via the frontend, ensure Grafana panel URLs in frontend reference '/grafana' base path.
