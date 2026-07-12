import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { knownRooms } from '../../config/clusterTopology'
import { apiClient } from '../../services/api'
import type { LightDevice } from '../../types/light'
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

const ROOM_OPTIONS = knownRooms()

type EditDraft = {
  display_name: string
  room: string
  per_room_index: number
}

function makeLightKey(light: { location: string; cluster: string; device_name: string }): string {
  return `${light.location}\u0000${light.cluster}\u0000${light.device_name}`
}

export default function DfrBoardsPanel({
  refreshKey = 0,
  onRefresh,
}: {
  refreshKey?: number
  onRefresh?: () => void
}) {
  const [data, setData] = useState<DfrAssignmentsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workingKey, setWorkingKey] = useState<string | null>(null)
  const [renameDraftByLightKey, setRenameDraftByLightKey] = useState<Record<string, string>>({})
  const [editDraftByLightKey, setEditDraftByLightKey] = useState<Record<string, EditDraft | null>>({})
  const [testProgressKey, setTestProgressKey] = useState<string | null>(null)
  const [removeConfirmKey, setRemoveConfirmKey] = useState<string | null>(null)
  const [roomLightsCache, setRoomLightsCache] = useState<Record<string, LightDevice[]>>({})

  const deviceIdByKey = useMemo(() => {
    const m = new Map<string, number>()
    for (const lights of Object.values(roomLightsCache)) {
      for (const l of lights) {
        if (l.device_id != null) {
          m.set(makeLightKey(l), l.device_id)
        }
      }
    }
    return m
  }, [roomLightsCache])

  const refresh = useCallback(async () => {
    setLoading(true)
    setRoomLightsCache({})
    try {
      const res = await apiClient.getDfrAssignments()
      setData(res)
      const rooms = new Set<string>()
      for (const l of res.lights ?? []) {
        rooms.add(l.location)
      }
      const entries = await Promise.all(
        [...rooms].map(async (room): Promise<readonly [string, LightDevice[]]> => {
          try {
            const lights = await apiClient.getLightsByRoom(room)
            return [room, lights] as const
          } catch {
            return [room, [] as LightDevice[]] as const
          }
        })
      )
      const cache: Record<string, LightDevice[]> = {}
      for (const [room, lights] of entries) {
        cache[room] = lights
      }
      setRoomLightsCache(cache)
    } catch (err) {
      logger.error('Failed to load DFR assignments', err)
      toast.error('Failed to load DFR boards')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh, refreshKey])

  const lightsByKey = useMemo(() => {
    const m = new Map<string, DfrLight>()
    for (const l of data?.lights ?? []) {
      m.set(makeLightKey(l), l)
    }
    return m
  }, [data?.lights])

  const fetchRoomLights = useCallback(async (room: string): Promise<LightDevice[]> => {
    if (roomLightsCache[room]) {
      return roomLightsCache[room]
    }
    try {
      const lights = await apiClient.getLightsByRoom(room)
      setRoomLightsCache((prev) => ({ ...prev, [room]: lights }))
      return lights
    } catch (err) {
      logger.error(`Failed to fetch lights for room ${room}`, err)
      return []
    }
  }, [roomLightsCache])

  const maxIndexForRoom = useCallback((room: string): number => {
    const fromData = (data?.lights ?? [])
      .filter((l) => l.location === room)
      .map((l) => {
        const match = l.device_name.match(/_(\d+)$/)
        return match ? parseInt(match[1], 10) : 0
      })
    const fromCache = (roomLightsCache[room] ?? [])
      .map((l) => l.per_room_index ?? 0)
    const all = [...fromData, ...fromCache]
    return all.length > 0 ? Math.max(...all) : 0
  }, [data?.lights, roomLightsCache])

  const indexExistsInRoom = useCallback((room: string, index: number, excludeLightKey?: string): boolean => {
    const fromData = (data?.lights ?? [])
      .filter((l) => l.location === room && makeLightKey(l) !== excludeLightKey)
      .some((l) => {
        const match = l.device_name.match(/_(\d+)$/)
        return match ? parseInt(match[1], 10) === index : false
      })
    if (fromData) return true
    const fromCache = (roomLightsCache[room] ?? [])
      .filter((l) => excludeLightKey ? makeLightKey(l) !== excludeLightKey : true)
      .some((l) => l.per_room_index === index)
    return fromCache
  }, [data?.lights, roomLightsCache])

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
      onRefresh?.()
      toast.success('Light name updated')
    } catch (err) {
      logger.error('Failed to rename light', err)
      toast.error('Failed to rename light')
    } finally {
      setWorkingKey(null)
    }
  }

  function openEditForm(lightKey: string) {
    const light = lightsByKey.get(lightKey)
    if (!light) return
    const match = light.device_name.match(/_(\d+)$/)
    const currentIndex = match ? parseInt(match[1], 10) : 1
    setEditDraftByLightKey((prev) => ({
      ...prev,
      [lightKey]: {
        display_name: light.display_name ?? '',
        room: light.location,
        per_room_index: currentIndex,
      },
    }))
    void fetchRoomLights(light.location)
  }

  function closeEditForm(lightKey: string) {
    setEditDraftByLightKey((prev) => {
      const next = { ...prev }
      delete next[lightKey]
      return next
    })
  }

  function updateEditDraft(lightKey: string, patch: Partial<EditDraft>) {
    setEditDraftByLightKey((prev) => {
      const current = prev[lightKey]
      if (!current) return prev
      const updated = { ...current, ...patch }
      if (patch.room && patch.room !== current.room) {
        updated.per_room_index = maxIndexForRoom(patch.room) + 1
        void fetchRoomLights(patch.room)
      }
      return { ...prev, [lightKey]: updated }
    })
  }

  function validateEditIndex(lightKey: string) {
    const draft = editDraftByLightKey[lightKey]
    if (!draft) return
    if (indexExistsInRoom(draft.room, draft.per_room_index, lightKey)) {
      toast.error(`Index ${draft.per_room_index} already exists in ${draft.room}`)
    }
  }

  async function saveEdit(lightKey: string) {
    const light = lightsByKey.get(lightKey)
    const draft = editDraftByLightKey[lightKey]
    if (!light || !draft) return
    if (!draft.display_name.trim()) {
      toast.error('Display name is required')
      return
    }
    if (indexExistsInRoom(draft.room, draft.per_room_index, lightKey)) {
      toast.error(`Index ${draft.per_room_index} already exists in ${draft.room}`)
      return
    }
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    const deviceId = deviceIdByKey.get(lightKey)
    if (deviceId == null) {
      toast.error('Light device ID not found — refresh and try again')
      return
    }
    const opKey = `edit:${lightKey}`
    setWorkingKey(opKey)
    try {
      const match = light.device_name.match(/_(\d+)$/)
      const oldIndex = match ? parseInt(match[1], 10) : 0
      const body: Parameters<typeof apiClient.updateLight>[1] = {
        display_name: draft.display_name.trim(),
      }
      if (draft.room !== light.location) {
        body.room = draft.room
      }
      if (draft.per_room_index !== oldIndex) {
        body.per_room_index = draft.per_room_index
      }
      await apiClient.updateLight(deviceId, body)
      await refresh()
      onRefresh?.()
      closeEditForm(lightKey)
      toast.success('Light updated')
    } catch (err) {
      logger.error('Failed to update light', err)
      toast.error(extractErrorMessage(err, 'Failed to update light'))
    } finally {
      setWorkingKey(null)
    }
  }

  async function testLight(boardId: number, channel: 0 | 1) {
    const channelKey: '0' | '1' = channel === 0 ? '0' : '1'
    const assignment = data?.assignments?.[String(boardId)]?.[channelKey] ?? null
    if (!assignment) {
      toast.error('No light assigned to this channel')
      return
    }
    const lightKey = makeLightKey(assignment)
    const light = lightsByKey.get(lightKey)
    if (!light) {
      toast.error('Light not found')
      return
    }
    const deviceId = deviceIdByKey.get(lightKey)
    if (deviceId == null) {
      toast.error('Light device ID not found — refresh and try again')
      return
    }
    const opKey = `test:${boardId}:${channel}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    setTestProgressKey(opKey)
    const progressTimer = window.setTimeout(() => {
      setTestProgressKey(null)
    }, 5000)
    try {
      await apiClient.testLight(deviceId)
      toast.success('Light test completed')
    } catch (err) {
      logger.error('Failed to test light', err)
      toast.error(extractErrorMessage(err, 'Failed to test light'))
    } finally {
      window.clearTimeout(progressTimer)
      setTestProgressKey(null)
      setWorkingKey(null)
    }
  }

  async function removeLight(boardId: number, channel: 0 | 1) {
    const channelKey: '0' | '1' = channel === 0 ? '0' : '1'
    const assignment = data?.assignments?.[String(boardId)]?.[channelKey] ?? null
    if (!assignment) {
      toast.error('No light assigned to this channel')
      return
    }
    const lightKey = makeLightKey(assignment)
    const light = lightsByKey.get(lightKey)
    if (!light) {
      toast.error('Light not found')
      return
    }
    const deviceId = deviceIdByKey.get(lightKey)
    if (deviceId == null) {
      toast.error('Light device ID not found — refresh and try again')
      return
    }
    const opKey = `remove:${boardId}:${channel}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    try {
      const result = await apiClient.deleteLight(deviceId)
      await refresh()
      onRefresh?.()
      setRemoveConfirmKey(null)
      if (result.warning) {
        toast.success(`Light removed — ${result.warning}`)
      } else {
        toast.success('Light removed')
      }
    } catch (err) {
      logger.error('Failed to remove light', err)
      toast.error(extractErrorMessage(err, 'Failed to remove light'))
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
            const assignedLight = assignment ? lightsByKey.get(makeLightKey(assignment)) : null
            const renameKey = assignment ? makeLightKey(assignment) : ''
            const renameValue =
              renameDraftByLightKey[renameKey] ??
              (assignedLight?.display_name ?? assignment?.display_name ?? assignment?.device_name ?? '')
            const editKey = assignment ? makeLightKey(assignment) : ''
            const editDraft = editDraftByLightKey[editKey]
            const testKey = `test:${board.board_id}:${ch}`
            const isTesting = testProgressKey === testKey
            const removeKey = `remove:${board.board_id}:${ch}`
            const isConfirmingRemove = removeConfirmKey === removeKey

            return (
              <div
                data-testid={`dfr-slot-${board.board_id}-${ch}`}
                className="rounded-md border border-border-subtle bg-surface-secondary p-2 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold text-text-default">CH{ch}</div>
                  {assignment ? (
                    <span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2 py-0.5 text-xs font-medium text-btn-primary-text">
                      {assignment.location}
                    </span>
                  ) : (
                    <span className="text-xs text-text-subtle">—</span>
                  )}
                </div>

                {assignment ? (
                  <div className="space-y-1">
                    {editDraft ? (
                      <div data-testid={`edit-form-${editKey}`} className="space-y-1">
                        <div className="text-[11px] text-text-subtle">Display name</div>
                        <input
                          value={editDraft.display_name}
                          onChange={(e) => updateEditDraft(editKey, { display_name: e.target.value })}
                          className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                          placeholder="Light name"
                        />
                        <div className="text-[11px] text-text-subtle">Room</div>
                        <select
                          value={editDraft.room}
                          onChange={(e) => updateEditDraft(editKey, { room: e.target.value })}
                          className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                        >
                          {ROOM_OPTIONS.map((room) => (
                            <option key={room} value={room}>{room}</option>
                          ))}
                        </select>
                        <div className="text-[11px] text-text-subtle">Per-room index</div>
                        <input
                          type="number"
                          min={1}
                          value={editDraft.per_room_index}
                          onBlur={() => validateEditIndex(editKey)}
                          onChange={(e) => updateEditDraft(editKey, { per_room_index: parseInt(e.target.value, 10) || 1 })}
                          className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                        />
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => void saveEdit(editKey)}
                            disabled={!!workingKey}
                            className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => closeEditForm(editKey)}
                            disabled={!!workingKey}
                            className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
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
                            disabled={!!workingKey}
                            className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                            placeholder="Light name"
                          />
                          <button
                            type="button"
                            onClick={() => void saveRename(renameKey)}
                            disabled={!!workingKey}
                            className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                          >
                            Save
                          </button>
                        </div>
                        <div className="flex items-center gap-1 pt-1">
                          <button
                            type="button"
                            data-testid={`edit-btn-${editKey}`}
                            onClick={() => openEditForm(editKey)}
                            disabled={!!workingKey}
                            className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            data-testid={`test-btn-${board.board_id}-${ch}`}
                            onClick={() => void testLight(board.board_id, ch)}
                            disabled={!!workingKey || isTesting}
                            className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                          >
                            {isTesting ? 'Testing…' : 'Test'}
                          </button>
                          {isTesting && (
                            <div
                              data-testid={`test-progress-${board.board_id}-${ch}`}
                              className="flex-1 h-1 rounded-full bg-border-emphasis overflow-hidden"
                            >
                              <div
                                className="h-full bg-btn-primary-light transition-all"
                                style={{ width: '100%', animation: 'dfr-test-progress 5s linear' }}
                              />
                            </div>
                          )}
                          {isConfirmingRemove ? (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                data-testid={`remove-confirm-${board.board_id}-${ch}`}
                                onClick={() => void removeLight(board.board_id, ch)}
                                disabled={!!workingKey}
                                className="rounded-md bg-status-danger-bg/60 px-2 py-1 text-xs font-medium text-status-danger-text hover:bg-status-danger-bg/80 disabled:opacity-50"
                              >
                                Confirm
                              </button>
                              <button
                                type="button"
                                onClick={() => setRemoveConfirmKey(null)}
                                disabled={!!workingKey}
                                className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              data-testid={`remove-btn-${board.board_id}-${ch}`}
                              onClick={() => setRemoveConfirmKey(removeKey)}
                              disabled={!!workingKey}
                              className="rounded-md border border-status-danger-border/60 bg-surface-primary px-2 py-1 text-xs text-status-danger-text hover:bg-status-danger-bg/30 disabled:opacity-50"
                            >
                              Remove
                            </button>
                          )}
                        </div>
                        {isConfirmingRemove && (
                          <div className="text-[11px] text-status-danger-text">
                            Remove light? (Its relay will also be unbound.)
                          </div>
                        )}
                        <div className="text-[11px] text-text-subtle">
                          Device key: <span className="font-mono">{assignment.device_name}</span>
                        </div>
                      </>
                    )}
                  </div>
                ) : null}
              </div>
            )
          }

          return (
            <div
              key={board.board_id}
              className="rounded-lg border border-border-subtle bg-surface-primary p-2 flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
                    DFR{board.board_id}
                  </div>
                  <div className="text-xs text-text-subtle font-mono">
                    {board.i2c_address}
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

              <div className="grid grid-cols-2 gap-2 flex-1 overflow-auto">
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
