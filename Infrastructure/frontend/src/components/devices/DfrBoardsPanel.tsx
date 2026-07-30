import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { useControlSnapshot } from '../../hooks/useControlSnapshot'
import { apiClient } from '../../services/api'
import type { ControlSnapshotResponse } from '../../services/api/devices'
import { extractErrorMessage } from '../../utils/errors'
import { logger } from '../../utils/logger'

const DFR_BOARD_IDS = [0, 1, 2]
const DFR_CHANNELS = [0, 1] as const

type DfrBoard = ControlSnapshotResponse['dfr_boards'][number]
type DfrChannel = DfrBoard['channels'][number]
type DfrAssignment = DfrChannel['assignment']

export default function DfrBoardsPanel() {
  const { snapshot, refresh } = useControlSnapshot()
  const [workingKey, setWorkingKey] = useState<string | null>(null)
  const [renameDraftByKey, setRenameDraftByKey] = useState<Record<string, string>>({})
  const [testProgressKey, setTestProgressKey] = useState<string | null>(null)

  const boardsByKey = useMemo(() => {
    const m = new Map<number, DfrBoard>()
    if (!snapshot) return m
    for (const board of snapshot.dfr_boards) {
      m.set(board.board_id, board)
    }
    return m
  }, [snapshot])

  async function saveRename(boardId: number, ch: number, deviceId: number, currentName: string) {
    const key = `${boardId}:${ch}`
    const draft = (renameDraftByKey[key] ?? '').trim()
    if (!draft) {
      toast.error('Display name is required')
      return
    }
    if (draft === currentName) {
      return
    }
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(`rename:${key}`)
    try {
      await apiClient.updateDevice(deviceId, { display_name: draft })
      await refresh()
      toast.success('Light name updated')
    } catch (err) {
      logger.error('Failed to rename light', err)
      toast.error(extractErrorMessage(err, 'Failed to rename light'))
    } finally {
      setWorkingKey(null)
    }
  }

  async function testLight(boardId: number, ch: number, deviceId: number) {
    const opKey = `test:${boardId}:${ch}`
    if (workingKey) {
      toast.error('Another DFR action is in progress')
      return
    }
    setWorkingKey(opKey)
    setTestProgressKey(opKey)
    const progressTimer = window.setTimeout(() => setTestProgressKey(null), 5000)
    try {
      await apiClient.testLight(deviceId)
      toast.success('Light test completed')
    } catch (err) {
      logger.error('Failed to test light', err)
      toast.error(extractErrorMessage(err, 'Failed to test light'))
    } finally {
      window.clearTimeout(progressTimer)
      setTestProgressKey(null)
      setWorkingKey(null)
    }
  }

  return (
    <div className="rounded-lg border border-border-subtle bg-surface-primary p-2 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
          DFR0971 boards
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-md border border-border-emphasis bg-surface-secondary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary"
        >
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {DFR_BOARD_IDS.map((boardId) => {
          const board = boardsByKey.get(boardId)
          const isAvailable = board?.available ?? false

          return (
            <div
              key={boardId}
              className="rounded-lg border border-border-subtle bg-surface-primary p-2 flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">
                  DFR{boardId}
                </div>
                <div
                  className={`text-[11px] rounded-full px-2 py-0.5 border ${
                    isAvailable
                      ? 'bg-status-success-bg/30 text-status-success-text border-status-success-border/60'
                      : 'bg-status-danger-bg/30 text-status-danger-text border-status-danger-border/60'
                  }`}
                >
                  {isAvailable ? 'Available' : 'Missing'}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 flex-1 overflow-auto">
                {DFR_CHANNELS.map((ch) => {
                  const channelData = board?.channels.find((c) => c.channel === ch)
                  return (
                    <SlotRenderer
                      key={ch}
                      boardId={boardId}
                      ch={ch}
                      channelData={channelData}
                      renameDraftByKey={renameDraftByKey}
                      setRenameDraftByKey={setRenameDraftByKey}
                      testLight={testLight}
                      testProgressKey={testProgressKey}
                      saveRename={saveRename}
                      workingKey={workingKey}
                    />
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface SlotProps {
  boardId: number
  ch: 0 | 1
  channelData: DfrChannel | undefined
  renameDraftByKey: Record<string, string>
  setRenameDraftByKey: React.Dispatch<React.SetStateAction<Record<string, string>>>
  testLight: (boardId: number, ch: number, deviceId: number) => Promise<void>
  testProgressKey: string | null
  saveRename: (boardId: number, ch: number, deviceId: number, currentName: string) => Promise<void>
  workingKey: string | null
}

function SlotRenderer({
  boardId,
  ch,
  channelData,
  renameDraftByKey,
  setRenameDraftByKey,
  testLight,
  testProgressKey,
  saveRename,
  workingKey,
}: SlotProps) {
  const key = `${boardId}:${ch}`
  const testKey = `test:${boardId}:${ch}`
  const isTesting = testProgressKey === testKey
  const assignment: DfrAssignment = channelData?.assignment ?? null
  const commandedIntensity = channelData?.commanded_intensity ?? null
  const commandAcknowledged = channelData?.command_acknowledged ?? false

  const renameValue = assignment
    ? (renameDraftByKey[key] ?? (assignment.display_name ?? assignment.device_name ?? ''))
    : ''

  return (
    <div
      data-testid={`dfr-slot-${boardId}-${ch}`}
      className="rounded-md border border-border-subtle bg-surface-secondary p-2 space-y-2"
    >
      <div className="flex items-center justify-between">
        <div className="text-xs font-semibold text-text-default">CH{ch}</div>
        {assignment ? (
          <span className="inline-flex items-center rounded-full bg-btn-primary-dim/30 px-2 py-0.5 text-xs font-medium text-btn-primary-text">
            {assignment.location}
          </span>
        ) : (
          <span className="text-xs text-text-subtle">—</span>
        )}
      </div>

      {assignment ? (
        <div className="space-y-1">
          <div className="text-[11px] text-text-subtle">Commanded intensity</div>
          <div className="flex items-center gap-2">
            <div className="text-sm font-mono text-text-input">
              {commandedIntensity !== null ? `${commandedIntensity.toFixed(1)}%` : '—'}
            </div>
            <div
              className={`text-[10px] rounded-full px-1.5 py-0.5 border ${
                commandAcknowledged
                  ? 'bg-status-success-bg/30 text-status-success-text border-status-success-border/60'
                  : 'bg-status-warning-bg/30 text-status-warning-text border-status-warning-border/60'
              }`}
            >
              {commandAcknowledged ? 'Ack' : 'Pending'}
            </div>
          </div>

          <div className="text-[11px] text-text-subtle">Display name</div>
          <div className="flex items-center gap-2">
            <input
              value={renameValue}
              onChange={(e) =>
                setRenameDraftByKey((prev) => ({ ...prev, [key]: e.target.value }))
              }
              disabled={!!workingKey}
              className="w-full rounded-sm border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-input focus:outline-hidden focus:ring-2 focus:ring-btn-primary-light disabled:opacity-50"
              placeholder="Light name"
            />
            <button
              type="button"
              onClick={() => void saveRename(boardId, ch, assignment.device_id, assignment.display_name ?? assignment.device_name ?? '')}
              disabled={!!workingKey}
              className="rounded-md bg-btn-primary px-2 py-1 text-xs font-medium text-btn-primary-text hover:bg-btn-primary-hover disabled:opacity-50"
            >
              Save
            </button>
          </div>

          <div className="flex items-center gap-1 pt-1">
            <button
              type="button"
              data-testid={`test-btn-${boardId}-${ch}`}
              onClick={() => void testLight(boardId, ch, assignment.device_id)}
              disabled={!!workingKey || isTesting}
              className="rounded-md border border-border-emphasis bg-surface-primary px-2 py-1 text-xs text-text-secondary hover:bg-surface-tertiary disabled:opacity-50"
            >
              {isTesting ? 'Testing…' : 'Test'}
            </button>
            {isTesting && (
              <div
                data-testid={`test-progress-${boardId}-${ch}`}
                className="flex-1 h-1 rounded-full bg-border-emphasis overflow-hidden"
              >
                <div
                  className="h-full bg-btn-primary-light transition-all"
                  style={{ width: '100%', animation: 'dfr-test-progress 5s linear' }}
                />
              </div>
            )}
          </div>
          <div className="text-[11px] text-text-subtle">
            Device key: <span className="font-mono">{assignment.device_name}</span>
          </div>
        </div>
      ) : (
        <div className="text-xs text-text-subtle">Unassigned</div>
      )}
    </div>
  )
}
