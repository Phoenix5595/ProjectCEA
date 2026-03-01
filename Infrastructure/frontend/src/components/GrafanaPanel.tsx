interface GrafanaPanelProps {
  dashboardUid: string
  panelId?: number
  title?: string
  height?: string | number
}

export default function GrafanaPanel({
  dashboardUid,
  panelId,
  title,
  height = 400,
}: GrafanaPanelProps) {
  const embedUrl = `/grafana/d-solo/${dashboardUid}?orgId=1${panelId ? `&panelId=${panelId}` : ''}&theme=dark`

  const heightStyle = typeof height === 'number' ? `${height}px` : height

  return (
    <div className="w-full flex flex-col">
      {title && (
        <h3 className="text-sm font-semibold mb-2 text-text-default">{title}</h3>
      )}
      <div
        className="w-full rounded-lg overflow-hidden border border-border-default"
        style={{ height: heightStyle, minHeight: '100px' }}
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
