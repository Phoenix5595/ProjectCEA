import type uPlot from 'uplot'
import { describe, expect, it, vi } from 'vitest'

import type { PhotoperiodInterval } from '../../../data'
import { nowDividerPlugin } from '../nowDividerPlugin'
import { photoperiodPlugin } from '../photoperiodPlugin'

type Phase = PhotoperiodInterval['phase']

interface DrawFrame {
  bbox: { top: number; left: number; width: number; height: number }
  /** Linear time→pixel mapping across the visible x window. */
  valToPos: (value: number) => number
}

interface PaintedRect {
  x: number
  y: number
  width: number
  height: number
  style: string
}

/** Canvas stub that records each fill together with the fillStyle in force. */
function recordingContext(): {
  context: CanvasRenderingContext2D
  rects: PaintedRect[]
} {
  const rects: PaintedRect[] = []
  let style = ''
  const context = {
    save: vi.fn(),
    restore: vi.fn(),
    get fillStyle() {
      return style
    },
    set fillStyle(value: string) {
      style = value
    },
    fillRect: vi.fn((x: number, y: number, width: number, height: number) => {
      rects.push({ x, y, width, height, style })
    }),
  } as unknown as CanvasRenderingContext2D
  return { context, rects }
}

function drawPlugin(
  plugin: uPlot.Plugin,
  context: CanvasRenderingContext2D,
  frame: DrawFrame = {
    bbox: { top: 0, left: 0, width: 800, height: 100 },
    valToPos: value => value,
  }
): void {
  const hooks = plugin.hooks?.drawClear
  const draw = Array.isArray(hooks) ? hooks[0] : hooks
  draw?.({ ctx: context, ...frame } as unknown as uPlot)
}

/** Real plot geometry: x window [windowStart, windowEnd] mapped onto bbox. */
function plotGeometry(
  windowStart: number,
  windowEnd: number,
  bbox: DrawFrame['bbox']
): DrawFrame {
  const span = windowEnd - windowStart
  return {
    bbox,
    valToPos: value => bbox.left + ((value - windowStart) / span) * bbox.width,
  }
}

describe('live overlay plugins', () => {
  it('reads the current photoperiod intervals on every draw', () => {
    // Given: a live accessor whose interval data changes after chart creation.
    let intervals: Array<{ start: number; end: number; phase: Phase }> = [
      { start: 10, end: 20, phase: 'SUN' },
    ]
    const plugin = photoperiodPlugin(() => intervals)
    const { context, rects } = recordingContext()

    // When: uPlot redraws after the rolling live frame advances.
    intervals = [{ start: 30, end: 40, phase: 'MOON' }]
    drawPlugin(plugin, context)

    // Then: it paints the current frame, not the creation-time closure.
    expect(rects).toEqual([{ x: 30, y: 0, width: 10, height: 100, style: expect.any(String) }])
  })

  it('paints SUN and MOON with fixed, distinct fills and no theme input', () => {
    // Given: identical interval geometry rendered as SUN, then MOON.
    const draw = (phase: 'SUN' | 'MOON'): string => {
      const { context, rects } = recordingContext()
      drawPlugin(photoperiodPlugin(() => [{ start: 0, end: 100, phase }]), context)
      return rects[0]?.style ?? ''
    }
    const sun = draw('SUN')
    const moon = draw('MOON')

    // Then: the two semantic fills differ, stay fixed across redraws, and the
    // plugin accepts no caller colors (theme tokens cannot reach the canvas).
    expect(sun).not.toBe(moon)
    expect(draw('SUN')).toBe(sun)
    expect(draw('MOON')).toBe(moon)
  })

  it('clamps intervals carried in from outside the plot to the bbox', () => {
    // Given: a real window [1000, 2000] mapped onto a plot inset in the canvas.
    const geometry = plotGeometry(1000, 2000, { top: 10, left: 200, width: 600, height: 100 })
    const { context, rects } = recordingContext()
    drawPlugin(
      photoperiodPlugin(() => [
        { start: 800, end: 1200, phase: 'SUN' }, // carried in from the left
        { start: 1800, end: 2200, phase: 'SUN' }, // carried in from the right
        { start: 500, end: 2500, phase: 'MOON' }, // spans both edges
      ]),
      context,
      geometry
    )

    // Then: each fill stops at the plot edge and never leaks into the axes.
    expect(rects).toEqual([
      { x: 200, y: 10, width: 120, height: 100, style: expect.any(String) },
      { x: 680, y: 10, width: 120, height: 100, style: expect.any(String) },
      { x: 200, y: 10, width: 600, height: 100, style: expect.any(String) },
    ])
  })

  it('skips intervals with no overlap in the plot area', () => {
    // Given: intervals fully outside the window, touching an edge, or
    // zero-width, under the same real geometry.
    const geometry = plotGeometry(1000, 2000, { top: 10, left: 200, width: 600, height: 100 })
    const { context, rects } = recordingContext()
    drawPlugin(
      photoperiodPlugin(() => [
        { start: 0, end: 100, phase: 'SUN' }, // entirely left
        { start: 3000, end: 4000, phase: 'SUN' }, // entirely right
        { start: 100, end: 1000, phase: 'SUN' }, // touches the left edge
        { start: 1500, end: 1500, phase: 'MOON' }, // zero width
      ]),
      context,
      geometry
    )

    // Then: no empty overlap paints a rectangle.
    expect(rects).toEqual([])
  })

  it('leaves UNKNOWN spans unpainted', () => {
    // Given: an UNKNOWN span covering the whole window plus a paintable SUN.
    const geometry = plotGeometry(1000, 2000, { top: 10, left: 200, width: 600, height: 100 })
    const { context, rects } = recordingContext()
    drawPlugin(
      photoperiodPlugin(() => [
        { start: 900, end: 2100, phase: 'UNKNOWN' },
        { start: 1200, end: 1400, phase: 'SUN' },
      ]),
      context,
      geometry
    )

    // Then: only the SUN interval is painted.
    expect(rects).toEqual([
      { x: 200 + 0.2 * 600, y: 10, width: 0.2 * 600, height: 100, style: expect.any(String) },
    ])
  })

  it('draws a subtle now divider without obscuring nearby data', () => {
    // Given: the current live divider and a canvas context.
    const plugin = nowDividerPlugin(() => 50, '#fff')
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      setLineDash: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
    } as unknown as CanvasRenderingContext2D

    // When: uPlot draws the divider.
    drawPlugin(plugin, context)

    // Then: it uses the intentionally non-obstructive stroke settings.
    expect(context.lineWidth).toBe(1)
    expect(context.globalAlpha).toBe(0.25)
  })
})
