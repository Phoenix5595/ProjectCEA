import { ZONES } from '../../config/zones';
import {
  APPROVED_DEVICE_TYPES,
  NON_LIGHT_DEVICE_TYPES,
  approvedTypeLabel,
  type DeviceFormState,
  type DfrOption,
  type RelayOption,
} from './deviceFormHelpers';

interface DeviceFormProps {
  mode: 'add' | 'edit';
  form: DeviceFormState;
  onChange: (next: DeviceFormState) => void;
  relayOptions: RelayOption[];
  dfrOptions: DfrOption[];
  working: boolean;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
  error?: string | null;
  /** Canonical machine name shown alongside the display_name input when editing. */
  canonicalName?: string | null;
  /** Read-only physical R label when editing and a relay is bound. */
  currentRelayLabel?: string | null;
  /** Read-only inherited schedule summary when editing. */
  scheduleSummary?: string | null;
  /** Read-only command mode when editing. */
  commandMode?: string | null;
  /** When set, replaces Save/Cancel with a steal-confirmation prompt. */
  stealPrompt?: {
    ownerLabel: string;
    onConfirm: () => void;
    onCancel: () => void;
  } | null;
}

const inputClass =
  'w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50';

const readOnlyClass = 'text-sm text-text-secondary';

const submitClass =
  'rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50';
const cancelClass =
  'rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50';

export default function DeviceForm({
  mode,
  form,
  onChange,
  relayOptions,
  dfrOptions,
  working,
  onSubmit,
  onCancel,
  submitLabel,
  error,
  canonicalName,
  currentRelayLabel,
  scheduleSummary,
  commandMode,
  stealPrompt,
}: DeviceFormProps) {
  const isLight = form.device_type === 'light';

  function patch<K extends keyof DeviceFormState>(key: K, value: DeviceFormState[K]) {
    onChange({ ...form, [key]: value });
  }

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'Enter' && !working) {
      event.preventDefault();
      onSubmit();
    } else if (event.key === 'Escape' && !working) {
      event.preventDefault();
      onCancel();
    }
  }

  return (
    <tr
      data-testid={mode === 'add' ? 'add-row' : `edit-row`}
      className="bg-surface-secondary/40"
      onKeyDown={handleKeyDown}
    >
      <td className="whitespace-nowrap px-2 py-1">
        <div className="flex flex-col gap-0.5">
          <input
            type="text"
            value={form.display_name}
            onChange={(e) => patch('display_name', e.target.value)}
            disabled={working}
            className={inputClass}
            placeholder="Display name"
            autoFocus
          />
          {mode === 'edit' && canonicalName ? (
            <span className="text-[10px] uppercase tracking-wider text-text-muted">
              {canonicalName}
            </span>
          ) : null}
        </div>
      </td>

      <td className="whitespace-nowrap px-2 py-1">
        {mode === 'add' ? (
          <select
            value={form.device_type}
            onChange={(e) => {
              const nextType = e.target.value as DeviceFormState['device_type'];
              const updates: Partial<DeviceFormState> = { device_type: nextType };
              if (nextType !== 'light') {
                updates.board_id = '0';
                updates.dimming_channel = '0';
              }
              onChange({ ...form, ...updates });
            }}
            disabled={working}
            className={inputClass}
          >
            <option value="">Select type</option>
            {APPROVED_DEVICE_TYPES.map((t) => (
              <option key={t} value={t}>
                {approvedTypeLabel(t)}
              </option>
            ))}
          </select>
        ) : (
          <span className={readOnlyClass}>
            {form.device_type ? approvedTypeLabel(form.device_type) : '—'}
          </span>
        )}
      </td>

      <td className="whitespace-nowrap px-2 py-1">
        {mode === 'add' ? (
          <select
            value={form.room}
            onChange={(e) => patch('room', e.target.value)}
            disabled={working}
            className={inputClass}
          >
            <option value="">Select room</option>
            {ZONES.map((z) => (
              <option key={z.location} value={z.location}>
                {z.location}
              </option>
            ))}
          </select>
        ) : (
          <span className={readOnlyClass}>{form.room || '—'}</span>
        )}
      </td>

      <td className="whitespace-nowrap px-2 py-1">
        <select
          value={form.relay_channel}
          onChange={(e) => patch('relay_channel', e.target.value)}
          disabled={working}
          className={inputClass}
        >
          <option value="">No relay</option>
          {relayOptions.map((option) => (
            <option key={option.channel} value={option.channel}>
              {option.label}
            </option>
          ))}
        </select>
        {mode === 'edit' && currentRelayLabel ? (
          <span className="block text-[10px] uppercase tracking-wider text-text-muted">
            current: {currentRelayLabel}
          </span>
        ) : null}
      </td>

      {isLight ? (
        <>
          <td className="whitespace-nowrap px-2 py-1">
            <select
              value={form.board_id}
              onChange={(e) => patch('board_id', e.target.value)}
              disabled={working}
              className={inputClass}
            >
              {dfrOptions
                .filter((o) => o.channel === Number(form.dimming_channel || 0))
                .length > 0
                ? null
                : null}
              {Array.from(new Set(dfrOptions.map((o) => o.boardId)))
                .sort((a, b) => a - b)
                .map((boardId) => (
                  <option key={boardId} value={boardId}>
                    {boardId}
                  </option>
                ))}
            </select>
          </td>
          <td className="whitespace-nowrap px-2 py-1">
            <select
              value={form.dimming_channel}
              onChange={(e) => patch('dimming_channel', e.target.value)}
              disabled={working}
              className={inputClass}
            >
              {Array.from(new Set(dfrOptions.map((o) => o.channel)))
                .sort((a, b) => a - b)
                .map((channel) => (
                  <option key={channel} value={channel}>
                    {channel}
                  </option>
                ))}
            </select>
          </td>
        </>
      ) : (
        <>
          <td className="px-2 py-1 text-sm text-text-subtle">—</td>
          <td className="px-2 py-1 text-sm text-text-subtle">—</td>
        </>
      )}

      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
        {mode === 'edit' ? <span>{commandMode ?? '—'}</span> : <span className="text-text-subtle">—</span>}
      </td>

      <td className="whitespace-nowrap px-2 py-1 text-sm text-text-secondary">
        {mode === 'edit' ? (
          <span title={scheduleSummary ?? undefined}>{scheduleSummary ?? '—'}</span>
        ) : (
          <span className="text-text-subtle">—</span>
        )}
      </td>

      <td className="whitespace-nowrap px-2 py-1">
        <div className="flex flex-col gap-1">
          {stealPrompt ? (
            <>
              <span
                className="text-[11px] text-status-danger-text"
                data-testid="steal-prompt"
              >
                Steal relay from {stealPrompt.ownerLabel}?
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid="steal-confirm"
                  onClick={stealPrompt.onConfirm}
                  disabled={working}
                  className="rounded-md bg-status-danger-bg/60 px-2 py-1 text-xs font-medium text-status-danger-text hover:bg-status-danger-bg/80 disabled:opacity-50"
                >
                  Confirm steal
                </button>
                <button
                  type="button"
                  data-testid="steal-cancel"
                  onClick={stealPrompt.onCancel}
                  disabled={working}
                  className={cancelClass}
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid={mode === 'add' ? 'add-submit' : 'edit-save'}
                  onClick={onSubmit}
                  disabled={working}
                  className={submitClass}
                >
                  {submitLabel}
                </button>
                <button
                  type="button"
                  data-testid={mode === 'add' ? 'add-cancel' : 'edit-cancel'}
                  onClick={onCancel}
                  disabled={working}
                  className={cancelClass}
                >
                  Cancel
                </button>
              </div>
              {error ? (
                <span
                  className="text-[11px] text-status-danger-text"
                  data-testid="form-error"
                >
                  {error}
                </span>
              ) : null}
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

export { NON_LIGHT_DEVICE_TYPES };
