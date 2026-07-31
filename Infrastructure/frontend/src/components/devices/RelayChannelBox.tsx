import type { RelayChannelViewModel } from './relayViewModel'
import { formatElapsedSince } from './relayViewModel'

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

function resolveObservedStateText(channel: RelayChannelViewModel): string {
  if (!channel.isStateKnown) return 'UNKNOWN'
  return channel.observedState ? 'ON' : 'OFF'
}

function resolveModeOutlineClass(channel: RelayChannelViewModel): string {
  if (channel.stale) {
    return 'border-status-warning-vivid bg-status-warning-bg/40'
  }
  if (channel.syncing) {
    return 'border-status-info-vivid bg-status-info-bg/40'
  }
  if (channel.alarm) {
    return 'border-status-danger-vivid bg-status-danger-bg/40'
  }
  if (channel.commandMode === 'auto' || channel.commandMode === 'scheduled') {
    return 'border-status-info-vivid bg-status-info-bg/40'
  }
  if (channel.commandMode === 'timed_on') {
    return 'border-status-success-vivid bg-status-success-bg/40'
  }
  if (channel.commandMode === 'manual_off') {
    return 'border-status-danger-vivid bg-status-danger-bg/40'
  }
  return 'border-status-danger-vivid bg-status-danger-bg/40'
}

function resolveButtonState(channel: RelayChannelViewModel): ButtonState {
  const observedText = resolveObservedStateText(channel)
  return {
    text: observedText,
    outlineClass: 'bg-surface-primary/80 text-text-input border border-border-emphasis',
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

  const modeOutlineClass = resolveModeOutlineClass(channel)
  const button = resolveButtonState(channel)

  const interactiveClasses = onSelect && !isDisabled
    ? 'cursor-pointer hover:border-btn-primary-hover hover:bg-surface-primary/40'
    : ''

  const baseClasses = [
    'group/relay relative w-full aspect-[20/11] min-h-0 rounded-sm border-2 text-left transition-all overflow-visible',
    isForeignTile || isUnassignedInRoomView
      ? 'bg-surface-tertiary/40 border-border-subtle opacity-50 grayscale'
      : `bg-surface-tertiary ${modeOutlineClass}`,
    isCompact ? 'p-1' : 'p-1.5',
    !isDisabled ? interactiveClasses : '',
    isEditing ? 'ring-2 ring-btn-primary-light' : '',
    isMenuOpen ? 'z-30' : 'z-0',
  ]
    .filter(Boolean)
    .join(' ')

  const tooltipParts = [
    `R${relayNum}`,
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
    <div className="flex h-full gap-2" title={tooltipTitle}>
      <div className="flex min-w-0 flex-1 flex-col justify-between py-0.5">
        <div>
          <span className="text-[12px] font-bold text-text-input">R{relayNum}</span>
          <div className="mt-0.5 truncate text-[10px] font-medium text-text-default">{deviceLabel}</div>
        </div>
        <div>
          <div className="truncate text-[9px] text-text-secondary">{typeLabel}</div>
          <div className="truncate text-[9px] text-text-muted">{locationLabel}</div>
        </div>
      </div>

      <div className={`flex h-full shrink-0 flex-col items-center justify-between gap-1 ${isCompact ? 'min-w-10' : 'min-w-[60px]'}`}>
        <div className="relative flex aspect-square h-[55%] items-center justify-center">
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
            className={`flex h-full w-full items-center justify-center rounded-sm px-2 text-[14px] font-black uppercase leading-none ${button.outlineClass} ${!isDisabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
            title={isDisabled ? 'Channel unavailable' : 'Click for control mode'}
          >
            {button.text}
          </button>
          {menu}
        </div>
        <div className="flex flex-1 w-full items-center justify-center rounded-sm bg-surface-primary/60 px-1">
          <span className="shrink-0 font-mono text-[10px] font-semibold text-text-muted">{elapsedLabel}</span>
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
