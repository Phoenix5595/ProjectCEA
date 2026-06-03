import { useEffect, useState } from 'react';

import GrowCalendar from './GrowCalendar';
import FlowerGrowWizard from './FlowerGrowWizard';
import { useCalendarEvents } from '../../hooks/useCalendarEvents';
import { apiClient } from '../../services/api';
import type { ModeScheduleResponse } from '../../types/calendar';

interface CalendarOverviewPageProps {
  location: string;
  cluster?: string;
}

export default function CalendarOverviewPage({
  location,
  cluster = 'main',
}: CalendarOverviewPageProps) {
  const { events, loading, refresh } = useCalendarEvents(location);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [modeSchedule, setModeSchedule] = useState<ModeScheduleResponse | null>(null);

  useEffect(() => {
    if (location === 'Flower Room') {
      void apiClient.getModeSchedule(location, cluster).then(setModeSchedule).catch(() => setModeSchedule(null));
    }
  }, [location, cluster, events]);

  const mismatch =
    modeSchedule &&
    modeSchedule.expected.mode_name &&
    (modeSchedule.active.mode_name !== modeSchedule.expected.mode_name ||
      (modeSchedule.expected.submode_name &&
        modeSchedule.active.submode_name !== modeSchedule.expected.submode_name));

  return (
    <div className="p-4 flex flex-col gap-4 min-h-screen bg-surface-base">
      {mismatch && (
        <div
          role="alert"
          className="rounded-lg border border-amber-500/60 bg-amber-500/15 px-3 py-2 text-sm text-amber-100"
        >
          Scheduled: {modeSchedule.expected.mode_name}
          {modeSchedule.expected.submode_name ? ` / ${modeSchedule.expected.submode_name}` : ''} (
          {modeSchedule.expected.title}) — Active: {modeSchedule.active.mode_name ?? '—'}
          {modeSchedule.active.submode_name ? ` / ${modeSchedule.active.submode_name}` : ''}
        </div>
      )}
      <GrowCalendar
        variant="full"
        viewMode="room"
        location={location}
        events={events}
        loading={loading}
        onRefresh={refresh}
        showAddTask={location === 'Flower Room'}
        onAddTask={() => setWizardOpen(true)}
      />
      {location === 'Flower Room' && (
        <FlowerGrowWizard
          open={wizardOpen}
          onClose={() => setWizardOpen(false)}
          onCreated={refresh}
        />
      )}
    </div>
  );
}
