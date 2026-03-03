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
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-2">
 <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-4">Notes</div>
 <div className="text-text-subtle text-sm">Loading...</div>
 </div>
 )
 }

 return (
 <div className="bg-surface-primary rounded-lg border border-border-subtle p-2">
 <div className="text-text-muted uppercase font-bold tracking-wider text-[14px] mb-4">
 Notes {currentMode && <span className="text-accent-data">({currentMode})</span>}
 </div>
 
 <div className="space-y-2">
 <textarea
 value={content}
 onChange={handleContentChange}
 placeholder={`Add your notes for ${currentMode || 'this mode'} here...`}
 className="w-full bg-surface-secondary border border-border-default rounded-sm text-text-input text-sm px-3 py-2 resize-none min-h-[400px] focus:outline-hidden focus:border-accent-vivid transition-colors"
 style={{ fontFamily: 'monospace' }}
 />
 <div className="text-text-subtle text-xs text-center">
 {saving ? 'Saving...' : `Auto-saved for ${currentMode || 'this mode'}`}
 </div>
 </div>
 </div>
 )
}
