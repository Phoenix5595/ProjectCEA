/**
 * Public contracts barrel for the monitoring feature API boundary.
 *
 * Re-exports the shared primitives plus the sensor and control contract
 * modules. Consumers import every schema and type from this single module.
 */
export * from './contracts/shared'
export * from './contracts/sensor'
export * from './contracts/control'
