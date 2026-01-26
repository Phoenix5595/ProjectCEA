import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { PIDControlMode, PIDParameters } from '../types/pid'

export default function VerticalPIDBlock() {
  const [device, setDevice] = useState('heater')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<any[]>([])
  const [parameters, setParameters] = useState<PIDParameters>({
    kp: 0,
    ki: 0,
    kd: 0
  })
  const [mode, setMode] = useState<PIDControlMode>('on_off')

  useEffect(() => {
    loadData()
  }, [device])

  useEffect(() => {
    if (historyOpen) {
      loadHistory()
    }
  }, [historyOpen, device])

  async function loadData() {
    setLoading(true)
    try {
      const [params, modeInfo] = await Promise.all([
        apiClient.getPIDParameters(device),
        apiClient.getPIDMode(device)
      ])
      setParameters(params)
      setMode(modeInfo.mode)
    } catch (err) {
      logger.error('Failed to load PID data:', err)
    } finally {
      setLoading(false)
    }
  }

  async function loadHistory() {
    try {
      const data = await apiClient.getPIDParameterHistory(device, 20)
      setHistory(data)
    } catch (err) {
      logger.error('Failed to load PID history:', err)
      setHistory([])
    }
  }

  async function handleParameterChange(param: keyof PIDParameters, value: number) {
    setSaving(true)
    try {
      await apiClient.updatePIDParameters(device, { [param]: value })
      setParameters(prev => ({ ...prev, [param]: value }))
    } catch (err) {
      logger.error(`Failed to update ${param}:`, err)
    } finally {
      setSaving(false)
    }
  }

  async function handleModeChange(newMode: PIDControlMode) {
    setSaving(true)
    try {
      await apiClient.setPIDMode(device, { mode: newMode })
      setMode(newMode)
    } catch (err) {
      logger.error('Failed to update PID mode:', err)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">PID Control</div>
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  const isAuto = mode === 'auto_pid'
  const isOff = mode === 'on_off'

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">PID Control</div>
      
      <div className="space-y-4">
        {/* Device Selector */}
        <div className="space-y-2">
          <label className="text-gray-300 text-sm font-medium">Device</label>
          <select
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded text-gray-200 text-sm px-3 py-2"
          >
            <option value="heater">Heater</option>
            <option value="fan">Fan</option>
            <option value="co2">CO2</option>
          </select>
        </div>

        {/* Mode Selector */}
        <div className="space-y-2">
          <label className="text-gray-300 text-sm font-medium">Control Mode</label>
          <div className="grid grid-cols-3 gap-2">
            <button
              onClick={() => handleModeChange('on_off')}
              disabled={saving}
              className={`px-3 py-2 rounded text-xs font-medium transition-colors ${
                mode === 'on_off'
                  ? 'bg-gray-700 text-gray-100 border border-gray-600'
                  : 'bg-gray-800 text-gray-400 border border-gray-700 hover:bg-gray-700'
              }`}
            >
              ON/OFF
            </button>
            <button
              onClick={() => handleModeChange('pid')}
              disabled={saving}
              className={`px-3 py-2 rounded text-xs font-medium transition-colors ${
                mode === 'pid'
                  ? 'bg-cyan-900/50 text-cyan-400 border border-cyan-800/50'
                  : 'bg-gray-800 text-gray-400 border border-gray-700 hover:bg-gray-700'
              }`}
            >
              PID
            </button>
            <button
              onClick={() => handleModeChange('auto_pid')}
              disabled={saving}
              className={`px-3 py-2 rounded text-xs font-medium transition-colors ${
                mode === 'auto_pid'
                  ? 'bg-purple-900/50 text-purple-400 border border-purple-800/50'
                  : 'bg-gray-800 text-gray-400 border border-gray-700 hover:bg-gray-700'
              }`}
            >
              AUTO
            </button>
          </div>
        </div>

        {/* PID Parameters */}
        {!isOff && (
          <div className="space-y-3">
            <div className="space-y-2">
              <div className="flex justify-between items-center text-xs text-gray-400">
                <span>Kp (Proportional)</span>
                <span className="text-red-400 font-mono">{parameters.kp.toFixed(1)}</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                step="0.1"
                value={parameters.kp}
                onChange={(e) => handleParameterChange('kp', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(to right, #ef4444 0%, #ef4444 ${(parameters.kp / 100) * 100}%, #374151 ${(parameters.kp / 100) * 100}%, #374151 100%)`
                }}
              />
            </div>

            <div className="space-y-2">
              <div className="flex justify-between items-center text-xs text-gray-400">
                <span>Ki (Integral)</span>
                <span className="text-green-400 font-mono">{parameters.ki.toFixed(1)}</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                step="0.1"
                value={parameters.ki}
                onChange={(e) => handleParameterChange('ki', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(to right, #10b981 0%, #10b981 ${(parameters.ki / 100) * 100}%, #374151 ${(parameters.ki / 100) * 100}%, #374151 100%)`
                }}
              />
            </div>

            <div className="space-y-2">
              <div className="flex justify-between items-center text-xs text-gray-400">
                <span>Kd (Derivative)</span>
                <span className="text-blue-400 font-mono">{parameters.kd.toFixed(1)}</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                step="0.1"
                value={parameters.kd}
                onChange={(e) => handleParameterChange('kd', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(to right, #3b82f6 0%, #3b82f6 ${(parameters.kd / 100) * 100}%, #374151 ${(parameters.kd / 100) * 100}%, #374151 100%)`
                }}
              />
            </div>
          </div>
        )}

        {/* Save Button */}
        {!isOff && (
          <button
            onClick={() => loadData()}
            disabled={loading || saving || isAuto}
            className="w-full px-4 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-800 disabled:text-gray-600 rounded text-white text-xs font-bold tracking-wide transition-colors"
          >
            {saving ? 'SAVING...' : 'SAVE CONFIG'}
          </button>
        )}

        {/* History Toggle */}
        <button
          onClick={() => setHistoryOpen(!historyOpen)}
          className="w-full px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded text-gray-400 text-xs font-medium transition-colors border border-gray-700"
        >
          {historyOpen ? 'Hide History' : 'Show History'}
        </button>

        {/* History Display */}
        {historyOpen && (
          <div className="bg-gray-950/50 border-t border-gray-800 p-3">
            {history.length === 0 ? (
              <div className="text-xs text-gray-500 text-center py-2">No history available</div>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {history.map((entry, i) => (
                  <div key={i} className="text-xs bg-gray-900/50 rounded px-3 py-2 border border-gray-800/50">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-gray-500">{new Date(entry.timestamp).toLocaleString()}</span>
                      <span className="text-gray-600">{entry.reason}</span>
                    </div>
                    <div className="flex gap-4">
                      <span className="text-gray-400">Kp: <span className="text-red-400/70 font-mono line-through">{entry.old_values.kp}</span> → <span className="text-cyan-400 font-mono">{entry.new_values.kp}</span></span>
                      <span className="text-gray-400">Ki: <span className="text-red-400/70 font-mono line-through">{entry.old_values.ki}</span> → <span className="text-cyan-400 font-mono">{entry.new_values.ki}</span></span>
                      <span className="text-gray-400">Kd: <span className="text-red-400/70 font-mono line-through">{entry.old_values.kd}</span> → <span className="text-cyan-400 font-mono">{entry.new_values.kd}</span></span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
