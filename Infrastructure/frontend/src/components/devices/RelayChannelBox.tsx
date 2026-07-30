import type { RelayChannelViewModel } from './relayViewModel'
import { formatCountdown, formatElapsedSince } from './relayViewModel'

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

interface ButtonState {
  text: string
  outlineClass: string
}

interface LedState {
  ledClass: string
  title: string
}

function resolveLedState(channel: RelayChannelViewModel): LedState {
  if (!channel.isStateKnown) {
    return {
      ledClass: 'bg-neutral-500',
      title: 'Unknown (no observation yet)',
    }
  }
  if (channel.observedState) {
    return {
      ledClass: 'bg-status-success-vivid shadow-[0_0_5px_var(--status-success-vivid)]',
      title: 'ON (observed)',
    }
  }
  return {
    ledClass: 'bg-neutral-700',
    title: 'OFF (observed)',
  }
}

function resolveButtonState(channel: RelayChannelViewModel, nowMs: number): ButtonState {
  if (channel.stale) {
    return {
      text: 'STALE',
      outlineClass: 'bg-status-warning-bg/50 text-status-warning-text border border-status-warning-border/80',
    }
  }
  if (channel.syncing) {
    return {
      text: 'SYNC',
      outlineClass: 'bg-status-info-bg/50 text-status-info-text border border-status-info-border/80',
    }
  }
  if (channel.alarm) {
    return {
      text: 'ALARM',
      outlineClass: 'bg-status-danger-bg/50 text-status-danger-text border border-status-danger-border/80',
    }
  }
  if (channel.commandMode === 'timed_on' && channel.commandExpiresAt) {
    const countdown = formatCountdown(channel.commandExpiresAt, nowMs)
    if (countdown) {
      return {
        text: countdown,
        outlineClass: 'border-status-info-border/80 bg-status-info-bg/50 text-status-info-text border',
      }
    }
  }
  if (channel.commandMode === 'auto' || channel.commandMode === 'scheduled') {
    return {
      text: 'AUTO',
      outlineClass: 'bg-status-success-bg/50 text-status-success-text border border-status-success-border/80',
    }
  }
  if (channel.commandMode === 'manual_off') {
    return {
      text: 'MANUAL OFF',
      outlineClass: 'bg-black/40 text-text-muted border border-border-emphasis',
    }
  }
  return {
    text: '?',
    outlineClass: 'bg-status-warning-bg/40 text-status-warning-text border border-status-warning-border/70',
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
  const elapsedLabel = formatElapsedSince(channel.changedAt, nowMs)
  const relayNum = channel.physicalRelay
  const locationLabel = channel.location || 'Unassigned location'
  const deviceLabel = channel.deviceName || 'Unassigned'
  const typeLabel = channel.displayType || '-'

  const isForeignTile = !!currentLocation && !!channel.location && channel.location !== currentLocation
  const isUnassignedInRoomView = !channel.isAssigned && !!currentLocation
  const isDisabled = disabled || isForeignTile || isUnassignedInRoomView
  const isStale = channel.stale

  const led = resolveLedState(channel)
  const button = resolveButtonState(channel, nowMs)

  const interactiveClasses = onSelect && !isDisabled
    ? 'cursor-pointer hover:border-btn-primary-hover hover:bg-surface-primary/40'
    : ''

  const baseClasses = [
    'group/relay relative w-full rounded-sm border text-left transition-all overflow-visible',
    isForeignTile || isUnassignedInRoomView
      ? 'bg-surface-tertiary/40 border-border-subtle opacity-50 grayscale'
      : 'bg-surface-primary/80 border-border-emphasis',
    isCompact ? 'min-h-[52px] p-1' : 'min-h-[60px] p-1.5',
    !isDisabled ? interactiveClasses : '',
    isEditing ? 'ring-2 ring-btn-primary-light' : '',
    isMenuOpen ? 'z-30' : 'z-0',
  ]
    .filter(Boolean)
    .join(' ')

  const tooltipParts = [
    `R${relayNum}`,
    channel.pinLabel || '—',
    deviceLabel,
    locationLabel,
    elapsedLabel,
  ]
  if (channel.alarm) tooltipParts.push(`[${channel.alarm.severity}] ${channel.alarm.message}`)
  if (isStale) tooltipParts.push('STALE')
  const tooltipTitle = tooltipParts.join(' · ')

  const menu = isMenuOpen ? (
    <div
      className="absolute right-0 top-5 z-20 w-32 rounded-sm border border-border-emphasis bg-surface-primary p-1 shadow-lg"
      onClick={(event) => event.stopPropagation()}
    >
      {isStale ? (
        <button
          type="button"
          className="w-full rounded-sm px-2 py-1 text-left text-xs hover:bg-surface-secondary"
          onClick={() => onMenuAction?.(channel.channel, 'off')}
        >
          Off
        </button>
      ) : (
        <>
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
        </>
      )}
    </div>
  ) : null

  const content = (
    <div className="flex items-stretch gap-1.5" title={tooltipTitle}>
      <div className="flex shrink-0 items-center justify-center">
        <span className={`h-2 w-2 shrink-0 rounded-full ${led.ledClass}`} aria-hidden title={led.title} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-1">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[10px] font-semibold text-text-input">R{relayNum} · {channel.pinLabel || '—'}</span>
            </div>
            <div className="truncate text-[9px] text-text-muted">{locationLabel}</div>
          </div>
          <div className="relative shrink-0">
            <button
              type="button"
              disabled={isDisabled}
              onClick={(event) => {
                event.stopPropagation()
                if (isDisabled) {
                  return
                }
                onToggleMenu?.(channel.channel)
              }}
              className={`rounded-sm px-2 py-1 text-[20px] font-semibold uppercase ${button.outlineClass} ${!isDisabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
              title={isDisabled ? 'Channel unavailable' : 'Click for control mode'}
            >
              {button.text}
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
