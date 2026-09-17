import { useState } from 'react'
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
    <ControlTimeline mode={expanded ? 'expanded' : 'compact'} controller={controller} onExpand={() => setExpanded(true)} lockedPhotoperiodHours={lockedPhotoperiodHours} />
  )
}
