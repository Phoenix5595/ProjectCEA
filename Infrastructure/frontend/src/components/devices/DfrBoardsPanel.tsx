import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { apiClient } from '../../services/api'
import { extractErrorMessage } from '../../utils/errors'
import { logger } from '../../utils/logger'

type DfrBoard = {
  board_id: number
  i2c_address: string
  name?: string
  available?: boolean
}

type DfrLight = {
  location: string
  cluster: string
  device_name: string
  display_name?: string | null
  dimming_board_id?: number | null
  dimming_channel?: number | null
}

type DfrAssignment = {
  location: string
  cluster: string
  device_name: string
  display_name?: string | null
}

type DfrAssignmentsResponse = {
  boards: DfrBoard[]
  assignments: Record<string, { '0': DfrAssignment | null; '1': DfrAssignment | null }>
  lights: DfrLight[]
}

function makeLightKey(light: { location: string; cluster: string; device_name: string }): string {
  return `${light.location}\u0000${light.cluster}\u0000${light.device_name}`
}

function parseLightKey(key: string): { location: string; cluster: string; device_name: string } | null {
  const parts = key.split('\u0000')
  if (parts.length !== 3) return null
  return { location: parts[0], cluster: parts[1], device_name: parts[2] }
}

export default function DfrBoardsPanel() {
  const [data, setData] = useState<DfrAssignmentsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workingKey, setWorkingKey] = useState<string | null>(null)
  const [renameDraftByLightKey, setRenameDraftByLightKey] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.getDfrAssignments()
      setData(res)
    } catch (err) {
      logger.error('Failed to load DFR assignments', err)
      toast.error('Failed to load DFR boards')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const lightsByKey = useMemo(() => {
    const m = new Map<string, DfrLight>()
    for (const l of data?.lights ?? []) {
      m.set(makeLightKey(l), l)
    }
    return m
  }, [data?.lights])

  const lightOptions = useMemo(() => {
    const lights = [...(data?.lights ?? [])]
    lights.sort((a, b) => {
      const la = `${a.location} ${a.display_name || a.device_name}`
      const lb = `${b.location} ${b.display_name || b.device_name}`
      return la.localeCompare(lb)
    })
    return lights.map((l) => ({
      key: makeLightKey(l),
      label: `${l.display_name || l.device_name} — ${l.location}`,
    }))
  }, [data?.lights])

  async function applyAssignment(
    boardId: number,
    channel: 0 | 1,
    nextLightKey: string | null
  ) {
    const channelKey: '0' | '1' = channel === 0 ? '0' : '1'
    const assignment = data?.assignments?.[String(boardId)]?.[channelKey] ?? null
    const currentKey = assignment ? makeLightKey(assignment) : null
    const opKey = `board:${boardId}:ch:${channel}`

    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }

    setWorkingKey(opKey)
    try {
      // If a different light is currently occupying this channel, clear it first.
      if (currentKey && currentKey !== nextLightKey) {
        const current = parseLightKey(currentKey)
        if (current) {
          await apiClient.assignDfrChannel(current.location, current.cluster, current.device_name, null, null)
        }
      }

      if (nextLightKey) {
        const next = parseLightKey(nextLightKey)
        if (!next) {
          toast.error('Invalid light selection')
          return
        }
        await apiClient.assignDfrChannel(next.location, next.cluster, next.device_name, boardId, channel)
      }

      await refresh()
      toast.success('DFR assignment updated')
    } catch (err) {
      logger.error('Failed to update DFR assignment', err)
      toast.error(extractErrorMessage(err, 'Failed to update DFR assignment'))
      await refresh()
    } finally {
      setWorkingKey(null)
    }
  }

  async function saveRename(lightKey: string) {
    const light = lightsByKey.get(lightKey)
    if (!light) return
    const draft = (renameDraftByLightKey[lightKey] ?? '').trim()
    if (!draft) {
      toast.error('Display name is required')
      return
    }
    const opKey = `rename:${lightKey}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    try {
      await apiClient.updateDeviceConfig(light.location, light.cluster, light.device_name, draft)
      await refresh()
      toast.success('Light name updated')
    } catch (err) {
      logger.error('Failed to rename light', err)
      toast.error('Failed to rename light')
    } finally {
      setWorkingKey(null)
    }
  }

  if (loading && !data) {
    return (
      <div className="rounded-lg border border-border-subtle bg-surface-primary p-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-2">
          DFR0971 boards
        </div>
        <div className="text-text-subtle text-sm">Loading…</div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border-subtle bg-surface-primary p-2 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
          DFR0971 boards
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-md border border-border-emphasis bg-surface-secondary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary"
        >
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {(data?.boards ?? []).map((board) => {
          const boardKey = String(board.board_id)
          const a0 = data?.assignments?.[boardKey]?.['0'] ?? null
          const a1 = data?.assignments?.[boardKey]?.['1'] ?? null

          const renderChannel = (ch: 0 | 1, assignment: DfrAssignment | null) => {
            const selected = assignment ? makeLightKey(assignment) : ''
            const assignedLight = assignment ? lightsByKey.get(makeLightKey(assignment)) : null
            const renameKey = assignment ? makeLightKey(assignment) : ''
            const renameValue =
              renameDraftByLightKey[renameKey] ??
              (assignedLight?.display_name ?? assignment?.display_name ?? assignment?.device_name ?? '')

            return (
              <div className="rounded-md border border-border-subtle bg-surface-secondary p-2 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold text-text-default">Channel {ch}</div>
                  <div className="text-[11px] text-text-subtle">
                    {assignment ? `${assignment.location}` : 'Unassigned'}
                  </div>
                </div>

                <select
                  value={selected}
                  onChange={(e) => void applyAssignment(board.board_id, ch, e.target.value || null)}
                  className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                >
                  <option value="">Unassigned</option>
                  {lightOptions.map((opt) => (
                    <option key={opt.key} value={opt.key}>
                      {opt.label}
                    </option>
                  ))}
                </select>

                {assignment ? (
                  <div className="space-y-1">
                    <div className="text-[11px] text-text-subtle">Display name</div>
                    <div className="flex items-center gap-2">
                      <input
                        value={renameValue}
                        onChange={(e) =>
                          setRenameDraftByLightKey((prev) => ({
                            ...prev,
                            [renameKey]: e.target.value,
                          }))
                        }
                        className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                        placeholder="Light name"
                      />
                      <button
                        type="button"
                        onClick={() => void saveRename(renameKey)}
                        className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover"
                      >
                        Save
                      </button>
                    </div>
                    <div className="text-[11px] text-text-subtle">
                      Device key: <span className="font-mono">{assignment.device_name}</span>
                    </div>
                  </div>
                ) : null}
              </div>
            )
          }

          return (
            <div
              key={board.board_id}
              className="aspect-square rounded-lg border border-border-subtle bg-surface-primary p-2 flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold text-text-default">
                    {board.name || `Board ${board.board_id}`}
                  </div>
                  <div className="text-xs text-text-subtle">
                    ID {board.board_id} • {board.i2c_address}
                  </div>
                </div>
                <div
                  className={`text-[11px] rounded-full px-2 py-0.5 border ${
                    board.available
                      ? 'bg-status-success-bg/30 text-status-success-text border-status-success-border/60'
                      : 'bg-status-danger-bg/30 text-status-danger-text border-status-danger-border/60'
                  }`}
                >
                  {board.available ? 'Available' : 'Missing'}
                </div>
              </div>

              <div className="grid grid-cols-1 gap-2 flex-1 overflow-auto">
                {renderChannel(0, a0)}
                {renderChannel(1, a1)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

