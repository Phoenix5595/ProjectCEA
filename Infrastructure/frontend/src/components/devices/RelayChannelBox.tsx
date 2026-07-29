import type { RelayChannelViewModel } from './relayViewModel'
import { formatCountdown, formatElapsedSince, getRelayNumber } from './relayViewModel'

interface RelayChannelBoxProps {
  channel: RelayChannelViewModel
  nowMs: number
  variant?: 'panel' | 'compact'
  currentLocation?: string | null
  isEditing?: boolean
  disabled?: boolean
  onSelect?: (channel: number) => void
  isMenuOpen?: boolean
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
}

interface BadgeState {
  text: string
  outlineClass: string
  ledClass: string
}

function resolveBadgeState(channel: RelayChannelViewModel, nowMs: number): BadgeState {
  // 1. Override active — blue badge with countdown
  if (channel.overrideExpiresAt) {
    const countdown = formatCountdown(channel.overrideExpiresAt, nowMs)
    if (countdown) {
      return {
        text: countdown,
        outlineClass: 'border-status-info-border/80 bg-status-info-bg/50 text-status-info-text',
        ledClass: 'bg-blue-500 shadow-[0_0_5px_var(--blue-400)]',
      }
    }
    // Override expired — fall through to mode-based logic
  }

  // 2. Auto mode — green when active, red when inactive
  if (channel.mode === 'auto' || channel.mode === 'scheduled') {
    if (channel.isActive) {
      return {
        text: 'AUTO',
        outlineClass: 'bg-status-success-bg/50 text-status-success-text border border-status-success-border/80',
        ledClass: 'bg-status-success-vivid shadow-[0_0_5px_var(--status-success-vivid)]',
      }
    }
    return {
      text: 'AUTO',
      outlineClass: 'bg-status-danger-bg/30 text-status-danger-text border-status-danger-border/60',
      ledClass: 'bg-status-danger-vivid shadow-[0_0_4px_var(--status-danger-vivid)]',
    }
  }

  // 3. Manual off / manual with no override — black badge
  if (channel.mode === 'off' || channel.mode === 'manual') {
    return {
      text: 'OFF',
      outlineClass: 'bg-black/40 text-text-muted border border-border-emphasis',
      ledClass: 'bg-black',
    }
  }

  // 4. Unknown (mode is null) — amber badge
  return {
    text: '?',
    outlineClass: 'bg-status-warning-bg/40 text-status-warning-text border border-status-warning-border/70',
    ledClass: 'bg-status-warning-vivid shadow-[0_0_4px_var(--status-warning-vivid)]',
  }
}

export default function RelayChannelBox({
  channel,
  nowMs,
  variant = 'panel',
  currentLocation = null,
  isEditing = false,
  disabled = false,
  onSelect,
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
  const isDisabled = disabled || !isAssignedToRoom

  const badge = resolveBadgeState(channel, nowMs)
  const canControl = !isDisabled

  const interactiveClasses = onSelect && !isDisabled
    ? 'cursor-pointer hover:border-btn-primary-hover hover:bg-surface-primary/40'
    : ''

  const baseClasses = [
    'group/relay relative w-full rounded-sm border text-left transition-all overflow-visible',
    isAssignedToRoom
      ? 'bg-surface-primary/80 border-border-emphasis'
      : 'bg-surface-tertiary/40 border-border-subtle opacity-50 grayscale',
    isCompact ? 'min-h-[52px] p-1' : 'min-h-[60px] p-1.5',
    isAssignedToRoom && !isDisabled ? interactiveClasses : '',
    isEditing ? 'ring-2 ring-btn-primary-light' : '',
    isMenuOpen ? 'z-30' : 'z-0',
  ]
    .filter(Boolean)
    .join(' ')

  const tooltipTitle = `R${relayNum} · ${channel.pinLabel} · ${deviceLabel} · ${locationLabel} · ${elapsedLabel}`

  const menu = isMenuOpen ? (
    <div
      className="absolute right-0 top-5 z-20 w-32 rounded-sm border border-border-emphasis bg-surface-primary p-1 shadow-lg"
      onClick={(event) => event.stopPropagation()}
    >
      {channel.isAssigned && (
        <button type="button" className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary" onClick={() => onMenuAction?.(channel.channel, 'auto')}>
          Auto
        </button>
      )}
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
        <span className={`h-2 w-2 shrink-0 rounded-full ${badge.ledClass}`} aria-hidden />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] font-semibold text-text-input">R{relayNum} · {channel.pinLabel}</span>
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
              className={`rounded-sm px-2 py-1 text-[20px] font-semibold uppercase ${badge.outlineClass} ${canControl ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
              title={canControl ? 'Click for control mode' : 'Toggle relay channel'}
            >
              {badge.text}
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
    <button
      type="button"
      className={baseClasses}
      disabled={isDisabled}
      onClick={() => onSelect(channel.channel)}
    >
      {content}
    </button>
  )
}
