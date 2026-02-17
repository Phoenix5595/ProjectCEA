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
 return (
 <div className="w-full flex gap-1">
 {MODES.map((option) => {
 const isSelected = mode === option.id;
 return (
 <button
 key={option.id}
 onClick={() => onChange(option.id)}
 disabled={disabled}
 className={`
 flex-1 py-1 rounded border text-[10px] font-bold transition-all
 ${isSelected 
 ? 'border-accent-hover bg-accent-dim/40 text-accent-data shadow-xs' 
 : 'border-border-default bg-surface-secondary text-text-subtle hover:bg-surface-tertiary'
 }
 ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
 `}
 >
 {isSelected && <span className="mr-1">●</span>}
 {!isSelected && <span className="mr-1">○</span>}
 {option.label}
 </button>
 );
 })}
 </div>
 );
}
