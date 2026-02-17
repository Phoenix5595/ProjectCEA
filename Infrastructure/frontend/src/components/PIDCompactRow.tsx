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
 <div className="bg-surface-primary rounded-xl border border-border-subtle shadow-xl overflow-hidden">
 <div className="flex border-b border-border-subtle">
 {DEVICE_TYPES.map(t => (
 <button
 key={t}
 onClick={() => setDevice(t)}
 disabled={loading}
 className={`flex-1 py-3 text-xs font-bold tracking-widest transition-colors uppercase ${
 device === t 
 ? 'bg-surface-secondary text-accent-data border-b-2 border-accent-vivid' 
 : 'text-text-subtle hover:text-text-secondary hover:bg-surface-secondary/50'
 }`}
 >
 {t}
 </button>
 ))}
 </div>

 <div className="p-5 space-y-6">
 <div className="space-y-2">
 <div className="flex items-center justify-between">
 <label className="text-[10px] font-bold text-text-subtle uppercase tracking-wider">Control Mode</label>
 <div className={`h-2 w-2 rounded-full ${
 isAuto ? 'bg-mode-auto shadow-[0_0_8px_rgba(168,85,247,0.5)]' :
 isOff ? 'bg-status-danger-vivid' : 'bg-accent-vivid shadow-[0_0_8px_rgba(6,182,212,0.5)]'
 }`} />
 </div>
 
 <div className="grid grid-cols-3 gap-1 bg-surface-base p-1 rounded-lg border border-border-subtle">
 {(['auto_pid', 'pid', 'on_off'] as PIDControlMode[]).map(m => (
 <button
 key={m}
 onClick={() => handleModeChange(m)}
 disabled={loading}
 className={`py-2 px-2 rounded-md text-[11px] font-bold transition-all uppercase ${
 mode === m 
 ? m === 'auto_pid' ? 'bg-mode-auto-dim/50 text-mode-auto-text border border-mode-auto-border/50' :
 m === 'on_off' ? 'bg-status-danger-bg/30 text-status-danger border border-status-danger-border/50' :
 'bg-accent-dim/30 text-accent-data border border-accent-dim/50'
 : 'text-text-subtle hover:text-text-secondary hover:bg-surface-secondary'
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
 <label className="text-[10px] font-medium text-text-subtle block text-center uppercase">Kp (Prop)</label>
 <input
 type="number"
 step="0.1"
 value={kp}
 onChange={(e) => setKp(parseFloat(e.target.value) || 0)}
 disabled={isAuto || isOff || loading}
 className="w-full bg-surface-base border border-border-default focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid rounded-sm px-2 py-2 text-center text-sm font-mono text-text-input disabled:opacity-50 transition-colors"
 />
 </div>
 <div className="space-y-1">
 <label className="text-[10px] font-medium text-text-subtle block text-center uppercase">Ki (Int)</label>
 <input
 type="number"
 step="0.001"
 value={ki}
 onChange={(e) => setKi(parseFloat(e.target.value) || 0)}
 disabled={isAuto || isOff || loading}
 className="w-full bg-surface-base border border-border-default focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid rounded-sm px-2 py-2 text-center text-sm font-mono text-text-input disabled:opacity-50 transition-colors"
 />
 </div>
 <div className="space-y-1">
 <label className="text-[10px] font-medium text-text-subtle block text-center uppercase">Kd (Deriv)</label>
 <input
 type="number"
 step="0.01"
 value={kd}
 onChange={(e) => setKd(parseFloat(e.target.value) || 0)}
 disabled={isAuto || isOff || loading}
 className="w-full bg-surface-base border border-border-default focus:border-accent-vivid focus:ring-1 focus:ring-accent-vivid rounded-sm px-2 py-2 text-center text-sm font-mono text-text-input disabled:opacity-50 transition-colors"
 />
 </div>
 </div>
 </div>

 <div className="flex items-center justify-between pt-2 border-t border-border-subtle/50">
 <button
 onClick={() => setHistoryOpen(!historyOpen)}
 className="flex items-center gap-2 text-[11px] font-medium text-text-subtle hover:text-accent-data transition-colors"
 >
 <span>HISTORY</span>
 <span className="text-[9px]">{historyOpen ? '▲' : '▼'}</span>
 </button>

 <div className="flex gap-3">
 {!isOff && !isAuto && (
 <button
 onClick={handleReset}
 disabled={loading || saving}
 className="text-[11px] font-medium text-text-subtle hover:text-status-danger transition-colors px-2"
 >
 RESET
 </button>
 )}
 
 {!isOff && (
 <button
 onClick={handleSave}
 disabled={loading || saving || isAuto}
 className="px-4 py-1.5 bg-accent-active hover:bg-accent-hover disabled:bg-surface-secondary disabled:text-text-faint rounded-sm text-text-default text-[11px] font-bold tracking-wide transition-colors shadow-lg shadow-accent-dim/20"
 >
 {saving ? 'SAVING...' : 'SAVE CONFIG'}
 </button>
 )}
 </div>
 </div>
 </div>

 {historyOpen && (
 <div className="bg-surface-base/50 border-t border-border-subtle p-4 animate-in slide-in-from-top-2 duration-200">
 {history.length === 0 ? (
 <div className="text-xs text-text-subtle text-center py-2">No history available</div>
 ) : (
 <div className="space-y-2 max-h-48 overflow-y-auto">
 {history.map((entry, i) => (
 <div key={i} className="text-xs bg-surface-primary/50 rounded-sm px-3 py-2 border border-border-subtle/50">
 <div className="flex items-center justify-between mb-1">
 <span className="text-text-subtle">{new Date(entry.timestamp).toLocaleString()}</span>
 <span className="text-text-faint">{entry.reason}</span>
 </div>
 <div className="flex gap-4">
 <span className="text-text-muted">Kp: <span className="text-status-danger/70 font-mono line-through">{entry.old_values.kp}</span> → <span className="text-accent-data font-mono">{entry.new_values.kp}</span></span>
 <span className="text-text-muted">Ki: <span className="text-status-danger/70 font-mono line-through">{entry.old_values.ki}</span> → <span className="text-accent-data font-mono">{entry.new_values.ki}</span></span>
 <span className="text-text-muted">Kd: <span className="text-status-danger/70 font-mono line-through">{entry.old_values.kd}</span> → <span className="text-accent-data font-mono">{entry.new_values.kd}</span></span>
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
