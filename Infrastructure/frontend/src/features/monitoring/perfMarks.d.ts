export {}

import type { MonitoringPerfDebug } from './perfMarks'

declare global {
  interface Window {
    readonly __monitoringPerf?: MonitoringPerfDebug
  }
}
