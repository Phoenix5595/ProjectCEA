import type { RelayChannelViewModel } from './relayViewModel'
import { formatElapsedSince, getRelayNumber } from './relayViewModel'

interface RelayChannelBoxProps {
  channel: RelayChannelViewModel
  nowMs: number
  variant?: 'panel' | 'compact'
  currentLocation?: string | null
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

function RelayStatusLed({ tone }: { tone: 'unknown' | 'active' | 'idle' }) {
  const ledClass =
    tone === 'unknown'
      ? 'bg-status-warning-vivid shadow-[0_0_4px_var(--status-warning-vivid)]'
      : tone === 'active'
        ? 'bg-status-success-vivid shadow-[0_0_5px_var(--status-success-vivid)]'
        : 'bg-surface-quinary border border-border-emphasis'

  return <span className={`h-2 w-2 shrink-0 rounded-full ${ledClass}`} aria-hidden />
}

export default function RelayChannelBox({
  channel,
  nowMs,
  variant = 'panel',
  currentLocation = null,
  isEditing = false,
  onSelect,
  statusText,
  statusTone,
  isMenuOpen = false,
  onToggleMenu,
  onMenuAction,
}: RelayChannelBoxProps) {
  const isCompact = variant === 'compact'
  const elapsedLabel = formatElapsedSince(channel.lastStateChangeAt, nowMs)
  const relayNum = getRelayNumber(channel.channel)
  const locationLabel = channel.location || 'Unassigned location'
  const deviceLabel = channel.deviceName || 'Unassigned'
  const typeLabel = channel.displayType || '-'

  const isAssignedToRoom = !currentLocation || channel.location === currentLocation

  const resolvedTone: 'unknown' | 'active' | 'idle' =
    statusTone || (!channel.isStateKnown ? 'unknown' : channel.isActive ? 'active' : 'idle')
  const resolvedText = statusText || (!channel.isStateKnown ? 'Unknown' : channel.isActive ? 'ON' : 'IDLE')
  const canControl = isAssignedToRoom && Boolean(channel.assignedDeviceName && channel.location && channel.cluster)

  const interactiveClasses = onSelect
    ? 'cursor-pointer hover:border-btn-primary-hover hover:bg-surface-primary/40'
    : ''

  const baseClasses = [
    'group/relay relative w-full rounded-sm border text-left transition-all overflow-visible',
    isAssignedToRoom
      ? 'bg-surface-primary/80 border-border-emphasis'
      : 'bg-surface-tertiary/40 border-border-subtle opacity-50',
    isCompact ? 'min-h-[52px] p-1' : 'min-h-[60px] p-1.5',
    isAssignedToRoom ? interactiveClasses : '',
    isEditing ? 'ring-2 ring-btn-primary-light' : '',
    isMenuOpen ? 'z-30' : 'z-0',
  ]
    .filter(Boolean)
    .join(' ')

  const tooltipTitle = `R${relayNum} · CH ${channel.channel} · ${channel.pinLabel} · ${deviceLabel} · ${locationLabel} · ${elapsedLabel}`

  const menu = isMenuOpen ? (
    <div
      className="absolute right-0 top-5 z-20 w-32 rounded-sm border border-border-emphasis bg-surface-primary p-1 shadow-lg"
      onClick={(event) => event.stopPropagation()}
    >
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'auto')}>
        Auto
      </button>
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-5m')}>
        ON 5m
      </button>
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-10m')}>
        ON 10m
      </button>
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-30m')}>
        ON 30m
      </button>
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'timer-1h')}>
        ON 1h
      </button>
      <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'off')}>
        Off
      </button>
    </div>
  ) : null

  const content = (
    <div className="flex items-stretch gap-1.5" title={tooltipTitle}>
      <div className="flex shrink-0 items-center justify-center">
        <RelayStatusLed tone={resolvedTone} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] font-semibold text-text-input">R{relayNum} · CH {channel.channel}</span>
            </div>
            <div className="truncate text-[9px] text-text-muted">{locationLabel}</div>
          </div>
          <div className="relative shrink-0">
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
              className={`rounded-sm px-1 py-px text-[8px] font-semibold uppercase ${stateBadgeClasses(resolvedTone)} ${canControl ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
              title={canControl ? 'Click for control mode' : 'Assign a device first'}
            >
              {resolvedText}
            </button>
            {menu}
          </div>
        </div>

        <div className="mt-0.5 truncate text-[9px] font-medium text-text-default">{deviceLabel}</div>
        <div className="flex items-center justify-between gap-1 text-[8px] text-text-secondary">
          <span className="truncate">{typeLabel}</span>
          <span className="shrink-0 font-mono text-text-muted">{elapsedLabel}</span>
        </div>
      </div>
    </div>
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
