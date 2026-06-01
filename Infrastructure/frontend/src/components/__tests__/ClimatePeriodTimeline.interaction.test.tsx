import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ClimatePeriodTimeline from '../ClimatePeriodTimeline'

/**
 * Helper to render ClimatePeriodTimeline with type-safe existing props
 * plus any new props (not yet on the interface) via `as any` spread.
 */
function renderTimeline(overrides: Record<string, any> = {}) {
  const defaults = {
    periods: [],
    lightDayStart: '06:00',
    lightDayEnd: '18:00',
  }
  return render(<ClimatePeriodTimeline {...defaults} {...(overrides as any)} />)
}

describe('ClimatePeriodTimeline — interactive slider handles', () => {
  // ── Test 1: Drag left edge handle updates day start (5-min snap) ──────────
  it('dragging left edge handle updates day start time with 5-min snap', () => {
    const onDayStartChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange: vi.fn(),
    })

    // The timeline-day-band and its edge handles should render
    const leftHandle = screen.getByTestId('timeline-handle-start')

    // Simulate drag: mousedown on handle, mousemove to new position, mouseup
    fireEvent.mouseDown(leftHandle, { clientX: 250 })
    fireEvent.mouseMove(window, { clientX: 300 })
    fireEvent.mouseUp(window)

    // Should have been called with a 5-min snapped time string
    expect(onDayStartChange).toHaveBeenCalled()
    const calledWith = onDayStartChange.mock.calls[0]?.[0]
    // Assert minutes end in 00, 05, 10, ..., 55
    expect(calledWith).toMatch(
      /:0[05]$|:1[05]$|:2[05]$|:3[05]$|:4[05]$|:5[05]$/
    )
  })

  // ── Test 2: Locked photoperiod adjusts both edges ─────────────────────────
  it('locked photoperiod: dragging right edge adjusts left edge to maintain duration', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange,
      lockedPhotoperiodHours: 12,
    })

    const rightHandle = screen.getByTestId('timeline-handle-end')

    fireEvent.mouseDown(rightHandle, { clientX: 750 })
    fireEvent.mouseMove(window, { clientX: 780 })
    fireEvent.mouseUp(window)

    // Both callbacks should fire because the locked mode shifts both edges
    expect(onDayEndChange).toHaveBeenCalled()
    expect(onDayStartChange).toHaveBeenCalled()
  })

  // ── Test 3: Body drag shifts whole band ───────────────────────────────────
  it('dragging body of day band shifts the band', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange,
    })

    const bandBody = screen.getByTestId('timeline-day-band')

    fireEvent.mouseDown(bandBody, { clientX: 500 })
    fireEvent.mouseMove(window, { clientX: 550 })
    fireEvent.mouseUp(window)

    // Both edges should shift when dragging the body
    expect(onDayStartChange).toHaveBeenCalled()
    expect(onDayEndChange).toHaveBeenCalled()
  })

  // ── Test 4: Right-click shows ramp duration popover ───────────────────────
  it('right-click on day band shows ramp duration popover', () => {
    const onRampUpChange = vi.fn()
    const onRampDownChange = vi.fn()
    renderTimeline({
      rampUpDuration: 15,
      rampDownDuration: 30,
      onRampUpChange,
      onRampDownChange,
    })

    const band = screen.getByTestId('timeline-day-band')
    fireEvent.contextMenu(band, { clientX: 400, clientY: 150 })

    // Popover should appear with number inputs for ramp durations
    expect(screen.getByTestId('timeline-ramp-popover')).toBeInTheDocument()
    expect(screen.getByLabelText('Ramp up (min)')).toHaveValue(15)
    expect(screen.getByLabelText('Ramp down (min)')).toHaveValue(30)
  })

  // ── Test 5: Ramp gradient width proportional to duration ──────────────────
  it('ramp gradient width is proportional to ramp duration', () => {
    renderTimeline({ rampUpDuration: 15 })

    const rampGradient = screen.getByTestId('timeline-ramp-up-gradient')
    const widthPercent = parseFloat(rampGradient.style.width)

    // 15 minutes / 1440 minutes * 100 = ~1.04%
    expect(widthPercent).toBeCloseTo((15 / 1440) * 100, 1)
  })

  // ── Test 6: Edge handles show ew-resize cursor (locked mode) ──────────────
  it('locked photoperiod: edge handles still show ew-resize cursor', () => {
    renderTimeline({ lockedPhotoperiodHours: 12 })

    const leftHandle = screen.getByTestId('timeline-handle-start')
    expect(leftHandle).toHaveStyle({ cursor: 'ew-resize' })
  })
})
