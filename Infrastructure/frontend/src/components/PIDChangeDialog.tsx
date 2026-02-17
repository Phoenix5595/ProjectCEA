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
 <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-surface-base/60">
 <div className="bg-surface-secondary rounded-lg shadow-xl max-w-md w-full p-6 border border-border-default">
 <div className="flex items-center justify-between mb-4">
 <h3 className="text-lg font-bold text-text-default flex items-center gap-2">
 <span>⚡</span> PID Parameters Updated
 </h3>
 <button
 onClick={onClose}
 className="text-text-muted hover:text-text-secondary"
 >
 <span className="sr-only">Close</span>
 <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
 </svg>
 </button>
 </div>

 <div className="space-y-4">
 <div>
 <span className="text-sm font-medium text-text-muted">Device</span>
 <p className="text-lg font-semibold text-text-default capitalize">{deviceType}</p>
 </div>

 <div className="bg-surface-primary/50 rounded-md p-4">
 <h4 className="text-sm font-medium text-text-secondary mb-2">Changes</h4>
 <div className="space-y-2">
 {['kp', 'ki', 'kd'].map((param) => (
 <div key={param} className="flex justify-between items-center text-sm">
 <span className="text-text-muted uppercase w-8">{param}</span>
 <div className="flex items-center gap-3 font-mono">
 <span className="text-text-muted">
 {oldValues[param as keyof typeof oldValues]?.toFixed(3) ?? '-'}
 </span>
 <span className="text-text-muted">→</span>
 <span className="text-accent-primary font-bold">
 {newValues[param as keyof typeof newValues]?.toFixed(3) ?? '-'}
 </span>
 </div>
 </div>
 ))}
 </div>
 </div>

 <div>
 <h4 className="text-sm font-medium text-text-secondary mb-1">Reason</h4>
 <p className="text-sm text-text-muted italic">
 "{reason}"
 </p>
 </div>

 {tuningMetrics && (
 <div className="border-t border-border-default pt-3">
 <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2">
 Tuning Metrics
 </h4>
 <div className="grid grid-cols-2 gap-4 text-sm">
 {tuningMetrics.ku !== undefined && (
 <div>
 <span className="text-text-muted block text-xs">Ultimate Gain (Ku)</span>
 <span className="font-mono text-text-default">{tuningMetrics.ku.toFixed(2)}</span>
 </div>
 )}
 {tuningMetrics.tu !== undefined && (
 <div>
 <span className="text-text-muted block text-xs">Ultimate Period (Tu)</span>
 <span className="font-mono text-text-default">{tuningMetrics.tu.toFixed(1)}s</span>
 </div>
 )}
 </div>
 </div>
 )}
 </div>

 <div className="mt-6">
 <button
 onClick={onClose}
 className="w-full bg-accent-primary hover:bg-accent-primary/80 text-text-default font-medium py-2 px-4 rounded-md transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-surface-primary"
 >
 Dismiss
 </button>
 </div>
 </div>
 </div>
 );
}
