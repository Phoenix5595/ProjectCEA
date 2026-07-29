import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { knownRooms } from '../../config/clusterTopology'
import { useDeviceRegistry } from '../../hooks/useDeviceRegistry'
import { apiClient } from '../../services/api'
import type { DeviceRegistryEntry } from '../../types/device'
import { extractErrorMessage } from '../../utils/errors'
import { logger } from '../../utils/logger'

const DFR_BOARD_META: Record<number, { i2c_address: string }> = {
  0: { i2c_address: '0x88' },
  1: { i2c_address: '0x89' },
  2: { i2c_address: '0x90' },
}

const DFR_BOARD_IDS = [0, 1, 2]
const DFR_CHANNELS = [0, 1] as const
const ROOM_OPTIONS = knownRooms()

type EditDraft = {
  display_name: string
  room: string
  per_room_index: number
}

function lightKeyOf(entry: DeviceRegistryEntry): string {
  return `${entry.location}\u0000${entry.cluster}\u0000${entry.device_name}`
}

function extractIndex(entry: DeviceRegistryEntry): number {
  const match = entry.device_name.match(/_(\d+)$/)
  return match ? parseInt(match[1], 10) : 0
}

export default function DfrBoardsPanel() {
  const { registry, refresh } = useDeviceRegistry()
  const [workingKey, setWorkingKey] = useState<string | null>(null)
  const [renameDraftByKey, setRenameDraftByKey] = useState<Record<string, string>>({})
  const [editDraftByKey, setEditDraftByKey] = useState<Record<string, EditDraft | null>>({})
  const [testProgressKey, setTestProgressKey] = useState<string | null>(null)
  const [removeConfirmKey, setRemoveConfirmKey] = useState<string | null>(null)

  const lights = useMemo(
    () => registry.filter((e) => e.device_type === 'light' && e.board_id != null && e.dimming_channel != null),
    [registry],
  )

  const lightsByKey = useMemo(() => {
    const m = new Map<string, DeviceRegistryEntry>()
    for (const l of lights) m.set(lightKeyOf(l), l)
    return m
  }, [lights])

  const assignedBoards = useMemo(() => {
    const s = new Set<number>()
    for (const l of lights) if (l.board_id != null) s.add(l.board_id)
    return s
  }, [lights])

  const boardSlots = useMemo(() => {
    const result: Record<number, Record<'0' | '1', DeviceRegistryEntry | null>> = {}
    for (const boardId of DFR_BOARD_IDS) {
      result[boardId] = { '0': null, '1': null }
    }
    for (const l of lights) {
      if (l.board_id == null || l.dimming_channel == null) continue
      if (!(l.board_id in result)) continue
      const chKey = String(l.dimming_channel) as '0' | '1'
      if (chKey in result[l.board_id]) {
        result[l.board_id][chKey] = l
      }
    }
    return result
  }, [lights])

  function indexExistsInRoom(room: string, index: number, excludeKey?: string): boolean {
    return lights
      .filter((l) => l.location === room && lightKeyOf(l) !== excludeKey)
      .some((l) => extractIndex(l) === index)
  }

  function maxIndexForRoom(room: string): number {
    const indices = lights.filter((l) => l.location === room).map(extractIndex)
    return indices.length > 0 ? Math.max(...indices) : 0
  }

  async function saveRename(key: string) {
    const light = lightsByKey.get(key)
    if (!light) return
    const draft = (renameDraftByKey[key] ?? '').trim()
    if (!draft) {
      toast.error('Display name is required')
      return
    }
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(`rename:${key}`)
    try {
      await apiClient.updateDevice(light.device_id, { display_name: draft })
      await refresh()
      toast.success('Light name updated')
    } catch (err) {
      logger.error('Failed to rename light', err)
      toast.error(extractErrorMessage(err, 'Failed to rename light'))
    } finally {
      setWorkingKey(null)
    }
  }

  function openEditForm(key: string) {
    const light = lightsByKey.get(key)
    if (!light) return
    setEditDraftByKey((prev) => ({
      ...prev,
      [key]: {
        display_name: light.display_name ?? '',
        room: light.location,
        per_room_index: extractIndex(light) || 1,
      },
    }))
  }

  function closeEditForm(key: string) {
    setEditDraftByKey((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  function updateEditDraft(key: string, patch: Partial<EditDraft>) {
    setEditDraftByKey((prev) => {
      const current = prev[key]
      if (!current) return prev
      const updated = { ...current, ...patch }
      if (patch.room && patch.room !== current.room) {
        updated.per_room_index = maxIndexForRoom(patch.room) + 1
      }
      return { ...prev, [key]: updated }
    })
  }

  function validateEditIndex(key: string) {
    const draft = editDraftByKey[key]
    if (!draft) return
    if (indexExistsInRoom(draft.room, draft.per_room_index, key)) {
      toast.error(`Index ${draft.per_room_index} already exists in ${draft.room}`)
    }
  }

  async function saveEdit(key: string) {
    const light = lightsByKey.get(key)
    const draft = editDraftByKey[key]
    if (!light || !draft) return
    if (!draft.display_name.trim()) {
      toast.error('Display name is required')
      return
    }
    if (indexExistsInRoom(draft.room, draft.per_room_index, key)) {
      toast.error(`Index ${draft.per_room_index} already exists in ${draft.room}`)
      return
    }
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(`edit:${key}`)
    try {
      const body: Record<string, unknown> = { display_name: draft.display_name.trim() }
      if (draft.room !== light.location) body.room = draft.room
      const oldIndex = extractIndex(light)
      if (draft.per_room_index !== oldIndex) body.per_room_index = draft.per_room_index
      await apiClient.updateDevice(light.device_id, body)
      await refresh()
      closeEditForm(key)
      toast.success('Light updated')
    } catch (err) {
      logger.error('Failed to update light', err)
      toast.error(extractErrorMessage(err, 'Failed to update light'))
    } finally {
      setWorkingKey(null)
    }
  }

  async function testLight(key: string, boardId: number, ch: 0 | 1) {
    const light = lightsByKey.get(key)
    if (!light) {
      toast.error('Light not found')
      return
    }
    const opKey = `test:${boardId}:${ch}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    setTestProgressKey(opKey)
    const progressTimer = window.setTimeout(() => setTestProgressKey(null), 5000)
    try {
      await apiClient.testLight(light.device_id)
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

  async function removeLight(key: string, boardId: number, ch: 0 | 1) {
    const light = lightsByKey.get(key)
    if (!light) {
      toast.error('Light not found')
      return
    }
    const opKey = `remove:${boardId}:${ch}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    try {
      await apiClient.deleteDevice(light.device_id)
      await refresh()
      setRemoveConfirmKey(null)
      toast.success('Light removed')
    } catch (err) {
      logger.error('Failed to remove light', err)
      toast.error(extractErrorMessage(err, 'Failed to remove light'))
    } finally {
      setWorkingKey(null)
    }
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
        {DFR_BOARD_IDS.map((boardId) => {
          const meta = DFR_BOARD_META[boardId]
          const slots = boardSlots[boardId] ?? { '0': null, '1': null }
          const isAvailable = assignedBoards.has(boardId)

          return (
            <div
              key={boardId}
              className="rounded-lg border border-border-subtle bg-surface-primary p-2 flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
                    DFR{boardId}
                  </div>
                  <div className="text-xs text-text-subtle font-mono">
                    {meta?.i2c_address ?? '?'}
                  </div>
                </div>
                <div
                  className={`text-[11px] rounded-full px-2 py-0.5 border ${
                    isAvailable
                      ? 'bg-status-success-bg/30 text-status-success-text border-status-success-border/60'
                      : 'bg-status-danger-bg/30 text-status-danger-text border-status-danger-border/60'
                  }`}
                >
                  {isAvailable ? 'Available' : 'Missing'}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 flex-1 overflow-auto">
                {DFR_CHANNELS.map((ch) => {
                  const chKey = String(ch) as '0' | '1'
                  const assignment = slots[chKey]
                  return renderSlot({
                    boardId,
                    ch,
                    assignment,
                    renameDraftByKey,
                    setRenameDraftByKey,
                    editDraftByKey,
                    updateEditDraft,
                    validateEditIndex,
                    saveEdit,
                    closeEditForm,
                    openEditForm,
                    saveRename,
                    testLight,
                    testProgressKey,
                    removeConfirmKey,
                    setRemoveConfirmKey,
                    removeLight,
                    workingKey,
                  })
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface RenderSlotParams {
  boardId: number
  ch: 0 | 1
  assignment: DeviceRegistryEntry | null
  renameDraftByKey: Record<string, string>
  setRenameDraftByKey: React.Dispatch<React.SetStateAction<Record<string, string>>>
  editDraftByKey: Record<string, EditDraft | null>
  updateEditDraft: (key: string, patch: Partial<EditDraft>) => void
  validateEditIndex: (key: string) => void
  saveEdit: (key: string) => Promise<void>
  closeEditForm: (key: string) => void
  openEditForm: (key: string) => void
  saveRename: (key: string) => Promise<void>
  testLight: (key: string, boardId: number, ch: 0 | 1) => Promise<void>
  testProgressKey: string | null
  removeConfirmKey: string | null
  setRemoveConfirmKey: React.Dispatch<React.SetStateAction<string | null>>
  removeLight: (key: string, boardId: number, ch: 0 | 1) => Promise<void>
  workingKey: string | null
}

function renderSlot(p: RenderSlotParams) {
  const { boardId, ch, assignment } = p
  const key = assignment ? lightKeyOf(assignment) : ''
  const editDraft = key ? p.editDraftByKey[key] : null
  const testKey = `test:${boardId}:${ch}`
  const isTesting = p.testProgressKey === testKey
  const removeKey = `remove:${boardId}:${ch}`
  const isConfirmingRemove = p.removeConfirmKey === removeKey
  const renameValue = key
    ? (p.renameDraftByKey[key] ?? (assignment?.display_name ?? assignment?.device_name ?? ''))
    : ''

  return (
    <div
      key={ch}
      data-testid={`dfr-slot-${boardId}-${ch}`}
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

      {assignment && key ? (
        <div className="space-y-1">
          {editDraft ? (
            <div data-testid={`edit-form-${key}`} className="space-y-1">
              <div className="text-[11px] text-text-subtle">Display name</div>
              <input
                value={editDraft.display_name}
                onChange={(e) => p.updateEditDraft(key, { display_name: e.target.value })}
                className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
                placeholder="Light name"
              />
              <div className="text-[11px] text-text-subtle">Room</div>
              <select
                value={editDraft.room}
                onChange={(e) => p.updateEditDraft(key, { room: e.target.value })}
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
                onBlur={() => p.validateEditIndex(key)}
                onChange={(e) => p.updateEditDraft(key, { per_room_index: parseInt(e.target.value, 10) || 1 })}
                className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light"
              />
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => void p.saveEdit(key)}
                  disabled={!!p.workingKey}
                  className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => p.closeEditForm(key)}
                  disabled={!!p.workingKey}
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
                    p.setRenameDraftByKey((prev) => ({ ...prev, [key]: e.target.value }))
                  }
                  disabled={!!p.workingKey}
                  className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                  placeholder="Light name"
                />
                <button
                  type="button"
                  onClick={() => void p.saveRename(key)}
                  disabled={!!p.workingKey}
                  className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                >
                  Save
                </button>
              </div>
              <div className="flex items-center gap-1 pt-1">
                <button
                  type="button"
                  data-testid={`edit-btn-${key}`}
                  onClick={() => p.openEditForm(key)}
                  disabled={!!p.workingKey}
                  className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                >
                  Edit
                </button>
                <button
                  type="button"
                  data-testid={`test-btn-${boardId}-${ch}`}
                  onClick={() => void p.testLight(key, boardId, ch)}
                  disabled={!!p.workingKey || isTesting}
                  className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                >
                  {isTesting ? 'Testing…' : 'Test'}
                </button>
                {isTesting && (
                  <div
                    data-testid={`test-progress-${boardId}-${ch}`}
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
                      data-testid={`remove-confirm-${boardId}-${ch}`}
                      onClick={() => void p.removeLight(key, boardId, ch)}
                      disabled={!!p.workingKey}
                      className="rounded-md bg-status-danger-bg/60 px-2 py-1 text-xs font-medium text-status-danger-text hover:bg-status-danger-bg/80 disabled:opacity-50"
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      onClick={() => p.setRemoveConfirmKey(null)}
                      disabled={!!p.workingKey}
                      className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    data-testid={`remove-btn-${boardId}-${ch}`}
                    onClick={() => p.setRemoveConfirmKey(removeKey)}
                    disabled={!!p.workingKey}
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
