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
  // ── Test 1: Drag night-band right edge → day start (5-min snap) ──────────
  it('dragging right edge of night band updates day start time with 5-min snap', () => {
    const onDayStartChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange: vi.fn(),
    })

const rightHandle = screen.getAllByTestId('timeline-handle-night-end')[0]

    fireEvent.mouseDown(rightHandle, { clientX: 250 })
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

  // ── Test 2: Locked photoperiod drags both edges ─────────────────────────
  it('locked photoperiod: dragging night-band left edge adjusts both edges', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange,
      lockedPhotoperiodHours: 12,
    })

    const leftHandle = screen.getAllByTestId('timeline-handle-night-end')[0]

    fireEvent.mouseDown(leftHandle, { clientX: 750 })
    fireEvent.mouseMove(window, { clientX: 780 })
    fireEvent.mouseUp(window)

    expect(onDayEndChange).toHaveBeenCalled()
    expect(onDayStartChange).toHaveBeenCalled()
  })

  // ── Test 3: Body drag shifts night band ───────────────────────────────────
  it('dragging body of night band shifts the band', () => {
    const onDayStartChange = vi.fn()
    const onDayEndChange = vi.fn()
    renderTimeline({
      onDayStartChange,
      onDayEndChange,
    })

    const bandBody = screen.getAllByTestId('timeline-night-band')[0]

    fireEvent.mouseDown(bandBody, { clientX: 500 })
    fireEvent.mouseMove(window, { clientX: 550 })
    fireEvent.mouseUp(window)

    expect(onDayStartChange).toHaveBeenCalled()
    expect(onDayEndChange).toHaveBeenCalled()
  })

  // ── Test 4: Right-click shows ramp duration popover ───────────────────────
  it('right-click on night band shows ramp duration popover', () => {
    const onRampUpChange = vi.fn()
    const onRampDownChange = vi.fn()
    renderTimeline({
      rampUpDuration: 15,
      rampDownDuration: 30,
      onRampUpChange,
      onRampDownChange,
    })

    const band = screen.getAllByTestId('timeline-night-band')[0]
    fireEvent.contextMenu(band, { clientX: 400, clientY: 150 })

    expect(screen.getByTestId('timeline-ramp-popover')).toBeInTheDocument()
    expect(screen.getByLabelText('Ramp up (min)')).toHaveValue(15)
    expect(screen.getByLabelText('Ramp down (min)')).toHaveValue(30)
  })

  // ── Test 5: Ramp gradient width proportional to duration ──────────────────
  it('ramp gradient width is proportional to ramp duration', () => {
    renderTimeline({ rampUpDuration: 15 })

    const rampGradient = screen.getAllByTestId('timeline-ramp-up-gradient')[0]
    const widthPercent = parseFloat(rampGradient.style.width)

    expect(widthPercent).toBeCloseTo((15 / 1440) * 100, 1)
  })

  // ── Test 6: Edge handles show ew-resize cursor ──────────────────────────
  it('night-band edge handles show ew-resize cursor', () => {
    renderTimeline({ lockedPhotoperiodHours: 12 })

    const leftHandle = screen.getAllByTestId('timeline-handle-night-start')[0]
    expect(leftHandle).toHaveStyle({ cursor: 'ew-resize' })
  })
})
