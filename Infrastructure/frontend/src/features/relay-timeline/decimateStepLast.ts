/**
 * Step-after decimation for the relay/PID requested-output waveform.
 *
 * The waveform is a step series: each sample holds until the next one. When
 * the samples outnumber the plot's pixel columns, every column keeps only its
 * last sample so the drawn step never exceeds one point per pixel while the
 * value that wins inside a column is the latest recorded one.
 */
export interface StepPoints {
  readonly xs: number[]
  readonly ys: number[]
}

export function decimateStepLast(
  xs: readonly number[],
  ys: readonly number[],
  columns: number
): StepPoints {
  const length = Math.min(xs.length, ys.length)
  if (length === 0) return { xs: [], ys: [] }
  const columnCount = Math.floor(columns)
  if (!Number.isFinite(columnCount) || columnCount <= 0 || length <= columnCount) {
    return { xs: [...xs.slice(0, length)], ys: [...ys.slice(0, length)] }
  }
  const first = xs[0] ?? 0
  const last = xs[length - 1] ?? first
  const span = last - first
  if (!(span > 0)) return { xs: [first], ys: [ys[length - 1] ?? 0] }

  const outXs: number[] = []
  const outYs: number[] = []
  for (let index = 0; index < length; index += 1) {
    const x = xs[index]
    const y = ys[index]
    if (x === undefined || y === undefined) continue
    const column = Math.min(columnCount - 1, Math.floor(((x - first) / span) * columnCount))
    const previousX = outXs[outXs.length - 1]
    if (previousX === undefined || column > Math.floor(((previousX - first) / span) * columnCount)) {
      outXs.push(x)
      outYs.push(y)
    } else {
      // Same column: the later sample replaces the earlier one.
      outXs[outXs.length - 1] = x
      outYs[outYs.length - 1] = y
    }
  }
  return { xs: outXs, ys: outYs }
}
