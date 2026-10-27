import { useSearchParams } from 'react-router-dom'

import CanBusSection from './CanBusSection'
import Rs485PlacementSection from './Rs485PlacementSection'

type SensorSettingsTab = 'can' | 'rs485'

export default function SensorSettingsTabs({
  initialStatus,
}: {
  initialStatus?: 'all' | 'unassigned'
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  // Missing or unknown `settings_tab` values land on CAN, the older surface.
  const activeTab: SensorSettingsTab =
    searchParams.get('settings_tab') === 'rs485' ? 'rs485' : 'can'

  function selectTab(tab: SensorSettingsTab) {
    const next = new URLSearchParams(searchParams)
    next.set('settings_tab', tab)
    setSearchParams(next)
  }

  return (
    <section aria-label="Sensor settings">
      <div
        role="tablist"
        aria-label="Sensor settings tabs"
        className="flex gap-1 border-b border-border-subtle"
      >
        <button
          id="sensor-settings-can-tab"
          type="button"
          role="tab"
          aria-selected={activeTab === 'can'}
          aria-controls="sensor-settings-can-panel"
          className={`px-3 py-2 text-sm ${activeTab === 'can' ? 'text-accent-vivid border-b-2 border-accent-vivid' : 'text-text-secondary'}`}
          onClick={() => selectTab('can')}
        >
          CAN bus
        </button>
        <button
          id="sensor-settings-rs485-tab"
          type="button"
          role="tab"
          aria-selected={activeTab === 'rs485'}
          aria-controls="sensor-settings-rs485-panel"
          className={`px-3 py-2 text-sm ${activeTab === 'rs485' ? 'text-accent-vivid border-b-2 border-accent-vivid' : 'text-text-secondary'}`}
          onClick={() => selectTab('rs485')}
        >
          RS485
        </button>
      </div>
      <div
        role="tabpanel"
        id={activeTab === 'can' ? 'sensor-settings-can-panel' : 'sensor-settings-rs485-panel'}
        aria-labelledby={
          activeTab === 'can' ? 'sensor-settings-can-tab' : 'sensor-settings-rs485-tab'
        }
      >
        {activeTab === 'can' ? (
          <CanBusSection initialStatus={initialStatus} />
        ) : (
          <Rs485PlacementSection />
        )}
      </div>
    </section>
  )
}
