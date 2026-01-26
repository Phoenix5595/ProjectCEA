import { useState, useEffect } from 'react'
import { logger } from '../utils/logger'

interface VerticalNotesBlockProps {
  location: string | null
  cluster: string | null
  currentMode?: string
}

export default function VerticalNotesBlock({ location, cluster, currentMode }: VerticalNotesBlockProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)

  // Get storage key based on location, cluster, and mode
  const getStorageKey = () => {
    const loc = location || 'unknown'
    const clust = cluster || 'unknown'
    const mode = currentMode || 'default'
    return `cea-notes-${loc}-${clust}-${mode}`
  }

  useEffect(() => {
    loadContent()
  }, [location, cluster, currentMode])

  async function loadContent() {
    setLoading(true)
    try {
      const storageKey = getStorageKey()
      const storedContent = localStorage.getItem(storageKey)
      if (storedContent) {
        setContent(storedContent)
      } else {
        setContent('')
      }
    } catch (err) {
      logger.error('Failed to load notes:', err)
    } finally {
      setLoading(false)
    }
  }

  function handleContentChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const newContent = e.target.value
    setContent(newContent)
    
    // Auto-save to localStorage
    try {
      const storageKey = getStorageKey()
      localStorage.setItem(storageKey, newContent)
    } catch (err) {
      logger.error('Failed to save notes:', err)
    }
  }

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">Notes</div>
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">
        Notes {currentMode && <span className="text-cyan-400">({currentMode})</span>}
      </div>
      
      <div className="space-y-2">
        <textarea
          value={content}
          onChange={handleContentChange}
          placeholder={`Add your notes for ${currentMode || 'this mode'} here...`}
          className="w-full bg-gray-800 border border-gray-700 rounded text-gray-200 text-sm px-3 py-2 resize-none min-h-[400px] focus:outline-none focus:border-cyan-500 transition-colors"
          style={{ fontFamily: 'monospace' }}
        />
        <div className="text-gray-500 text-xs text-center">
          Auto-saved for {currentMode || 'this mode'}
        </div>
      </div>
    </div>
  )
}
