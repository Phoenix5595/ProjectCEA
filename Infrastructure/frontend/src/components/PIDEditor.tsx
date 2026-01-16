import { useState, useEffect } from 'react';
import { apiClient } from '../services/api';
import { logger } from '../utils/logger';
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
  const [selectedDeviceType, setSelectedDeviceType] = useState<string>('heater');
  const [formData, setFormData] = useState<PIDParameterUpdate>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [currentParams, setCurrentParams] = useState<PIDParameters | null>(null);
  const [currentMode, setCurrentMode] = useState<PIDControlMode>('pid');
  const [hysteresisData, setHysteresisData] = useState({ high: 0.5, low: 0.5 });
  const [autotuneState, setAutotuneState] = useState<AutotuneState | null>(null);

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
      alert('Failed to change mode');
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
      alert('Please fix validation errors before submitting');
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
      alert(`Error updating PID parameters: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
          Device Type
        </label>
        <select
          value={selectedDeviceType}
          onChange={(e) => setSelectedDeviceType(e.target.value)}
          className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          {DEVICE_TYPES.map(type => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Note: Fans can use PID control for temperature cooling. Dehumidifiers are ON/OFF only.
        </p>
      </div>

      <PIDModeSelector 
        mode={currentMode} 
        onChange={handleModeChange}
        disabled={loading}
      />

      {currentMode === 'auto_pid' && autotuneState && (
        <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg border border-blue-200 dark:border-blue-800">
          <div className="flex items-center justify-between mb-2">
            <h4 className="font-semibold text-blue-900 dark:text-blue-100 flex items-center gap-2">
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
              </span>
              Auto-tuning in Progress
            </h4>
            <span className="text-sm font-medium px-2 py-1 bg-blue-100 dark:bg-blue-800 text-blue-800 dark:text-blue-100 rounded-full capitalize">
              {autotuneState.status}
            </span>
          </div>
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2.5 mb-2">
            <div 
              className="bg-blue-600 h-2.5 rounded-full transition-all duration-500" 
              style={{ width: `${(autotuneState.cycles_completed / 5) * 100}%` }}
            ></div>
          </div>
          <div className="flex justify-between text-sm text-gray-600 dark:text-gray-400">
            <span>Cycle {autotuneState.cycles_completed} / 5</span>
            <span>Est. Remaining: {autotuneState.estimated_remaining_cycles} cycles</span>
          </div>
        </div>
      )}

      {currentMode === 'on_off' ? (
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Hysteresis High
            </label>
            <input
              type="number"
              step="0.1"
              value={hysteresisData.high}
              onChange={(e) => setHysteresisData(prev => ({ ...prev, high: parseFloat(e.target.value) }))}
              onBlur={() => handleModeChange('on_off')}
              className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Hysteresis Low
            </label>
            <input
              type="number"
              step="0.1"
              value={hysteresisData.low}
              onChange={(e) => setHysteresisData(prev => ({ ...prev, low: parseFloat(e.target.value) }))}
              onBlur={() => handleModeChange('on_off')}
              className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Kp (Proportional Gain)
            </label>
            <input
              type="number"
              step="0.01"
              value={formData.kp ?? ''}
              onChange={(e) => handleChange('kp', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${
                errors.kp ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'
              } ${currentMode === 'auto_pid' ? 'opacity-50 cursor-not-allowed' : ''}`}
            />
            {currentParams?.kp !== undefined && (
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Current: {currentParams.kp}
              </div>
            )}
            {errors.kp && (
              <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.kp}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Ki (Integral Gain)
            </label>
            <input
              type="number"
              step="0.001"
              value={formData.ki ?? ''}
              onChange={(e) => handleChange('ki', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${
                errors.ki ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'
              } ${currentMode === 'auto_pid' ? 'opacity-50 cursor-not-allowed' : ''}`}
            />
            {currentParams?.ki !== undefined && (
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Current: {currentParams.ki}
              </div>
            )}
            {errors.ki && (
              <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.ki}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Kd (Derivative Gain)
            </label>
            <input
              type="number"
              step="0.01"
              value={formData.kd ?? ''}
              onChange={(e) => handleChange('kd', parseFloat(e.target.value))}
              disabled={currentMode === 'auto_pid'}
              className={`border rounded-md px-3 py-2 w-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 ${
                errors.kd ? 'border-red-500 dark:border-red-400' : 'border-gray-300 dark:border-gray-600'
              } ${currentMode === 'auto_pid' ? 'opacity-50 cursor-not-allowed' : ''}`}
            />
            {currentParams?.kd !== undefined && (
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Current: {currentParams.kd}
              </div>
            )}
            {errors.kd && (
              <p className="text-sm text-red-500 dark:text-red-400 mt-1">{errors.kd}</p>
            )}
          </div>
        </div>
      )}

      {currentMode !== 'auto_pid' && currentMode !== 'on_off' && (
        <div className="flex gap-4 mt-6">
          <button
            onClick={handleSubmit}
            disabled={loading || Object.keys(errors).length > 0}
            className="bg-blue-600 dark:bg-blue-500 text-white px-6 py-2 rounded-md hover:bg-blue-700 dark:hover:bg-blue-600 disabled:opacity-50"
          >
            {loading ? 'Saving...' : 'Save PID Parameters'}
          </button>
          <button
            onClick={async () => {
              if (window.confirm('Reset PID parameters to config defaults?')) {
                setLoading(true);
                try {
                  const reset = await apiClient.resetPIDParameters(selectedDeviceType);
                  setFormData({ kp: reset.kp, ki: reset.ki, kd: reset.kd });
                  setCurrentParams(reset);
                } catch (e) {
                  console.error('Reset failed:', e);
                }
                setLoading(false);
              }
            }}
            disabled={loading}
            className="bg-gray-500 dark:bg-gray-600 text-white px-4 py-2 rounded-md hover:bg-gray-600 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            Reset to Defaults
          </button>
        </div>
      )}

      {currentParams?.updated_at && (
        <div className="text-xs text-gray-400 dark:text-gray-500 mt-8 pt-4 border-t border-gray-200 dark:border-gray-700">
          Last updated: {new Date(currentParams.updated_at).toLocaleString()} 
          {currentParams.updated_by && ` by ${currentParams.updated_by}`}
          {currentParams.source && ` (${currentParams.source})`}
        </div>
      )}

      <PIDHistoryTerminal deviceType={selectedDeviceType} />
    </div>
  );
}
