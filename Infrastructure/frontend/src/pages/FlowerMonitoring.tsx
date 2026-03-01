import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

export default function FlowerMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4 overflow-hidden">
      <div className="mx-auto h-full" style={{ maxWidth: '1600px', height: 'calc(100vh - 2rem)' }}>
        <h1 className="text-2xl font-bold text-text-default mb-4">Flower Room Monitoring</h1>

        <div className="flex flex-col gap-4 h-[calc(100%-4rem)] overflow-hidden">
          <div className="flex flex-col lg:flex-row gap-4 h-full overflow-hidden">
            
            <div className="w-full lg:w-1/6 flex flex-col gap-4 h-full overflow-y-auto">
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={1} title="Averages" height={280} />
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={2} title="Front Cluster" height={280} />
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={3} title="Back Cluster" height={280} />
            </div>

            <div className="w-full lg:w-5/6 flex flex-col gap-4 h-full overflow-hidden">
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={4} title="Temperature, RH and VPD" height="100%" />
            </div>

          </div>

          <div className="w-full h-64 overflow-hidden">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={6} title="Statistics" height={250} />
          </div>
        </div>
      </div>
    </div>
  )
}
