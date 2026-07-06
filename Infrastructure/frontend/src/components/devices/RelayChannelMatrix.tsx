import RelayChannelBox from './RelayChannelBox'
import type { RelayChannelViewModel } from './relayViewModel'
import { RELAY_MATRIX_ROWS, splitRelayByPhysicalLayout } from './relayViewModel'

export type RelayMatrixVariant = 'panel' | 'compact'

interface RelayChannelMatrixProps {
  channels: RelayChannelViewModel[]
  nowMs: number
  /** @deprecated Use variant="compact" instead */
  compact?: boolean
  variant?: RelayMatrixVariant
  location?: string
  editingChannel?: number | null
  onSelectChannel?: (channel: number) => void
  statusByChannel?: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }>
  menuOpenChannel?: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
}

interface ChannelBoxRenderProps {
  nowMs: number
  variant: RelayMatrixVariant
  currentLocation: string | undefined
  editingChannel: number | null
  onSelectChannel?: (channel: number) => void
  statusByChannel?: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }>
  menuOpenChannel: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
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
      statusText={props.statusByChannel?.[channel.channel]?.text}
      statusTone={props.statusByChannel?.[channel.channel]?.tone}
      isMenuOpen={props.menuOpenChannel === channel.channel}
      onToggleMenu={props.onToggleMenu}
      onMenuAction={props.onMenuAction}
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

export default function RelayChannelMatrix({
  channels,
  nowMs,
  compact = false,
  variant: variantProp,
  location,
  editingChannel = null,
  onSelectChannel,
  statusByChannel,
  menuOpenChannel = null,
  onToggleMenu,
  onMenuAction,
}: RelayChannelMatrixProps) {
  const variant: RelayMatrixVariant = variantProp ?? (compact ? 'compact' : 'panel')
  const { leftColumn, rightColumn } = splitRelayByPhysicalLayout(channels)

  const boxProps: ChannelBoxRenderProps = {
    nowMs,
    variant,
    currentLocation: location,
    editingChannel: editingChannel ?? null,
    onSelectChannel,
    statusByChannel,
    menuOpenChannel: menuOpenChannel ?? null,
    onToggleMenu,
    onMenuAction,
  }

  const gridCols = '1fr auto 1fr'

  return (
    <div className="rounded-sm bg-surface-secondary p-0">
      <div
        className="grid gap-x-1.5 gap-y-1 p-1"
        style={{
          ...GRID_BACKGROUND_STYLE,
          gridTemplateColumns: gridCols,
          gridTemplateRows: `repeat(${RELAY_MATRIX_ROWS}, auto)`,
        }}
      >
      <div
        className="flex items-center justify-center border-x border-border-emphasis/70 bg-surface-tertiary/30"
        style={{
          gridColumn: 2,
          gridRow: '1 / -1',
        }}
      />

      {Array.from({ length: RELAY_MATRIX_ROWS }, (_, rowIndex) => {
        const channelLeft = leftColumn[rowIndex]
        const channelRight = rightColumn[rowIndex]
        const gridRow = rowIndex + 1

        return (
          <div key={rowIndex} className="contents">
            <div className="min-w-0" style={{ gridColumn: 1, gridRow }}>
              {channelLeft ? renderChannelBox(channelLeft, boxProps) : null}
            </div>
            <div className="min-w-0" style={{ gridColumn: 3, gridRow }}>
              {channelRight ? renderChannelBox(channelRight, boxProps) : null}
            </div>
          </div>
        )
      })}
      </div>
    </div>
  )
}
