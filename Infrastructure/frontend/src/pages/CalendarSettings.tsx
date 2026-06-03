import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { apiClient } from '../services/api';

export default function CalendarSettings() {
  const [connection, setConnection] = useState<Record<string, unknown> | null>(null);
  const [caldavBaseUrl, setCaldavBaseUrl] = useState('');
  const [username, setUsername] = useState('');
  const [appPassword, setAppPassword] = useState('');
  const [targetCalendarUrl, setTargetCalendarUrl] = useState('');
  const [calendars, setCalendars] = useState<Array<{ name: string; url: string }>>([]);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    void apiClient.getCalendarSyncConnection().then(setConnection).catch(() => setConnection(null));
  }, []);

  const handleTest = async () => {
    try {
      const list = await apiClient.testCalendarSyncConnection({
        caldav_base_url: caldavBaseUrl,
        username,
        app_password: appPassword,
      });
      setCalendars(list);
      toast.success(`Found ${list.length} calendar(s)`);
    } catch {
      toast.error('Connection test failed');
    }
  };

  const handleSave = async () => {
    try {
      const row = await apiClient.saveCalendarSyncConnection({
        caldav_base_url: caldavBaseUrl,
        username,
        app_password: appPassword,
        target_calendar_url: targetCalendarUrl,
      });
      setConnection(row);
      toast.success('Nextcloud connected');
    } catch {
      toast.error('Failed to save connection');
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await apiClient.runCalendarSync();
      toast.success(`Sync: pushed ${result.pushed ?? 0}, deleted ${result.deleted ?? 0}`);
    } catch {
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="p-6 max-w-xl">
      <h1 className="text-2xl font-bold text-text-default mb-4">Calendar sync (Nextcloud)</h1>
      {connection && (
        <p className="text-sm text-text-secondary mb-4">
          Connected: {String(connection.display_name ?? connection.account_email ?? 'Nextcloud')}
          {connection.last_sync_at
            ? ` · Last sync ${String(connection.last_sync_at)}`
            : null}
        </p>
      )}
      <div className="space-y-3">
        <input
          placeholder="CalDAV base URL"
          className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={caldavBaseUrl}
          onChange={(e) => setCaldavBaseUrl(e.target.value)}
        />
        <input
          placeholder="Username"
          className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <input
          type="password"
          placeholder="App password"
          className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={appPassword}
          onChange={(e) => setAppPassword(e.target.value)}
        />
        <div className="flex gap-2">
          <button type="button" onClick={() => void handleTest()} className="px-3 py-1 rounded bg-surface-secondary text-text-default">
            Test
          </button>
        </div>
        {calendars.length > 0 && (
          <select
            className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
            value={targetCalendarUrl}
            onChange={(e) => setTargetCalendarUrl(e.target.value)}
          >
            <option value="">Select calendar</option>
            {calendars.map((c) => (
              <option key={c.url} value={c.url}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        <input
          placeholder="Or paste target calendar URL"
          className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={targetCalendarUrl}
          onChange={(e) => setTargetCalendarUrl(e.target.value)}
        />
        <div className="flex gap-2">
          <button type="button" onClick={() => void handleSave()} className="px-3 py-1 rounded bg-accent text-surface-base">
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
      </div>
    </div>
  );
}
