import type { RelayChannelViewModel } from './relayViewModel'
import { formatElapsedSince } from './relayViewModel'

interface RelayChannelBoxProps {
  channel: RelayChannelViewModel
  nowMs: number
  compact?: boolean
  isEditing?: boolean
  onSelect?: (channel: number) => void
  statusText?: string
  statusTone?: 'unknown' | 'active' | 'idle'
  isMenuOpen?: boolean
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
}

function stateBadgeClasses(tone: 'unknown' | 'active' | 'idle'): string {
  if (tone === 'unknown') {
    return 'bg-status-warning-bg/40 text-status-warning-text border border-status-warning-border/70'
  }

  if (tone === 'active') {
    return 'bg-status-success-bg/50 text-status-success-text border border-status-success-border/80'
  }

  return 'bg-surface-tertiary text-text-muted border border-border-emphasis'
}

export default function RelayChannelBox({
  channel,
  nowMs,
  compact = false,
  isEditing = false,
  onSelect,
  statusText,
  statusTone,
  isMenuOpen = false,
  onToggleMenu,
  onMenuAction,
}: RelayChannelBoxProps) {
  const elapsedLabel = formatElapsedSince(channel.lastStateChangeAt, nowMs)
  const locationLabel = channel.location || null

  const interactiveClasses = onSelect
    ? 'cursor-pointer hover:border-btn-primary-hover hover:shadow-md'
    : ''

  const surfaceClasses = channel.isStateKnown
    ? channel.isActive
      ? 'bg-status-success-bg/15 border-status-success-border/70'
      : 'bg-surface-secondary border-border-emphasis'
    : 'bg-surface-secondary/80 border-status-warning-border/60'

  const baseClasses = [
    'relative w-full rounded-md border p-1 text-left transition-all overflow-visible',
    compact ? 'h-[96px] p-2' : 'h-[120px] p-2',
    surfaceClasses,
    interactiveClasses,
    isEditing ? 'ring-2 ring-btn-primary-light' : '',
    isMenuOpen ? 'z-30' : 'z-0',
  ]
    .filter(Boolean)
    .join(' ')

  const resolvedTone: 'unknown' | 'active' | 'idle' = statusTone || (!channel.isStateKnown ? 'unknown' : channel.isActive ? 'active' : 'idle')
  const resolvedText = statusText || (!channel.isStateKnown ? 'Unknown' : channel.isActive ? 'ON' : 'IDLE')
  const canControl = Boolean(channel.assignedDeviceName && channel.location && channel.cluster)

  const menu = isMenuOpen ? (
    <div
      className="absolute right-0 top-6 z-20 w-32 rounded-md border border-border-emphasis bg-surface-primary p-1 shadow-lg"
      onClick={(event) => event.stopPropagation()}
    >
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'auto')}>
        Auto
      </button>
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-5m')}>
        ON 5m
      </button>
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-10m')}>
        ON 10m
      </button>
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-30m')}>
        ON 30m
      </button>
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-1h')}>
        ON 1h
      </button>
      <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'off')}>
        Off
      </button>
    </div>
  ) : null

  const content = (
    <>
      <div className="flex items-start justify-between gap-1.5">
        <div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold text-text-input">CH {channel.channel}</span>
            <span className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase bg-surface-tertiary text-text-secondary border border-border-emphasis">
              {channel.pinLabel}
            </span>
          </div>
          <div className="mt-0.5 text-[10px] text-text-muted truncate whitespace-nowrap">
            {locationLabel || 'Unassigned location'}
          </div>
        </div>
        <div className="relative">
          <button
            type="button"
            disabled={!canControl}
            onClick={(event) => {
              event.stopPropagation()
              if (!canControl) {
                return
              }
              onToggleMenu?.(channel.channel)
            }}
            className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase ${stateBadgeClasses(resolvedTone)} ${canControl ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
            title={canControl ? 'Click for control mode' : 'Assign a device first'}
          >
            {resolvedText}
          </button>
          {menu}
        </div>
      </div>

      <div className="mt-1.5 text-[10px] text-text-muted">
        Last change: <span className="font-mono text-text-secondary">{elapsedLabel}</span>
      </div>

      <div className="mt-1.5 space-y-0.5">
        <div className="text-xs font-medium text-text-default truncate">
          {channel.deviceName || 'Unassigned'}
        </div>
        <div className="text-[11px] text-text-secondary truncate">
          {channel.displayType || '-'}
        </div>
      </div>
    </>
  )

  if (!onSelect) {
    return <div className={baseClasses}>{content}</div>
  }

  return (
    <button type="button" className={baseClasses} onClick={() => onSelect(channel.channel)}>
      {content}
    </button>
  )
}

