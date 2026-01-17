import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { PIDControlMode } from '../types/pid'

const DEVICE_TYPES = ['heater', 'fan', 'co2']

export default function PIDCompactRow() {
  const [device, setDevice] = useState('heater')
  const [mode, setMode] = useState<PIDControlMode>('pid')
  const [kp, setKp] = useState(0)
  const [ki, setKi] = useState(0)
  const [kd, setKd] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  useEffect(() => {
    loadData()
  }, [device])

  async function loadData() {
    setLoading(true)
    try {
      const [params, modeInfo] = await Promise.all([
        apiClient.getPIDParameters(device),
        apiClient.getPIDMode(device)
      ])
      setKp(params.kp)
      setKi(params.ki)
      setKd(params.kd)
      setMode(modeInfo.mode)
    } catch (err) {
      logger.error('Failed to load PID data:', err)
    } finally {
      setLoading(false)
    }
  }

  async function handleModeChange(newMode: PIDControlMode) {
    try {
      await apiClient.setPIDMode(device, { mode: newMode })
      setMode(newMode)
    } catch (err) {
      logger.error('Failed to change mode:', err)
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      await apiClient.updatePIDParameters(device, { kp, ki, kd })
    } catch (err) {
      logger.error('Failed to save PID parameters:', err)
    } finally {
      setSaving(false)
    }
  }

  async function handleReset() {
    if (!window.confirm('Reset PID parameters to defaults?')) return
    setLoading(true)
    try {
      const reset = await apiClient.resetPIDParameters(device)
      setKp(reset.kp)
      setKi(reset.ki)
      setKd(reset.kd)
    } catch (err) {
      logger.error('Failed to reset PID parameters:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-3">
      <div className="flex items-center gap-3 text-[12px] flex-wrap">
          <span className="text-gray-400 uppercase font-bold tracking-wider text-[14px]">PID</span>

        <select
          value={device}
          onChange={(e) => setDevice(e.target.value)}
          disabled={loading}
          className="bg-gray-800 border border-gray-700 px-2 py-1 rounded text-gray-200 text-[12px]"
        >
          {DEVICE_TYPES.map(t => (
            <option key={t} value={t}>{t.toUpperCase()}</option>
          ))}
        </select>

        <div className="flex gap-1">
          {(['auto_pid', 'pid', 'on_off'] as PIDControlMode[]).map(m => (
            <button
              key={m}
              onClick={() => handleModeChange(m)}
              disabled={loading}
              className={`px-2 py-1 rounded text-[12px] font-medium transition-colors ${
                mode === m 
                  ? 'bg-cyan-700 text-white' 
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {m === 'auto_pid' ? 'Auto' : m === 'pid' ? 'PID' : 'ON/OFF'}
            </button>
          ))}
        </div>

        {mode !== 'on_off' && (
          <div className="flex gap-2 items-center">
            <label className="flex items-center gap-1 text-gray-400">
              Kp:
              <input
                type="number"
                step="0.1"
                value={kp}
                onChange={(e) => setKp(parseFloat(e.target.value) || 0)}
                disabled={mode === 'auto_pid' || loading}
                className="w-16 bg-gray-800 border border-gray-700 px-1 py-0.5 rounded text-center text-[16px] text-gray-200 disabled:opacity-50"
              />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              Ki:
              <input
                type="number"
                step="0.001"
                value={ki}
                onChange={(e) => setKi(parseFloat(e.target.value) || 0)}
                disabled={mode === 'auto_pid' || loading}
                className="w-16 bg-gray-800 border border-gray-700 px-1 py-0.5 rounded text-center text-[16px] text-gray-200 disabled:opacity-50"
              />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              Kd:
              <input
                type="number"
                step="0.01"
                value={kd}
                onChange={(e) => setKd(parseFloat(e.target.value) || 0)}
                disabled={mode === 'auto_pid' || loading}
                className="w-16 bg-gray-800 border border-gray-700 px-1 py-0.5 rounded text-center text-[16px] text-gray-200 disabled:opacity-50"
              />
            </label>
          </div>
        )}

        <div className="flex gap-2 ml-auto">
          {mode === 'pid' && (
            <>
              <button
                onClick={handleReset}
                disabled={loading || saving}
                className="text-gray-400 hover:text-white text-[12px] px-2 py-1"
              >
                Reset
              </button>
              <button
                onClick={handleSave}
                disabled={loading || saving}
                className="px-3 py-1 bg-cyan-700 hover:bg-cyan-600 rounded text-white text-[12px] font-bold disabled:opacity-50"
              >
                {saving ? '...' : 'Save'}
              </button>
            </>
          )}
          <button
            onClick={() => setHistoryOpen(!historyOpen)}
            className="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-gray-400 text-[12px]"
          >
            Hist {historyOpen ? '▲' : '▼'}
          </button>
        </div>
      </div>

      {historyOpen && (
        <div className="mt-2 pt-2 border-t border-gray-800">
          <div className="text-[12px] text-gray-500">
            PID history for {device.toUpperCase()} - Use full PID Editor for detailed history view
          </div>
        </div>
      )}
    </div>
  )
}
