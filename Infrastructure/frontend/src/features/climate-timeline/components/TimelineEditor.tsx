import { useState } from 'react'
import type { TimelineDraftController } from '../state/useTimelineDraft'
import { ControlTimeline } from './ControlTimeline'

export type TimelineEditorProps = {
  readonly controller: TimelineDraftController
  readonly lockedPhotoperiodHours?: number | null
}

export function TimelineEditor({ controller, lockedPhotoperiodHours = null }: TimelineEditorProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <ControlTimeline mode={expanded ? 'expanded' : 'compact'} controller={controller} onExpand={() => setExpanded(true)} lockedPhotoperiodHours={lockedPhotoperiodHours} />
  )
}
