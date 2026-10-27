/**
 * Sensor Settings (R9).
 *
 * A shared header introduces one tab per sensor bus: CAN bus commissioning and
 * identity confirmation, and RS485 soil-probe placement. The active tab lives
 * in the URL (`settings_tab=can|rs485`) so the Flower Soil page can deep-link
 * straight to the RS485 bus; missing or unknown values land on CAN. Only the
 * active bus mounts, so the RS485 poller does not run while the operator works
 * on CAN and vice versa. No sensor deletion, relay controls or free-text
 * location exists here.
 */
import SensorSettingsTabs from './SensorSettingsTabs'

export default function SensorSettingsPanel({
  initialStatus,
}: {
  initialStatus?: 'all' | 'unassigned'
}) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-text-input">Sensor Settings</h2>
        <p className="mt-1 text-sm text-text-muted">
          CAN nodes are commissioned to room positions; RS485 soil probes are placed by dragging or
          with the placement buttons.
        </p>
      </div>

      <SensorSettingsTabs initialStatus={initialStatus} />
    </div>
  )
}
