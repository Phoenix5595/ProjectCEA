import { useState, useEffect } from 'react';
import { apiClient } from '../services/api';
import { logger } from '../utils/logger'
import { useToast } from '../contexts/ToastContext';
import { validatePIDParameter } from '../utils/validation';
import type { 
  PIDParameters, 
  PIDParameterUpdate, 
  PIDControlMode, 
  AutotuneState,
  PIDModeUpdate
} from '../types/pid';
import PIDModeSelector from './PIDModeSelector';
import PIDHistoryTerminal from './PIDHistoryTerminal';

const DEVICE_TYPES = ['heater', 'fan', 'co2'];


export default function PIDEditor() {
  const { showToast } = useToast();
  const [selectedDeviceType, setSelectedDeviceType] = useState<string>('heater');
  const [formData, setFormData] = useState<PIDParameterUpdate>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [currentParams, setCurrentParams] = useState<PIDParameters | null>(null);
  const [currentMode, setCurrentMode] = useState<PIDControlMode>('pid');
  const [hysteresisData, setHysteresisData] = useState({ high: 0.5, low: 0.5 });
  const [autotuneState, setAutotuneState] = useState<AutotuneState | null>(null);

  // Collapse history by default
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    loadAllData();
    const interval = setInterval(loadAutotuneStatus, 2000);
    return () => clearInterval(interval);
  }, [selectedDeviceType]);

  async function loadAllData() {
    setLoading(true);
    try {
      await Promise.all([
        loadPIDParameters(),
        loadModeInfo(),
        loadAutotuneStatus()
      ]);
    } catch (error) {
      logger.error('Error loading data:', error);
    } finally {
      setLoading(false);
    }
  }

  async function loadPIDParameters() {
    try {
      const params = await apiClient.getPIDParameters(selectedDeviceType);
      setCurrentParams(params);
      setFormData({
        kp: params.kp,
        ki: params.ki,
        kd: params.kd,
      });
      setErrors({});
    } catch (error) {
      logger.error('Error loading PID parameters:', error);
      setCurrentParams(null);
    }
  }

  async function loadModeInfo() {
    try {
      const info = await apiClient.getPIDMode(selectedDeviceType);
      setCurrentMode(info.mode);
      setHysteresisData({
        high: info.hysteresis_high,
        low: info.hysteresis_low
      });
    } catch (error) {
      logger.error('Error loading mode info:', error);
    }
  }

  async function loadAutotuneStatus() {
    if (currentMode !== 'auto_pid') return;
    try {
      const status = await apiClient.getAutotuneStatus(selectedDeviceType);
      setAutotuneState(status);
    } catch (error) {
      logger.error('Error loading autotune status:', error);
    }
  }

  async function handleModeChange(mode: PIDControlMode) {
    try {
      const update: PIDModeUpdate = { mode };
      if (mode === 'on_off') {
        update.hysteresis_high = hysteresisData.high;
        update.hysteresis_low = hysteresisData.low;
      }
      await apiClient.setPIDMode(selectedDeviceType, update);
      setCurrentMode(mode);
      if (mode === 'auto_pid') {
        loadAutotuneStatus();
      }
    } catch (error) {
      logger.error('Error changing mode:', error);
      showToast('Failed to change mode', 'error');
    }
  }

  function handleChange(param: 'kp' | 'ki' | 'kd', value: number) {
    setFormData(prev => ({ ...prev, [param]: value }));
    const result = validatePIDParameter(selectedDeviceType, param, value);
    if (!result.isValid) {
      setErrors(prev => ({ ...prev, [param]: result.error || 'Invalid value' }));
    } else {
      setErrors(prev => {
        const newErrors = { ...prev };
        delete newErrors[param];
        return newErrors;
      });
    }
  }

  async function handleSubmit() {
    if (Object.keys(errors).length > 0) {
      showToast('Please fix validation errors before submitting', 'error');
      return;
    }

    if (currentParams?.updated_at) {
      const fresh = await apiClient.getPIDParameters(selectedDeviceType);
      if (fresh.updated_at !== currentParams.updated_at) {
        if (!window.confirm('Parameters have been changed externally. Overwrite?')) {
          loadAllData();
          return;
        }
      }
    }

    setLoading(true);
    try {
      const newParams = await apiClient.updatePIDParameters(selectedDeviceType, formData);
      setCurrentParams(newParams);
      loadPIDParameters();
    } catch (error: any) {
      showToast(`Error updating PID parameters: ${error.response?.data?.detail || error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-gray-900 rounded-sm p-2 border border-gray-800 h-full flex flex-col">
      <div className="flex justify-between items-center mb-2">
        <div className="text-xs font-bold text-gray-400 uppercase tracking-wider">
           PID CONTROL
        </div>
        <div className="flex gap-2">
            <select
               value={selectedDeviceType}
               onChange={(e) => setSelectedDeviceType(e.target.value)}
               className="h-6 px-1 py-0 bg-gray-800 border border-gray-700 text-gray-300 text-[10px] rounded-sm focus:outline-hidden"
            >
               {DEVICE_TYPES.map(type => (
                  <option key={type} value={type}>{type.toUpperCase()}</option>
               ))}
            </select>
        </div>
      </div>

      <div className="mb-2">
          <PIDModeSelector 
            mode={currentMode} 
            onChange={handleModeChange}
            disabled={loading}
          />
      </div>

      {currentMode === 'auto_pid' && autotuneState && (
        <div className="bg-blue-900/20 p-2 rounded-sm border border-blue-800 mb-2">
          <div className="flex items-center justify-between mb-1">
            <h4 className="font-semibold text-blue-100 flex items-center gap-2 text-[10px]">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-blue-500"></span>
              </span>
              Auto-tuning
            </h4>
            <span className="text-[10px] font-medium px-2 py-0 bg-blue-800 text-blue-100 rounded-full capitalize">
              {autotuneState.status}
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1 mb-1">
            <div 
              className="bg-blue-500 h-1 rounded-full transition-all duration-500" 
              style={{ width: `${(autotuneState.cycles_completed / 5) * 100}%` }}
            ></div>
          </div>
        </div>
      )}

      {currentMode === 'on_off' ? (
        <div className="grid grid-cols-2 gap-2 mb-2">
          <div>
            <label className="block text-[10px] font-medium text-gray-400 mb-0.5">
              Hyst High
            </label>
            <input
              type="number"
              step="0.1"
              value={hysteresisData.high}
              onChange={(e) => setHysteresisData(prev => ({ ...prev, high: parseFloat(e.target.value) }))}
              onBlur={() => handleModeChange('on_off')}
              className="border border-gray-700 rounded-sm px-2 py-1 w-full bg-gray-800 text-gray-200 text-xs h-6"
            />
          </div>
          <div>
            <label className="block text-[10px] font-medium text-gray-400 mb-0.5">
              Hyst Low
            </label>
            <input
              type="number"
              step="0.1"
              value={hysteresisData.low}
              onChange={(e) => setHysteresisData(prev => ({ ...prev, low: parseFloat(e.target.value) }))}
              onBlur={() => handleModeChange('on_off')}
              className="border border-gray-700 rounded-sm px-2 py-1 w-full bg-gray-800 text-gray-200 text-xs h-6"
            />
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 mb-2">
          <div>
            <label className="block text-[10px] font-medium text-gray-400 mb-0.5">Kp</label>
            <input
              type="number"
              step="0.01"
              value={formData.kp ?? ''}
              onChange={(e) => handleChange('kp', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded px-1 py-0.5 w-full bg-gray-800 text-gray-200 text-xs h-6 ${
                errors.kp ? 'border-red-500' : 'border-gray-700'
              } ${currentMode === 'auto_pid' ? 'opacity-50' : ''}`}
            />
          </div>
          <div>
            <label className="block text-[10px] font-medium text-gray-400 mb-0.5">Ki</label>
            <input
              type="number"
              step="0.001"
              value={formData.ki ?? ''}
              onChange={(e) => handleChange('ki', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded px-1 py-0.5 w-full bg-gray-800 text-gray-200 text-xs h-6 ${
                errors.ki ? 'border-red-500' : 'border-gray-700'
              } ${currentMode === 'auto_pid' ? 'opacity-50' : ''}`}
            />
          </div>
          <div>
            <label className="block text-[10px] font-medium text-gray-400 mb-0.5">Kd</label>
            <input
              type="number"
              step="0.01"
              value={formData.kd ?? ''}
              onChange={(e) => handleChange('kd', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded px-1 py-0.5 w-full bg-gray-800 text-gray-200 text-xs h-6 ${
                errors.kd ? 'border-red-500' : 'border-gray-700'
              } ${currentMode === 'auto_pid' ? 'opacity-50' : ''}`}
            />
          </div>
        </div>
      )}

      {currentMode !== 'auto_pid' && currentMode !== 'on_off' && (
        <div className="flex gap-2 mb-2 justify-end">
          <button
            onClick={async () => {
              if (window.confirm('Reset?')) {
                setLoading(true);
                try {
                  const reset = await apiClient.resetPIDParameters(selectedDeviceType);
                  setFormData({ kp: reset.kp, ki: reset.ki, kd: reset.kd });
                  setCurrentParams(reset);
                } catch (e) {
                  // ignore
                }
                setLoading(false);
              }
            }}
            disabled={loading}
            className="text-gray-400 hover:text-white text-[10px]"
          >
            Reset
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || Object.keys(errors).length > 0}
            className="bg-cyan-700 hover:bg-cyan-600 text-white px-3 py-0.5 rounded-sm text-[10px] font-bold"
          >
            {loading ? '...' : 'SAVE'}
          </button>
        </div>
      )}
      
      <div className="mt-auto border-t border-gray-800 pt-2">
         <div 
           className="flex justify-between items-center cursor-pointer"
           onClick={() => setHistoryOpen(!historyOpen)}
         >
           <span className="text-[10px] text-gray-500 flex items-center gap-1">
             <span>{historyOpen ? '▼' : '▶'}</span> History
           </span>
         </div>
         {historyOpen && (
           <div className="mt-2">
              <PIDHistoryTerminal deviceType={selectedDeviceType} maxLines={10} />
           </div>
         )}
      </div>
    </div>
  );
}
