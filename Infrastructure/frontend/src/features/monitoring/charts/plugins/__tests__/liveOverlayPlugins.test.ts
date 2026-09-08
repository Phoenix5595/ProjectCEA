import { describe, expect, it, vi } from 'vitest'
import type uPlot from 'uplot'
import { nowDividerPlugin } from '../nowDividerPlugin'
import { photoperiodPlugin } from '../photoperiodPlugin'

function drawPlugin(plugin: uPlot.Plugin, context: CanvasRenderingContext2D): void {
  const hooks = plugin.hooks?.drawClear
  const draw = Array.isArray(hooks) ? hooks[0] : hooks
  draw?.({
    ctx: context,
    bbox: { top: 0, height: 100 },
    valToPos: (value: number) => value,
  } as unknown as uPlot)
}

describe('live overlay plugins', () => {
  it('reads the current photoperiod intervals on every draw', () => {
    // Given: a live accessor whose interval data changes after chart creation.
    let intervals: Array<{ start: number; end: number; phase: 'SUN' | 'MOON' }> = [{ start: 10, end: 20, phase: 'SUN' }]
    const plugin = photoperiodPlugin(() => intervals, { sunBg: '#ff0', moonBg: '#00f' })
    const fillRect = vi.fn()
    const context = { fillRect, save: vi.fn(), restore: vi.fn() } as unknown as CanvasRenderingContext2D

    // When: uPlot redraws after the rolling live frame advances.
    intervals = [{ start: 30, end: 40, phase: 'MOON' }]
    drawPlugin(plugin, context)

    // Then: it paints the current frame, not the creation-time closure.
    expect(fillRect).toHaveBeenCalledWith(30, 0, 10, 100)
  })

  it('draws a subtle now divider without obscuring nearby data', () => {
    // Given: the current live divider and a canvas context.
    const plugin = nowDividerPlugin(() => 50, '#fff')
    const context = {
      save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
    } as unknown as CanvasRenderingContext2D

    // When: uPlot draws the divider.
    drawPlugin(plugin, context)

    // Then: it uses the intentionally non-obstructive stroke settings.
    expect(context.lineWidth).toBe(1)
    expect(context.globalAlpha).toBe(0.25)
  })
})
