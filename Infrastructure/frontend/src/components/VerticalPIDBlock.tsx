import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { PIDControlMode, PIDParameters } from '../types/pid'

export default function VerticalPIDBlock() {
  const [device, setDevice] = useState('heater')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<any[]>([])
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
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)

  useEffect(() => {
    loadData()
  }, [device])

  useEffect(() => {
    loadHistory() // Always load history
  }, [device])

  async function loadData() {
    setLoading(true)
    try {
      const [params, modeInfo] = await Promise.all([
        apiClient.getPIDParameters(device),
        apiClient.getPIDMode(device)
      ])
      setParameters(params)
      setTempParameters(params) // Initialize temp values
      setMode(modeInfo.mode)
      setHasUnsavedChanges(false)
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

  const handleParameterChange = useCallback((param: keyof PIDParameters, value: number) => {
    // Only update temporary values, don't save automatically
    setTempParameters(prev => ({ ...prev, [param]: value }))
    setHasUnsavedChanges(true)
  }, [])

  const saveParameters = useCallback(async () => {
    if (!hasUnsavedChanges) return
    
    setSaving(true)
    try {
      await apiClient.updatePIDParameters(device, tempParameters)
      setParameters(tempParameters)
      setHasUnsavedChanges(false)
      // Reload history to show the new changes
      loadHistory()
    } catch (err) {
      logger.error('Failed to save PID parameters:', err)
    } finally {
      setSaving(false)
    }
  }, [device, tempParameters, hasUnsavedChanges])

  const handleModeChange = useCallback(async (newMode: PIDControlMode) => {
    // Simple prevention of same mode
    if (newMode === mode) return
    
    // Make API call first, then update UI
    try {
      await apiClient.setPIDMode(device, { mode: newMode })
      
      // Update UI only after successful API call
      setMode(newMode)
      
      // Clear unsaved changes when switching to AUTO mode
      if (newMode === 'auto_pid') {
        setTempParameters(parameters)
        setHasUnsavedChanges(false)
      }
    } catch (err) {
      logger.error('Failed to update PID mode:', err)
    }
  }, [device, mode, parameters])

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">PID Control</div>
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  const isAuto = mode === 'auto_pid'
  const isOff = mode === 'on_off'

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-2 h-full flex flex-col">
      {/* Header with Device and Mode */}
      <div className="flex justify-between items-start mb-4">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px]">PID Control</div>
        
        {/* Device Selector - Upper Left */}
        <select
          value={device}
          onChange={(e) => setDevice(e.target.value)}
          disabled={saving}
          className="bg-gray-800 border border-gray-700 rounded text-gray-200 text-xs px-2 py-1 focus:outline-none focus:border-cyan-500 transition-colors"
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
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-500/50 ${
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
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-500/50 ${
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
            className={`px-2 py-1 rounded text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-500/50 ${
              mode === 'auto_pid'
                ? 'bg-purple-900/50 text-purple-400 border border-purple-800/50'
                : 'bg-gray-800 text-gray-400 border border-gray-700 hover:bg-gray-700'
            }`}
          >
            AUTO
          </button>
        </div>
      </div>

      {/* PID Parameters - 3 Number Fields Side by Side */}
      {!isOff && (
        <>
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="text-gray-400 text-xs font-medium block mb-1">Kp</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.kp}
                onChange={(e) => handleParameterChange('kp', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-gray-800 border border-gray-700 rounded text-red-400 text-sm px-2 py-1 text-center font-mono focus:outline-none focus:border-cyan-500 transition-colors"
              />
            </div>
            <div className="flex-1">
              <label className="text-gray-400 text-xs font-medium block mb-1">Ki</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.ki}
                onChange={(e) => handleParameterChange('ki', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-gray-800 border border-gray-700 rounded text-green-400 text-sm px-2 py-1 text-center font-mono focus:outline-none focus:border-cyan-500 transition-colors"
              />
            </div>
            <div className="flex-1">
              <label className="text-gray-400 text-xs font-medium block mb-1">Kd</label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={tempParameters.kd}
                onChange={(e) => handleParameterChange('kd', parseFloat(e.target.value))}
                disabled={isAuto || saving}
                className="w-full bg-gray-800 border border-gray-700 rounded text-blue-400 text-sm px-2 py-1 text-center font-mono focus:outline-none focus:border-cyan-500 transition-colors"
              />
            </div>
          </div>
          
          {/* Save Button */}
          {hasUnsavedChanges && (
            <div className="mt-2">
              <button
                onClick={saveParameters}
                disabled={saving}
                className="w-full px-3 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-800 disabled:text-gray-600 rounded text-white text-xs font-bold tracking-wide transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          )}
        </>
      )}

      {/* History - Always Visible */}
      <div className="flex-1 bg-gray-950/50 border-t border-gray-800 p-3 flex flex-col">
        <div className="text-gray-400 text-xs font-medium mb-2">Recent Changes</div>
        <div className="flex-1 overflow-y-auto">
          {history.length === 0 ? (
            <div className="text-xs text-gray-500 text-center py-2">No history available</div>
          ) : (
            <div className="space-y-1">
              {history.slice(0, 10).map((entry, i) => (
                <div key={i} className="text-xs bg-gray-900/50 rounded px-2 py-1 border border-gray-800/50">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-gray-500">{new Date(entry.changed_at || entry.timestamp).toLocaleTimeString()}</span>
                    <span className="text-gray-600">{entry.source || 'Unknown'}</span>
                  </div>
                  <div className="flex gap-3 text-xs">
                    <span className="text-gray-400">Kp: <span className="text-red-400/70 font-mono">{typeof entry.kp === 'number' ? entry.kp.toFixed(2) : entry.kp}</span></span>
                    <span className="text-gray-400">Ki: <span className="text-green-400/70 font-mono">{typeof entry.ki === 'number' ? entry.ki.toFixed(2) : entry.ki}</span></span>
                    <span className="text-gray-400">Kd: <span className="text-blue-400/70 font-mono">{typeof entry.kd === 'number' ? entry.kd.toFixed(2) : entry.kd}</span></span>
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
