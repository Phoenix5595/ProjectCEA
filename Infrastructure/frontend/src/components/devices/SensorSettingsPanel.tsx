/**
 * Sensor Settings: one row per physical registry record (CAN node or
 * RS-485 probe), unassigned first.
 *
 * CAN form: room select plus controlled position select from the frontend
 * topology mirror (Flower Front/Back; unsplit rooms Main). RS-485 form:
 * Front Bed or Back Bed only, with occupancy shown and a disabled option
 * when the bed is full. Save keeps the draft on 409/422 and renders the
 * server error inline; success refreshes the registry. No sensor deletion,
 * relay controls, free-text location, or manual bed slot exists here.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import {
  extractSoilApiError,
  soilApi,
  type AssignmentRequest,
  type Rs485Bed,
  type SensorRegistryRecord,
} from '../../features/soil'
import { canSlugFor, knownRooms, sensorSubclustersFor } from '../../config/clusterTopology'
import { logger } from '../../utils/logger'

const BED_CAPACITY = 4
const STALE_THRESHOLD_MS = 60_000

const POSITION_LABELS: Record<string, string> = {
  front: 'Front',
  back: 'Back',
  main: 'Main',
}

interface Draft {
  room: string
  location: 'front' | 'back' | 'main'
  bed: Rs485Bed | ''
}

function draftFor(record: SensorRegistryRecord): Draft {
  if (record.assignment === null) {
    return {
      room: record.bus === 'can' ? knownRooms()[0] ?? 'Flower Room' : '',
      location: 'main',
      bed: '',
    }
  }
  if (record.assignment.kind === 'can') {
    return {
      room: record.assignment.room,
      location: record.assignment.location_in_room,
      bed: '',
    }
  }
  return { room: '', location: 'main', bed: record.assignment.bed as Rs485Bed }
}

function assignmentBody(record: SensorRegistryRecord, draft: Draft): AssignmentRequest | null {
  if (record.bus === 'can') {
    return draft.room === ''
      ? null
      : { kind: 'can', room: draft.room, location_in_room: draft.location }
  }
  return draft.bed === '' ? null : { kind: 'rs485', bed: draft.bed }
}

function formatInstant(date: Date): string {
  return `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

function freshnessText(record: SensorRegistryRecord, nowMs: number): string | null {
  const stale = nowMs - record.last_seen.getTime() > STALE_THRESHOLD_MS
  if (!stale) return null
  return record.status === 'unassigned' ? 'Stale' : 'Stale'
}

export default function SensorSettingsPanel({
  initialStatus,
}: {
  initialStatus?: 'all' | 'unassigned'
}) {
  const [records, setRecords] = useState<SensorRegistryRecord[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [unassignedOnly, setUnassignedOnly] = useState(initialStatus === 'unassigned')
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [savingIds, setSavingIds] = useState<Set<number>>(new Set())

  const refresh = useCallback(async () => {
    try {
      const list = await soilApi.listRegistry()
      setRecords(list.records)
      setLoadError(null)
      setDrafts((previous) => {
        const next: Record<number, Draft> = {}
        for (const record of list.records) {
          next[record.registry_id] = previous[record.registry_id] ?? draftFor(record)
        }
        return next
      })
    } catch (error) {
      logger.error('Failed to load sensor registry', error)
      setLoadError('Failed to load the sensor registry. Fix connectivity and retry.')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const canPositionOccupied = useCallback(
    (room: string, position: 'front' | 'back' | 'main', excludeRegistryId: number): boolean => {
      if (records === null) return false
      return records.some(
        (other) =>
          other.bus === 'can' &&
          other.registry_id !== excludeRegistryId &&
          other.assignment !== null &&
          other.assignment.kind === 'can' &&
          other.assignment.room === room &&
          other.assignment.location_in_room === position,
      )
    },
    [records],
  )

  const bedOccupancy = useCallback(
    (bed: Rs485Bed): number => {
      if (records === null) return 0
      return records.filter(
        (record) =>
          record.bus === 'rs485' &&
          record.assignment !== null &&
          record.assignment.kind === 'rs485' &&
          record.assignment.bed === bed,
      ).length
    },
    [records],
  )

  const updateDraft = useCallback((registryId: number, patch: Partial<Draft>) => {
    setDrafts((previous) => ({
      ...previous,
      [registryId]: { ...previous[registryId], ...patch },
    }))
  }, [])

  const save = useCallback(
    async (record: SensorRegistryRecord) => {
      const draft = drafts[record.registry_id] ?? draftFor(record)
      const body = assignmentBody(record, draft)
      if (body === null) {
        setRowErrors((previous) => ({
          ...previous,
          [record.registry_id]: 'Choose a placement first.',
        }))
        return
      }
      setSavingIds((previous) => new Set(previous).add(record.registry_id))
      setRowErrors((previous) => {
        const next = { ...previous }
        delete next[record.registry_id]
        return next
      })
      try {
        await soilApi.assign(record.registry_id, body)
        toast.success(`Soil/CAN sensor #${record.hardware_address} assigned`)
        await refresh()
      } catch (error) {
        const detail = extractSoilApiError(error)
        logger.error(`Failed assignment for registry ${record.registry_id}`, error)
        setRowErrors((previous) => ({
          ...previous,
          [record.registry_id]: detail.errorCode
            ? `${detail.errorCode}: ${detail.message}`
            : detail.message,
        }))
      } finally {
        setSavingIds((previous) => {
          const next = new Set(previous)
          next.delete(record.registry_id)
          return next
        })
      }
    },
    [drafts, refresh],
  )

  const sortedRecords = useMemo(() => {
    if (records === null) return null
    const unassigned = records.filter((item) => item.status === 'unassigned')
    const assigned = records.filter((item) => item.status === 'assigned')
    return [...unassigned, ...assigned]
  }, [records])

  if (records === null) {
    return <p className="text-sm text-text-muted">Loading registry…</p>
  }

  const nowMs = Date.now()
  const visibleRecords = (sortedRecords ?? []).filter(
    (record) => !unassignedOnly || record.status === 'unassigned',
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold text-text-input">Sensor Settings</h2>
          <p className="mt-1 text-sm text-text-muted">
            Commission detected CAN nodes and RS-485 probes into rooms, positions, and beds.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
          <input
            type="checkbox"
            checked={unassignedOnly}
            onChange={(event) => setUnassignedOnly(event.target.checked)}
          />
          Unassigned only
        </label>
      </div>

      {loadError !== null && (
        <div
          role="alert"
          className="rounded border border-status-danger-border/60 bg-status-danger-bg/30 px-3 py-2 text-sm text-status-danger-text"
        >
          {loadError}
        </div>
      )}

      {visibleRecords.length === 0 && (
        <p className="text-sm text-text-muted">No registry records match this filter.</p>
      )}

      <div className="space-y-3">
        {visibleRecords.map((record) => {
          const draft = drafts[record.registry_id] ?? draftFor(record)
          const isCan = record.bus === 'can'
          return (
            <div
              key={record.registry_id}
              className="rounded border border-border-subtle bg-bg-card p-4 space-y-3"
            >
              <div className="flex flex-wrap items-center gap-3">
                <span className="rounded bg-bg-subtle px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                  {isCan ? 'CAN' : 'RS-485'}
                </span>
                <span className="font-mono text-sm text-text-input">#{record.hardware_address}</span>
                <span className="text-sm font-medium text-text-input">Soil probe #{record.hardware_address}</span>
                <span
                  className={
                    record.status === 'unassigned'
                      ? 'rounded border border-status-warn-border/70 bg-status-warn-bg/30 px-2 py-0.5 text-xs font-semibold text-status-warn-text'
                      : 'rounded border border-status-success-border/60 bg-status-success-bg/30 px-2 py-0.5 text-xs font-semibold text-status-success-text'
                  }
                >
                  {record.status}
                </span>
                {freshnessText(record, nowMs) !== null && (
                  <span className="text-xs font-semibold uppercase tracking-wide text-status-warn-text">
                    Stale
                  </span>
                )}
                <span className="ml-auto text-xs text-text-muted">
                  First seen {formatInstant(record.first_seen)} · Last seen {formatInstant(record.last_seen)}
                </span>
              </div>

              {isCan ? (
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                    Room
                    <select
                      className="rounded border border-border-subtle bg-bg-card px-2 py-1.5 text-sm normal-case tracking-normal text-text-input"
                      value={draft.room}
                      onChange={(event) => {
                        const room = event.target.value
                        const positions = sensorSubclustersFor(room)
                          .map(canSlugFor)
                          .filter((slug): slug is NonNullable<typeof slug> => slug !== null)
                        const keepsDraft = positions.includes(draft.location)
                        updateDraft(record.registry_id, {
                          room,
                          location: keepsDraft
                            ? draft.location
                            : positions[positions.length - 1] ?? 'main',
                        })
                      }}
                    >
                      {knownRooms().map((room) => (
                        <option key={room} value={room}>
                          {room}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                    Position
                    <select
                      className="rounded border border-border-subtle px-2 py-1.5 text-sm normal-case tracking-normal text-text-input"
                      value={draft.location}
                      onChange={(event) =>
                        updateDraft(record.registry_id, {
                          location: event.target.value as Draft['location'],
                        })
                      }
                    >
                      {sensorSubclustersFor(draft.room)
                        .map(canSlugFor)
                        .filter((slug): slug is NonNullable<typeof slug> => slug !== null)
                        .map((position) => {
                        const occupied = canPositionOccupied(
                          draft.room,
                          position,
                          record.registry_id,
                        )
                        const isDraft = draft.location === position
                        const disabled = occupied && !isDraft
                        const label = POSITION_LABELS[position] ?? position
                        return (
                          <option key={position} value={position} disabled={disabled}>
                            {label}
                            {disabled ? ' (occupied)' : ''}
                          </option>
                        )
                      })}
                    </select>
                  </label>
                </div>
              ) : (
                <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Flower bed
                  <select
                    className="rounded border border-border-subtle px-2 py-1.5 text-sm normal-case tracking-normal text-text-input"
                    value={draft.bed}
                    onChange={(event) =>
                      updateDraft(record.registry_id, { bed: event.target.value as Rs485Bed })
                    }
                  >
                    <option value="">Choose a bed…</option>
                    {(['Front Bed', 'Back Bed'] as Rs485Bed[]).map((bed) => {
                      const occupancy = bedOccupancy(bed)
                      const full = occupancy >= BED_CAPACITY
                      const isDraft = draft.bed === bed
                      return (
                        <option key={bed} value={bed} disabled={full && !isDraft}>
                          {bed} — {occupancy}/{BED_CAPACITY}
                          {full ? ' (full)' : ''}
                        </option>
                      )
                    })}
                  </select>
                </label>
              )}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  disabled={savingIds.has(record.registry_id)}
                  onClick={() => void save(record)}
                  className="rounded border border-btn-primary-light bg-btn-primary-light/20 px-3 py-1.5 text-sm font-semibold text-btn-primary-text disabled:opacity-50"
                >
                  {savingIds.has(record.registry_id) ? 'Saving…' : 'Save assignment'}
                </button>
                {rowErrors[record.registry_id] !== undefined && (
                  <span role="alert" className="text-sm text-status-danger-text">
                    {rowErrors[record.registry_id]}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
