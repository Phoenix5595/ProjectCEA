import { useState } from 'react'
import ClimatePeriodsTable from '../../../components/ClimatePeriodsTable'
import { apiClient } from '../../../services/api'
import type { TimelineSavedBaseline } from '../state/timelineDraft'
import { useTimelineDraft } from '../state/useTimelineDraft'
import { ControlTimeline } from './ControlTimeline'

export type TimelineEditorProps = {
  readonly saved: TimelineSavedBaseline
  readonly lockedPhotoperiodHours?: number | null
}

export function TimelineEditor({ saved, lockedPhotoperiodHours = null }: TimelineEditorProps) {
  const [expanded, setExpanded] = useState(false)
  const controller = useTimelineDraft({ saved, publicationPort: apiClient })

  return (
    <div className="flex min-h-0 w-full min-w-0 flex-col gap-1">
      <div className="min-h-[300px] shrink-0 overflow-visible rounded-lg border border-border-subtle bg-surface-primary p-0 md:h-[300px] md:overflow-hidden">
        <ControlTimeline mode={expanded ? 'expanded' : 'compact'} controller={controller} onExpand={() => setExpanded(true)} lockedPhotoperiodHours={lockedPhotoperiodHours} />
      </div>
      <div className="min-h-0 overflow-auto rounded-lg border border-border-subtle bg-surface-primary p-1">
        <ClimatePeriodsTable periods={controller.state.draft.periods.map((period) => ({ ...period }))} onChange={controller.editPeriods} />
      </div>
    </div>
  )
}
