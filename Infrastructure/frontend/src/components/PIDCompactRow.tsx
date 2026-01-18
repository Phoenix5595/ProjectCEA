import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'
import type { PIDControlMode, PIDHistoryEntry } from '../types/pid'

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
  const [history, setHistory] = useState<PIDHistoryEntry[]>([])

  useEffect(() => {
    setLoading(true)
    loadData()
  }, [device])

  useEffect(() => {
    if (historyOpen) {
      loadHistory()
    }
  }, [historyOpen, device])

  async function loadHistory() {
    try {
      const data = await apiClient.getPIDParameterHistory(device, 20)
      setHistory(data)
    } catch (err) {
      logger.error('Failed to load PID history:', err)
      setHistory([])
    }
  }

  async function loadData() {
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

  const isAuto = mode === 'auto_pid'
  const isOff = mode === 'on_off'

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 shadow-xl overflow-hidden">
      <div className="flex border-b border-gray-800">
        {DEVICE_TYPES.map(t => (
          <button
            key={t}
            onClick={() => setDevice(t)}
            disabled={loading}
            className={`flex-1 py-3 text-xs font-bold tracking-widest transition-colors uppercase ${
              device === t 
                ? 'bg-gray-800 text-cyan-400 border-b-2 border-cyan-500' 
                : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/50'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="p-5 space-y-6">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Control Mode</label>
            <div className={`h-2 w-2 rounded-full ${
              isAuto ? 'bg-purple-500 shadow-[0_0_8px_rgba(168,85,247,0.5)]' :
              isOff ? 'bg-red-500' : 'bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]'
            }`} />
          </div>
          
          <div className="grid grid-cols-3 gap-1 bg-gray-950 p-1 rounded-lg border border-gray-800">
            {(['auto_pid', 'pid', 'on_off'] as PIDControlMode[]).map(m => (
              <button
                key={m}
                onClick={() => handleModeChange(m)}
                disabled={loading}
                className={`py-2 px-2 rounded-md text-[11px] font-bold transition-all uppercase ${
                  mode === m 
                    ? m === 'auto_pid' ? 'bg-purple-900/50 text-purple-300 border border-purple-700/50' :
                      m === 'on_off' ? 'bg-red-900/30 text-red-400 border border-red-800/50' :
                      'bg-cyan-900/30 text-cyan-300 border border-cyan-800/50'
                    : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                }`}
              >
                {m === 'auto_pid' ? 'Auto-Tune' : m === 'pid' ? 'Manual PID' : 'ON/OFF'}
              </button>
            ))}
          </div>
        </div>

        <div className={`transition-opacity duration-300 ${isOff ? 'opacity-30 pointer-events-none grayscale' : 'opacity-100'}`}>
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="text-[10px] font-medium text-gray-500 block text-center uppercase">Kp (Prop)</label>
              <input
                type="number"
                step="0.1"
                value={kp}
                onChange={(e) => setKp(parseFloat(e.target.value) || 0)}
                disabled={isAuto || isOff || loading}
                className="w-full bg-gray-950 border border-gray-700 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 rounded px-2 py-2 text-center text-sm font-mono text-gray-200 disabled:opacity-50 transition-colors"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-medium text-gray-500 block text-center uppercase">Ki (Int)</label>
              <input
                type="number"
                step="0.001"
                value={ki}
                onChange={(e) => setKi(parseFloat(e.target.value) || 0)}
                disabled={isAuto || isOff || loading}
                className="w-full bg-gray-950 border border-gray-700 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 rounded px-2 py-2 text-center text-sm font-mono text-gray-200 disabled:opacity-50 transition-colors"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-medium text-gray-500 block text-center uppercase">Kd (Deriv)</label>
              <input
                type="number"
                step="0.01"
                value={kd}
                onChange={(e) => setKd(parseFloat(e.target.value) || 0)}
                disabled={isAuto || isOff || loading}
                className="w-full bg-gray-950 border border-gray-700 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 rounded px-2 py-2 text-center text-sm font-mono text-gray-200 disabled:opacity-50 transition-colors"
              />
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between pt-2 border-t border-gray-800/50">
          <button
            onClick={() => setHistoryOpen(!historyOpen)}
            className="flex items-center gap-2 text-[11px] font-medium text-gray-500 hover:text-cyan-400 transition-colors"
          >
            <span>HISTORY</span>
            <span className="text-[9px]">{historyOpen ? '▲' : '▼'}</span>
          </button>

          <div className="flex gap-3">
             {!isOff && !isAuto && (
               <button
                 onClick={handleReset}
                 disabled={loading || saving}
                 className="text-[11px] font-medium text-gray-500 hover:text-red-400 transition-colors px-2"
               >
                 RESET
               </button>
             )}
             
             {!isOff && (
               <button
                 onClick={handleSave}
                 disabled={loading || saving || isAuto}
                 className="px-4 py-1.5 bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-800 disabled:text-gray-600 rounded text-white text-[11px] font-bold tracking-wide transition-colors shadow-lg shadow-cyan-900/20"
               >
                 {saving ? 'SAVING...' : 'SAVE CONFIG'}
               </button>
             )}
          </div>
        </div>
      </div>

      {historyOpen && (
        <div className="bg-gray-950/50 border-t border-gray-800 p-4 animate-in slide-in-from-top-2 duration-200">
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
  )
}
