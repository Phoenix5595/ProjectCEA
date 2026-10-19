import { useSearchParams } from 'react-router-dom'

import CalendarSettings from '../../pages/CalendarSettings'

import SystemSettingsPanel from './SystemSettingsPanel'

export default function SystemSettingsTabs() {
  const [searchParams, setSearchParams] = useSearchParams()
  const calendarSelected = searchParams.get('settings_tab') === 'calendar'

  return (
    <section aria-label="System Settings">
      <div role="tablist" aria-label="Settings tabs" className="flex gap-1 border-b border-border-subtle">
        <button
          id="system-settings-tab"
          type="button"
          role="tab"
          aria-selected={!calendarSelected}
          aria-controls="system-settings-panel"
          className={`px-3 py-2 text-sm ${!calendarSelected ? 'text-accent-vivid border-b-2 border-accent-vivid' : 'text-text-secondary'}`}
          onClick={() => setSearchParams({ tab: 'settings' })}
        >
          System
        </button>
        <button
          id="nextcloud-calendar-settings-tab"
          type="button"
          role="tab"
          aria-selected={calendarSelected}
          aria-controls="nextcloud-calendar-settings-panel"
          className={`px-3 py-2 text-sm ${calendarSelected ? 'text-accent-vivid border-b-2 border-accent-vivid' : 'text-text-secondary'}`}
          onClick={() => setSearchParams({ tab: 'settings', settings_tab: 'calendar' })}
        >
          Nextcloud Calendar
        </button>
      </div>
      <div
        role="tabpanel"
        id={calendarSelected ? 'nextcloud-calendar-settings-panel' : 'system-settings-panel'}
        aria-labelledby={calendarSelected ? 'nextcloud-calendar-settings-tab' : 'system-settings-tab'}
      >
        {calendarSelected ? <CalendarSettings /> : <SystemSettingsPanel />}
      </div>
    </section>
  )
}
