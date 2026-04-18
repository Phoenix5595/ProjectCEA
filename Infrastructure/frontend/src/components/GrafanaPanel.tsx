import { GRAFANA_BASE_URL } from '../config/env'

interface GrafanaPanelProps {
  dashboardUid: string
  panelId?: number
  title?: string
  height?: string | number
  timeRange?: boolean
}

export default function GrafanaPanel({
  dashboardUid,
  panelId,
  title,
  height = 400,
  timeRange = false,
}: GrafanaPanelProps) {
  // Base URL is build-time env (VITE_GRAFANA_BASE_URL); see config/env.ts.
  const embedUrl = timeRange
    ? `${GRAFANA_BASE_URL}/d/${dashboardUid}?orgId=1&theme=dark&kiosk=1&from=now-6h&to=now`
    : `${GRAFANA_BASE_URL}/d-solo/${dashboardUid}?orgId=1${panelId ? `&panelId=${panelId}` : ''}&theme=dark&from=now-6h&to=now`

  const heightStyle = typeof height === 'number' ? `${height}px` : height

  return (
    <div className="w-full h-full flex flex-col">
      {title && !timeRange && (
        <h3 className="text-sm font-semibold mb-2 text-text-default">{title}</h3>
      )}
      <div
        className="w-full rounded-lg overflow-hidden border border-border-default flex-grow"
        style={{ height: heightStyle }}
      >
        <iframe
          src={embedUrl}
          title={title || `Grafana Panel ${panelId}`}
          width="100%"
          height="100%"
          className="w-full h-full"
          loading="lazy"
          style={{ border: 'none' }}
        />
      </div>
    </div>
  )
}
