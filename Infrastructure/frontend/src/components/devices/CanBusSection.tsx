/**
 * Sensor Settings → CAN bus (R9).
 *
 * One card per CAN registry record, identity-pending first, then unassigned,
 * then assigned. A confirmed node gets a room select plus a controlled
 * position select from the frontend topology mirror (Flower Front/Back;
 * unsplit rooms Main), and is saved to the registry. Pending identities are
 * confirmed as an older identity or as a new sensor. Save keeps the draft on
 * 409/422 and renders the server error inline; success refreshes the registry.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { canSlugFor, knownRooms, sensorSubclustersFor } from '../../config/clusterTopology'
import {
  extractSoilApiError,
  soilApi,
  type AssignmentRequest,
  type IdentityRequest,
  type SensorRegistryRecord,
} from '../../features/soil'
import { formatUtcInstant } from '../../features/soil/format'
import { logger } from '../../utils/logger'

const STALE_THRESHOLD_MS = 60_000

// Match the services' ten-minute identity-absence threshold.
const IDENTITY_ABSENCE_MS = 10 * 60 * 1000

const POSITION_LABELS: Record<string, string> = {
  front: 'Front',
  back: 'Back',
  main: 'Main',
}

interface Draft {
  room: string
  location: 'front' | 'back' | 'main'
}

function draftFor(record: SensorRegistryRecord): Draft {
  if (record.assignment === null || record.assignment.kind !== 'can') {
    return { room: knownRooms()[0] ?? 'Flower Room', location: 'main' }
  }
  return { room: record.assignment.room, location: record.assignment.location_in_room }
}

function assignmentBody(draft: Draft): AssignmentRequest | null {
  return draft.room === ''
    ? null
    : { kind: 'can', room: draft.room, location_in_room: draft.location }
}

function freshnessText(record: SensorRegistryRecord, nowMs: number): string | null {
  const stale = nowMs - record.last_seen.getTime() > STALE_THRESHOLD_MS
  if (!stale) return null
  return 'Stale'
}

export default function CanBusSection({ initialStatus }: { initialStatus?: 'all' | 'unassigned' }) {
  const [records, setRecords] = useState<SensorRegistryRecord[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [unassignedOnly, setUnassignedOnly] = useState(initialStatus === 'unassigned')
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({})
  const [savingIds, setSavingIds] = useState<Set<number>>(new Set())
  const [identityTargets, setIdentityTargets] = useState<Record<number, number>>({})

  const refresh = useCallback(async () => {
    try {
      const list = await soilApi.listRegistry()
      const canRecords = list.records.filter(record => record.bus === 'can')
      setRecords(canRecords)
      setLoadError(null)
      setDrafts(previous => {
        const next: Record<number, Draft> = {}
        for (const record of canRecords) {
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
        other =>
          other.bus === 'can' &&
          other.registry_id !== excludeRegistryId &&
          other.assignment !== null &&
          other.assignment.kind === 'can' &&
          other.assignment.room === room &&
          other.assignment.location_in_room === position
      )
    },
    [records]
  )

  const updateDraft = useCallback((registryId: number, patch: Partial<Draft>) => {
    setDrafts(previous => ({
      ...previous,
      [registryId]: { ...previous[registryId], ...patch },
    }))
  }, [])

  const save = useCallback(
    async (record: SensorRegistryRecord) => {
      const draft = drafts[record.registry_id] ?? draftFor(record)
      const body = assignmentBody(draft)
      if (body === null) {
        setRowErrors(previous => ({
          ...previous,
          [record.registry_id]: 'Choose a placement first.',
        }))
        return
      }
      setSavingIds(previous => new Set(previous).add(record.registry_id))
      setRowErrors(previous => {
        const next = { ...previous }
        delete next[record.registry_id]
        return next
      })
      try {
        await soilApi.assign(record.registry_id, body)
        toast.success(`CAN sensor #${record.hardware_address} assigned`)
        await refresh()
      } catch (error) {
        const detail = extractSoilApiError(error)
        logger.error(`Failed assignment for registry ${record.registry_id}`, error)
        setRowErrors(previous => ({
          ...previous,
          [record.registry_id]: detail.errorCode
            ? `${detail.errorCode}: ${detail.message}`
            : detail.message,
        }))
      } finally {
        setSavingIds(previous => {
          const next = new Set(previous)
          next.delete(record.registry_id)
          return next
        })
      }
    },
    [drafts, refresh]
  )

  const resolveIdentity = useCallback(
    async (record: SensorRegistryRecord, body: IdentityRequest) => {
      setSavingIds(previous => new Set(previous).add(record.registry_id))
      setRowErrors(previous => {
        const next = { ...previous }
        delete next[record.registry_id]
        return next
      })
      try {
        await soilApi.resolveIdentity(record.registry_id, body)
        toast.success(
          body.decision === 'new' ? 'Confirmed as a new sensor' : 'Sensor identity confirmed'
        )
        await refresh()
      } catch (error) {
        const detail = extractSoilApiError(error)
        logger.error(`Failed identity confirmation for registry ${record.registry_id}`, error)
        setRowErrors(previous => ({
          ...previous,
          [record.registry_id]: detail.errorCode
            ? `${detail.errorCode}: ${detail.message}`
            : detail.message,
        }))
      } finally {
        setSavingIds(previous => {
          const next = new Set(previous)
          next.delete(record.registry_id)
          return next
        })
      }
    },
    [refresh]
  )

  const sortedRecords = useMemo(() => {
    if (records === null) return null
    const pending = records.filter(item => item.identity_status === 'pending')
    const unassigned = records.filter(
      item => item.identity_status !== 'pending' && item.status === 'unassigned'
    )
    const assigned = records.filter(
      item => item.identity_status !== 'pending' && item.status === 'assigned'
    )
    return [...pending, ...unassigned, ...assigned]
  }, [records])

  const nowMs = Date.now()
  const visibleRecords = (sortedRecords ?? []).filter(
    record =>
      !unassignedOnly || record.identity_status === 'pending' || record.status === 'unassigned'
  )

  return (
    <section aria-labelledby="sensor-settings-can" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="sensor-settings-can" className="text-lg font-semibold text-text-input">
          CAN bus
        </h3>
        <label className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
          <input
            type="checkbox"
            checked={unassignedOnly}
            onChange={event => setUnassignedOnly(event.target.checked)}
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

      {records === null && loadError === null && (
        <p className="text-sm text-text-muted">Loading registry…</p>
      )}

      {records !== null && visibleRecords.length === 0 && (
        <p className="text-sm text-text-muted">No registry records match this filter.</p>
      )}

      <div className="space-y-3">
        {visibleRecords.map(record => {
          const draft = drafts[record.registry_id] ?? draftFor(record)
          const eligibleTargets = (records ?? []).filter(
            candidate =>
              candidate.registry_id !== record.registry_id &&
              candidate.bus === record.bus &&
              (candidate.identity_status === 'detached' ||
                (candidate.identity_status === 'confirmed' &&
                  nowMs - candidate.last_seen.getTime() > IDENTITY_ABSENCE_MS))
          )
          const selectedTarget =
            eligibleTargets.find(
              candidate => candidate.registry_id === identityTargets[record.registry_id]
            ) ??
            eligibleTargets.find(
              candidate => candidate.registry_id === record.pending_candidate_registry_id
            ) ??
            eligibleTargets[0]
          return (
            <div
              key={record.registry_id}
              className="rounded border border-border-subtle bg-surface-primary p-4 space-y-3"
            >
              <div className="flex flex-wrap items-center gap-3">
                <span className="rounded bg-surface-tertiary px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                  CAN
                </span>
                <span className="font-mono text-sm text-text-input">
                  {record.hardware_address === null
                    ? 'No current address'
                    : `#${record.hardware_address}`}
                </span>
                <span className="text-sm font-medium text-text-input">{record.display_name}</span>
                <span
                  className={
                    record.status === 'unassigned'
                      ? 'rounded border border-status-warning/70 bg-status-warning-bg/30 px-2 py-0.5 text-xs font-semibold text-status-warning-text'
                      : 'rounded border border-status-success-border/60 bg-status-success-bg/30 px-2 py-0.5 text-xs font-semibold text-status-success-text'
                  }
                >
                  {record.status}
                </span>

                {record.identity_status !== 'confirmed' && (
                  <span className="rounded border border-status-warning/70 bg-status-warning-bg/30 px-2 py-0.5 text-xs font-semibold text-status-warning-text">
                    {record.identity_status === 'pending' ? 'Identity pending' : 'Detached'}
                  </span>
                )}
                {freshnessText(record, nowMs) !== null && (
                  <span className="text-xs font-semibold uppercase tracking-wide text-status-warning-text">
                    Stale
                  </span>
                )}
                <span className="ml-auto text-xs text-text-muted">
                  First seen {formatUtcInstant(record.first_seen)} · Last seen{' '}
                  {formatUtcInstant(record.last_seen)}
                </span>
              </div>

              {record.identity_status === 'pending' ? (
                <div className="flex flex-wrap items-end gap-3">
                  {eligibleTargets.length > 0 ? (
                    <>
                      <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                        Same sensor as
                        <select
                          aria-label={`Previous identity for ${record.display_name}`}
                          className="rounded border border-border-subtle bg-surface-secondary px-2 py-1.5 text-sm normal-case tracking-normal text-text-input"
                          value={selectedTarget?.registry_id ?? ''}
                          onChange={event =>
                            setIdentityTargets(previous => ({
                              ...previous,
                              [record.registry_id]: Number(event.target.value),
                            }))
                          }
                        >
                          {eligibleTargets.map(candidate => (
                            <option key={candidate.registry_id} value={candidate.registry_id}>
                              #{candidate.registry_id} · {candidate.display_name} ·{' '}
                              {candidate.hardware_address === null
                                ? 'detached'
                                : `address ${candidate.hardware_address}`}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        disabled={savingIds.has(record.registry_id) || selectedTarget === undefined}
                        onClick={() => {
                          if (selectedTarget !== undefined) {
                            void resolveIdentity(record, {
                              decision: 'same',
                              target_registry_id: selectedTarget.registry_id,
                            })
                          }
                        }}
                        className="rounded border border-btn-primary-light bg-btn-primary-light/20 px-3 py-1.5 text-sm font-semibold text-btn-primary-text disabled:opacity-50"
                      >
                        Same sensor as {selectedTarget?.display_name ?? 'selected identity'}
                      </button>
                    </>
                  ) : (
                    <p className="text-sm text-text-muted">
                      No older identity is eligible to merge. Confirm this as a new sensor if it is
                      a replacement.
                    </p>
                  )}
                  <button
                    type="button"
                    disabled={savingIds.has(record.registry_id)}
                    onClick={() =>
                      void resolveIdentity(record, {
                        decision: 'new',
                      })
                    }
                    className="rounded border border-border-subtle bg-surface-secondary px-3 py-1.5 text-sm font-semibold text-text-input disabled:opacity-50"
                  >
                    New sensor
                  </button>
                </div>
              ) : record.identity_status === 'detached' ? (
                <p className="text-sm text-text-muted">
                  This identity has no current hardware address and can only be selected when
                  confirming a pending sensor.
                </p>
              ) : (
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
                    Room
                    <select
                      className="rounded border border-border-subtle bg-surface-secondary px-2 py-1.5 text-sm normal-case tracking-normal text-text-input"
                      value={draft.room}
                      onChange={event => {
                        const room = event.target.value
                        const positions = sensorSubclustersFor(room)
                          .map(canSlugFor)
                          .filter((slug): slug is NonNullable<typeof slug> => slug !== null)
                        const keepsDraft = positions.includes(draft.location)
                        updateDraft(record.registry_id, {
                          room,
                          location: keepsDraft
                            ? draft.location
                            : (positions[positions.length - 1] ?? 'main'),
                        })
                      }}
                    >
                      {knownRooms().map(room => (
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
                      onChange={event =>
                        updateDraft(record.registry_id, {
                          location: event.target.value as Draft['location'],
                        })
                      }
                    >
                      {sensorSubclustersFor(draft.room)
                        .map(canSlugFor)
                        .filter((slug): slug is NonNullable<typeof slug> => slug !== null)
                        .map(position => {
                          const occupied = canPositionOccupied(
                            draft.room,
                            position,
                            record.registry_id
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
              )}

              <div className="flex flex-wrap items-center gap-3">
                {record.identity_status === 'confirmed' && (
                  <button
                    type="button"
                    disabled={savingIds.has(record.registry_id)}
                    onClick={() => void save(record)}
                    className="rounded border border-btn-primary-light bg-btn-primary-light/20 px-3 py-1.5 text-sm font-semibold text-btn-primary-text disabled:opacity-50"
                  >
                    {savingIds.has(record.registry_id) ? 'Saving…' : 'Save assignment'}
                  </button>
                )}
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
    </section>
  )
}
