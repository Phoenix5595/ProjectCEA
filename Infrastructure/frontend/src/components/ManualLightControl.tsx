import { useState, useEffect } from 'react'

import { apiClient } from '../services/api'
import { extractErrorMessage } from '../utils/errors'
import { logger } from '../utils/logger'

interface RawLightDevice {
  device_type?: string
  display_name?: string
}

interface ManualLightControlProps {
  location: string
  cluster: string
  compact?: boolean
}

interface LightDeviceDetails {
  device_name: string
  display_name?: string
}

type ActiveMode = 'auto' | 'off' | '5m' | '30m' | '1h' | '8h' | null

export default function ManualLightControl({
  location,
  cluster,
  compact = false,
}: ManualLightControlProps) {
  const [lightDetails, setLightDetails] = useState<LightDeviceDetails[]>([])
  const [loadingDetails, setLoadingDetails] = useState(true)
  const [activeTimer, setActiveTimer] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeMode, setActiveMode] = useState<ActiveMode>(null)

  // Load device details on mount
  useEffect(() => {
    async function loadDeviceDetails() {
      setLoadingDetails(true)
      try {
        logger.debug('ManualLightControl: Fetching devices for', location, cluster)
        const devices = await apiClient.getDevicesForLocationClusterWithDetails(location, cluster)
        logger.debug('ManualLightControl: Raw devices received', devices)
        const allDevices = Object.entries(devices)
        logger.debug(
          'ManualLightControl: All device entries',
          allDevices.map(([name, dev]) => ({ name, type: dev?.device_type }))
        )
        const lights = (allDevices as [string, RawLightDevice][])
          .filter(([_, device]) => {
            const isLight = device?.device_type === 'light'
            logger.debug(
              'ManualLightControl: Device',
              _,
              'type:',
              device?.device_type,
              'isLight:',
              isLight
            )
            return isLight
          })
          .map(([deviceName, device]) => ({
            device_name: deviceName,
            display_name: device.display_name,
          }))
        logger.debug('ManualLightControl: Filtered lights result', lights)
        setLightDetails(lights)
      } catch (err) {
        logger.error('ManualLightControl: Error loading device details:', err)
        setLightDetails([])
      } finally {
        setLoadingDetails(false)
      }
    }
    loadDeviceDetails()
  }, [location, cluster])

  // Keep the existing 5s timer refresh, using the command owner's expiry projection.
  useEffect(() => {
    if (lightDetails.length === 0) {
      setActiveTimer(null)
      return
    }

    let cancelled = false

    async function refreshExpiry() {
      try {
        const snapshot = await apiClient.getControlSnapshot()
        if (cancelled) return
        let earliest: number | null = null
        let hasAutomatic = false
        let hasManualOff = false
        for (const relay of snapshot.relays) {
          const assignment = relay.assignment
          if (
            assignment?.location !== location ||
            assignment.cluster !== cluster ||
            assignment.device_type !== 'light'
          ) {
            continue
          }
          hasAutomatic ||= relay.command_mode === 'auto' || relay.command_mode === 'scheduled'
          hasManualOff ||= relay.command_mode === 'manual_off'
          const raw = relay.command_expires_at
          if (!raw) continue
          const expiresAt = Date.parse(raw)
          if (Number.isNaN(expiresAt)) continue
          const remainingMs = expiresAt - Date.now()
          if (remainingMs <= 0) continue
          const remainingMin = Math.ceil(remainingMs / 60000)
          if (earliest === null || remainingMin < earliest) earliest = remainingMin
        }
        setActiveTimer(earliest)
        if (earliest === null) {
          setActiveMode(
            hasAutomatic && !hasManualOff ? 'auto' : hasManualOff && !hasAutomatic ? 'off' : null
          )
        }
      } catch (err) {
        logger.error('ManualLightControl: Error polling command expiry:', err)
      }
    }

    refreshExpiry()
    const poll = setInterval(refreshExpiry, 5000)

    return () => {
      cancelled = true
      clearInterval(poll)
    }
  }, [location, cluster, lightDetails])

  async function turnOnLights(durationMinutes: number) {
    setLoading(true)
    setError(null)

    try {
      const durationSeconds = durationMinutes * 60

      for (const light of lightDetails) {
        try {
          await apiClient.commandDevice(location, cluster, light.device_name, {
            action: 'TIMED_ON',
            duration_seconds: durationSeconds,
            reason: 'Manual light timer',
          })
        } catch (err) {
          logger.error(`Error controlling light ${light.device_name}:`, err)
          setError(extractErrorMessage(err, `Failed to control ${light.device_name}`))
        }
      }

      // Set active mode based on duration
      const modeKey: ActiveMode =
        durationMinutes === 5
          ? '5m'
          : durationMinutes === 30
            ? '30m'
            : durationMinutes === 60
              ? '1h'
              : durationMinutes === 480
                ? '8h'
                : null
      setActiveMode(modeKey)

      setActiveTimer(durationMinutes)
    } catch (err) {
      logger.error('Error turning on lights:', err)
      setError(extractErrorMessage(err, 'Failed to turn on lights'))
    } finally {
      setLoading(false)
    }
  }

  async function turnOffLights() {
    setLoading(true)
    setError(null)

    try {
      setActiveTimer(null)

      // Set active mode to 'off'
      setActiveMode('off')

      // Turn off all lights
      for (const light of lightDetails) {
        try {
          await apiClient.commandDevice(location, cluster, light.device_name, {
            action: 'MANUAL_OFF',
            reason: 'Manual light OFF',
          })
        } catch (err) {
          logger.error(`Error controlling light ${light.device_name}:`, err)
          setError(extractErrorMessage(err, `Failed to control ${light.device_name}`))
        }
      }
    } catch (err) {
      logger.error('Error turning off lights:', err)
      setError(extractErrorMessage(err, 'Failed to turn off lights'))
    } finally {
      setLoading(false)
    }
  }

  async function restoreMode() {
    setLoading(true)
    setError(null)

    try {
      setActiveTimer(null)

      // Set active mode to 'auto' (schedule is now active)
      setActiveMode('auto')

      for (const light of lightDetails) {
        try {
          await apiClient.commandDevice(location, cluster, light.device_name, {
            action: 'AUTO',
            reason: 'Manual light control released',
          })
        } catch (err) {
          logger.error(`Error restoring mode for ${light.device_name}:`, err)
          setError(extractErrorMessage(err, `Failed to restore mode for ${light.device_name}`))
        }
      }
    } catch (err) {
      logger.error('Error restoring mode:', err)
      setError(extractErrorMessage(err, 'Failed to restore mode'))
    } finally {
      setLoading(false)
    }
  }

  // Always render the component, even if no lights found
  if (import.meta.env.DEV) {
    logger.debug('ManualLightControl RENDER:', {
      location,
      cluster,
      lightDetailsCount: lightDetails.length,
      loadingDetails,
      lightDetails,
    })
  }

  return (
    <div
      className={
        compact
          ? 'flex h-full w-full min-h-0 flex-col justify-center gap-2 bg-surface-base/80 p-2 rounded-sm'
          : 'mt-4 pt-4 border-t border-border-subtle bg-surface-base/80 p-3 rounded-sm w-full'
      }
    >
      {!compact && (
        <div className="text-sm font-medium text-text-secondary mb-2">Manual Control</div>
      )}
      {compact && (
        <div className="text-xs font-medium text-text-secondary uppercase tracking-wider">
          Manual Override
        </div>
      )}
      {loadingDetails && <div className="text-xs text-text-muted mb-2">Loading lights...</div>}
      {!loadingDetails && lightDetails.length === 0 && (
        <div className="text-xs text-status-danger mb-2 font-semibold">
          ⚠️ No lights found in this zone (location: {location}, cluster: {cluster})
        </div>
      )}
      {!loadingDetails && lightDetails.length > 0 && (
        <div className="text-xs text-status-success mb-2 font-semibold">
          ✓ Found {lightDetails.length} light(s): {lightDetails.map(l => l.device_name).join(', ')}
        </div>
      )}
      {error && <div className="text-xs text-status-danger mb-2">{error}</div>}
      {activeTimer !== null && (
        <div className="text-xs text-btn-primary-data mb-2">Timer: {activeTimer} min remaining</div>
      )}
      {!loadingDetails && lightDetails.length > 0 ? (
        <div className={compact ? 'grid grid-cols-3 gap-2' : 'flex flex-wrap gap-2'}>
          <button
            onClick={restoreMode}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-btn-primary-light text-text-default rounded hover:bg-btn-primary-hover disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === 'auto' ? 'ring-2 ring-btn-primary-text ring-offset-2' : ''
            }`}
          >
            Auto
          </button>
          <button
            onClick={() => turnOffLights()}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-surface-quinary text-text-default rounded hover:bg-surface-quaternary disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === 'off' ? 'ring-2 ring-text-secondary ring-offset-2' : ''
            }`}
          >
            Off
          </button>
          <button
            onClick={() => turnOnLights(5)}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-status-success-bg text-status-success-text rounded hover:bg-status-success-bg disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === '5m' ? 'ring-2 ring-status-success ring-offset-2' : ''
            }`}
          >
            5m
          </button>
          <button
            onClick={() => turnOnLights(30)}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-status-success-bg text-status-success-text rounded hover:bg-status-success-bg disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === '30m' ? 'ring-2 ring-status-success ring-offset-2' : ''
            }`}
          >
            30m
          </button>
          <button
            onClick={() => turnOnLights(60)}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-status-success-bg text-status-success-text rounded hover:bg-status-success-bg disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === '1h' ? 'ring-2 ring-status-success ring-offset-2' : ''
            }`}
          >
            1h
          </button>
          <button
            onClick={() => turnOnLights(480)}
            disabled={loading}
            className={`px-3 py-1.5 text-sm font-medium bg-status-success-bg text-status-success-text rounded hover:bg-status-success-bg disabled:opacity-50 disabled:cursor-not-allowed ${
              activeMode === '8h' ? 'ring-2 ring-status-success ring-offset-2' : ''
            }`}
          >
            8h
          </button>
        </div>
      ) : !loadingDetails ? (
        <div className="text-xs text-orange-400 mb-2 italic">
          Buttons will appear when lights are detected
        </div>
      ) : null}
    </div>
  )
}
