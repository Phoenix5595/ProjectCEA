import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { ZONES } from '../../config/zones'
import { apiClient } from '../../services/api'
import { useDeviceRegistry } from '../../hooks/useDeviceRegistry'
import { DEVICE_TYPES } from '../../types/relay'
import type { DeviceRegistryEntry } from '../../types/device'
import { extractErrorMessage } from '../../utils/errors'
import { logger } from '../../utils/logger'
import { getReadableDeviceType } from './relayViewModel'

const RELAY_CHANNELS = Array.from({ length: 16 }, (_, i) => i)
const DFR_BOARDS = [0, 1, 2]
const DFR_CHANNELS = [0, 1]

interface DeviceForm {
  room: string
  device_type: string
  display_name: string
  channel: string
  board_id: string
  dimming_channel: string
}

const EMPTY_FORM: DeviceForm = {
  room: ZONES[0]?.location ?? '',
  device_type: '',
  display_name: '',
  channel: '',
  board_id: '0',
  dimming_channel: '0',
}

function isLight(device: DeviceRegistryEntry): boolean {
  return device.device_type === 'light'
}

function relayChannelOf(device: DeviceRegistryEntry): number | null {
  if (device.channel != null) return device.channel
  return device.relay_channel ?? null
}

export default function DeviceTable({
  refreshKey = 0,
  onRefresh,
}: {
  refreshKey?: number
  onRefresh?: () => void
}) {
  const { registry: devicesFromHook, loading: hookLoading, refresh: hookRefresh } = useDeviceRegistry()
  const [devices, setDevices] = useState<DeviceRegistryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<DeviceForm>(EMPTY_FORM)
  const [adding, setAdding] = useState(false)
  const [addForm, setAddForm] = useState<DeviceForm>(EMPTY_FORM)
  const [working, setWorking] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [displacedDeviceId, setDisplacedDeviceId] = useState<number | null>(null)
  const [sortConfig, setSortConfig] = useState<{
    key: string | null
    direction: 'asc' | 'desc' | null
  }>({ key: null, direction: null })

  useEffect(() => {
    setSortConfig({ key: null, direction: null })
  }, [refreshKey])

  useEffect(() => {
    setDevices(devicesFromHook)
    setLoading(hookLoading)
  }, [devicesFromHook, hookLoading])

  const refresh = useCallback(async () => {
    await hookRefresh()
    onRefresh?.()
  }, [hookRefresh, onRefresh])

  useEffect(() => {
    void refresh()
  }, [refresh, refreshKey])

  const sortedDevices = useMemo(() => {
    if (sortConfig.key === null || sortConfig.direction === null) {
      return devices
    }

    const sorted = [...devices]
    const dir = sortConfig.direction === 'asc' ? 1 : -1

    sorted.sort((a, b) => {
      switch (sortConfig.key) {
        case 'name': {
          const av = (a.display_name ?? a.device_name).toLowerCase()
          const bv = (b.display_name ?? b.device_name).toLowerCase()
          return av < bv ? -dir : av > bv ? dir : 0
        }
        case 'type': {
          const av = a.device_type.toLowerCase()
          const bv = b.device_type.toLowerCase()
          return av < bv ? -dir : av > bv ? dir : 0
        }
        case 'room': {
          const av = a.location.toLowerCase()
          const bv = b.location.toLowerCase()
          return av < bv ? -dir : av > bv ? dir : 0
        }
        case 'relayCh': {
          const av = relayChannelOf(a)
          const bv = relayChannelOf(b)
          if (av == null && bv == null) return 0
          if (av == null) return 1
          if (bv == null) return -1
          return (av - bv) * dir
        }
        case 'dfrBoard': {
          const aLight = isLight(a)
          const bLight = isLight(b)
          if (!aLight && !bLight) return 0
          if (!aLight) return 1
          if (!bLight) return -1
          const av = a.board_id ?? null
          const bv = b.board_id ?? null
          if (av == null && bv == null) return 0
          if (av == null) return 1
          if (bv == null) return -1
          return (av - bv) * dir
        }
        case 'dfrChannel': {
          const aLight = isLight(a)
          const bLight = isLight(b)
          if (!aLight && !bLight) return 0
          if (!aLight) return 1
          if (!bLight) return -1
          const av = a.dimming_channel ?? null
          const bv = b.dimming_channel ?? null
          if (av == null && bv == null) return 0
          if (av == null) return 1
          if (bv == null) return -1
          return (av - bv) * dir
        }
        default:
          return 0
      }
    })

    return sorted
  }, [devices, sortConfig])

  function toggleSort(key: string) {
    setSortConfig((prev) => {
      if (prev.key !== key) {
        return { key, direction: 'asc' }
      }
      if (prev.direction === 'asc') {
        return { key, direction: 'desc' }
      }
      return { key: null, direction: null }
    })
  }

  function sortIndicator(key: string): string {
    if (sortConfig.key !== key || sortConfig.direction === null) return ''
    return sortConfig.direction === 'asc' ? ' ▲' : ' ▼'
  }

  function startEdit(device: DeviceRegistryEntry) {
    if (editingId !== null) {
      toast.error('Save current changes before editing another device')
      return
    }
    const ch = relayChannelOf(device)
    setEditingId(device.device_id)
    setDeleteConfirmId(null)
    setEditForm({
      room: device.location,
      device_type: device.device_type,
      display_name: device.display_name ?? '',
      channel: ch != null ? String(ch) : '',
      board_id: device.board_id != null ? String(device.board_id) : '0',
      dimming_channel: device.dimming_channel != null ? String(device.dimming_channel) : '0',
    })
  }

  function cancelEdit() {
    setEditingId(null)
    setEditForm(EMPTY_FORM)
  }

  function startAdd() {
    if (editingId !== null) {
      toast.error('Save current changes before adding a device')
      return
    }
    setAdding(true)
    setAddForm(EMPTY_FORM)
  }

  function cancelAdd() {
    setAdding(false)
    setAddForm(EMPTY_FORM)
  }

  async function submitAdd() {
    const f = addForm
    if (!f.display_name.trim()) {
      toast.error('Display name is required')
      return
    }
    if (!f.device_type) {
      toast.error('Device type is required')
      return
    }
    if (!f.room) {
      toast.error('Room is required')
      return
    }

    const body: Record<string, unknown> = {
      device_type: f.device_type,
      room: f.room,
      display_name: f.display_name.trim(),
    }

    if (f.device_type === 'light') {
      body.board_id = parseInt(f.board_id, 10)
      body.dimming_channel = parseInt(f.dimming_channel, 10)
      if (f.channel !== '') {
        body.relay_channel = parseInt(f.channel, 10)
      }
    } else {
      body.channel = f.channel === '' ? null : parseInt(f.channel, 10)
    }

    setWorking(true)
    try {
      await apiClient.createDevice(body)
      await refresh()
      onRefresh?.()
      cancelAdd()
      toast.success('Device created')
    } catch (err) {
      logger.error('Failed to create device', err)
      toast.error(extractErrorMessage(err, 'Failed to create device'))
    } finally {
      setWorking(false)
    }
  }

  async function submitEdit(device: DeviceRegistryEntry) {
    const f = editForm
    if (!f.display_name.trim()) {
      toast.error('Display name is required')
      return
    }

    const body: Record<string, unknown> = {
      display_name: f.display_name.trim(),
    }

    if (isLight(device)) {
      if (f.channel !== '') {
        body.relay_channel = parseInt(f.channel, 10)
      } else {
        body.relay_channel = null
      }
      if (f.board_id !== '') {
        body.board_id = parseInt(f.board_id, 10)
      }
      if (f.dimming_channel !== '') {
        body.dimming_channel = parseInt(f.dimming_channel, 10)
      }
    } else {
      if (f.channel !== '') {
        body.channel = parseInt(f.channel, 10)
      } else {
        body.channel = null
      }
    }

    setWorking(true)
    try {
      const result = await apiClient.updateDevice(device.device_id, body)
      if (result.displaced_device_id != null) {
        setDisplacedDeviceId(result.displaced_device_id)
        const displaced = devices.find((d) => d.device_id === result.displaced_device_id)
        const displacedLabel = displaced?.display_name ?? displaced?.device_name ?? `#${result.displaced_device_id}`
        toast.warning(`Relay channel stolen from ${displacedLabel}`)
      } else {
        setDisplacedDeviceId(null)
      }
      await refresh()
      cancelEdit()
      toast.success('Device updated')
    } catch (err) {
      logger.error('Failed to update device', err)
      toast.error(extractErrorMessage(err, 'Failed to update device'))
    } finally {
      setWorking(false)
    }
  }

  async function confirmDelete(device: DeviceRegistryEntry) {
    setWorking(true)
    try {
      await apiClient.deleteDevice(device.device_id)
      await refresh()
      onRefresh?.()
      setDeleteConfirmId(null)
      toast.success('Device deleted')
    } catch (err) {
      logger.error('Failed to delete device', err)
      toast.error(extractErrorMessage(err, 'Failed to delete device'))
    } finally {
      setWorking(false)
    }
  }

  if (loading && devices.length === 0) {
    return (
      <div className="rounded-lg border border-border-subtle bg-surface-primary p-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-2">
          Device Registry
        </div>
        <div className="text-text-subtle text-sm">Loading…</div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border-subtle bg-surface-primary p-2 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
          Device Registry
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={working}
          className="rounded-md border border-border-emphasis bg-surface-secondary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-border-default">
          <thead className="bg-surface-secondary">
            <tr>
              <th
                onClick={() => toggleSort('name')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                Device Name{sortIndicator('name')}
              </th>
              <th
                onClick={() => toggleSort('type')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                Type{sortIndicator('type')}
              </th>
              <th
                onClick={() => toggleSort('room')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                Room{sortIndicator('room')}
              </th>
              <th
                onClick={() => toggleSort('relayCh')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                Relay Ch{sortIndicator('relayCh')}
              </th>
              <th
                onClick={() => toggleSort('dfrBoard')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                DFR Board{sortIndicator('dfrBoard')}
              </th>
              <th
                onClick={() => toggleSort('dfrChannel')}
                className="cursor-pointer px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted hover:bg-surface-tertiary"
              >
                DFR Channel{sortIndicator('dfrChannel')}
              </th>
              <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle bg-surface-primary">
            {sortedDevices.map((device) => {
              const isEditing = editingId === device.device_id
              const isConfirmingDelete = deleteConfirmId === device.device_id
              const light = isLight(device)
              const ch = relayChannelOf(device)

              if (isEditing) {
                return (
                  <tr key={device.device_id} data-testid={`edit-row-${device.device_id}`}>
                    <td className="whitespace-nowrap px-2 py-1">
                      <input
                        type="text"
                        value={editForm.display_name}
                        onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                        disabled={working}
                        className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                        placeholder="Display name"
                      />
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                      {getReadableDeviceType(device.device_type)}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                      {device.location}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1">
                      <select
                        value={editForm.channel}
                        onChange={(e) => setEditForm({ ...editForm, channel: e.target.value })}
                        disabled={working}
                        className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                      >
                        <option value="">No relay</option>
                        {RELAY_CHANNELS.map((c) => (
                          <option key={c} value={c}>R{c + 1}</option>
                        ))}
                      </select>
                    </td>
                    {light ? (
                      <>
                        <td className="whitespace-nowrap px-2 py-1">
                          <select
                            value={editForm.board_id}
                            onChange={(e) => setEditForm({ ...editForm, board_id: e.target.value })}
                            disabled={working}
                            className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                          >
                            {DFR_BOARDS.map((b) => (
                              <option key={b} value={b}>{b}</option>
                            ))}
                          </select>
                        </td>
                        <td className="whitespace-nowrap px-2 py-1">
                          <select
                            value={editForm.dimming_channel}
                            onChange={(e) => setEditForm({ ...editForm, dimming_channel: e.target.value })}
                            disabled={working}
                            className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                          >
                            {DFR_CHANNELS.map((c) => (
                              <option key={c} value={c}>{c}</option>
                            ))}
                          </select>
                        </td>
                      </>
                    ) : (
                      <td className="px-2 py-1" />
                    )}
                    {!light && <td className="px-2 py-1" />}
                    <td className="whitespace-nowrap px-2 py-1">
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          data-testid={`edit-save-${device.device_id}`}
                          onClick={() => void submitEdit(device)}
                          disabled={working}
                          className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          data-testid={`edit-cancel-${device.device_id}`}
                          onClick={cancelEdit}
                          disabled={working}
                          className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              }

              return (
                <tr
                  key={device.device_id}
                  data-testid={`device-row-${device.device_id}`}
                  className={`hover:bg-surface-secondary cursor-pointer ${device.device_id === displacedDeviceId ? 'ring-2 ring-status-danger' : ''}`}
                  onClick={() => startEdit(device)}
                >
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-input">
                    {device.display_name ?? device.device_name}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    <span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2.5 py-0.5 text-xs font-medium text-btn-primary-text">
                      {getReadableDeviceType(device.device_type)}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    {device.location}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    {ch != null ? `R${ch + 1}` : '—'}
                  </td>
                  {light ? (
                    <>
                      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                        {device.board_id ?? '—'}
                      </td>
                      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                        {device.dimming_channel ?? '—'}
                      </td>
                    </>
                  ) : (
                    <td className="px-2 py-1 text-sm text-text-subtle">—</td>
                  )}
                  {!light && <td className="px-2 py-1 text-sm text-text-subtle">—</td>}
                  <td className="whitespace-nowrap px-2 py-1" onClick={(e) => e.stopPropagation()}>
                    {isConfirmingDelete ? (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          data-testid={`delete-confirm-${device.device_id}`}
                          onClick={() => void confirmDelete(device)}
                          disabled={working}
                          className="rounded-md bg-status-danger-bg/60 px-2 py-1 text-xs font-medium text-status-danger-text hover:bg-status-danger-bg/80 disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          data-testid={`delete-cancel-${device.device_id}`}
                          onClick={() => setDeleteConfirmId(null)}
                          disabled={working}
                          className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        data-testid={`delete-btn-${device.device_id}`}
                        onClick={() => setDeleteConfirmId(device.device_id)}
                        disabled={working}
                        className="rounded-md border border-status-danger-border/60 bg-surface-primary px-2 py-1 text-xs text-status-danger-text hover:bg-status-danger-bg/30 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}

            {adding && (
              <tr data-testid="add-row">
                <td className="whitespace-nowrap px-2 py-1">
                  <input
                    type="text"
                    value={addForm.display_name}
                    onChange={(e) => setAddForm({ ...addForm, display_name: e.target.value })}
                    disabled={working}
                    className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                    placeholder="Display name"
                  />
                </td>
                <td className="whitespace-nowrap px-2 py-1">
                  <select
                    value={addForm.device_type}
                    onChange={(e) => setAddForm({ ...addForm, device_type: e.target.value })}
                    disabled={working}
                    className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                  >
                    <option value="">Select type</option>
                    {DEVICE_TYPES.map((t) => (
                      <option key={t} value={t}>{getReadableDeviceType(t)}</option>
                    ))}
                  </select>
                </td>
                <td className="whitespace-nowrap px-2 py-1">
                  <select
                    value={addForm.room}
                    onChange={(e) => setAddForm({ ...addForm, room: e.target.value })}
                    disabled={working}
                    className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                  >
                    <option value="">Select room</option>
                    {ZONES.map((z) => (
                      <option key={z.location} value={z.location}>{z.location}</option>
                    ))}
                  </select>
                </td>
                <td className="whitespace-nowrap px-2 py-1">
                  <select
                    value={addForm.channel}
                    onChange={(e) => setAddForm({ ...addForm, channel: e.target.value })}
                    disabled={working}
                    className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                  >
                    <option value="">No relay</option>
                    {RELAY_CHANNELS.map((c) => (
                      <option key={c} value={c}>R{c + 1}</option>
                    ))}
                  </select>
                </td>
                {addForm.device_type === 'light' ? (
                  <>
                    <td className="whitespace-nowrap px-2 py-1">
                      <select
                        value={addForm.board_id}
                        onChange={(e) => setAddForm({ ...addForm, board_id: e.target.value })}
                        disabled={working}
                        className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                      >
                        {DFR_BOARDS.map((b) => (
                          <option key={b} value={b}>{b}</option>
                        ))}
                      </select>
                    </td>
                    <td className="whitespace-nowrap px-2 py-1">
                      <select
                        value={addForm.dimming_channel}
                        onChange={(e) => setAddForm({ ...addForm, dimming_channel: e.target.value })}
                        disabled={working}
                        className="rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
                      >
                        {DFR_CHANNELS.map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </td>
                  </>
                ) : (
                  <td className="px-2 py-1" />
                )}
                {addForm.device_type !== 'light' && <td className="px-2 py-1" />}
                <td className="whitespace-nowrap px-2 py-1">
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      data-testid="add-submit"
                      onClick={() => void submitAdd()}
                      disabled={working}
                      className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
                    >
                      Add
                    </button>
                    <button
                      type="button"
                      data-testid="add-cancel"
                      onClick={cancelAdd}
                      disabled={working}
                      className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {!adding && (
        <button
          type="button"
          data-testid="add-device-btn"
          onClick={startAdd}
          disabled={working || editingId !== null}
          className="w-full rounded-md border border-dashed border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
        >
          + Add device
        </button>
      )}
    </div>
  )
}