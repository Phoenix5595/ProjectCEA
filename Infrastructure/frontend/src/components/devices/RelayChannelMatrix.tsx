import { useState } from 'react'

import RelayChannelBox from './RelayChannelBox'
import type { RelayChannelViewModel } from './relayViewModel'
import { RELAY_MATRIX_ROWS, splitRelayByPhysicalLayout } from './relayViewModel'

export type RelayMatrixVariant = 'panel' | 'compact'

interface RelayChannelMatrixProps {
  channels: RelayChannelViewModel[]
  nowMs: number
  variant?: RelayMatrixVariant
  location?: string
  editingChannel?: number | null
  onSelectChannel?: (channel: number) => void
  menuOpenChannel?: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (
    channel: number,
    action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off'
  ) => void
  onMoveRelay?: (deviceId: number, sourceChannel: number, targetChannel: number) => void
  movingChannels?: ReadonlySet<number>
}

interface ChannelBoxRenderProps {
  nowMs: number
  variant: RelayMatrixVariant
  currentLocation: string | undefined
  editingChannel: number | null
  onSelectChannel?: (channel: number) => void
  menuOpenChannel: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (
    channel: number,
    action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off'
  ) => void
  dragEnabled: boolean
  dragChannel: number | null
  hoverChannel: number | null
  movingChannels?: ReadonlySet<number>
  onDragStartChannel: (event: React.DragEvent<HTMLElement>, channel: number) => void
  onDragOverChannel: (event: React.DragEvent<HTMLElement>, channel: number) => void
  onDragLeaveChannel: (channel: number) => void
  onDropChannel: (channel: number) => void
  onDragEnd: () => void
}

function dropLabelFor(
  channel: RelayChannelViewModel,
  hoverChannel: number | null,
  dragChannel: number | null
): string | null {
  if (hoverChannel !== channel.channel || dragChannel === channel.channel) return null
  if (!channel.isAssigned) return 'Move here'
  return `Swap with ${channel.deviceName ?? channel.assignedDeviceName ?? 'device'}`
}

function renderChannelBox(channel: RelayChannelViewModel, props: ChannelBoxRenderProps) {
  return (
    <RelayChannelBox
      key={channel.channel}
      channel={channel}
      nowMs={props.nowMs}
      variant={props.variant}
      currentLocation={props.currentLocation}
      isEditing={props.editingChannel === channel.channel}
      onSelect={props.onSelectChannel}
      isMenuOpen={props.menuOpenChannel === channel.channel}
      onToggleMenu={props.onToggleMenu}
      onMenuAction={props.onMenuAction}
      draggable={props.dragEnabled && channel.isAssigned && channel.deviceId !== null}
      dropLabel={dropLabelFor(channel, props.hoverChannel, props.dragChannel)}
      moving={props.movingChannels?.has(channel.channel) ?? false}
      onDragStart={event => props.onDragStartChannel(event, channel.channel)}
      onDragOver={event => props.onDragOverChannel(event, channel.channel)}
      onDragLeave={() => props.onDragLeaveChannel(channel.channel)}
      onDrop={() => props.onDropChannel(channel.channel)}
      onDragEnd={props.onDragEnd}
    />
  )
}

const GRID_BACKGROUND_STYLE = {
  backgroundImage: `
    linear-gradient(to right, var(--border-subtle) 1px, transparent 1px),
    linear-gradient(to bottom, var(--border-subtle) 1px, transparent 1px)
  `,
  backgroundSize: '12px 12px',
} as const

/**
 * Grid layout: 8 rows × 2 columns, matching the two physical MCP23017 banks
 * (left bank R1-R8, right bank R9-R16). Horizontal spacing between the banks
 * is 110% of the relay box width via a CSS custom property.
 */
const RELAY_GRID_COLUMNS = 'var(--relay-w) var(--relay-w)'

export default function RelayChannelMatrix({
  channels,
  nowMs,
  variant = 'panel',
  location,
  editingChannel = null,
  onSelectChannel,
  menuOpenChannel = null,
  onToggleMenu,
  onMenuAction,
  onMoveRelay,
  movingChannels,
}: RelayChannelMatrixProps) {
  const { rows } = splitRelayByPhysicalLayout(channels)
  const [dragChannel, setDragChannel] = useState<number | null>(null)
  const [hoverChannel, setHoverChannel] = useState<number | null>(null)

  const dragEnabled = variant === 'panel' && !!onMoveRelay && (movingChannels?.size ?? 0) === 0

  function handleDragStart(event: React.DragEvent<HTMLElement>, channel: number) {
    if (event.dataTransfer) {
      event.dataTransfer.setData('text/plain', String(channel))
      event.dataTransfer.effectAllowed = 'move'
    }
    setDragChannel(channel)
    setHoverChannel(null)
  }

  function handleDragOver(event: React.DragEvent<HTMLElement>, channel: number) {
    event.preventDefault()
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move'
    }
    setHoverChannel(channel)
  }

  function handleDragLeave(channel: number) {
    setHoverChannel(previous => (previous === channel ? null : previous))
  }

  function handleDrop(channel: number) {
    const sourceChannel = dragChannel
    setDragChannel(null)
    setHoverChannel(null)
    if (sourceChannel === null || sourceChannel === channel) return
    const source = channels.find(candidate => candidate.channel === sourceChannel)
    if (!source || source.deviceId === null) return
    onMoveRelay?.(source.deviceId, source.channel, channel)
  }

  function handleDragEnd() {
    setDragChannel(null)
    setHoverChannel(null)
  }

  const boxProps: ChannelBoxRenderProps = {
    nowMs,
    variant,
    currentLocation: location,
    editingChannel: editingChannel ?? null,
    onSelectChannel,
    menuOpenChannel: menuOpenChannel ?? null,
    onToggleMenu,
    onMenuAction,
    dragEnabled,
    dragChannel,
    hoverChannel,
    movingChannels,
    onDragStartChannel: handleDragStart,
    onDragOverChannel: handleDragOver,
    onDragLeaveChannel: handleDragLeave,
    onDropChannel: handleDrop,
    onDragEnd: handleDragEnd,
  }

  const relayWidth = variant === 'panel' ? '198px' : '150px'
  const relayHeight = 'calc(var(--relay-w) * 11 / 20)'

  return (
    <div className="rounded-sm bg-surface-secondary p-0">
      <div
        data-testid="relay-channel-matrix"
        className="relative grid p-1 min-w-0 max-w-full overflow-auto"
        style={{
          ...GRID_BACKGROUND_STYLE,
          ['--relay-w' as string]: relayWidth,
          ['--relay-h' as string]: relayHeight,
          gridTemplateColumns: RELAY_GRID_COLUMNS,
          gridTemplateRows: `repeat(${RELAY_MATRIX_ROWS}, var(--relay-h))`,
          columnGap: 'calc(var(--relay-w) * 0.45)',
          rowGap: '4px',
        }}
      >
        {rows.map((row, rowIndex) => {
          const gridRow = rowIndex + 1

          return (
            <div key={rowIndex} className="contents">
              <div className="min-w-0" style={{ gridColumn: 1, gridRow }}>
                {renderChannelBox(row.leftChannel, boxProps)}
              </div>
              <div className="min-w-0" style={{ gridColumn: 2, gridRow }}>
                {renderChannelBox(row.rightChannel, boxProps)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
