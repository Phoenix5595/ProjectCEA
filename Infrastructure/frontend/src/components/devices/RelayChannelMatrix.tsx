import RelayChannelBox from './RelayChannelBox'
import type { RelayChannelViewModel } from './relayViewModel'

interface RelayChannelMatrixProps {
  channels: RelayChannelViewModel[]
  nowMs: number
  compact?: boolean
  editingChannel?: number | null
  onSelectChannel?: (channel: number) => void
  statusByChannel?: Record<number, { text: string; tone: 'unknown' | 'active' | 'idle' }>
  menuOpenChannel?: number | null
  onToggleMenu?: (channel: number) => void
  onMenuAction?: (channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off') => void
}

export default function RelayChannelMatrix({
  channels,
  nowMs,
  compact = false,
  editingChannel = null,
  onSelectChannel,
  statusByChannel,
  menuOpenChannel = null,
  onToggleMenu,
  onMenuAction,
}: RelayChannelMatrixProps) {
  const columnA = channels.slice(0, 8)
  const columnB = channels.slice(8, 16)

  return (
    <div className="rounded-md border border-border-emphasis bg-surface-secondary p-2">
      {!compact && (
        <div className="mb-2 grid grid-cols-2 gap-2 px-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
          <div>Bank A (0-7)</div>
          <div>Bank B (8-15)</div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <div className="grid grid-rows-8 gap-2">
          {columnA.map((channel) => (
            <RelayChannelBox
              key={channel.channel}
              channel={channel}
              nowMs={nowMs}
              compact
              isEditing={editingChannel === channel.channel}
              onSelect={onSelectChannel}
              statusText={statusByChannel?.[channel.channel]?.text}
              statusTone={statusByChannel?.[channel.channel]?.tone}
              isMenuOpen={menuOpenChannel === channel.channel}
              onToggleMenu={onToggleMenu}
              onMenuAction={onMenuAction}
            />
          ))}
        </div>

        <div className="grid grid-rows-8 gap-2">
          {columnB.map((channel) => (
            <RelayChannelBox
              key={channel.channel}
              channel={channel}
              nowMs={nowMs}
              compact
              isEditing={editingChannel === channel.channel}
              onSelect={onSelectChannel}
              statusText={statusByChannel?.[channel.channel]?.text}
              statusTone={statusByChannel?.[channel.channel]?.tone}
              isMenuOpen={menuOpenChannel === channel.channel}
              onToggleMenu={onToggleMenu}
              onMenuAction={onMenuAction}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

