/**
 * Time-range toolbar for the monitoring dashboards.
 *
 * A controlled component: the parent owns the store range and passes it in via
 * `range`/`isLive`, plus callbacks that map to `MonitoringStore.setLiveRange`,
 * `setFixedRange`, `pause`, `resume`, and the chart's `resetZoom`. The toolbar
 * renders bounded presets, a live/paused indicator with Pause/Resume, absolute
 * Toronto wall-time inputs (with DST gap/fold handling), a Now action, Reset
 * Zoom, a timezone label, inline validation, and URL state via
 * `useSearchParams` so browser back/forward restores the range.
 */
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { MonitoringRange } from '../state'
import { MonitoringStatus } from './MonitoringStatus'
export type { ToolbarMonitoring } from './TimeRangeToolbar.monitoring'
import {
  PRESETS,
  offsetLabel,
  parseUrlRange,
  parseWallInput,
  resolveWallTimeWithChoice,
  serializeRange,
  validateRange,
  type FallFoldChoice,
} from './timeRangeToolbar.time'
import {
  AbsoluteRangeForm,
  fixedInputError,
  formatWallInput,
  type FallFoldState,
} from './TimeRangeToolbar.inputs'

export interface TimeRangeToolbarProps {
  range: MonitoringRange
  isLive: boolean
  onLive: (duration: number) => void
  onFixedRange: (start: Date, end: Date) => void
  onPause: () => void
  onResume: () => void
  onResetZoom: () => void
  now?: () => Date
  defaultDuration?: number
  monitoring?: import('./TimeRangeToolbar.monitoring').ToolbarMonitoring
}

export function TimeRangeToolbar({
  range,
  isLive,
  onLive,
  onFixedRange,
  onPause,
  onResume,
  onResetZoom,
  now,
  defaultDuration,
  monitoring,
}: TimeRangeToolbarProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const nowFn = now ?? (() => new Date())
  const live = range.kind === 'live'
  const effectiveEnd = live ? nowFn() : range.end
  const effectiveStart = live ? new Date(effectiveEnd.getTime() - range.duration) : range.start
  const effectiveStartInput = formatWallInput(effectiveStart)
  const effectiveEndInput = formatWallInput(effectiveEnd)

  const [selectedDuration, setSelectedDuration] = useState<number>(() =>
    range.kind === 'live' ? range.duration : (defaultDuration ?? PRESETS[1].duration),
  )
  const [paused, setPaused] = useState(false)
  const [startInput, setStartInput] = useState(effectiveStartInput)
  const [endInput, setEndInput] = useState(effectiveEndInput)
  const [editingRange, setEditingRange] = useState(false)
  const [fallFold, setFallFold] = useState<FallFoldState | null>(null)
  const [error, setError] = useState<string | null>(null)

  const callbacksRef = useRef({ onLive, onFixedRange })
  callbacksRef.current = { onLive, onFixedRange }
  const lastWrittenRef = useRef<string | null>(null)
  const initializedRef = useRef(false)
  const prevRangeRef = useRef(range)

  // Keep the selected preset duration in sync with the live range.
  useEffect(() => {
    if (range.kind === 'live') setSelectedDuration(range.duration)
  }, [range])

  useEffect(() => {
    setEditingRange(false)
    setError(null)
    setFallFold(null)
  }, [range])

  useEffect(() => {
    if (editingRange) return
    setStartInput(effectiveStartInput)
    setEndInput(effectiveEndInput)
  }, [editingRange, effectiveEndInput, effectiveStartInput])

  // Read the URL on mount and on external changes (browser back/forward).
  useEffect(() => {
    const current = searchParams.toString()
    if (current === lastWrittenRef.current) return
    const parsed = parseUrlRange(searchParams)
    if (parsed.kind === 'live') {
      setSelectedDuration(parsed.duration)
      callbacksRef.current.onLive(parsed.duration)
    } else if (parsed.kind === 'fixed') {
      callbacksRef.current.onFixedRange(parsed.start, parsed.end)
    }
    initializedRef.current = true
  }, [searchParams])

  // Write the URL only after the initial read and when the range actually
  // changes, so the mount read is never overwritten by the default range.
  useEffect(() => {
    if (!initializedRef.current) return
    if (prevRangeRef.current === range) return
    prevRangeRef.current = range
    const serialized = serializeRange(range)
    const nextSearchParams = new URLSearchParams(searchParams)
    nextSearchParams.delete('range')
    nextSearchParams.delete('start')
    nextSearchParams.delete('end')
    new URLSearchParams(serialized).forEach((value, key) => nextSearchParams.set(key, value))
    const nextSerialized = nextSearchParams.toString()
    if (nextSerialized === searchParams.toString()) return
    lastWrittenRef.current = nextSerialized
    setSearchParams(nextSearchParams, { replace: false })
  }, [range, searchParams, setSearchParams])

  const tzTimestamp = live ? nowFn() : range.start
  const tzLabel = offsetLabel(tzTimestamp)

  function applyPreset(duration: number): void {
    setSelectedDuration(duration)
    setError(null)
    callbacksRef.current.onLive(duration)
  }

  function handlePause(): void {
    setPaused(true)
    onPause()
  }

  function handleResume(): void {
    setPaused(false)
    onResume()
  }

  function handleNow(): void {
    setError(null)
    callbacksRef.current.onLive(selectedDuration)
  }

  function submitFixed(override?: { field: 'start' | 'end'; choice: FallFoldChoice }): void {
    const startChoice =
      override?.field === 'start' ? override.choice : fallFold?.field === 'start' ? fallFold.choice : null
    const endChoice =
      override?.field === 'end' ? override.choice : fallFold?.field === 'end' ? fallFold.choice : null
    const startComps = parseWallInput(startInput)
    const endComps = parseWallInput(endInput)
    if (startComps === null || endComps === null) {
      setError('Enter both a start and an end time')
      return
    }
    const startRes = resolveWallTimeWithChoice(startComps, startChoice)
    const endRes = resolveWallTimeWithChoice(endComps, endChoice)
    if (startRes.kind === 'nonexistent' || endRes.kind === 'nonexistent') {
      setError('That time does not exist in Toronto (spring-forward gap)')
      return
    }
    if (startRes.kind === 'ambiguous') {
      setFallFold({
        field: 'start',
        choice: null,
        firstUtc: startRes.firstUtc,
        secondUtc: startRes.secondUtc,
      })
      setError(null)
      return
    }
    if (endRes.kind === 'ambiguous') {
      setFallFold({
        field: 'end',
        choice: null,
        firstUtc: endRes.firstUtc,
        secondUtc: endRes.secondUtc,
      })
      setError(null)
      return
    }
    const validation = validateRange(startRes.utc, endRes.utc)
    if (validation !== null) {
      setError(validation)
      return
    }
    setError(null)
    setFallFold(null)
    setEditingRange(false)
    callbacksRef.current.onFixedRange(startRes.utc, endRes.utc)
  }

  function chooseFallFold(choice: FallFoldChoice): void {
    if (fallFold === null) return
    setFallFold({ ...fallFold, choice })
    submitFixed({ field: fallFold.field, choice })
  }

  function clearInputs(): void {
    setEditingRange(false)
    setError(null)
    setFallFold(null)
  }

  const errorId = 'mon-toolbar-error'
  const inputError = fixedInputError(startInput, endInput)
  const formError = error ?? inputError
  const activeDuration = range.kind === 'live' ? range.duration : selectedDuration
  const livePreset = PRESETS.find((preset) => preset.duration === activeDuration)
  const statusLabel = isLive ? (paused ? 'PAUSED' : 'LIVE') : 'FIXED'

  return (
    <div className="mon-toolbar">
      <div className="mon-toolbar__presets" role="group" aria-label="Time range presets">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            className="mon-toolbar__preset"
            aria-pressed={live && range.duration === p.duration}
            onClick={() => applyPreset(p.duration)}
          >
            {p.label}
          </button>
        ))}
        <button type="button" className="mon-toolbar__now" onClick={handleNow}>
          Now
        </button>
      </div>
      {monitoring && (
        <MonitoringStatus
          errors={monitoring.errors}
          tailLoading={monitoring.tailLoading}
          reconciling={monitoring.reconciling}
          anchorQuality={monitoring.anchorQuality}
          projectionRevision={monitoring.projectionRevision}
          runtimeSnapshotVersion={monitoring.runtimeSnapshotVersion}
          lastGoodRangeAt={monitoring.lastGoodRangeAt}
          rangeErrorAt={monitoring.rangeErrorAt}
          onRetry={monitoring.onRetry}
          isLive={live && !paused}
        />
      )}

      <div className="mon-toolbar__status">
        <span className="mon-toolbar__live" role="status">
          <span>{statusLabel}</span>
          {isLive && !paused && <> · {livePreset?.label ?? 'custom'}</>}
        </span>
        {live && (
          <button type="button" onClick={paused ? handleResume : handlePause}>
            {paused ? 'Resume' : 'Pause'}
          </button>
        )}
        <button type="button" onClick={onResetZoom}>
          Reset Zoom
        </button>
        <span className="mon-toolbar__tz">{tzLabel}</span>
      </div>

      <AbsoluteRangeForm
        startInput={startInput}
        endInput={endInput}
        error={formError}
        fallFold={fallFold}
        errorId={errorId}
        onStartChange={(value) => {
          setStartInput(value)
          setEditingRange(true)
          setError(null)
          setFallFold(null)
        }}
        onEndChange={(value) => {
          setEndInput(value)
          setEditingRange(true)
          setError(null)
          setFallFold(null)
        }}
        onApply={() => submitFixed()}
        onClear={clearInputs}
        onFallFoldChoice={chooseFallFold}
        applyDisabled={inputError !== null}
      />
    </div>
  )
}
