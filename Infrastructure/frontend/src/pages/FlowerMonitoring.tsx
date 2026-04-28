import GrafanaPanel from '../components/GrafanaPanel'
import { useParams } from 'react-router-dom'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

export default function FlowerMonitoring() {
  const { cluster } = useParams<{ cluster?: string }>()
  const selected = cluster === 'front' || cluster === 'back' ? cluster : 'back'
  return (
    <div className="bg-surface-base overflow-hidden -mt-4 -mb-4" style={{ height: 'calc(100vh - 60px)' }}>
      <div className="h-full flex flex-col">
        <div className="px-2 py-1 text-xs text-text-muted uppercase tracking-wide">Cluster: {selected}</div>
        <div className="flex-grow overflow-hidden">
          <GrafanaPanel 
            dashboardUid={DASHBOARD_UID} 
            timeRange={true}
            refresh="1s"
            height="100%" 
          />
        </div>
      </div>
    </div>
  )
}
