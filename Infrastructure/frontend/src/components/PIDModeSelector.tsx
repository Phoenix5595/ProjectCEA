import type { PIDControlMode } from '../types/pid';

interface PIDModeSelectorProps {
  mode: PIDControlMode;
  onChange: (mode: PIDControlMode) => void;
  disabled?: boolean;
}

const MODES: { id: PIDControlMode; label: string; description: string }[] = [
  {
    id: 'auto_pid',
    label: 'Auto PID',
    description: 'System continuously tunes K values using relay feedback'
  },
  {
    id: 'pid',
    label: 'PID',
    description: 'Manual PID control with custom K values'
  },
  {
    id: 'on_off',
    label: 'ON/OFF',
    description: 'Simple binary control with hysteresis'
  }
];

export default function PIDModeSelector({ mode, onChange, disabled = false }: PIDModeSelectorProps) {
  const currentMode = MODES.find(m => m.id === mode) || MODES.find(m => m.id === 'pid') || MODES[0];

  return (
    <div className="w-full bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm border border-gray-200 dark:border-gray-700">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
        Control Mode
      </h3>
      
      <div className="grid grid-cols-3 gap-3 mb-4">
        {MODES.map((option) => {
          const isSelected = mode === option.id;
          return (
            <button
              key={option.id}
              onClick={() => onChange(option.id)}
              disabled={disabled}
              className={`
                relative flex flex-col items-center justify-center p-3 rounded-lg border-2 transition-all
                ${isSelected 
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' 
                  : 'border-gray-200 dark:border-gray-600 hover:border-gray-300 dark:hover:border-gray-500 text-gray-600 dark:text-gray-400'
                }
                ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
              `}
            >
              <span className="text-sm font-semibold mb-2">{option.label}</span>
              <div className={`
                w-4 h-4 rounded-full border flex items-center justify-center
                ${isSelected ? 'border-blue-500' : 'border-gray-400'}
              `}>
                {isSelected && (
                  <div className="w-2 h-2 rounded-full bg-blue-500" />
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="flex items-start gap-2 text-sm text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 p-3 rounded-md">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 flex-shrink-0 text-blue-500">
          <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
        </svg>
        <span>
          {currentMode.description}
        </span>
      </div>
    </div>
  );
}
