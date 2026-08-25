export interface ChartSize {
  readonly width: number
  readonly height: number
}

export function measureChartContainer(container: HTMLElement): ChartSize {
  const width = container.clientWidth > 0 ? container.clientWidth : 600
  const height = container.clientHeight > 0 ? container.clientHeight : 300
  return { width, height }
}
