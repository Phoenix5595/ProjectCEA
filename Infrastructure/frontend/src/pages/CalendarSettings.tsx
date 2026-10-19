import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { apiClient } from '../services/api'
import { extractErrorMessage } from '../utils/errors'
import type { CalendarConnection, CalendarMirrorPreview } from '../types/calendar'

export default function CalendarSettings() {
  const [connection, setConnection] = useState<CalendarConnection | null>(null)
  const [caldavBaseUrl, setCaldavBaseUrl] = useState('')
  const [username, setUsername] = useState('')
  const [appPassword, setAppPassword] = useState('')
  const [targetCalendarUrl, setTargetCalendarUrl] = useState('')
  const [calendars, setCalendars] = useState<Array<{ name: string; url: string }>>([])
  const [syncing, setSyncing] = useState(false)
  const [mirrorPreview, setMirrorPreview] = useState<CalendarMirrorPreview | null>(null)
  const [backupConfirmed, setBackupConfirmed] = useState(false)
  const [connectionValidated, setConnectionValidated] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [flowerCalendarTransitionsEnabled, setFlowerCalendarTransitionsEnabled] = useState(true)
  const [savingFlowerCalendarTransitions, setSavingFlowerCalendarTransitions] = useState(false)

  useEffect(() => {
    void apiClient
      .getCalendarSyncConnection()
      .then(row => {
        setConnection(row)
        if (row) {
          setCaldavBaseUrl(row.caldav_base_url)
          setUsername(row.account_email ?? '')
          setTargetCalendarUrl(row.target_calendar_url)
        }
      })
      .catch(error => toast.error(extractErrorMessage(error, 'Calendar settings unavailable')))
    void apiClient
      .getFlowerCalendarModeTransitions()
      .then(setting => setFlowerCalendarTransitionsEnabled(setting.enabled))
      .catch(error =>
        toast.error(extractErrorMessage(error, 'Failed to load Flower calendar control'))
      )
  }, [])

  const handleFlowerCalendarTransitionChange = async (enabled: boolean) => {
    setSavingFlowerCalendarTransitions(true)
    try {
      const setting = await apiClient.updateFlowerCalendarModeTransitions(enabled)
      setFlowerCalendarTransitionsEnabled(setting.enabled)
      toast.success(`Flower calendar mode transitions ${setting.enabled ? 'enabled' : 'disabled'}`)
    } catch (error) {
      toast.error(extractErrorMessage(error, 'Failed to save Flower calendar control'))
    } finally {
      setSavingFlowerCalendarTransitions(false)
    }
  }

  const handleTest = async () => {
    try {
      const list = await apiClient.testCalendarSyncConnection({
        caldav_base_url: caldavBaseUrl,
        username,
        app_password: appPassword,
      })
      setCalendars(list)
      setTargetCalendarUrl(list.find(calendar => calendar.name === 'Growing')?.url ?? '')
      setConnectionValidated(true)
      toast.success(`Found ${list.length} calendar(s)`)
    } catch {
      setConnectionValidated(false)
      toast.error('Connection test failed')
    }
  }

  const handleSave = async () => {
    if (!connectionValidated || !targetCalendarUrl) return
    try {
      const row = await apiClient.saveCalendarSyncConnection({
        caldav_base_url: caldavBaseUrl,
        username,
        app_password: appPassword,
        target_calendar_url: targetCalendarUrl,
      })
      setConnection(row)
      setMirrorPreview(null)
      setAppPassword('')
      setConnectionValidated(false)
      toast.success('Credentials saved securely—reconnect to mirror local events')
    } catch {
      toast.error('Failed to save connection')
    }
  }

  const handleMirrorPreview = async () => {
    if (!connection) return
    try {
      setMirrorPreview(await apiClient.previewCalendarReconnect(connection.id))
      setBackupConfirmed(false)
    } catch {
      toast.error('Unable to preview Calendar mirror')
    }
  }

  const handleReconnect = async () => {
    if (!mirrorPreview || !backupConfirmed) return
    setReconnecting(true)
    try {
      const result = await apiClient.reconnectCalendar(
        mirrorPreview.connection_id,
        mirrorPreview.generation
      )
      toast.success(`Mirror: ${result.sync.confirmed.length} confirmed, ${result.sync.pending.length} pending, ${result.sync.errors.length} errors`)
      setConnection(current => current ? { ...current, enabled: true, generation: result.generation } : current)
      setMirrorPreview(null)
    } catch {
      toast.error('Reconnect failed; local events remain saved')
    } finally {
      setReconnecting(false)
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    try {
      const result = await apiClient.runCalendarSync()
      toast.success(`Sync: pushed ${result.pushed ?? 0}, deleted ${result.deleted ?? 0}`)
    } catch {
      toast.error('Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="p-6 max-w-xl">
      <h1 className="text-2xl font-bold text-text-default mb-4">Calendar sync (Nextcloud)</h1>
      <div className="mb-4 flex items-center justify-between gap-3 text-sm text-text-default">
        <div>
          <p className="font-medium">Flower calendar mode transitions</p>
          <p className="text-text-secondary">
            Let the Flower grow calendar change the active mode.
          </p>
        </div>
        <label className="flex items-center gap-2">
          <span>{flowerCalendarTransitionsEnabled ? 'Enabled' : 'Disabled'}</span>
          <input
            aria-label="Flower calendar mode transitions"
            type="checkbox"
            checked={flowerCalendarTransitionsEnabled}
            disabled={savingFlowerCalendarTransitions}
            onChange={event => void handleFlowerCalendarTransitionChange(event.target.checked)}
          />
        </label>
      </div>
      {connection && (
        <p className="text-sm text-text-secondary mb-4">
          {connection.enabled ? 'Connected: ' : 'Saved—awaiting reconnect: '}{String(connection.display_name ?? connection.account_email ?? 'Nextcloud')}
          {connection.last_sync_at ? ` · Last sync ${String(connection.last_sync_at)}` : null}
        </p>
      )}
      <div className="space-y-3">
        <Input
          placeholder="CalDAV base URL"
          aria-label="CalDAV base URL"
          value={caldavBaseUrl}
          onChange={e => { setCaldavBaseUrl(e.target.value); setConnectionValidated(false) }}
        />
        <Input
          placeholder="Username"
          aria-label="Username"
          value={username}
          onChange={e => { setUsername(e.target.value); setConnectionValidated(false) }}
        />
        <Input
          type="password"
          placeholder="App password"
          aria-label="App password"
          value={appPassword}
          onChange={e => { setAppPassword(e.target.value); setConnectionValidated(false) }}
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleTest()}
            className="px-3 py-1 rounded bg-surface-secondary text-text-default"
          >
            Test
          </button>
        </div>
        {calendars.length > 0 && (
          <Select
            value={targetCalendarUrl || '__none__'}
            onValueChange={v => setTargetCalendarUrl(v === '__none__' ? '' : v)}
          >
            <SelectTrigger className="w-full" aria-label="Target calendar">
              <SelectValue placeholder="Select calendar" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">Select calendar</SelectItem>
              {calendars.map(c => (
                <SelectItem key={c.url} value={c.url}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Input
          placeholder="Or paste target calendar URL"
          aria-label="Target calendar URL"
          value={targetCalendarUrl}
          onChange={e => setTargetCalendarUrl(e.target.value)}
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={!connectionValidated || !targetCalendarUrl}
            className="px-3 py-1 rounded bg-accent-vivid text-accent-vivid-foreground disabled:opacity-50"
          >
            Save
          </button>
          <button
            type="button"
            disabled={syncing}
            onClick={() => void handleSync()}
            className="px-3 py-1 rounded bg-surface-secondary text-text-default"
          >
            Sync now
          </button>
        </div>
      {connection && (
        <div className="mt-3 space-y-2">
          <button
            type="button"
            disabled={reconnecting}
            onClick={() => void handleMirrorPreview()}
            className="px-3 py-1 rounded bg-surface-secondary text-text-default disabled:opacity-50"
          >
            Reconnect and mirror all local events
          </button>
          {mirrorPreview && (
            <div className="space-y-2 text-sm text-text-default">
              <p>Mirror {mirrorPreview.event_count} non-deleted local events into Growing.</p>
              <p>Back up the existing Calendar collection before reconnecting.</p>
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={backupConfirmed} onChange={event => setBackupConfirmed(event.target.checked)} />
                <span>I have backed up the existing Growing calendar.</span>
              </label>
              <button
                type="button"
                disabled={!backupConfirmed || reconnecting}
                onClick={() => void handleReconnect()}
                className="px-3 py-1 rounded bg-accent-vivid text-accent-vivid-foreground disabled:opacity-50"
              >
                {reconnecting ? 'Reconnecting…' : 'Confirm reconnect'}
              </button>
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  )
}
