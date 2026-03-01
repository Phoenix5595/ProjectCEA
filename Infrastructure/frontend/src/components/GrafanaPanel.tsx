interface GrafanaPanelProps {
  dashboardUid: string
  panelId?: number
  title?: string
  width?: string | number
  height?: string | number
  fullDashboard?: boolean
}

/**
 * Reusable component for embedding Grafana dashboards via iframe.
 * Uses relative URL /grafana for authentication (same auth as main app).
 */
export default function GrafanaPanel({
  dashboardUid,
  panelId,
  title,
  width = '100%',
  height = 400,
  fullDashboard = false,
}: GrafanaPanelProps) {
  const baseUrl = '/grafana'
  
  const embedUrl = fullDashboard
    ? `${baseUrl}/d/${dashboardUid}?orgId=1&theme=dark&kiosk=tv`
    : `${baseUrl}/d-solo/${dashboardUid}?orgId=1${panelId ? `&panelId=${panelId}` : ''}&theme=dark`

  return (
    <div className="w-full h-full flex flex-col">
      {title && !fullDashboard && (
        <h3 className="text-lg font-semibold mb-3 text-text-default">
          {title}
        </h3>
      )}
      <div
        className="w-full rounded-lg overflow-hidden border border-border-default flex-grow"
        style={{ height: typeof height === 'number' ? `${height}px` : height, minHeight: fullDashboard ? '800px' : 'auto' }}
      >
        <iframe
          src={embedUrl}
          title={title || `Grafana Dashboard ${dashboardUid}`}
          width={width}
          height="100%"
          className="w-full h-full"
          loading="lazy"
          style={{ border: 'none' }}
        />
      </div>
    </div>
  )
}
