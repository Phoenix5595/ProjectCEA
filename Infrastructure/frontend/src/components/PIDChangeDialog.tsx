interface PIDChangeDialogProps {
  isOpen: boolean;
  onClose: () => void;
  deviceType: string;
  oldValues: { kp: number; ki: number; kd: number };
  newValues: { kp: number; ki: number; kd: number };
  reason: string;
  tuningMetrics?: { ku?: number; tu?: number; method?: string };
}

export default function PIDChangeDialog({
  isOpen,
  onClose,
  deviceType,
  oldValues,
  newValues,
  reason,
  tuningMetrics
}: PIDChangeDialogProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full p-6 border border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <span>⚡</span> PID Parameters Updated
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300"
          >
            <span className="sr-only">Close</span>
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <span className="text-sm font-medium text-gray-500 dark:text-gray-400">Device</span>
            <p className="text-lg font-semibold text-gray-900 dark:text-white capitalize">{deviceType}</p>
          </div>

          <div className="bg-gray-50 dark:bg-gray-900/50 rounded-md p-4">
            <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Changes</h4>
            <div className="space-y-2">
              {['kp', 'ki', 'kd'].map((param) => (
                <div key={param} className="flex justify-between items-center text-sm">
                  <span className="text-gray-500 dark:text-gray-400 uppercase w-8">{param}</span>
                  <div className="flex items-center gap-3 font-mono">
                    <span className="text-gray-600 dark:text-gray-400">
                      {oldValues[param as keyof typeof oldValues]?.toFixed(3) ?? '-'}
                    </span>
                    <span className="text-gray-400">→</span>
                    <span className="text-blue-600 dark:text-blue-400 font-bold">
                      {newValues[param as keyof typeof newValues]?.toFixed(3) ?? '-'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Reason</h4>
            <p className="text-sm text-gray-600 dark:text-gray-400 italic">
              "{reason}"
            </p>
          </div>

          {tuningMetrics && (
            <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
              <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
                Tuning Metrics
              </h4>
              <div className="grid grid-cols-2 gap-4 text-sm">
                {tuningMetrics.ku !== undefined && (
                  <div>
                    <span className="text-gray-500 dark:text-gray-400 block text-xs">Ultimate Gain (Ku)</span>
                    <span className="font-mono text-gray-900 dark:text-white">{tuningMetrics.ku.toFixed(2)}</span>
                  </div>
                )}
                {tuningMetrics.tu !== undefined && (
                  <div>
                    <span className="text-gray-500 dark:text-gray-400 block text-xs">Ultimate Period (Tu)</span>
                    <span className="font-mono text-gray-900 dark:text-white">{tuningMetrics.tu.toFixed(1)}s</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="mt-6">
          <button
            onClick={onClose}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
