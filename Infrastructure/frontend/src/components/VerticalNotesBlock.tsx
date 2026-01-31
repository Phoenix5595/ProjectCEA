import { useState, useEffect, useRef, useCallback } from 'react'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'

interface VerticalNotesBlockProps {
  location: string | null
  cluster: string | null
  currentMode?: string
}

const SAVE_DEBOUNCE_MS = 600

export default function VerticalNotesBlock({ location, cluster, currentMode }: VerticalNotesBlockProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const getStorageKey = useCallback(() => {
    const loc = location || 'unknown'
    const clust = cluster || 'unknown'
    const mode = currentMode || 'default'
    return `cea-notes-${loc}-${clust}-${mode}`
  }, [location, cluster, currentMode])

  useEffect(() => {
    loadContent()
  }, [location, cluster, currentMode])

  async function loadContent() {
    const loc = location || 'unknown'
    const clust = cluster || 'unknown'
    const mode = currentMode || 'default'
    setLoading(true)
    try {
      const { content: apiContent } = await apiClient.getNotes(loc, clust, mode)
      if (apiContent != null && apiContent !== '') {
        setContent(apiContent)
        return
      }
      const storageKey = getStorageKey()
      const storedContent = localStorage.getItem(storageKey)
      if (storedContent) {
        setContent(storedContent)
      } else {
        setContent('')
      }
    } catch (err) {
      logger.error('Failed to load notes from API, using localStorage:', err)
      const storageKey = getStorageKey()
      const storedContent = localStorage.getItem(storageKey)
      setContent(storedContent ?? '')
    } finally {
      setLoading(false)
    }
  }

  async function persistContent(newContent: string) {
    const loc = location || 'unknown'
    const clust = cluster || 'unknown'
    const mode = currentMode || 'default'
    setSaving(true)
    try {
      await apiClient.saveNotes(loc, clust, mode, newContent)
      try {
        localStorage.setItem(getStorageKey(), newContent)
      } catch {
        // ignore localStorage errors
      }
    } catch (err) {
      logger.error('Failed to save notes to API:', err)
      try {
        localStorage.setItem(getStorageKey(), newContent)
      } catch (e) {
        logger.error('Failed to save notes to localStorage:', e)
      }
    } finally {
      setSaving(false)
    }
  }

  function handleContentChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const newContent = e.target.value
    setContent(newContent)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    saveTimeoutRef.current = setTimeout(() => {
      saveTimeoutRef.current = null
      persistContent(newContent)
    }, SAVE_DEBOUNCE_MS)
  }

  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    }
  }, [])

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
        <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">Notes</div>
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-2">
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
          {saving ? 'Saving...' : `Auto-saved for ${currentMode || 'this mode'}`}
        </div>
      </div>
    </div>
  )
}
