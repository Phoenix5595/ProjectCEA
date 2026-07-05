import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { apiClient } from '../services/api'
import type { PIDControlMode, PIDParameters, PIDParameterUpdate } from '../types/pid'
import { logger } from '../utils/logger'

interface PIDHistoryItem {
  changed_at?: string
  timestamp?: string
  source?: string
  kp?: number | string
  ki?: number | string
  kd?: number | string
}

interface VerticalPIDBlockProps {
  location: string
  cluster: string
}

const DEFAULT_HYSTERESIS = 0.1

export default function VerticalPIDBlock({ location, cluster }: VerticalPIDBlockProps) {
  const [device, setDevice] = useState('heater')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<PIDHistoryItem[]>([])
  const [parameters, setParameters] = useState<PIDParameters>({
    kp: 0,
    ki: 0,
    kd: 0
  })
  const [mode, setMode] = useState<PIDControlMode>('on_off')
  const [tempParameters, setTempParameters] = useState<PIDParameters>({
    kp: 0,
    ki: 0,
    kd: 0
  })
  const [hysteresis, setHysteresis] = useState<number>(DEFAULT_HYSTERESIS)
  const [tempHysteresis, setTempHysteresis] = useState<number>(DEFAULT_HYSTERESIS)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)

  useEffect(() => {
    loadData()
  }, [device, location, cluster])

  useEffect(() => {
    loadHistory()
  }, [device, location, cluster])

  async function loadData() {
    setLoading(true)
    try {
      const [params, modeInfo] = await Promise.all([
        apiClient.getPIDParametersForRoom(location, cluster, device),
        apiClient.getPIDModeForRoom(location, cluster, device)
      ])
      setParameters(params)
      setTempParameters(params)
      setMode(modeInfo.mode)
      const loadedHyst = params.binary_hysteresis ?? DEFAULT_HYSTERESIS
      setHysteresis(loadedHyst)
      setTempHysteresis(loadedHyst)
      setHasUnsavedChanges(false)
    } catch (err) {
      logger.error('Failed to load PID data:', err)
    } finally {
      setLoading(false)
    }
  }

  async function loadHistory() {
    try {
      const data = await apiClient.getPIDParameterHistoryForRoom(location, cluster, device, 20)
      setHistory(data)
    } catch (err) {
      logger.error('Failed to load PID history:', err)
      setHistory([])
    }
  }

  const handleParameterChange = useCallback((param: keyof PIDParameters, value: number) => {
    setTempParameters(prev => ({ ...prev, [param]: value }))
    setHasUnsavedChanges(true)
  }, [])

  const handleHysteresisChange = useCallback((value: number) => {
    setTempHysteresis(value)
    setHasUnsavedChanges(true)
  }, [])

  const saveParameters = useCallback(async () => {
    if (!hasUnsavedChanges) return

    setSaving(true)
    try {
      const update: PIDParameterUpdate = {
        kp: tempParameters.kp,
        ki: tempParameters.ki,
        kd: tempParameters.kd
      }
      if (mode === 'on_off' && tempHysteresis !== hysteresis) {
        update.binary_hysteresis = tempHysteresis
      }
      await apiClient.updatePIDParametersForRoom(location, cluster, device, update)
      setParameters(tempParameters)
      setHysteresis(tempHysteresis)
      setHasUnsavedChanges(false)
      loadHistory()
    } catch (err) {
      logger.error('Failed to save PID parameters:', err)
    } finally {
      setSaving(false)
    }
  }, [location, cluster, device, tempParameters, tempHysteresis, hysteresis, mode, hasUnsavedChanges])

  const handleModeChange = useCallback(async (newMode: PIDControlMode) => {
    if (newMode === mode) return

    try {
      await apiClient.setPIDModeForRoom(location, cluster, device, { mode: newMode })

      setMode(newMode)

      if (newMode === 'auto_pid') {
        setTempParameters(parameters)
        setTempHysteresis(hysteresis)
        setHasUnsavedChanges(false)
      }
    } catch (err) {
      logger.error('Failed to update PID mode:', err)
      toast.error('Failed to update PID mode')
    }
  }, [location, cluster, device, mode, parameters, hysteresis])

  if (loading) {
    return (
      <div className="bg-surface-primary rounded-lg border border-border-subtle p-2">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-4">PID Control</div>
        <div className="text-text-subtle text-sm">Loading...</div>
      </div>
    )
  }

  const isAuto = mode === 'auto_pid'
  const isOff = mode === 'on_off'

  return (
    <div className="bg-surface-primary rounded-lg border border-border-subtle p-2 h-full flex flex-col">
      {/* Header with Device and Mode */}
      <div className="flex justify-between items-start mb-4">
        <div className="text-text-muted uppercase font-bold tracking-wider text-[14px]">PID Control</div>

        {/* Device Selector - Upper Left */}
        <select
          value={device}
          onChange={(e) => setDevice(e.target.value)}
          disabled={saving}
          className="bg-surface-secondary border border-border-default rounded-sm text-text-input text-xs px-2 py-1 focus:outline-hidden focus:border-accent-vivid transition-colors"
        >
          <option value="heater">Heater</option>
          <option value="fan">Fan</option>
          <option value="co2">CO2</option>
        </select>

        {/* Mode Selector - Upper Right */}
        <div className="flex gap-1">
          <button
            onClick={() => handleModeChange('on_off')}
            disabled={saving}
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent-vivid/50 ${
              mode === 'on_off'
                ? 'bg-surface-tertiary text-text-input border border-border-emphasis'
                : 'bg-surface-secondary text-text-muted border border-border-default hover:bg-surface-tertiary'
            }`}
          >
            ON/OFF
          </button>
          <button
            onClick={() => handleModeChange('pid')}
            disabled={saving}
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent-vivid/50 ${
              mode === 'pid'
                ? 'bg-accent-dim/50 text-accent-data border border-accent-dim/50'
                : 'bg-surface-secondary text-text-muted border border-border-default hover:bg-surface-tertiary'
            }`}
          >
            PID
          </button>
          <button
            onClick={() => handleModeChange('auto_pid')}
            disabled={saving}
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent-vivid/50 ${
              mode === 'auto_pid'
                ? 'bg-mode-auto-dim/50 text-mode-auto-text border border-mode-auto-border/50'
                : 'bg-surface-secondary text-text-muted border border-border-default hover:bg-surface-tertiary'
            }`}
          >
            AUTO
          </button>
        </div>
      </div>

      {/* Hysteresis Band - ON/OFF mode only */}
      {isOff && (
        <div className="mb-2">
          <label className="text-text-muted text-xs font-medium block mb-1">Hysteresis Band</label>
          <input
            type="number"
            min="0"
            max="10"
            step="0.01"
            value={tempHysteresis}
            onChange={(e) => handleHysteresisChange(parseFloat(e.target.value))}
            disabled={saving}
            className="w-full bg-surface-secondary border border-border-default rounded-sm text-text-input text-sm px-2 py-1 text-center font-mono tabular-nums focus:outline-hidden focus:border-accent-vivid transition-colors"
          />
        </div>
      )}

      {/* PID Parameters - 3 Number Fields Side by Side */}
      {!isOff && (
        <>
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="text-text-muted text-xs font-medium block mb-1">Kp</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.kp}
                onChange={(e) => handleParameterChange('kp', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-surface-secondary border border-border-default rounded-sm text-status-danger text-sm px-2 py-1 text-center font-mono tabular-nums focus:outline-hidden focus:border-accent-vivid transition-colors"
              />
            </div>
            <div className="flex-1">
              <label className="text-text-muted text-xs font-medium block mb-1">Ki</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.ki}
                onChange={(e) => handleParameterChange('ki', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-surface-secondary border border-border-default rounded-sm text-status-success text-sm px-2 py-1 text-center font-mono tabular-nums focus:outline-hidden focus:border-accent-vivid transition-colors"
              />
            </div>
            <div className="flex-1">
              <label className="text-text-muted text-xs font-medium block mb-1">Kd</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.kd}
                onChange={(e) => handleParameterChange('kd', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-surface-secondary border border-border-default rounded-sm text-btn-primary-data text-sm px-2 py-1 text-center font-mono tabular-nums focus:outline-hidden focus:border-accent-vivid transition-colors"
              />
            </div>
          </div>

          {/* Save Button */}
          {hasUnsavedChanges && (
            <div className="mt-2">
              <button
                onClick={saveParameters}
                disabled={saving}
                className="w-full px-3 py-2 bg-accent-active hover:bg-accent-hover disabled:bg-surface-secondary disabled:text-text-faint rounded-sm text-text-default text-xs font-bold tracking-wide transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent-vivid/50"
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          )}
        </>
      )}

      {/* Save Button for ON/OFF mode (hysteresis-only changes) */}
      {isOff && hasUnsavedChanges && (
        <div className="mt-2">
          <button
            onClick={saveParameters}
            disabled={saving}
            className="w-full px-3 py-2 bg-accent-active hover:bg-accent-hover disabled:bg-surface-secondary disabled:text-text-faint rounded-sm text-text-default text-xs font-bold tracking-wide transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent-vivid/50"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      )}

      {/* History - Always Visible */}
      <div className="flex-1 bg-surface-base/50 border-t border-border-subtle p-3 flex flex-col">
        <div className="text-text-muted text-xs font-medium mb-2">Recent Changes</div>
        <div className="flex-1 overflow-y-auto">
          {history.length === 0 ? (
            <div className="text-xs text-text-subtle text-center py-2">No history available</div>
          ) : (
            <div className="space-y-1">
              {history.slice(0, 10).map((entry, i) => (
                <div key={i} className="text-xs bg-surface-primary/50 rounded-sm px-2 py-1 border border-border-subtle/50">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-text-subtle">{new Date(entry.changed_at || entry.timestamp || 0).toLocaleTimeString()}</span>
                    <span className="text-text-faint">{entry.source || 'Unknown'}</span>
                  </div>
                  <div className="flex gap-3 text-xs">
                    <span className="text-text-muted">Kp: <span className="text-status-danger/70 font-mono tabular-nums">{typeof entry.kp === 'number' ? entry.kp.toFixed(2) : entry.kp}</span></span>
                    <span className="text-text-muted">Ki: <span className="text-status-success/70 font-mono tabular-nums">{typeof entry.ki === 'number' ? entry.ki.toFixed(2) : entry.ki}</span></span>
                    <span className="text-text-muted">Kd: <span className="text-btn-primary-data/70 font-mono tabular-nums">{typeof entry.kd === 'number' ? entry.kd.toFixed(2) : entry.kd}</span></span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
