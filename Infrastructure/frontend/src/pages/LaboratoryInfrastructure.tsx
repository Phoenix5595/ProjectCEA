import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Laboratory Infrastructure page - System infrastructure monitoring
 * Embeds Grafana dashboard for services, database, and Redis metrics
 */
export default function LaboratoryInfrastructure() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'lab-infrastructure'

  return (
    <div className="min-h-screen bg-gray-950 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">Infrastructure</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="System Infrastructure"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'lab-infrastructure' ? (
          <p className="text-gray-500 text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in LaboratoryInfrastructure.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
