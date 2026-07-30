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

/**
 * Grid layout: 8 rows × 2 columns, matching the two physical MCP23017 banks
 * (left bank R1-R8, right bank R9-R16). Horizontal spacing between the banks
 * is 110% of the relay box width via a CSS custom property.
 */
const RELAY_GRID_COLUMNS = 'var(--relay-w) var(--relay-w)'

export default function RelayChannelMatrix({
  channels,
  nowMs,
  compact = false,
  variant: variantProp,
  location,
  editingChannel = null,
  onSelectChannel,
  menuOpenChannel = null,
  onToggleMenu,
  onMenuAction,
}: RelayChannelMatrixProps) {
  const variant: RelayMatrixVariant = variantProp ?? (compact ? 'compact' : 'panel')
  const { rows } = splitRelayByPhysicalLayout(channels)

  const boxProps: ChannelBoxRenderProps = {
    nowMs,
    variant,
    currentLocation: location,
    editingChannel: editingChannel ?? null,
    onSelectChannel,
    menuOpenChannel: menuOpenChannel ?? null,
    onToggleMenu,
    onMenuAction,
  }

  const relayWidth = variant === 'panel' ? '198px' : '150px'

  return (
    <div className="rounded-sm bg-surface-secondary p-0">
      <div
        className="relative grid p-1 min-w-0 max-w-full overflow-auto"
        style={{
          ...GRID_BACKGROUND_STYLE,
          ['--relay-w' as string]: relayWidth,
          ['--relay-h' as string]: 'calc(var(--relay-w) * 11 / 20)',
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
