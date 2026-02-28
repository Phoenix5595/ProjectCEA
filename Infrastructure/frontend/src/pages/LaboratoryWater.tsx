import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Laboratory Water page - Water/irrigation metrics
 * Embeds Grafana dashboard for water and irrigation monitoring
 */
export default function LaboratoryWater() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'lab-water'

  return (
    <div className="min-h-screen bg-gray-950 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">Water & Irrigation</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Water & Irrigation Metrics"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'lab-water' ? (
          <p className="text-gray-500 text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in LaboratoryWater.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
