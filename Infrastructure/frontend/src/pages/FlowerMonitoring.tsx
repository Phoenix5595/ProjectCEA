import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Flower Room Monitoring page
 * Renders a 6-panel Grafana grid for flower room metrics
 */
export default function FlowerMonitoring() {
  // Grafana dashboard UID for Flower Room monitoring
  const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Flower Room Monitoring</h1>

        {/* Responsive 2-column grid on md, 1 column on small screens */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Panel 1: Averages - full width (col-span-2) */}
          <div className="md:col-span-2">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={1}
              title="Averages"
              height={320}
            />
          </div>

          {/* Panel 2: Front Cluster - half width */}
          <div className="md:col-span-1">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={2}
              title="Front Cluster"
              height={320}
            />
          </div>

          {/* Panel 3: Back Cluster - half width */}
          <div className="md:col-span-1">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={3}
              title="Back Cluster"
              height={320}
            />
          </div>

          {/* Panel 4: Temperature, RH and VPD - full width, timeseries */}
          <div className="md:col-span-2">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={4}
              title="Temperature, RH and VPD"
              height={400}
            />
          </div>

          {/* Panel 5: CO2 and Pressure - full width, timeseries */}
          <div className="md:col-span-2">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={5}
              title="CO2 and Pressure"
              height={400}
            />
          </div>

          {/* Panel 6: Statistics - All Sensors - full width */}
          <div className="md:col-span-2">
            <GrafanaPanel
              dashboardUid={DASHBOARD_UID}
              panelId={6}
              title="Statistics - All Sensors"
              height={320}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
