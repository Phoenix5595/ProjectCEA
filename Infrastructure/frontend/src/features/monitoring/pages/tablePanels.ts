/**
 * Table helpers for the monitoring pages.
 *
 * Extracts the canonical table panels from a manifest and splits the store's
 * live values by node suffix so the reusable table primitives receive the
 * correct Front/Back arrays. Room-agnostic: `tablePanel` works for any room;
 * `splitLiveByNode` is used by the Flower page (Front/Back), while Veg passes
 * the full live array to `SensorValueTable` with `nodeSuffix="v"`.
 */
import type { LiveSensorValue } from '../api'
import type { MonitoringManifest, TablePanelSpec } from '../config'

export function tablePanel(
  manifest: MonitoringManifest,
  id: string,
): TablePanelSpec | null {
  const panel = manifest.panels.find((p) => p.kind === 'table' && p.id === id)
  return panel && panel.kind === 'table' ? panel : null
}

/** Split live values into Front (`_f`) and Back (`_b`) arrays. */
export function splitLiveByNode(
  live: LiveSensorValue[],
): { front: LiveSensorValue[]; back: LiveSensorValue[] } {
  const front: LiveSensorValue[] = []
  const back: LiveSensorValue[] = []
  for (const v of live) {
    if (v.sensor.endsWith('_f')) front.push(v)
    else if (v.sensor.endsWith('_b')) back.push(v)
  }
  return { front, back }
}
