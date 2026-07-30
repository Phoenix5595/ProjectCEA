import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { apiClient } from '../../services/api';
import type {
  ControlSnapshotResponse,
  RegistryDeviceCreateBody,
  RegistryDeviceUpdateBody,
} from '../../services/api/devices';
import { useControlSnapshot } from '../../hooks/useControlSnapshot';
import type { DeviceRegistryEntry } from '../../types/device';
import { extractErrorMessage } from '../../utils/errors';
import { logger } from '../../utils/logger';
import DeviceForm from './DeviceForm';
import {
  EMPTY_DEVICE_FORM,
  approvedTypeLabel,
  buildCreateBody,
  buildDfrOptions,
  buildLightEditBody,
  buildNonLightEditBody,
  buildRelayOptions,
  formatInheritedSchedule,
  parseConflictDetail,
  toRegistryUpdate,
  validateAddForm,
  validateEditDisplayName,
  type DeviceFormState,
} from './deviceFormHelpers';

function relayChannelOf(device: DeviceRegistryEntry): number | null {
  if (device.channel != null) return device.channel;
  return device.relay_channel ?? null;
}

function isLight(device: DeviceRegistryEntry): boolean {
  return device.device_type === 'light';
}

function editFormFromDevice(device: DeviceRegistryEntry): DeviceFormState {
  const ch = relayChannelOf(device);
  return {
    display_name: device.display_name ?? '',
    device_type: device.device_type as DeviceFormState['device_type'],
    room: device.location,
    relay_channel: ch != null ? String(ch) : '',
    board_id: device.board_id != null ? String(device.board_id) : '0',
    dimming_channel:
      device.dimming_channel != null ? String(device.dimming_channel) : '0',
  };
}

interface ParsedConflict {
  assignment: 'relay' | 'DFR';
  ownerDeviceId: number;
  ownerDeviceName: string;
  ownerDisplayName: string | null;
  displacedDeviceId: number | null;
}

function ownerLabelFromConflict(conflict: ParsedConflict): string {
  return conflict.ownerDisplayName ?? conflict.ownerDeviceName ?? `#${conflict.ownerDeviceId}`;
}

export default function DeviceTable({
  refreshKey = 0,
  onRefresh,
}: {
  refreshKey?: number;
  onRefresh?: () => void;
}) {
  const {
    registry: devicesFromHook,
    snapshot,
    loading: hookLoading,
    refreshNow,
  } = useControlSnapshot();

  const [devices, setDevices] = useState<DeviceRegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<DeviceFormState>(EMPTY_DEVICE_FORM);
  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<DeviceFormState>(EMPTY_DEVICE_FORM);
  const [working, setWorking] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [displacedDeviceId, setDisplacedDeviceId] = useState<number | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [addConflict, setAddConflict] = useState<ParsedConflict | null>(null);
  const [editConflict, setEditConflict] = useState<ParsedConflict | null>(null);
  const [sortConfig, setSortConfig] = useState<{
    key: string | null;
    direction: 'asc' | 'desc' | null;
  }>({ key: null, direction: null });

  const relayOptions = useMemo(() => buildRelayOptions(snapshot), [snapshot]);
  const dfrOptions = useMemo(() => buildDfrOptions(snapshot), [snapshot]);

  const relayByChannel = useMemo(() => {
    const map = new Map<number, ControlSnapshotResponse['relays'][number]>();
    if (snapshot) {
      for (const r of snapshot.relays) {
        map.set(r.channel, r);
      }
    }
    return map;
  }, [snapshot]);

  useEffect(() => {
    setSortConfig({ key: null, direction: null });
  }, [refreshKey]);

  useEffect(() => {
    setDevices(devicesFromHook);
    setLoading(hookLoading);
  }, [devicesFromHook, hookLoading]);

  const refresh = useCallback(async () => {
    await refreshNow();
    onRefresh?.();
  }, [refreshNow, onRefresh]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const sortedDevices = useMemo(() => {
    if (sortConfig.key === null || sortConfig.direction === null) {
      return devices;
    }
    const sorted = [...devices];
    const dir = sortConfig.direction === 'asc' ? 1 : -1;

    sorted.sort((a, b) => {
      switch (sortConfig.key) {
        case 'name': {
          const av = (a.display_name ?? a.device_name).toLowerCase();
          const bv = (b.display_name ?? b.device_name).toLowerCase();
          return av < bv ? -dir : av > bv ? dir : 0;
        }
        case 'type': {
          const av = a.device_type.toLowerCase();
          const bv = b.device_type.toLowerCase();
          return av < bv ? -dir : av > bv ? dir : 0;
        }
        case 'room': {
          const av = a.location.toLowerCase();
          const bv = b.location.toLowerCase();
          return av < bv ? -dir : av > bv ? dir : 0;
        }
        case 'relayCh': {
          const av = relayChannelOf(a);
          const bv = relayChannelOf(b);
          if (av == null && bv == null) return 0;
          if (av == null) return 1;
          if (bv == null) return -1;
          return (av - bv) * dir;
        }
        case 'dfrBoard': {
          const aLight = isLight(a);
          const bLight = isLight(b);
          if (!aLight && !bLight) return 0;
          if (!aLight) return 1;
          if (!bLight) return -1;
          const av = a.board_id ?? null;
          const bv = b.board_id ?? null;
          if (av == null && bv == null) return 0;
          if (av == null) return 1;
          if (bv == null) return -1;
          return (av - bv) * dir;
        }
        case 'dfrChannel': {
          const aLight = isLight(a);
          const bLight = isLight(b);
          if (!aLight && !bLight) return 0;
          if (!aLight) return 1;
          if (!bLight) return -1;
          const av = a.dimming_channel ?? null;
          const bv = b.dimming_channel ?? null;
          if (av == null && bv == null) return 0;
          if (av == null) return 1;
          if (bv == null) return -1;
          return (av - bv) * dir;
        }
        default:
          return 0;
      }
    });

    return sorted;
  }, [devices, sortConfig]);

  function toggleSort(key: string) {
    setSortConfig((prev) => {
      if (prev.key !== key) return { key, direction: 'asc' };
      if (prev.direction === 'asc') return { key, direction: 'desc' };
      return { key: null, direction: null };
    });
  }

  function sortIndicator(key: string): string {
    if (sortConfig.key !== key || sortConfig.direction === null) return '';
    return sortConfig.direction === 'asc' ? ' ▲' : ' ▼';
  }

  function physicalRelayLabel(device: DeviceRegistryEntry): string | null {
    const ch = relayChannelOf(device);
    if (ch == null) return null;
    const relay = relayByChannel.get(ch);
    if (!relay) return null;
    return `R${relay.physical_relay}`;
  }

  function commandModeFor(device: DeviceRegistryEntry): string | null {
    const ch = relayChannelOf(device);
    if (ch == null) return null;
    const relay = relayByChannel.get(ch);
    return relay?.command_mode ?? null;
  }

  function startEdit(device: DeviceRegistryEntry) {
    if (editingId !== null) {
      toast.error('Save current changes before editing another device');
      return;
    }
    setEditingId(device.device_id);
    setDeleteConfirmId(null);
    setEditForm(editFormFromDevice(device));
    setEditError(null);
    setEditConflict(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditForm(EMPTY_DEVICE_FORM);
    setEditError(null);
    setEditConflict(null);
  }

  function startAdd() {
    if (editingId !== null) {
      toast.error('Save current changes before adding a device');
      return;
    }
    setAdding(true);
    setAddForm(EMPTY_DEVICE_FORM);
    setAddError(null);
    setAddConflict(null);
  }

  function cancelAdd() {
    setAdding(false);
    setAddForm(EMPTY_DEVICE_FORM);
    setAddError(null);
    setAddConflict(null);
  }

  function handleConflict(
    err: unknown,
    mode: 'add' | 'edit',
  ): boolean {
    const parsed = parseConflictDetail(err);
    if (!parsed) return false;
    if (parsed.assignment === 'DFR') {
      const label = ownerLabelFromConflict(parsed);
      const message = `DFR slot already assigned to ${label}`;
      if (mode === 'add') setAddError(message);
      else setEditError(message);
      toast.error(message);
      return true;
    }
    if (parsed.assignment === 'relay') {
      if (mode === 'add') setAddConflict(parsed);
      else setEditConflict(parsed);
      return true;
    }
    return false;
  }

  async function submitAdd(confirmedRelaySteal = false) {
    const validation = validateAddForm(addForm);
    if (!validation.ok) {
      setAddError(validation.error);
      toast.error(validation.error ?? 'Invalid form');
      return;
    }
    setAddError(null);
    const body: RegistryDeviceCreateBody = buildCreateBody(addForm);
    setWorking(true);
    try {
      await apiClient.createDevice(body, confirmedRelaySteal);
      await refreshNow();
      onRefresh?.();
      cancelAdd();
      toast.success('Device created');
    } catch (err) {
      if (!confirmedRelaySteal && handleConflict(err, 'add')) {
        setWorking(false);
        return;
      }
      logger.error('Failed to create device', err);
      setAddError(extractErrorMessage(err, 'Failed to create device'));
      toast.error(extractErrorMessage(err, 'Failed to create device'));
    } finally {
      setWorking(false);
    }
  }

  async function submitEdit(
    device: DeviceRegistryEntry,
    confirmedRelaySteal = false,
  ) {
    const validation = validateEditDisplayName(editForm.display_name);
    if (!validation.ok) {
      setEditError(validation.error);
      toast.error(validation.error ?? 'Invalid form');
      return;
    }
    setEditError(null);
    const editBody = isLight(device)
      ? buildLightEditBody(editForm)
      : buildNonLightEditBody(editForm);
    const body: RegistryDeviceUpdateBody = toRegistryUpdate(editBody);
    setWorking(true);
    try {
      const result = await apiClient.updateDevice(
        device.device_id,
        body,
        confirmedRelaySteal,
      );
      if (result.displaced_device_id != null) {
        setDisplacedDeviceId(result.displaced_device_id);
        const displaced = devices.find(
          (d) => d.device_id === result.displaced_device_id,
        );
        const displacedLabel =
          displaced?.display_name ??
          displaced?.device_name ??
          `#${result.displaced_device_id}`;
        toast.warning(`Relay channel stolen from ${displacedLabel}`);
      } else {
        setDisplacedDeviceId(null);
      }
      await refreshNow();
      cancelEdit();
      toast.success('Device updated');
    } catch (err) {
      if (!confirmedRelaySteal && handleConflict(err, 'edit')) {
        setWorking(false);
        return;
      }
      logger.error('Failed to update device', err);
      setEditError(extractErrorMessage(err, 'Failed to update device'));
      toast.error(extractErrorMessage(err, 'Failed to update device'));
    } finally {
      setWorking(false);
    }
  }

  async function confirmDelete(device: DeviceRegistryEntry) {
    setWorking(true);
    try {
      await apiClient.deleteDevice(device.device_id);
      await refreshNow();
      onRefresh?.();
      setDeleteConfirmId(null);
      toast.success('Device deleted');
    } catch (err) {
      logger.error('Failed to delete device', err);
      toast.error(extractErrorMessage(err, 'Failed to delete device'));
    } finally {
      setWorking(false);
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
    );
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
                Device{sortIndicator('name')}
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
                Relay{sortIndicator('relayCh')}
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
                DFR Ch{sortIndicator('dfrChannel')}
              </th>
              <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                Mode
              </th>
              <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                Schedule
              </th>
              <th className="px-2 py-1 text-left text-xs font-medium uppercase tracking-wider text-text-muted">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle bg-surface-primary">
            {sortedDevices.map((device) => {
              const isEditing = editingId === device.device_id;
              const isConfirmingDelete = deleteConfirmId === device.device_id;
              const light = isLight(device);
              const relayLabel = physicalRelayLabel(device);
              const mode = commandModeFor(device);
              const schedule = formatInheritedSchedule(
                device.inherited_schedule_count,
                device.inherited_schedule_summary,
              );

              if (isEditing) {
                return (
                  <DeviceForm
                    key={device.device_id}
                    mode="edit"
                    form={editForm}
                    onChange={setEditForm}
                    relayOptions={relayOptions}
                    dfrOptions={dfrOptions}
                    working={working}
                    onSubmit={() => void submitEdit(device)}
                    onCancel={cancelEdit}
                    submitLabel="Save"
                    error={editError}
                    canonicalName={device.device_name}
                    currentRelayLabel={relayLabel}
                    scheduleSummary={schedule}
                    commandMode={mode}
                    stealPrompt={
                      editConflict
                        ? {
                            ownerLabel: ownerLabelFromConflict(editConflict),
                            onConfirm: () => void submitEdit(device, true),
                            onCancel: () => setEditConflict(null),
                          }
                        : null
                    }
                  />
                );
              }

              return (
                <tr
                  key={device.device_id}
                  data-testid={`device-row-${device.device_id}`}
                  className={`hover:bg-surface-secondary cursor-pointer ${
                    device.device_id === displacedDeviceId
                      ? 'ring-2 ring-status-danger'
                      : ''
                  }`}
                  onClick={() => startEdit(device)}
                >
                  <td className="whitespace-nowrap px-2 py-1">
                    <div className="flex flex-col">
                      <span className="text-sm text-text-input">
                        {device.display_name ?? device.device_name}
                      </span>
                      {device.display_name ? (
                        <span className="text-[10px] uppercase tracking-wider text-text-muted">
                          {device.device_name}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    <span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2.5 py-0.5 text-xs font-medium text-btn-primary-text">
                      {approvedTypeLabel(device.device_type)}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    {device.location}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    {relayLabel ?? '—'}
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
                    <>
                      <td className="px-2 py-1 text-sm text-text-subtle">—</td>
                      <td className="px-2 py-1 text-sm text-text-subtle">—</td>
                    </>
                  )}
                  <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
                    {mode ? (
                      <span className="inline-flex items-center rounded-full bg-surface-tertiary px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                        {mode}
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td
                    className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary"
                    title={Array.isArray(device.inherited_schedule_summary) ? device.inherited_schedule_summary.join(', ') : undefined}
                  >
                    {schedule}
                  </td>
                  <td
                    className="whitespace-nowrap px-2 py-1"
                    onClick={(e) => e.stopPropagation()}
                  >
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
                        disabled={working || editingId !== null}
                        className="rounded-md border border-status-danger-border/60 bg-surface-primary px-2 py-1 text-xs text-status-danger-text hover:bg-status-danger-bg/30 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}

            {adding ? (
              <DeviceForm
                mode="add"
                form={addForm}
                onChange={setAddForm}
                relayOptions={relayOptions}
                dfrOptions={dfrOptions}
                working={working}
                onSubmit={() => void submitAdd()}
                onCancel={cancelAdd}
                submitLabel="Add"
                error={addError}
                stealPrompt={
                  addConflict
                    ? {
                        ownerLabel: ownerLabelFromConflict(addConflict),
                        onConfirm: () => void submitAdd(true),
                        onCancel: () => setAddConflict(null),
                      }
                    : null
                }
              />
            ) : null}
          </tbody>
        </table>
      </div>

      {!adding ? (
        <button
          type="button"
          data-testid="add-device-btn"
          onClick={startAdd}
          disabled={working || editingId !== null}
          className="w-full rounded-md border border-dashed border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
        >
          + Add device
        </button>
      ) : null}
    </div>
  );
}
