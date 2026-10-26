/**
 * Sensor Settings → RS485: soil probe placement (R9). Allocation only, no
 * live values.
 *
 * Probes are tags in an Unassigned box, in the two 4×4 ft Flower beds
 * (Back Bed left, Front Bed right, free position) or in Veg Room
 * (room-level). Drag a tag onto a zone (native HTML5 drag; the drop point
 * inside a bed becomes `x_frac`/`y_frac`) or use the per-probe buttons.
 * The bus banner shows the soil service's commissioning state.
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import {
  extractSoilApiError,
  soilApi,
  type Rs485AssignmentRequest,
  type Rs485Bed,
  type SensorRegistryRecord,
  type SoilBed,
  type SoilBusStatus,
} from '../../features/soil'
import { formatUtcInstant } from '../../features/soil/format'
import { BED_DISPLAY_ORDER, fracFromPointer } from '../../features/soil/geometry'
import { logger } from '../../utils/logger'

const REFRESH_MS = 10_000

type Zone = 'unassigned' | Rs485Bed | 'Veg Room'

const BED_TEST_IDS: Record<Rs485Bed, string> = {
  'Back Bed': 'soil-bed-back',
  'Front Bed': 'soil-bed-front',
}

function zoneOf(record: SensorRegistryRecord): Zone {
  const assignment = record.assignment
  if (assignment === null || assignment.kind !== 'rs485') return 'unassigned'
  if (assignment.bed === 'Back Bed' || assignment.bed === 'Front Bed') return assignment.bed
  return 'Veg Room'
}

function bannerText(status: SoilBusStatus | null): string | null {
  if (status === null) return null
  switch (status.state) {
    case 'power_cycle_required':
      return `Power-cycle the new probe to finish address ${status.address ?? '?'}`
    case 'collision':
      return 'Several new probes on address 1 — connect one at a time'
    case 'no_free_address':
      return 'No free address left (2–254)'
    default:
      return null
  }
}

function byAddress(a: SensorRegistryRecord, b: SensorRegistryRecord): number {
  return (a.hardware_address ?? 0) - (b.hardware_address ?? 0)
}

const ZONE_BUTTONS: { zone: Zone; label: string }[] = [
  { zone: 'unassigned', label: 'Unassigned' },
  { zone: 'Back Bed', label: 'Back Bed' },
  { zone: 'Front Bed', label: 'Front Bed' },
  { zone: 'Veg Room', label: 'Veg Room' },
]

export default function Rs485PlacementSection() {
  const [records, setRecords] = useState<SensorRegistryRecord[]>([])
  const [beds, setBeds] = useState<SoilBed[]>([])
  const [busStatus, setBusStatus] = useState<SoilBusStatus | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    const [list, bedList, status] = await Promise.allSettled([
      soilApi.listRegistry({ bus: 'rs485' }),
      soilApi.beds(),
      soilApi.busStatus(),
    ])
    if (list.status === 'fulfilled') setRecords(list.value.records)
    else logger.error('Failed to load RS485 probes', list.reason)
    if (bedList.status === 'fulfilled') setBeds(bedList.value.beds)
    else logger.error('Failed to load soil beds', bedList.reason)
    if (status.status === 'fulfilled') setBusStatus(status.value)
    else logger.error('Failed to load soil bus status', status.reason)
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  const mutate = useCallback(
    async (record: SensorRegistryRecord, action: () => Promise<unknown>) => {
      setBusyId(record.registry_id)
      try {
        await action()
      } catch (error) {
        logger.error(`Failed placement for registry ${record.registry_id}`, error)
        toast.error(extractSoilApiError(error).message)
      } finally {
        setBusyId(null)
        await refresh()
      }
    },
    [refresh]
  )

  const place = useCallback(
    (record: SensorRegistryRecord, body: Rs485AssignmentRequest) =>
      mutate(record, () => soilApi.assign(record.registry_id, body)),
    [mutate]
  )

  const moveTo = useCallback(
    (record: SensorRegistryRecord, zone: Zone) => {
      const current = zoneOf(record)
      if (zone === 'unassigned') {
        if (current !== 'unassigned')
          void mutate(record, () => soilApi.unassign(record.registry_id))
        return
      }
      if (zone === 'Veg Room') {
        if (current !== 'Veg Room') void place(record, { kind: 'rs485', room: 'Veg Room' })
        return
      }
      const assignment = record.assignment
      const keep =
        current === zone && assignment?.kind === 'rs485' && assignment.x_frac !== null
          ? { x_frac: assignment.x_frac, y_frac: assignment.y_frac ?? 0.5 }
          : { x_frac: 0.5, y_frac: 0.5 }
      void place(record, { kind: 'rs485', room: 'Flower Room', bed: zone, ...keep })
    },
    [mutate, place]
  )

  const draggedRecord = (event: React.DragEvent<HTMLElement>): SensorRegistryRecord | undefined => {
    const id = Number(event.dataTransfer.getData('text/plain'))
    return records.find(record => record.registry_id === id)
  }

  const dropOnZone = (event: React.DragEvent<HTMLElement>, zone: Zone) => {
    event.preventDefault()
    const record = draggedRecord(event)
    if (record === undefined || busyId !== null) return
    if (zone === 'Back Bed' || zone === 'Front Bed') {
      const spot = fracFromPointer(
        event.currentTarget.getBoundingClientRect(),
        event.clientX,
        event.clientY
      )
      void place(record, { kind: 'rs485', room: 'Flower Room', bed: zone, ...spot })
      return
    }
    moveTo(record, zone)
  }

  const zoneHandlers = (zone: Zone) => ({
    onDragOver: (event: React.DragEvent<HTMLElement>) => event.preventDefault(),
    onDrop: (event: React.DragEvent<HTMLElement>) => dropOnZone(event, zone),
  })

  const tag = (record: SensorRegistryRecord, style?: React.CSSProperties) => (
    <div
      key={record.registry_id}
      draggable={busyId === null}
      data-testid={`soil-tag-${record.hardware_address}`}
      aria-label={`Soil ${record.hardware_address}`}
      title={`Last seen ${formatUtcInstant(record.last_seen)}`}
      onDragStart={event => {
        event.dataTransfer.setData('text/plain', String(record.registry_id))
        event.dataTransfer.effectAllowed = 'move'
      }}
      style={style}
      className="cursor-grab select-none whitespace-nowrap rounded border border-border-default bg-surface-tertiary px-1.5 py-0.5 font-mono text-xs text-text-input"
    >
      Soil {record.hardware_address}
    </div>
  )

  const sorted = [...records].sort(byAddress)
  const inZone = (zone: Zone) => sorted.filter(record => zoneOf(record) === zone)
  const banner = bannerText(busStatus)

  return (
    <section aria-labelledby="sensor-settings-rs485" className="space-y-4">
      <h3 id="sensor-settings-rs485" className="text-lg font-semibold text-text-input">
        RS485
      </h3>

      {banner !== null && (
        <div
          role="status"
          data-testid="soil-bus-status"
          className="rounded border border-status-warning/70 bg-status-warning-bg/30 px-3 py-2 text-sm text-status-warning-text"
        >
          {banner}
        </div>
      )}

      <div className="flex flex-wrap items-start gap-4">
        <div className="w-48 space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Unassigned
          </h4>
          <div
            data-testid="soil-unassigned"
            className="flex min-h-24 flex-wrap content-start gap-1 rounded border border-dashed border-border-default bg-surface-secondary p-2"
            {...zoneHandlers('unassigned')}
          >
            {inZone('unassigned').length === 0 ? (
              <span className="text-xs text-text-muted">No unassigned probes</span>
            ) : (
              inZone('unassigned').map(record => tag(record))
            )}
          </div>
        </div>

        <div className="space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Flower Room
          </h4>
          <div className="flex gap-4">
            {BED_DISPLAY_ORDER.map(name => {
              const bed = beds.find(candidate => candidate.name === name)
              return (
                <figure key={name} className="w-56 space-y-1">
                  <div
                    data-testid={BED_TEST_IDS[name]}
                    aria-label={`${name} placement area`}
                    className="relative w-full rounded border border-border-default bg-surface-secondary"
                    style={{ aspectRatio: bed ? `${bed.width_ft} / ${bed.length_ft}` : '1 / 1' }}
                    {...zoneHandlers(name)}
                  >
                    {inZone(name).map(record => {
                      const assignment = record.assignment
                      const x = assignment?.kind === 'rs485' ? (assignment.x_frac ?? 0.5) : 0.5
                      const y = assignment?.kind === 'rs485' ? (assignment.y_frac ?? 0.5) : 0.5
                      return tag(record, {
                        position: 'absolute',
                        left: `${x * 100}%`,
                        top: `${y * 100}%`,
                        transform: 'translate(-50%, -50%)',
                      })
                    })}
                  </div>
                  <figcaption className="text-xs text-text-muted">
                    {bed ? `${name} · ${bed.width_ft} × ${bed.length_ft} ft` : name}
                  </figcaption>
                </figure>
              )
            })}
          </div>
        </div>

        <div className="w-48 space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Veg Room
          </h4>
          <div
            data-testid="soil-veg"
            className="flex min-h-24 flex-wrap content-start gap-1 rounded border border-dashed border-border-default bg-surface-secondary p-2"
            {...zoneHandlers('Veg Room')}
          >
            {inZone('Veg Room').map(record => tag(record))}
          </div>
        </div>
      </div>

      <table aria-label="RS485 probes" className="w-full text-sm">
        <tbody>
          {sorted.map(record => {
            const current = zoneOf(record)
            return (
              <tr key={record.registry_id} className="border-t border-border-subtle">
                <td className="py-1 pr-3 font-mono text-text-input">
                  Soil {record.hardware_address}
                </td>
                <td className="py-1 pr-3 text-xs text-text-muted">
                  Last seen {formatUtcInstant(record.last_seen)}
                </td>
                <td className="py-1">
                  <div className="flex flex-wrap gap-1">
                    {ZONE_BUTTONS.map(({ zone, label }) => (
                      <button
                        key={zone}
                        type="button"
                        aria-pressed={current === zone}
                        disabled={busyId !== null}
                        onClick={() => moveTo(record, zone)}
                        className="rounded border border-border-subtle px-2 py-0.5 text-xs text-text-input aria-pressed:border-btn-primary-light aria-pressed:bg-btn-primary-light/20 disabled:opacity-50"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
