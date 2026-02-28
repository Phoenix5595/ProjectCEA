interface GrafanaPanelProps {
  dashboardUid: string
  panelId?: number
  title?: string
  width?: string | number
  height?: string | number
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
}: GrafanaPanelProps) {
  // Build Grafana embed URL
  // Using relative /grafana path for same-site authentication
  const baseUrl = '/grafana'
  const panelParam = panelId ? `&panelId=${panelId}` : ''
  const embedUrl = `${baseUrl}/d-solo/${dashboardUid}?orgId=1${panelParam}&theme=dark`

  return (
    <div className="w-full">
      {title && (
        <h3 className="text-lg font-semibold mb-3 text-gray-900 dark:text-gray-100">
          {title}
        </h3>
      )}
      <div
        className="w-full rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700"
        style={{ height: typeof height === 'number' ? `${height}px` : height }}
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
