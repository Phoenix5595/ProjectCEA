/**
 * Public barrel for the monitoring feature API boundary.
 *
 * Re-exports the contracts, error variants, clients, and per-service API
 * classes so feature consumers import from a single module.
 */
export * from './contracts'
export * from './errors'
export { MonitoringClient, buildQuery, monitoringRequestContextFromSearchParams } from './client'
export type { MonitoringRequestContext, MonitoringRequestOptions } from './client'
export { MonitoringApi } from './monitoringApi'
