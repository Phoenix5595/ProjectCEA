import { useState, useEffect } from 'react'
import { logger } from '../utils/logger'

interface Note {
  id: string
  content: string
  timestamp: string
  author: string
}

export default function VerticalNotesBlock() {
  const [notes, setNotes] = useState<Note[]>([])
  const [newNote, setNewNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadNotes()
  }, [])

  async function loadNotes() {
    setLoading(true)
    try {
      // For now, we'll use a simple approach - in a real implementation, 
      // you might want to add a notes API endpoint
      const storedNotes = localStorage.getItem('cea-notes')
      if (storedNotes) {
        setNotes(JSON.parse(storedNotes))
      }
    } catch (err) {
      logger.error('Failed to load notes:', err)
    } finally {
      setLoading(false)
    }
  }

  async function saveNotes(notesList: Note[]) {
    try {
      localStorage.setItem('cea-notes', JSON.stringify(notesList))
    } catch (err) {
      logger.error('Failed to save notes:', err)
    }
  }

  async function handleAddNote() {
    if (!newNote.trim()) return

    setSaving(true)
    try {
      const note: Note = {
        id: Date.now().toString(),
        content: newNote.trim(),
        timestamp: new Date().toISOString(),
        author: 'User' // In a real app, this would be the logged-in user
      }

      const updatedNotes = [note, ...notes]
      setNotes(updatedNotes)
      saveNotes(updatedNotes)
      setNewNote('')
    } catch (err) {
      logger.error('Failed to add note:', err)
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteNote(noteId: string) {
    try {
      const updatedNotes = notes.filter(note => note.id !== noteId)
      setNotes(updatedNotes)
      saveNotes(updatedNotes)
    } catch (err) {
      logger.error('Failed to delete note:', err)
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
      <div className="text-gray-400 uppercase font-bold tracking-wider text-[14px] mb-4">Notes</div>
      
      <div className="space-y-4">
        {/* Add New Note */}
        <div className="space-y-2">
          <textarea
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            placeholder="Add a new note..."
            className="w-full bg-gray-800 border border-gray-700 rounded text-gray-200 text-sm px-3 py-2 resize-none"
            rows={3}
          />
          <button
            onClick={handleAddNote}
            disabled={!newNote.trim() || saving}
            className="w-full px-4 py-2 bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-800 disabled:text-gray-600 rounded text-white text-xs font-bold tracking-wide transition-colors"
          >
            {saving ? 'Adding...' : 'Add Note'}
          </button>
        </div>

        {/* Notes List */}
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {notes.length === 0 ? (
            <div className="text-gray-500 text-sm text-center py-4">
              No notes yet. Add your first note above.
            </div>
          ) : (
            notes.map((note) => (
              <div
                key={note.id}
                className="bg-gray-800/50 border border-gray-700/50 rounded p-3 space-y-2"
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1">
                    <p className="text-gray-200 text-sm leading-relaxed">
                      {note.content}
                    </p>
                    <div className="flex items-center gap-2 mt-2">
                      <span className="text-gray-500 text-xs">
                        {new Date(note.timestamp).toLocaleDateString()}
                      </span>
                      <span className="text-gray-500 text-xs">
                        {new Date(note.timestamp).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit'
                        })}
                      </span>
                      <span className="text-gray-600 text-xs">
                        by {note.author}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteNote(note.id)}
                    className="text-gray-500 hover:text-red-400 transition-colors ml-2"
                    title="Delete note"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Note Count */}
        {notes.length > 0 && (
          <div className="text-gray-500 text-xs text-center pt-2 border-t border-gray-800">
            {notes.length} {notes.length === 1 ? 'note' : 'notes'}
          </div>
        )}
      </div>
    </div>
  )
}
