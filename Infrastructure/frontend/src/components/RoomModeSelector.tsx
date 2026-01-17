import { useState, useEffect } from 'react'
import { apiClient } from '../services/api'
import type { RoomMode, FlowerSubmode, RoomModeWithParams } from '../types/modes'
import { MODE_DISPLAY_NAMES, SUBMODE_DISPLAY_NAMES, MODE_COLORS, SUBMODE_COLORS } from '../types/modes'

interface RoomModeSelectorProps {
  currentMode: RoomModeWithParams | null
  onModeChange: (mode: string, submode?: string) => void
}

export default function RoomModeSelector({
  currentMode,
  onModeChange
}: RoomModeSelectorProps) {
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

  const currentModeName = currentMode?.mode_name || 'veg'
  const currentSubmodeName = currentMode?.submode_name
  const displayName = currentSubmodeName 
    ? `${MODE_DISPLAY_NAMES[currentModeName] || currentModeName} / ${SUBMODE_DISPLAY_NAMES[currentSubmodeName] || currentSubmodeName}`
    : MODE_DISPLAY_NAMES[currentModeName] || currentModeName

  const bgColor = currentSubmodeName 
    ? SUBMODE_COLORS[currentSubmodeName] || MODE_COLORS[currentModeName] || 'bg-gray-600'
    : MODE_COLORS[currentModeName] || 'bg-gray-600'

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={loading}
        className={`${bgColor} px-3 py-1 rounded text-white text-xs font-bold uppercase tracking-wider flex items-center gap-2 hover:opacity-90 transition-opacity disabled:opacity-50`}
      >
        {loading ? '...' : displayName}
        <svg className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute top-full mt-1 right-0 bg-gray-900 border border-gray-700 rounded-lg shadow-xl z-50 min-w-[160px] overflow-hidden">
          {modes.map(mode => (
            <div key={mode.id}>
              {mode.name === 'flower' ? (
                <div>
                  <div className="px-3 py-1.5 text-[10px] text-gray-500 uppercase font-bold border-b border-gray-800">
                    {MODE_DISPLAY_NAMES[mode.name] || mode.name}
                  </div>
                  {submodes.map(sub => (
                    <button
                      key={sub.id}
                      onClick={() => handleModeSelect(mode.name, sub.name)}
                      className={`w-full px-3 py-2 text-left text-xs hover:bg-gray-800 flex items-center gap-2 ${
                        currentModeName === mode.name && currentSubmodeName === sub.name ? 'bg-gray-800 text-white' : 'text-gray-300'
                      }`}
                    >
                      <span className={`w-2 h-2 rounded-full ${SUBMODE_COLORS[sub.name] || 'bg-pink-600'}`} />
                      {SUBMODE_DISPLAY_NAMES[sub.name] || sub.name}
                      {sub.week_start && sub.week_end && (
                        <span className="text-gray-500 ml-auto">W{sub.week_start}-{sub.week_end}</span>
                      )}
                    </button>
                  ))}
                </div>
              ) : (
                <button
                  onClick={() => handleModeSelect(mode.name)}
                  className={`w-full px-3 py-2 text-left text-xs hover:bg-gray-800 flex items-center gap-2 ${
                    currentModeName === mode.name && !currentSubmodeName ? 'bg-gray-800 text-white' : 'text-gray-300'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full ${MODE_COLORS[mode.name] || 'bg-gray-600'}`} />
                  {MODE_DISPLAY_NAMES[mode.name] || mode.name}
                  {mode.is_constant && <span className="text-gray-500 ml-auto">24h</span>}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
