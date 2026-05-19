import RelayChannelBox from './RelayChannelBox'
import type { RelayChannelViewModel } from './relayViewModel'
import { RELAY_CHANNEL_COUNT, RELAY_MATRIX_ROWS, splitRelayBanks } from './relayViewModel'

export type RelayMatrixVariant = 'panel' | 'compact'

interface RelayChannelMatrixProps {
  channels: RelayChannelViewModel[]
  nowMs: number
  /** @deprecated Use variant="compact" instead */
  compact?: boolean
  variant?: RelayMatrixVariant
  editingChannel?: number | null
  onSelectChannel?: (channel: number) => void
  statusByChannel?: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }>
  menuOpenChannel?: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
}

function TerminalStrip({ bankLabel }: { bankLabel: 'A' | 'B' }) {
  return (
    <div className="flex gap-0.5" aria-hidden>
      {(['COM', 'NO', 'NC'] as const).map((terminal) => (
        <div
          key={`${bankLabel}-${terminal}`}
          className="flex-1 rounded-sm border border-border-emphasis/80 bg-surface-tertiary/60 px-0.5 py-px text-center font-mono text-[7px] uppercase text-text-subtle"
        >
          {terminal}
        </div>
      ))}
    </div>
  )
}

function LowLevelInputStrip() {
  return (
    <div className="flex flex-wrap justify-center gap-0.5" aria-hidden>
      {Array.from({ length: RELAY_CHANNEL_COUNT }, (_, index) => (
        <span
          key={index}
          className="inline-flex h-3 min-w-[14px] items-center justify-center rounded-sm border border-border-emphasis/60 bg-surface-tertiary/50 font-mono text-[7px] text-text-subtle"
        >
          {index + 1}
        </span>
      ))}
    </div>
  )
}

interface ChannelBoxRenderProps {
  nowMs: number
  variant: RelayMatrixVariant
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
  editingChannel = null,
  onSelectChannel,
  statusByChannel,
  menuOpenChannel = null,
  onToggleMenu,
  onMenuAction,
}: RelayChannelMatrixProps) {
  const variant: RelayMatrixVariant = variantProp ?? (compact ? 'compact' : 'panel')
  const isPanel = variant === 'panel'
  const { bankA, bankB } = splitRelayBanks(channels)

  const boxProps: ChannelBoxRenderProps = {
    nowMs,
    variant,
    editingChannel: editingChannel ?? null,
    onSelectChannel,
    statusByChannel,
    menuOpenChannel: menuOpenChannel ?? null,
    onToggleMenu,
    onMenuAction,
  }

  const gridCols = isPanel ? 'auto 1fr auto 1fr' : '1fr auto 1fr'

  return (
    <div
      className={`rounded-sm border border-border-emphasis bg-surface-secondary shadow-[inset_0_1px_0_var(--border-subtle)] ${isPanel ? 'p-2' : 'p-1'}`}
    >
      {isPanel && (
        <div className="mb-2 border-b border-border-emphasis/80 pb-2">
          <div className="font-mono text-xs font-semibold uppercase tracking-wide text-text-default">
            16-CH Relay Module
          </div>
          <div className="font-mono text-[10px] text-text-muted">MCP23017 · SainSmart layout</div>
        </div>
      )}

      {isPanel && (
        <div className="mb-2 grid gap-x-1.5 gap-y-0.5" style={{ gridTemplateColumns: gridCols }}>
          <div />
          <TerminalStrip bankLabel="A" />
          <div />
          <TerminalStrip bankLabel="B" />
        </div>
      )}

      {isPanel && (
        <div
          className="mb-1 grid gap-x-1.5 px-0.5 text-[9px] font-semibold uppercase tracking-wide text-text-muted"
          style={{ gridTemplateColumns: gridCols }}
        >
          <div />
          <div>Bank A (CH 0–7)</div>
          <div />
          <div>Bank B (CH 8–15)</div>
        </div>
      )}

      <div
        className="grid gap-x-1.5 gap-y-1 rounded-sm border border-border-subtle/80 p-1"
        style={{
          ...GRID_BACKGROUND_STYLE,
          gridTemplateColumns: gridCols,
          gridTemplateRows: `repeat(${RELAY_MATRIX_ROWS}, auto)`,
        }}
      >
        <div
          className="flex items-center justify-center border-x border-border-emphasis/70 bg-surface-tertiary/30"
          style={{
            gridColumn: isPanel ? 3 : 2,
            gridRow: '1 / -1',
          }}
        >
          <span className="rotate-180 font-mono text-[8px] font-semibold uppercase tracking-widest text-text-subtle [writing-mode:vertical-rl]">
            COM
          </span>
        </div>

        {Array.from({ length: RELAY_MATRIX_ROWS }, (_, rowIndex) => {
          const channelA = bankA[rowIndex]
          const channelB = bankB[rowIndex]
          const gridRow = rowIndex + 1
          const bankACol = isPanel ? 2 : 1
          const bankBCol = isPanel ? 4 : 3

          return (
            <div key={rowIndex} className="contents">
              {isPanel && (
                <div
                  className="flex w-4 items-center justify-center font-mono text-[9px] text-text-subtle"
                  style={{ gridColumn: 1, gridRow }}
                >
                  {rowIndex + 1}
                </div>
              )}
              <div className="min-w-0" style={{ gridColumn: bankACol, gridRow }}>
                {channelA ? renderChannelBox(channelA, boxProps) : null}
              </div>
              <div className="min-w-0" style={{ gridColumn: bankBCol, gridRow }}>
                {channelB ? renderChannelBox(channelB, boxProps) : null}
              </div>
            </div>
          )
        })}
      </div>

      {isPanel && (
        <div className="mt-2 border-t border-border-emphasis/80 pt-2">
          <div className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-wide text-text-muted">
            Low-level Input
          </div>
          <LowLevelInputStrip />
        </div>
      )}
    </div>
  )
}
