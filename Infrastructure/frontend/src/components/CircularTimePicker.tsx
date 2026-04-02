import { useState, useEffect, useRef, useCallback, useLayoutEffect } from 'react'
import CircularClockFace from './CircularClockFace'
import { useClockInteraction } from '../hooks/useClockInteraction'
import { calculatePhotoperiod, minutesToTime, timeToMinutes } from '../utils/timeMath'

/**
 * When the picker container is narrower than this (px), fields move under the dial in one row.
 * Lower value → switch to stacked layout **later** (stay wide longer). Higher → stack sooner.
 * ZoneConfig ~30% column: 1080p FS ~576px stays wide; ~half-width windows in the ~400px range stack.
 */
const STACK_LAYOUT_MAX_WIDTH_PX = 480

interface CircularTimePickerProps {
  dayStartTime: string
  dayEndTime: string
  onDayStartChange: (time: string) => void
  onDayEndChange: (time: string) => void
  label?: string
  period?: 'day' | 'night'
  rampUpDuration?: number | null
  rampDownDuration?: number | null
  onRampUpChange?: (duration: number | null) => void
  onRampDownChange?: (duration: number | null) => void
  showPresetButtons?: boolean
  lockedPhotoperiodHours?: number | null
  size?: number
}

const inputTimeClass = `w-16 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded text-text-input`
const inputRampClass =
  'w-12 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded-sm text-text-input [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none'

export default function CircularTimePicker({
  dayStartTime,
  dayEndTime,
  onDayStartChange,
  onDayEndChange,
  label,
  period = 'day',
  rampUpDuration,
  rampDownDuration,
  onRampUpChange,
  onRampDownChange,
  showPresetButtons: _showPresetButtons = true,
  lockedPhotoperiodHours = null,
  size: propsSize,
}: CircularTimePickerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const outerRef = useRef<HTMLDivElement>(null)
  /** Flex cell that should receive remaining height (more stable than aspect-square on first paint). */
  const clockSlotRef = useRef<HTMLDivElement>(null)
  const clockSquareRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState(propsSize || 300)
  const [stackLayout, setStackLayout] = useState(false)

  /**
   * Size from the inner `aspect-square` box only when it is actually square (layout settled).
   * Do **not** use `min(slotWidth, slotHeight)` for a wide flex cell: e.g. 300×100 has ratio 3.0
   * (below the old 3.5 “skew” threshold) and wrongly produced a 100px dial until window resize.
   */
  const isSquareish = useCallback((w: number, h: number, maxAspect = 1.12) => {
    if (w < 8 || h < 8) return false
    return Math.max(w, h) / Math.min(w, h) <= maxAspect
  }, [])

  const measureClock = useCallback(() => {
    if (propsSize) return
    const sq = clockSquareRef.current
    if (sq) {
      const r = sq.getBoundingClientRect()
      if (isSquareish(r.width, r.height)) {
        const side = Math.floor(Math.min(r.width, r.height))
        setSize(Math.max(100, Math.min(side, 900)))
        return
      }
    }
    const slot = clockSlotRef.current
    if (slot) {
      const r = slot.getBoundingClientRect()
      if (isSquareish(r.width, r.height)) {
        const side = Math.floor(Math.min(r.width, r.height))
        setSize(Math.max(100, Math.min(side, 900)))
      }
    }
  }, [propsSize, isSquareish])

  /** Last resort: min of square sides even if aspect slightly off (fonts/layout drift). */
  const measureClockForce = useCallback(() => {
    if (propsSize) return
    const sq = clockSquareRef.current
    const slot = clockSlotRef.current
    const r = sq?.getBoundingClientRect() ?? slot?.getBoundingClientRect()
    if (!r || r.width < 8 || r.height < 8) return
    setSize(Math.max(100, Math.min(Math.floor(Math.min(r.width, r.height)), 900)))
  }, [propsSize])

  const syncStackFromOuterWidth = useCallback(() => {
    const el = outerRef.current
    if (!el) return
    const w = el.getBoundingClientRect().width
    setStackLayout(w < STACK_LAYOUT_MAX_WIDTH_PX)
  }, [])

  useLayoutEffect(() => {
    syncStackFromOuterWidth()
  }, [syncStackFromOuterWidth])

  useEffect(() => {
    if (!outerRef.current) return
    const el = outerRef.current
    const ro = new ResizeObserver(() => {
      syncStackFromOuterWidth()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [syncStackFromOuterWidth])

  useEffect(() => {
    if (propsSize) {
      setSize(propsSize)
      return
    }
    const slot = clockSlotRef.current
    const sq = clockSquareRef.current
    if (!slot && !sq) return
    const ro = new ResizeObserver(() => {
      measureClock()
    })
    if (slot) ro.observe(slot)
    if (sq) ro.observe(sq)
    return () => ro.disconnect()
  }, [propsSize, measureClock, stackLayout])

  useEffect(() => {
    if (propsSize) return
    const onResize = () => measureClock()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [propsSize, measureClock])

  /** Re-measure after layout mode changes; defer until nested flex / timeline row have heights. */
  useLayoutEffect(() => {
    if (propsSize) return
    measureClock()
    let raf1 = 0
    let raf2 = 0
    raf1 = requestAnimationFrame(() => {
      measureClock()
      raf2 = requestAnimationFrame(() => measureClock())
    })
    const timeouts = [0, 16, 50, 100, 200, 400].map((ms) => window.setTimeout(measureClock, ms))
    const tForce = window.setTimeout(measureClockForce, 500)
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      timeouts.forEach(clearTimeout)
      clearTimeout(tForce)
    }
  }, [propsSize, measureClock, measureClockForce, stackLayout])

  const { handleMouseDown } = useClockInteraction({
    canvasRef,
    dayStartTime,
    dayEndTime,
    onDayStartChange,
    onDayEndChange,
    lockedPhotoperiodHours,
  })

  useEffect(() => {
    if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
      const startMinutes = timeToMinutes(dayStartTime)
      const lockedDurationMinutes = lockedPhotoperiodHours * 60
      const expectedEndMinutes = (startMinutes + lockedDurationMinutes) % 1440
      const expectedEndTime = minutesToTime(expectedEndMinutes)

      if (dayEndTime !== expectedEndTime) {
        onDayEndChange(expectedEndTime)
      }
    }
  }, [dayStartTime, lockedPhotoperiodHours, dayEndTime, onDayEndChange])

  const locked = lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined

  const labelEl =
    label ? (
      <label className="block text-sm font-medium text-text-secondary mb-1 shrink-0 text-center">
        {label}
      </label>
    ) : null

  const clockArea = (
    <div
      ref={clockSlotRef}
      className={
        stackLayout
          ? 'flex flex-1 min-h-0 w-full min-w-0 flex-col items-center justify-center'
          : 'flex flex-1 min-h-0 min-w-0 w-full flex-col items-center justify-center'
      }
    >
      <div
        ref={clockSquareRef}
        className="relative w-full aspect-square max-h-full max-w-full min-w-0 flex items-center justify-center"
      >
        <canvas
          ref={canvasRef}
          width={size}
          height={size}
          onMouseDown={handleMouseDown}
          className="cursor-pointer max-w-full max-h-full"
        />
        <CircularClockFace
          canvasRef={canvasRef}
          size={size}
          dayStartTime={dayStartTime}
          dayEndTime={dayEndTime}
          period={period}
        />
      </div>
    </div>
  )

  const controlsRowBottom = (
    <div className="shrink-0 w-full pt-1 border-t border-border-subtle/60">
      <div className="flex flex-col items-center gap-1 px-1 pb-1">
        <div className="flex flex-row flex-wrap items-center justify-center gap-x-1 gap-y-1">
          <div className="flex items-center gap-1 shrink-0">
            <label className="text-[12px] text-text-muted whitespace-nowrap">Start</label>
            <input
              type="text"
              pattern="[0-2][0-9]:[0-5][0-9]"
              value={dayStartTime}
              onChange={(e) => {
                if (!locked) onDayStartChange(e.target.value)
              }}
              disabled={locked}
              className={`${inputTimeClass} ${locked ? 'opacity-50 cursor-not-allowed' : ''}`}
              style={{ padding: '0 2px' }}
            />
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <label className="text-[12px] text-text-muted whitespace-nowrap">End</label>
            <input
              type="text"
              pattern="[0-2][0-9]:[0-5][0-9]"
              value={dayEndTime}
              onChange={(e) => {
                if (!locked) onDayEndChange(e.target.value)
              }}
              disabled={locked}
              className={`${inputTimeClass} ${locked ? 'opacity-50 cursor-not-allowed' : ''}`}
              style={{ padding: '0 2px' }}
            />
          </div>
        </div>
        <div className="flex flex-row flex-wrap items-center justify-center gap-x-1 gap-y-1">
          {onRampUpChange && rampUpDuration !== undefined && (
            <div className="flex items-center gap-1 shrink-0">
              <label className="text-[12px] text-text-muted whitespace-nowrap">Ramp ↑</label>
              <input
                type="number"
                min="0"
                max="180"
                value={rampUpDuration ?? ''}
                onChange={(e) => {
                  const val = e.target.value ? parseInt(e.target.value) : null
                  onRampUpChange(val !== null && val > 180 ? 180 : val)
                }}
                className={inputRampClass}
              />
            </div>
          )}
          {onRampDownChange && rampDownDuration !== undefined && (
            <div className="flex items-center gap-1 shrink-0">
              <label className="text-[12px] text-text-muted whitespace-nowrap">Ramp ↓</label>
              <input
                type="number"
                min="0"
                max="180"
                value={rampDownDuration ?? ''}
                onChange={(e) => {
                  const val = e.target.value ? parseInt(e.target.value) : null
                  onRampDownChange(val !== null && val > 180 ? 180 : val)
                }}
                className={inputRampClass}
              />
            </div>
          )}
          <div className="flex items-center gap-1 shrink-0 pl-1 border-l border-border-subtle ml-1">
            <span className="text-[12px] text-text-muted whitespace-nowrap">Photoperiod</span>
            <span className="text-sm font-medium text-text-input font-mono tabular-nums whitespace-nowrap">
              {calculatePhotoperiod(dayStartTime, dayEndTime).toFixed(1)}h
            </span>
          </div>
        </div>
      </div>
    </div>
  )

  const controlsColumnRight = (
    <div className="pt-1 flex flex-col justify-center w-fit shrink-0">
      <div className="flex flex-col gap-1">
        <div className="flex flex-col gap-0.5">
          <label className="text-[12px] text-text-muted shrink-0">Start</label>
          <input
            type="text"
            pattern="[0-2][0-9]:[0-5][0-9]"
            value={dayStartTime}
            onChange={(e) => {
              if (!locked) onDayStartChange(e.target.value)
            }}
            disabled={locked}
            className={`${inputTimeClass} ${locked ? 'opacity-50 cursor-not-allowed' : ''}`}
            style={{ padding: '0 2px' }}
          />
        </div>
        <div className="flex flex-col gap-0.5">
          <label className="text-[12px] text-text-muted shrink-0">End</label>
          <input
            type="text"
            pattern="[0-2][0-9]:[0-5][0-9]"
            value={dayEndTime}
            onChange={(e) => {
              if (!locked) onDayEndChange(e.target.value)
            }}
            disabled={locked}
            className={`${inputTimeClass} ${locked ? 'opacity-50 cursor-not-allowed' : ''}`}
            style={{ padding: '0 2px' }}
          />
        </div>
        {onRampUpChange && rampUpDuration !== undefined && (
          <div className="flex flex-col gap-0.5">
            <label className="text-[12px] text-text-muted shrink-0">Ramp ↑</label>
            <input
              type="number"
              min="0"
              max="180"
              value={rampUpDuration ?? ''}
              onChange={(e) => {
                const val = e.target.value ? parseInt(e.target.value) : null
                onRampUpChange(val !== null && val > 180 ? 180 : val)
              }}
              className={inputRampClass}
            />
          </div>
        )}
        {onRampDownChange && rampDownDuration !== undefined && (
          <div className="flex flex-col gap-0.5">
            <label className="text-[12px] text-text-muted shrink-0">Ramp ↓</label>
            <input
              type="number"
              min="0"
              max="180"
              value={rampDownDuration ?? ''}
              onChange={(e) => {
                const val = e.target.value ? parseInt(e.target.value) : null
                onRampDownChange(val !== null && val > 180 ? 180 : val)
              }}
              className={inputRampClass}
            />
          </div>
        )}
      </div>
      <div className="flex flex-col items-center mt-1 text-text-muted gap-0.5">
        <span className="text-[12px]">Photoperiod</span>
        <span className="text-sm font-medium text-text-input font-mono tabular-nums">
          {calculatePhotoperiod(dayStartTime, dayEndTime).toFixed(1)}h
        </span>
      </div>
    </div>
  )

  return (
    <div ref={outerRef} className="flex h-full w-full min-h-0 flex-col gap-1">
      {stackLayout ? (
        <>
          {labelEl}
          {clockArea}
          {controlsRowBottom}
        </>
      ) : (
        <div className="flex flex-1 min-h-0 flex-row items-stretch gap-1 w-full">
          <div className="flex flex-col items-center flex-1 min-w-0 min-h-0 h-full gap-1">
            {labelEl}
            {clockArea}
          </div>
          {controlsColumnRight}
        </div>
      )}
    </div>
  )
}
