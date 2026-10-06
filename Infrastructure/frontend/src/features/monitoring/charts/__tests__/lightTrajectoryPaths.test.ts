import { describe, expect, it, vi } from 'vitest'
import type uPlot from 'uplot'

import type { AlignedData, AlignedSeries, LightTrajectorySegment } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import {
  lightTrajectoryPaths,
  lightTrajectorySubpaths,
} from '../options/lightTrajectoryPaths'

class RecordingPath2D {
  readonly commands: string[] = []

  moveTo(x: number, y: number): void {
    this.commands.push(`M${x},${y}`)
  }

  lineTo(x: number, y: number): void {
    this.commands.push(`L${x},${y}`)
  }
}

/** Read back the recorded drawing commands of a (stubbed) Path2D. */
function commandsOf(path: Path2D | null): string[] {
  return (path as unknown as RecordingPath2D | null)?.commands ?? []
}

function lightSeries(segments: readonly LightTrajectorySegment[]): AlignedSeries {
  return {
    key: seriesKey('light', 'light_f_1', 'linear'),
    label: 'light_f_1 - Intensity',
    kind: 'linear',
    source: 'light',
    metric: 'light_f_1',
    family: 'light',
    role: 'linear',
    y: [40, 0, 100, null, 5],
    origin: 'recorded',
    quality: 'exact',
    isAggregated: false,
    lightTrajectory: segments,
  }
}

function alignedData(series: AlignedSeries): AlignedData {
  return {
    x: [0, 10, 20, 30, 40, 50],
    series: [series],
    bands: [],
    photoperiod: [],
    nowIndex: 0,
    aggregated: false,
  }
}

function recordingPlot(): { plot: uPlot; paths: RecordingPath2D[] } {
  const paths: RecordingPath2D[] = []
  class Path2DRecorder extends RecordingPath2D {
    constructor() {
      super()
      paths.push(this)
    }
  }
  vi.stubGlobal('Path2D', Path2DRecorder)
  const plot = {
    series: [{}, { scale: 'light' }],
    valToPos: (value: number) => value,
  } as unknown as uPlot
  return { plot, paths }
}

function recorded(start: number, end: number, value: number): LightTrajectorySegment {
  return {
    start,
    end,
    shape: 'step',
    startValue: value,
    endValue: value,
    origin: 'recorded',
    quality: 'exact',
  }
}

function projectedStep(
  start: number,
  end: number,
  value: number | null,
  quality: LightTrajectorySegment['quality'] = 'estimated'
): LightTrajectorySegment {
  return {
    start,
    end,
    shape: 'step',
    startValue: value,
    endValue: value,
    origin: 'projected',
    quality,
  }
}

function projectedRamp(
  start: number,
  end: number,
  startValue: number,
  endValue: number
): LightTrajectorySegment {
  return {
    start,
    end,
    shape: 'linear',
    startValue,
    endValue,
    origin: 'projected',
    quality: 'estimated',
  }
}

describe('light trajectory subpath split', () => {
  it('keeps recorded coverage solid and projected coverage in its own subpath', () => {
    const segments = [
      recorded(0, 10, 40),
      projectedStep(10, 20, 40),
      projectedRamp(20, 30, 40, 100),
      projectedStep(30, 40, null, 'unavailable'),
      projectedStep(40, 50, 5),
    ]
    const { plot, paths } = recordingPlot()
    try {
      const subpaths = lightTrajectorySubpaths(segments, plot, 'light')
      expect(commandsOf(subpaths.solid)).toEqual(['M0,40', 'L10,40'])
      expect(commandsOf(subpaths.projected)).toEqual([
        'M10,40',
        'L20,40',
        'L30,100',
        'M40,5',
        'L50,5',
      ])
      expect(paths).toHaveLength(2)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('does not invent a connector across the recorded-to-projected seam', () => {
    const segments = [recorded(0, 10, 35), projectedStep(10, 20, 40)]
    const { plot } = recordingPlot()
    try {
      const subpaths = lightTrajectorySubpaths(segments, plot, 'light')
      // The solid path ends at the last recorded value; the dotted path starts
      // at its own projected value even when both meet at the same instant.
      expect(commandsOf(subpaths.solid)).toEqual(['M0,35', 'L10,35'])
      expect(commandsOf(subpaths.projected)).toEqual(['M10,40', 'L20,40'])
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('breaks both subpaths at unavailable coverage', () => {
    const segments = [
      recorded(0, 10, 25),
      projectedStep(10, 20, null, 'unavailable'),
      projectedStep(20, 30, 10),
    ]
    const { plot } = recordingPlot()
    try {
      const subpaths = lightTrajectorySubpaths(segments, plot, 'light')
      expect(commandsOf(subpaths.solid)).toEqual(['M0,25', 'L10,25'])
      expect(commandsOf(subpaths.projected)).toEqual(['M20,10', 'L30,10'])
      expect(
        lightTrajectorySubpaths([projectedStep(0, 10, null, 'unavailable')], plot, 'light')
      ).toEqual({ solid: null, projected: null })
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('light trajectory paths builder', () => {
  it('strokes the recorded subpath from current data and returns null without one', () => {
    const { plot } = recordingPlot()
    try {
      const key = seriesKey('light', 'light_f_1', 'linear')
      let data = alignedData(lightSeries([recorded(0, 10, 40), projectedStep(10, 20, 40)]))
      const buildPath = lightTrajectoryPaths(key, () => data)
      const stroke = buildPath(plot, 1, 0, 5)?.stroke
      if (stroke instanceof Map) throw new Error('Expected one canonical light path')
      expect(commandsOf(stroke ?? null)).toEqual(['M0,40', 'L10,40'])

      data = alignedData(lightSeries([projectedStep(0, 10, 25)]))
      expect(buildPath(plot, 1, 0, 1)).toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

