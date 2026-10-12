/**
 * Time-range toolbar for the monitoring dashboards.
 *
 * A controlled component: the parent owns the store range and passes it in via
 * `range`/`isLive`, plus callbacks that map to `MonitoringStore.setLiveRange`,
 * `setFixedRange`, `pause`, and `resume`. The toolbar
 * renders bounded presets, a live/paused indicator with Pause/Resume, absolute
 * Toronto wall-time inputs (with DST gap/fold handling), a Now action, Reset
 * Zoom, a timezone label, inline validation, and URL state via
 * `useSearchParams` so browser back/forward restores the range. `maxRangeMs`
 * caps fixed ranges (the default seven days; `null` for all-history pages),
 * and `suppressInitialUrlParse` lets pages hand an already-parsed range to
 * freshly mounted branch-local toolbars without a stale URL re-read.
 *
 * On historical routes (`historical`) the live preset group is replaced by the
 * fixed history presets plus the Calendar-backed "Current run" action: every
 * preset resolves to a `now`-anchored fixed range, the five-day minimum hands
 * off to regular Monitoring through `onMonitoringRoute`, and the current-run
 * button stays disabled with an honest explanation until the calendar returns
 * exactly one current run.
 */
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { MonitoringRange } from '../state'

import { MonitoringToolbarStatus } from './MonitoringToolbarStatus'
import type { ToolbarMonitoring } from './TimeRangeToolbar.monitoring'
export type { ToolbarMonitoring } from './TimeRangeToolbar.monitoring'
import {
  AbsoluteRangeFeedback,
  AbsoluteRangeForm,
  fixedInputError,
  formatWallInput,
  type FallFoldState,
} from './TimeRangeToolbar.inputs'
import {
  parseWallInput,
  resolveWallTimeWithChoice,
  type FallFoldChoice,
} from '../../../utils/torontoWallTime'

import {
  HISTORICAL_PRESETS,
  PRESETS,
  MAX_RANGE_MS,
  historicalPresetRange,
  offsetLabel,
  parseUrlRange,
  validateRange,
  withSerializedRange,
  type HistoricalPreset,
  type MaxRangeCap,
} from './timeRangeToolbar.time'

export interface HistoricalCurrentRunControl {
  /** Enabled only when the calendar returned exactly one current run. */
  readonly ready: boolean
  /** Honest loading/absent/ambiguous/error explanation, or the ready action summary. */
  readonly title: string
  /** Apply the unique-run range (room calendar start → now). */
  readonly onSelect: () => void
}

/**
 * Historical-route toolbar behavior. Presence replaces the live preset group
 * with the fixed history presets and switches the toolbar to the historical
 * range contract.
 */
export interface HistoricalToolbarControl {
  /** Route the five-day minimum handoff to the sector's regular Monitoring page. */
  readonly onMonitoringRoute: (start: Date, end: Date) => void
  /** Calendar current-run state behind the trailing preset action. */
  readonly currentRun: HistoricalCurrentRunControl
}

export interface TimeRangeToolbarProps {
  range: MonitoringRange
  isLive: boolean
  onLive: (duration: number) => void
  onFixedRange: (start: Date, end: Date) => void
  onPause: () => void
  onResume: () => void
  onResetZoom?: () => void
  now?: () => Date
  defaultDuration?: number
  /** Fixed-range cap; `null` opts the page into unlimited fixed ranges
   * (all-history overview pages). Live windows stay detailed and capped. */
  maxRangeMs?: MaxRangeCap
  /**
   * Skip the URL read on first mount and treat the incoming `range` as
   * authoritative. Range-owning pages pass this on toolbars that appear after a
   * mode change so a toolbar mounted during a pending router transition cannot
   * re-apply a stale URL range and fight the owner.
   */
  suppressInitialUrlParse?: boolean
  /** Historical-route preset group and current-run control; undefined on the
   * regular Monitoring routes, which keep the live preset group. */
  historical?: HistoricalToolbarControl
  monitoring?: ToolbarMonitoring
}

export function TimeRangeToolbar({
  range,
  onLive,
  onFixedRange,
  onPause,
  onResume,
  onResetZoom,
  now,
  defaultDuration,
  maxRangeMs,
  suppressInitialUrlParse,
  historical,
  monitoring,
}: TimeRangeToolbarProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const maxCapMs = maxRangeMs === undefined ? MAX_RANGE_MS : maxRangeMs
  const nowFn = now ?? (() => new Date())
  const live = range.kind === 'live'
  const effectiveEnd = live ? nowFn() : range.end
  const effectiveStart = live ? new Date(effectiveEnd.getTime() - range.duration) : range.start
  const effectiveStartInput = formatWallInput(effectiveStart)
  const effectiveEndInput = formatWallInput(effectiveEnd)

  const [selectedDuration, setSelectedDuration] = useState<number>(() =>
    range.kind === 'live' ? range.duration : (defaultDuration ?? PRESETS[1].duration)
  )
  const [startInput, setStartInput] = useState(effectiveStartInput)
  const [endInput, setEndInput] = useState(effectiveEndInput)
  const [editingRange, setEditingRange] = useState(false)
  const [fallFold, setFallFold] = useState<FallFoldState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)

  const callbacksRef = useRef({ onLive, onFixedRange })
  callbacksRef.current = { onLive, onFixedRange }
  const lastWrittenRef = useRef<string | null>(null)
  const initializedRef = useRef(false)
  const skipNextWriteRef = useRef(false)
  const prevRangeRef = useRef(range)

  // Keep the selected preset duration in sync with the live range.
  useEffect(() => {
    if (range.kind === 'live') setSelectedDuration(range.duration)
  }, [range])

  useEffect(() => {
    setEditingRange(false)
    setError(null)
    setFallFold(null)
    setPaused(false)
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
    lastWrittenRef.current = current
    const firstRead = !initializedRef.current
    initializedRef.current = true
    if (firstRead && suppressInitialUrlParse) return
    const parsed = parseUrlRange(searchParams, maxCapMs)
    const defaultDurationMs = defaultDuration ?? PRESETS[1].duration
    // When the parsed range already equals the owned range, applying it again
    // would only hand the parent a duplicate value; arming the write-skip for
    // an apply that never happens would then swallow the next genuine URL
    // write, so neither happens for an equal parse. A parameterless URL equals
    // the default live window for an owner already on it.
    const parseEqualsRange =
      (parsed.kind === 'live' && range.kind === 'live' && parsed.duration === range.duration) ||
      (parsed.kind === 'fixed' &&
        range.kind === 'fixed' &&
        parsed.start.getTime() === range.start.getTime() &&
        parsed.end.getTime() === range.end.getTime()) ||
      (parsed.kind === 'none' &&
        range.kind === 'live' &&
        range.duration === defaultDurationMs)
    if (parsed.kind === 'live') {
      setSelectedDuration(parsed.duration)
    } else if (parsed.kind === 'none' && !firstRead) {
      const duration = defaultDuration ?? PRESETS[1].duration
      setSelectedDuration(duration)
      setPaused(false)
    }
    if (parsed.kind !== 'none' && !parseEqualsRange) {
      skipNextWriteRef.current = true
      if (parsed.kind === 'live') callbacksRef.current.onLive(parsed.duration)
      else callbacksRef.current.onFixedRange(parsed.start, parsed.end)
    } else if (parsed.kind === 'none' && !firstRead) {
      skipNextWriteRef.current = true
      callbacksRef.current.onLive(defaultDuration ?? PRESETS[1].duration)
    }
  }, [maxCapMs, defaultDuration, range, searchParams])

  // Write the URL only after the initial read and when the range actually
  // changes, so the mount read is never overwritten by the default range.
  useEffect(() => {
    if (!initializedRef.current) return
    if (prevRangeRef.current === range) return
    prevRangeRef.current = range
    if (skipNextWriteRef.current) {
      skipNextWriteRef.current = false
      return
    }
    const nextSearchParams = withSerializedRange(searchParams, range)
    const nextSerialized = nextSearchParams.toString()
    if (nextSerialized === searchParams.toString()) return
    lastWrittenRef.current = nextSerialized
    setSearchParams(nextSearchParams, { replace: false })
  }, [range, searchParams, setSearchParams])

  const tzTimestamp = live ? nowFn() : range.start
  const tzLabel = offsetLabel(tzTimestamp)

  function applyPreset(duration: number): void {
    setPaused(false)
    setSelectedDuration(duration)
    setError(null)
    callbacksRef.current.onLive(duration)
  }

  /**
   * Historical presets always resolve to a `now`-anchored fixed range: day and
   * week presets subtract a fixed span, month presets subtract Toronto
   * calendar months. The five-day minimum routes to regular Monitoring; every
   * other preset stays on the Historical page as a fixed range.
   */
  function applyHistoricalPreset(preset: HistoricalPreset): void {
    const rangeNow = nowFn()
    const next = historicalPresetRange(preset, rangeNow)
    if (next === null || historical === undefined) return
    setPaused(false)
    setError(null)
    setFallFold(null)
    if (preset.route === 'monitoring') historical.onMonitoringRoute(next.start, next.end)
    else callbacksRef.current.onFixedRange(next.start, next.end)
  }

  function handleNow(): void {
    setPaused(false)
    setError(null)
    callbacksRef.current.onLive(selectedDuration)
  }

  function submitFixed(override?: { field: 'start' | 'end'; choice: FallFoldChoice }): void {
    const startChoice =
      override?.field === 'start'
        ? override.choice
        : fallFold?.field === 'start'
          ? fallFold.choice
          : null
    const endChoice =
      override?.field === 'end'
        ? override.choice
        : fallFold?.field === 'end'
          ? fallFold.choice
          : null
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
    const validation = validateRange(startRes.utc, endRes.utc, maxCapMs)
    if (validation !== null) {
      setError(validation)
      return
    }
    setError(null)
    setFallFold(null)
    setPaused(false)
    setEditingRange(false)
    callbacksRef.current.onFixedRange(startRes.utc, endRes.utc)
  }

  function chooseFallFold(choice: FallFoldChoice): void {
    if (fallFold === null) return
    setFallFold({ ...fallFold, choice })
    submitFixed({ field: fallFold.field, choice })
  }

  const errorId = 'mon-toolbar-error'
  const inputError = fixedInputError(startInput, endInput, maxCapMs)
  const formError = error ?? inputError
  return (
    <div className="mon-toolbar">
      <div className="mon-toolbar__controls">
        <div className="mon-toolbar__presets" role="group" aria-label="Time range presets">
          {historical === undefined ? (
            <>
              {PRESETS.map(p => (
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
            </>
          ) : (
            <>
              {HISTORICAL_PRESETS.map(p => (
                <button
                  key={p.label}
                  type="button"
                  className="mon-toolbar__preset"
                  onClick={() => applyHistoricalPreset(p)}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                className="mon-toolbar__now"
                disabled={!historical.currentRun.ready}
                title={historical.currentRun.title}
                onClick={() => historical.currentRun.onSelect()}
              >
                Current run
              </button>
            </>
          )}
        </div>
        <AbsoluteRangeForm
          startInput={startInput}
          endInput={endInput}
          error={formError}
          errorId={errorId}
          onStartChange={value => {
            setStartInput(value)
            setEditingRange(true)
            setError(null)
            setFallFold(null)
          }}
          onEndChange={value => {
            setEndInput(value)
            setEditingRange(true)
            setError(null)
            setFallFold(null)
          }}
          onApply={() => submitFixed()}
          applyDisabled={inputError !== null}
        />
        <div className="mon-toolbar__status">
          <span
            className={`mon-status__badge mon-status__badge--${live ? 'loading' : 'normal'}`}
            role="status"
          >
            {paused ? 'PAUSED' : live ? 'LIVE' : 'FIXED'}
          </span>
          {live && !paused && (
            <button
              type="button"
              onClick={() => {
                setPaused(true)
                onPause()
              }}
            >
              Pause
            </button>
          )}
          {live && paused && (
            <button
              type="button"
              onClick={() => {
                setPaused(false)
                onResume()
              }}
            >
              Resume
            </button>
          )}
          <span className="mon-toolbar__tz">{tzLabel}</span>
          {onResetZoom && (
            <button type="button" onClick={onResetZoom}>
              Reset Zoom
            </button>
          )}
        </div>
      </div>
      <AbsoluteRangeFeedback
        error={formError}
        fallFold={fallFold}
        errorId={errorId}
        onFallFoldChoice={chooseFallFold}
      />
      {monitoring && <MonitoringToolbarStatus monitoring={monitoring} />}
    </div>
  )
}
