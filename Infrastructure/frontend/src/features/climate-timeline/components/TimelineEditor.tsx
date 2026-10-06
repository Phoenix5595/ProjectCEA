import { useState, type ReactNode } from 'react'

import type { TimelineWindow } from '../state/timelineDraft'
import type { TimelineDraftController } from '../state/useTimelineDraft'
import type { ActiveClimateProjectionState } from '../state/useActiveClimateProjection'

import { ControlTimeline } from './ControlTimeline'

export type TimelineEditorProps = {
  readonly controller: TimelineDraftController
  readonly selectedLabel?: string
  readonly activeLabel?: string | null
  readonly operationalProjection?: ActiveClimateProjectionState
  readonly profileDetails?: ReactNode
  readonly profileWarning?: boolean
  readonly constantMode?: boolean
  readonly editingEnabled?: boolean
  readonly lockedPhotoperiodHours?: number | null
  readonly forcedMoonPhase?: boolean
  /** Daily/rolling display-window change; invalidates review, keeps draft. */
  readonly onWindowChange?: (window: TimelineWindow) => void
}

export function TimelineEditor({
  controller,
  selectedLabel,
  activeLabel,
  operationalProjection,
  profileDetails,
  profileWarning,
  constantMode,
  editingEnabled,
  lockedPhotoperiodHours = null,
  forcedMoonPhase = false,
  onWindowChange,
}: TimelineEditorProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <ControlTimeline
      mode={expanded ? 'expanded' : 'compact'}
      controller={controller}
      selectedLabel={selectedLabel}
      activeLabel={activeLabel}
      operationalProjection={operationalProjection}
      profileDetails={profileDetails}
      profileWarning={profileWarning}
      constantMode={constantMode}
      editingEnabled={editingEnabled}
      onExpand={() => setExpanded(true)}
      onCollapse={() => setExpanded(false)}
      lockedPhotoperiodHours={lockedPhotoperiodHours}
      forcedMoonPhase={forcedMoonPhase}
      onWindowChange={onWindowChange}
    />
  )
}
