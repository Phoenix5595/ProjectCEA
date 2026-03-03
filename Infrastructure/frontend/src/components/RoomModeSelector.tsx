import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import type { RoomMode, FlowerSubmode, RoomModeWithParams } from '../types/modes'
import { MODE_DISPLAY_NAMES, SUBMODE_DISPLAY_NAMES, MODE_COLORS, SUBMODE_COLORS } from '../types/modes'

interface RoomModeSelectorProps {
 currentMode: RoomModeWithParams | null
 onModeChange: (mode: string, submode?: string) => void
 location?: string // Add location prop to disable mode changes for Veg
}

const SUBMODE_ABBREV: Record<string, string> = {
 'stretch': 'STR',
 'bulk': 'BLK',
 'ripen': 'RPN'
}

export default function RoomModeSelector({
 currentMode,
 onModeChange,
 location
}: RoomModeSelectorProps) {
 const isVegRoom = location === 'Veg Room'
 const [modes, setModes] = useState<RoomMode[]>([])
 const [submodes, setSubmodes] = useState<FlowerSubmode[]>([])
 const [isOpen, setIsOpen] = useState(false)
 const [loading, setLoading] = useState(false)

 useEffect(() => {
 loadModes()
 }, [])

 async function loadModes() {
 try {
 const [modesData, submodesData] = await Promise.all([
 apiClient.getRoomModes(),
 apiClient.getFlowerSubmodes()
 ])
 setModes(modesData)
 setSubmodes(submodesData)
 } catch (err) {
 console.error('Failed to load modes:', err)
 }
 }

 async function handleModeSelect(modeName: string, submodeName?: string) {
 setLoading(true)
 try {
 await onModeChange(modeName, submodeName)
 setIsOpen(false)
 } finally {
 setLoading(false)
 }
 }

 async function handleSubmodeToggle(submodeName: string) {
 setLoading(true)
 try {
 await onModeChange('flower', submodeName)
 } finally {
 setLoading(false)
 }
 }

 const currentModeName = currentMode?.mode_name || 'veg'
 const currentSubmodeName = currentMode?.submode_name
 const isFlowerMode = currentModeName === 'flower'

 const displayName = MODE_DISPLAY_NAMES[currentModeName] || currentModeName
 const bgColor = MODE_COLORS[currentModeName] || 'bg-surface-tertiary'

 return (
 <div className="flex items-center gap-1">
 <div className="relative">
 <button
 onClick={() => !isVegRoom && setIsOpen(!isOpen)}
 disabled={loading || isVegRoom}
 className={`${bgColor} px-3 py-1 rounded-sm text-text-default text-xs font-bold uppercase tracking-wider flex items-center gap-2 hover:opacity-90 transition-opacity disabled:opacity-50 ${isVegRoom ? 'cursor-not-allowed' : ''}`}
 title={isVegRoom ? 'Veg room is locked to veg mode' : 'Change mode'}
 >
 {loading ? '...' : displayName}
 <svg className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
 </svg>
 </button>

 {isOpen && (
 <div className="absolute top-full mt-1 right-0 bg-surface-primary border border-border-default rounded-lg shadow-xl z-50 min-w-[160px] overflow-hidden">
 {modes.map(mode => (
 <button
 key={mode.id}
 onClick={() => handleModeSelect(mode.name, mode.name === 'flower' ? submodes[0]?.name : undefined)}
 className={`w-full px-3 py-2 text-left text-xs hover:bg-surface-secondary flex items-center gap-2 ${
 currentModeName === mode.name ? 'bg-surface-secondary text-text-default' : 'text-text-secondary'
 }`}
 >
 <span className={`w-2 h-2 rounded-full ${MODE_COLORS[mode.name] || 'bg-surface-tertiary'}`} />
 {MODE_DISPLAY_NAMES[mode.name] || mode.name}
 {mode.is_constant && <span className="text-text-subtle ml-auto">24h</span>}
 </button>
 ))}
 </div>
 )}
 </div>

 {isFlowerMode && submodes.length > 0 && (
 <div className="flex gap-0.5">
 {submodes.map(sub => (
 <button
 key={sub.id}
 onClick={() => handleSubmodeToggle(sub.name)}
 disabled={loading}
 title={`${SUBMODE_DISPLAY_NAMES[sub.name] || sub.name}${sub.week_start && sub.week_end ? ` (W${sub.week_start}-${sub.week_end})` : ''}`}
 className={`px-2 py-1 text-[10px] font-bold uppercase rounded transition-colors disabled:opacity-50 ${
 currentSubmodeName === sub.name
 ? `${SUBMODE_COLORS[sub.name] || 'bg-mode-submode'} text-text-default`
 : 'bg-surface-secondary text-text-muted hover:bg-surface-tertiary'
 }`}
 >
 {SUBMODE_ABBREV[sub.name] || sub.name.slice(0, 3).toUpperCase()}
 </button>
 ))}
 </div>
 )}
 </div>
 )
}
