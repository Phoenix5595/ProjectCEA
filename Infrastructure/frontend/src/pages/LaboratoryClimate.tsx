import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Laboratory Climate page - Lab + Outdoor climate monitoring
 * Embeds Grafana dashboard for climate metrics
 */
export default function LaboratoryClimate() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'lab-climate'

  return (
    <div className="min-h-screen bg-gray-950 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">Laboratory Climate</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Lab & Outdoor Climate"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'lab-climate' ? (
          <p className="text-gray-500 text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in LaboratoryClimate.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
