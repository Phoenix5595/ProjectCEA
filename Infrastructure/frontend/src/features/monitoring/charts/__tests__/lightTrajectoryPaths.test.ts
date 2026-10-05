import { describe, expect, it, vi } from 'vitest'
import type uPlot from 'uplot'

import type { AlignedData, AlignedSeries, LightTrajectorySegment } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { lightTrajectoryPaths } from '../options/lightTrajectoryPaths'

class RecordingPath2D {
  readonly commands: string[] = []

  moveTo(x: number, y: number): void {
    this.commands.push(`M${x},${y}`)
  }

  lineTo(x: number, y: number): void {
    this.commands.push(`L${x},${y}`)
  }
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

describe('light trajectory paths', () => {
  it('draws horizontal steps, vertical changes, ramp slopes, and separated gaps from current data', () => {
    const recordings: RecordingPath2D[] = []
    class Path2DRecorder extends RecordingPath2D {
      constructor() {
        super()
        recordings.push(this)
      }
    }
    vi.stubGlobal('Path2D', Path2DRecorder)
    try {
      const key = seriesKey('light', 'light_f_1', 'linear')
      const segments: readonly LightTrajectorySegment[] = [
        {
          start: 0,
          end: 10,
          shape: 'step',
          startValue: 40,
          endValue: 40,
          origin: 'recorded',
          quality: 'exact',
        },
        {
          start: 10,
          end: 20,
          shape: 'step',
          startValue: 0,
          endValue: 0,
          origin: 'projected',
          quality: 'estimated',
        },
        {
          start: 20,
          end: 30,
          shape: 'linear',
          startValue: 0,
          endValue: 100,
          origin: 'projected',
          quality: 'estimated',
        },
        {
          start: 30,
          end: 40,
          shape: 'step',
          startValue: null,
          endValue: null,
          origin: 'projected',
          quality: 'unavailable',
        },
        {
          start: 40,
          end: 50,
          shape: 'step',
          startValue: 5,
          endValue: 5,
          origin: 'projected',
          quality: 'estimated',
        },
      ]
      let data = alignedData(lightSeries(segments))
      const buildPath = lightTrajectoryPaths(key, () => data)
      const plot = {
        series: [{}, { scale: 'light' }],
        valToPos: (value: number) => value,
      } as unknown as uPlot
      const draw = (): RecordingPath2D => {
        const paths = buildPath(plot, 1, 0, 5)
        if (paths === null || paths.stroke === undefined) throw new Error('light path is required')
        return paths.stroke as unknown as RecordingPath2D
      }

      expect(draw().commands).toEqual([
        'M0,40',
        'L10,40',
        'L10,0',
        'L20,0',
        'L30,100',
        'M40,5',
        'L50,5',
      ])

      data = alignedData(
        lightSeries([
          {
            start: 0,
            end: 10,
            shape: 'step',
            startValue: 25,
            endValue: 25,
            origin: 'projected',
            quality: 'estimated',
          },
        ])
      )
      expect(draw().commands).toEqual(['M0,25', 'L10,25'])

      data = alignedData(
        lightSeries([
          {
            start: 0,
            end: 10,
            shape: 'step',
            startValue: null,
            endValue: null,
            origin: 'projected',
            quality: 'unavailable',
          },
        ])
      )
      expect(buildPath(plot, 1, 0, 1)).toBeNull()
      expect(recordings).toHaveLength(2)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
