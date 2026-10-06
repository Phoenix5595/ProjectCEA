import { Flower2, FlaskConical, Settings, Sprout } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { FC } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { MODE_DISPLAY_NAMES, SUBMODE_DISPLAY_NAMES, type ModeProfileIdentity } from '../types/modes'

import { AppRibbon } from './chrome/AppRibbon'

export type Sector = 'laboratory' | 'vegetation' | 'flower' | 'devices'

export interface TopRibbonProps {
  sector: Sector
  activeTab: string
  onTabChange: (tab: string) => void
  roomName?: string
  showActions?: boolean
  onSave?: () => void
  saving?: boolean
  saveSuccess?: string | null
  saveError?: string | null
  /** Nullable post-commit save warning; shown without claiming rollback. */
  saveWarning?: string | null
  /** Actually running profile; chip background + 'Active' labels key on it. */
  activeProfile?: ModeProfileIdentity | null
  configuredProfile?: ModeProfileIdentity | null
  /** Profile selected for inspection; accent ring is independent of active fill. */
  selectedProfile?: ModeProfileIdentity | null
  /** Catalogue-backed identities in display order. */
  modeOptions?: readonly ModeProfileIdentity[]
  submodeOptions?: readonly ModeProfileIdentity[]
  /** Read-only selection change. */
  onSelectProfile?: (modeName: string, submodeName?: string) => void
  /** Save the selected profile, then activate it. */
  onActivateSelected?: () => void
  /** True while save+activation sequencing is in flight. */
  activationPending?: boolean
  /** True while selection/catalogue state is loading. */
  selectionLoading?: boolean
  /** False when activation must not run. */
  canActivate?: boolean
  canSave?: boolean
  activationLabel?: string
}

interface Tab {
  id: string
  label: string
  path: string
}

const sectorTabs: Record<Sector, Tab[]> = {
  laboratory: [{ id: 'overview', label: 'Overview', path: '/laboratory' }],
  vegetation: [
    { id: 'overview', label: 'Overview', path: '/vegetation' },
    { id: 'monitoring', label: 'Monitoring', path: '/vegetation/monitoring' },
    { id: 'control', label: 'Control', path: '/vegetation/control' },
    { id: 'automation', label: 'Automation', path: '/vegetation/automation' },
  ],
  flower: [
    { id: 'overview', label: 'Overview', path: '/flower' },
    { id: 'monitoring', label: 'Monitoring', path: '/flower/monitoring' },
    { id: 'soil', label: 'Soil', path: '/flower/soil' },
    { id: 'control', label: 'Control', path: '/flower/control' },
    { id: 'automation', label: 'Automation', path: '/flower/automation' },
  ],
  devices: [{ id: 'overview', label: 'Overview', path: '/devices' }],
}

const sectorIcons: Record<Sector, LucideIcon> = {
  laboratory: FlaskConical,
  vegetation: Sprout,
  flower: Flower2,
  devices: Settings,
}

const sectorDefaultNames: Record<Sector, string> = {
  laboratory: 'Laboratory',
  vegetation: 'Vegetation Room',
  flower: 'Flower Room',
  devices: 'Device Configuration',
}

function displayModeName(name: string, table: Record<string, string>): string {
  const key = name.toLowerCase()
  return table[key] ?? name.charAt(0).toUpperCase() + name.slice(1)
}

const sameProfile = (
  left: ModeProfileIdentity | null | undefined,
  right: ModeProfileIdentity | null | undefined
): boolean =>
  left != null && right != null &&
  left.modeId === right.modeId && left.submodeId === right.submodeId

export type RibbonChipState = 'selected-active' | 'selected' | 'active' | 'idle'

const chipState = (
  candidate: ModeProfileIdentity,
  parent: boolean,
  selected: TopRibbonProps['selectedProfile'],
  active: TopRibbonProps['activeProfile']
): RibbonChipState => {
  const matches = (profile: ModeProfileIdentity | null | undefined) =>
    profile != null && profile.modeId === candidate.modeId &&
    (parent || profile.submodeId === candidate.submodeId)
  const isSelected = matches(selected)
  const isActive = matches(active)
  if (isSelected && isActive) return 'selected-active'
  if (isSelected) return 'selected'
  if (isActive) return 'active'
  return 'idle'
}

const chipClass = (state: RibbonChipState): string => {
  switch (state) {
    case 'selected-active':
      // Actual running identity keeps the success fill; selection adds the ring.
      return 'bg-status-success-bg text-status-success-text border-status-success-border ring-2 ring-accent-vivid ring-offset-1 ring-offset-surface-base'
    case 'active':
      return 'bg-status-success-bg text-status-success-text border-status-success-border'
    case 'selected':
      // Selected-but-not-running uses only an accent ring, no fill.
      return 'bg-transparent text-text-default border-border-default ring-2 ring-accent-vivid ring-offset-1 ring-offset-surface-base'
    case 'idle':
      return 'bg-transparent text-text-default border-border-default hover:bg-surface-tertiary hover:border-border-emphasis'
  }
}


const TopRibbon: FC<TopRibbonProps> = ({
  sector,
  activeTab,
  onTabChange,
  roomName,
  showActions = false,
  onSave,
  saving = false,
  saveSuccess,
  saveError,
  saveWarning,
  activeProfile,
  configuredProfile,
  selectedProfile,
  modeOptions,
  submodeOptions,
  onSelectProfile,
  onActivateSelected,
  activationPending = false,
  selectionLoading = false,
  canActivate = false,
  canSave = false,
  activationLabel = 'Activate',
}) => {
  const location = useLocation()
  const tabs = sectorTabs[sector]

  const currentPath = location.pathname
  const activeTabFromPath = tabs.find(tab => {
    if (tab.id === 'overview') {
      return currentPath === tab.path
    }
    return currentPath.startsWith(tab.path)
  })

  const activeTabId = activeTabFromPath?.id || activeTab || 'overview'
  const isControlPage = currentPath.includes('/control')

  const displayRoomName = roomName || sectorDefaultNames[sector]
  const SectorIconComponent = sectorIcons[sector]

  const profilesReady = modeOptions != null && onSelectProfile != null
  const savePending = saving || activationPending
  const sameSelectedActiveKnown = sameProfile(selectedProfile, activeProfile)
  const saveFeedback = saveError || saveWarning || saveSuccess
  const compactActivationLabel = savePending ? 'Working…' :
    sameSelectedActiveKnown ? 'Active' :
      sameProfile(selectedProfile, configuredProfile) ? 'Active?' : 'Activate'

  return (
    <AppRibbon position="top" sticky className="pl-2 overflow-x-visible">
      <h1 className="flex max-w-none shrink-0 items-center gap-1 overflow-hidden whitespace-nowrap text-base font-bold text-text-default">
        <SectorIconComponent className="size-5 shrink-0" />
        <span className="inline">{displayRoomName}</span>
      </h1>

      <nav className="flex min-w-0 flex-1">
        <div className="flex min-w-max gap-0.5">
          {tabs.map(tab => {
            const isActive = activeTabId === tab.id
            return (
              <Link
                key={tab.id}
                to={tab.path}
                onClick={() => onTabChange(tab.id)}
                className={`
                  px-1.5 py-1 text-sm font-medium whitespace-nowrap
                  transition-colors duration-200 rounded-lg
                  ${
                    isActive
                      ? 'bg-accent-vivid text-accent-vivid-foreground'
                      : 'text-text-secondary hover:text-text-default hover:bg-surface-tertiary'
                  }
                `}
              >
                {tab.label}
              </Link>
            )
          })}
        </div>
      </nav>

      {showActions && isControlPage && (
        <div className="ml-auto flex shrink-0 items-center gap-1 pl-2">
          {profilesReady && (
            <div
              className="flex items-center gap-1 min-w-0"
              aria-busy={selectionLoading || savePending}
            >
              {modeOptions.map(candidate => (
                <button
                  key={candidate.modeId}
                  type="button"
                  aria-pressed={selectedProfile?.modeId === candidate.modeId}
                  data-profile-state={chipState(candidate, true, selectedProfile, activeProfile)}
                  aria-label={`Select ${displayModeName(candidate.modeName, MODE_DISPLAY_NAMES)} profile`}
                  disabled={savePending || selectionLoading}
                  onClick={() => onSelectProfile(candidate.modeName)}
                  className={`px-2 py-0.5 text-sm font-bold rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${chipClass(
                    chipState(candidate, true, selectedProfile, activeProfile)
                  )}`}
                >
                  {displayModeName(candidate.modeName, MODE_DISPLAY_NAMES)}
                </button>
              ))}
              {submodeOptions != null && submodeOptions.length > 0 && (
                <div className="flex gap-1 ml-1 border-l border-border-subtle pl-1">
                  {submodeOptions.map(candidate => {
                    const { submodeId, submodeName } = candidate
                    if (submodeId === null || submodeName === null) return null
                    return (
                      <button
                        key={submodeId}
                        type="button"
                        aria-pressed={sameProfile(selectedProfile, candidate)}
                        data-profile-state={chipState(candidate, false, selectedProfile, activeProfile)}
                        aria-label={`Select Flower ${displayModeName(submodeName, SUBMODE_DISPLAY_NAMES)} profile`}
                        disabled={savePending || selectionLoading}
                        onClick={() => onSelectProfile(candidate.modeName, submodeName)}
                        className={`px-2 py-0.5 text-xs font-bold rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${chipClass(
                          chipState(candidate, false, selectedProfile, activeProfile)
                        )}`}
                      >
                        {displayModeName(submodeName, SUBMODE_DISPLAY_NAMES)}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}
          {onActivateSelected != null && (
            <button
              type="button"
              data-testid="activate-selected"
              onClick={onActivateSelected}
              disabled={!canActivate || savePending}
              aria-busy={savePending}
              aria-label={activationLabel}
              title={configuredProfile && !sameProfile(activeProfile, configuredProfile)
                ? `${activationLabel} · Running status updating` : activationLabel}
              className={`px-2 py-0.5 text-xs font-bold rounded transition-colors ${
                sameSelectedActiveKnown
                  ? 'bg-status-success-bg text-status-success-text cursor-not-allowed'
                  : 'bg-accent-vivid hover:bg-accent-hover text-accent-vivid-foreground hover:text-accent-hover-foreground'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              {compactActivationLabel}
            </button>
          )}
          <button
            type="button"
            data-testid="save-profile"
            onClick={onSave}
            aria-label={saving ? 'Saving profile' : 'Save profile'}
            disabled={!canSave || savePending}
            className="relative px-2 py-0.5 bg-accent-vivid hover:bg-accent-hover text-accent-vivid-foreground hover:text-accent-hover-foreground text-xs font-bold rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save profile'}
            {saveFeedback && (
              <span role="status" title={saveFeedback}
                className={`absolute right-0.5 top-0.5 text-9 leading-none ${
                  saveError ? 'text-status-danger-text' : saveWarning ? 'text-status-warning-text' : 'text-status-success-text'
                }`}>
                <span aria-hidden="true">{saveError || saveWarning ? '!' : '✓'}</span>
                <span className="sr-only">{saveFeedback}</span>
              </span>
            )}
          </button>
        </div>
      )}
    </AppRibbon>
  )
}

export default TopRibbon
