import { createContext, useContext, useState, type ReactNode } from 'react'

import type { ModeProfileIdentity } from '../types/modes'

/**
 * Page-registered control actions rendered by Layout's top ribbon.
 *
 * Grow-mode (profile) state lives here: the actually running profile
 * (`activeProfile`, background fill), the profile being inspected/edited
 * (`selectedProfile`, accent ring), catalogue-backed chip options, and the
 * selection/activation callbacks. Non-control sections register nothing
 * beyond the room label, so automation pages never expose grow-mode actions.
 */
export interface ControlActions {
  roomName?: string
  showActions?: boolean
  /** Existing save fields plus the nullable post-commit save warning. */
  saving?: boolean
  saveSuccess?: string | null
  saveError?: string | null
  saveWarning?: string | null
  /** Actually running profile; null when running identity is unknown. */
  activeProfile?: ModeProfileIdentity | null
  /** Committed database identity, not proof of a completed control tick. */
  configuredProfile?: ModeProfileIdentity | null
  /** Profile selected for inspection/editing; null when nothing is selected. */
  selectedProfile?: ModeProfileIdentity | null
  /** Catalogue-backed identities; labels never stand in for profile IDs. */
  modeOptions?: readonly ModeProfileIdentity[]
  submodeOptions?: readonly ModeProfileIdentity[]
  /** Read-only selection change; never mutates the running profile. */
  onSelectProfile?: (modeName: string, submodeName?: string) => void
  /** Save the selected profile, then activate it explicitly. */
  onActivateSelected?: () => void
  /** True while save+activation sequencing is in flight. */
  activationPending?: boolean
  /** True while selection/catalogue state is loading. */
  selectionLoading?: boolean
  /** False when activation must not run (pending edits, dirty/unready, conflict). */
  canActivate?: boolean
  canSave?: boolean
  activationLabel?: string
  onSave?: () => void
}

const ControlActionsContext = createContext<{
  actions: ControlActions
  setActions: (actions: ControlActions) => void
}>({
  actions: {},
  setActions: () => {},
})

export function ControlActionsProvider({ children }: { children: ReactNode }) {
  const [actions, setActions] = useState<ControlActions>({})
  return (
    <ControlActionsContext.Provider value={{ actions, setActions }}>
      {children}
    </ControlActionsContext.Provider>
  )
}

export function useControlActions() {
  return useContext(ControlActionsContext)
}
