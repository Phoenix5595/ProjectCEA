import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import ClimatePeriodsTable from '../components/ClimatePeriodsTable'
import RelayChannelMatrix from '../components/devices/RelayChannelMatrix'
import { buildRelayChannelViewModels } from '../components/devices/relayViewModel'
import type { RelayChannelViewModel } from '../components/devices/relayViewModel'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from '../components/ui/dialog'
import LightIntensity from '../components/LightIntensity'
import type { LightPendingState } from '../components/LightIntensity'
import ManualLightControl from '../components/ManualLightControl'
import PIDTuningPanel from '../components/PIDTuningPanel'
import VerticalNotesBlock from '../components/VerticalNotesBlock'
import {
  getLocationDisplayName,
  getLocationBackendName,
  getClusterDisplayName,
} from '../config/zones'
import { useControlActions } from '../contexts/ControlActionsContext'
import {
  EMPTY_MODE_CATALOGUE,
  modeOptionsFor,
  resolveSelectedProfileIdentity,
  submodeOptionsFor,
  type ModeCatalogue,
  type ActiveModeResponse,
} from '../contexts/roomProfileState'
import { createClimatePeriodsTableAdapter } from '../features/climate-timeline/adapters/climatePeriodsTableAdapter'
import type { TimelineSavedRequest } from '../features/climate-timeline/api/timeline'
import { TimelineEditor } from '../features/climate-timeline/components/TimelineEditor'
import { isCanonicalConstantMode } from '../features/climate-timeline/domain/modeClassifier'
import {
  isTimelineDraftEdited,
  isTimelineDraftPersisted,
  type TimelineOwnedValues,
  type TimelineWindow,
  type TimelineSavedBaseline,
} from '../features/climate-timeline/state/timelineDraft'
import {
  useTimelineDraft,
  type TimelineDraftController,
  type TimelineSaveResult,
} from '../features/climate-timeline/state/useTimelineDraft'
import { currentActiveProfileKey, useActiveClimateProjection } from '../features/climate-timeline/state/useActiveClimateProjection'
import { MonitoringApi } from '../features/monitoring/api/monitoringApi'
import { monitoringRequestContextFromSearchParams } from '../features/monitoring/api/client'
import { RelayPidTimeline } from '../features/relay-timeline/RelayPidTimeline'
import { useControlSnapshot } from '../hooks/useControlSnapshot'
import { apiClient } from '../services/api'
import { wsClient } from '../services/websocket'
import type { ClimatePeriod } from '../types/climatePeriod'
import type {
  ModeProfileIdentity,
  ModeActivationResponse,
} from '../types/modes'
import { extractErrorMessage } from '../utils/errors'
import { logger } from '../utils/logger'

export type ZoneConfigSection = 'control' | 'automation'

export interface ZoneConfigProps {
  location?: string
  cluster?: string
  section?: ZoneConfigSection
}

const EMPTY_TIMELINE_BASELINE: TimelineSavedBaseline = {
  room: { location: '', cluster: '' },
  baseConfigRevision: '',
  periods: [],
  photoperiod: {
    dayStartTime: '00:00',
    nightStartTime: '00:00',
    rampUpMinutes: 0,
    rampDownMinutes: 0,
  },
  parametersConfigured: false,
}


interface RawClimatePeriod {
  id?: number
  period_name: string
  start_time?: string | null
  end_time?: string | null
  ramp_minutes: number
  heating_setpoint?: number | null
  cooling_setpoint?: number | null
  vpd_setpoint?: number | null
  co2_setpoint?: number | null
  details?: string | null
}

function mapPeriodsFromApi(periods: RawClimatePeriod[]): ClimatePeriod[] {
  return periods.map(p => ({
    id: p.id,
    period_name: p.period_name,
    start_time: p.start_time ? p.start_time.substring(0, 5) : '00:00',
    end_time: p.end_time ? p.end_time.substring(0, 5) : '00:00',
    ramp_minutes: p.ramp_minutes,
    heating_setpoint: p.heating_setpoint ?? null,
    cooling_setpoint: p.cooling_setpoint ?? null,
    vpd_setpoint: p.vpd_setpoint != null ? Math.round(p.vpd_setpoint * 100) / 100 : null,
    co2_setpoint: p.co2_setpoint ?? null,
    details: p.details || '',
  }))
}

function createConstantPeriod(modeName: string): ClimatePeriod {
  return {
    period_name: `${modeName.charAt(0).toUpperCase()}${modeName.slice(1)} Constant`,
    start_time: '00:00',
    end_time: '00:00',
    ramp_minutes: 0,
    heating_setpoint: null,
    cooling_setpoint: null,
    vpd_setpoint: null,
    co2_setpoint: null,
    details: '24h constant setpoints',
  }
}

function sameProfile(left: ModeProfileIdentity | null, right: ModeProfileIdentity | null) {
  return left !== null && right !== null &&
    left.modeId === right.modeId && left.submodeId === right.submodeId
}

function profileLabel(profile: ModeProfileIdentity | null): string {
  if (!profile) return 'Unconfirmed'
  const mode = profile.modeName.charAt(0).toUpperCase() + profile.modeName.slice(1)
  if (profile.submodeName === null) return profile.modeName.toLowerCase() === 'flower' ? `${mode}/Base` : mode
  return `${mode}/${profile.submodeName.charAt(0).toUpperCase()}${profile.submodeName.slice(1)}`
}

function dailyWindow(): TimelineWindow {
  const now = new Date()
  const start = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return { start: new Date(start).toISOString(), end: new Date(start + 86_400_000).toISOString(), timezone: 'UTC' }
}

function profileFromRead(read: ActiveModeResponse | ModeActivationResponse): ModeProfileIdentity | null {
  const { mode_id: modeId, submode_id: submodeId } = read
  if (modeId == null || !Number.isSafeInteger(modeId) || modeId <= 0 || !read.mode_name) return null
  if (submodeId != null && (!Number.isSafeInteger(submodeId) || submodeId <= 0)) return null
  return { modeId, submodeId: submodeId ?? null, modeName: read.mode_name, submodeName: submodeId == null ? null : read.submode_name ?? `Submode ${submodeId}` }
}

export default function ZoneConfig({
  location: propsLocation,
  cluster: propsCluster,
  section = 'control',
}: ZoneConfigProps) {
  const { location: locationParam, cluster: urlCluster } = useParams<{ location: string; cluster: string }>()
  const { setActions } = useControlActions()
  const location = propsLocation || locationParam
    ? getLocationBackendName(propsLocation ?? locationParam ?? '')
    : null
  const cluster = propsCluster ?? urlCluster ?? 'main'
  const roomKey = `${location ?? ''}|${cluster}|${section}`
  const scopeRef = useRef(roomKey)
  scopeRef.current = roomKey
  const roomGeneration = useRef(0)
  const lastRegistryVersion = useRef<number | null>(null)
  const selectionEpoch = useRef(0)
  const selectedRef = useRef<ModeProfileIdentity | null>(null)
  const configuredRef = useRef<ModeProfileIdentity | null>(null)
  const operationRef = useRef<object | null>(null)
  const authorityFlight = useRef<{ generation: number; promise: Promise<void> } | null>(null)
  const refreshQueued = useRef(false)
  const profileRefreshPending = useRef(false)
  const profileFlight = useRef<{ key: string; promise: Promise<void> } | null>(null)
  const lightAuthorityWait = useRef<{
    target: ModeProfileIdentity
    generation: number
    settle(confirmed: boolean): void
  } | null>(null)
  const lightPendingRef = useRef<LightPendingState>({ modeId: null, devices: [] })
  const [catalogue, setCatalogue] = useState<ModeCatalogue>(EMPTY_MODE_CATALOGUE)
  const [catalogueLoading, setCatalogueLoading] = useState(section === 'control')
  const [configuredProfile, setConfiguredProfile] = useState<ModeProfileIdentity | null>(null)
  const [configuredReadable, setConfiguredReadable] = useState(false)
  const [selectedProfile, setSelectedProfile] = useState<ModeProfileIdentity | null>(null)
  const [metadataReadable, setMetadataReadable] = useState(false)
  const [selectionLoading, setSelectionLoading] = useState(false)
  const [fallbackPeriods, setFallbackPeriods] = useState<ClimatePeriod[]>([])
  const [authorityError, setAuthorityError] = useState<string | null>(null)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [activationPhase, setActivationPhase] = useState<'saving' | 'activating' | 'confirming' | null>(null)
  const [activationConfirmation, setActivationConfirmation] = useState<{
    target: ModeProfileIdentity
    configVersion: number
    warning: string | null
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [saveWarning, setSaveWarning] = useState<string | null>(null)
  const [lightPending, setLightPending] = useState<LightPendingState>({ modeId: null, devices: [] })
  lightPendingRef.current = lightPending
  const [pendingSelection, setPendingSelection] = useState<ModeProfileIdentity | null>(null)
  const lightIntensityRef = useRef<{
    savePendingChanges(): Promise<{ savedDevices: readonly string[]; failedDevices: readonly string[] }>
    discardPendingChanges(): void
  }>(null)

  const initializeDraftValues = useCallback((baseline: TimelineSavedBaseline): TimelineOwnedValues => {
    const selected = selectedRef.current
    if (
      baseline.baseConfigRevision && selected && selected.modeId === baseline.modeId &&
      selected.submodeId === (baseline.submodeId ?? null) &&
      isCanonicalConstantMode(selected.modeName) && baseline.periods.length === 0
    ) return { periods: [createConstantPeriod(selected.modeName)], photoperiod: baseline.photoperiod }
    return baseline
  }, [])
  const loadWindowBaseline = useCallback(async (request: TimelineSavedRequest) => {
    try { return await apiClient.getSaved(request) }
    catch { return apiClient.getConfiguration(request) }
  }, [])
  const timelineController = useTimelineDraft({
    saved: EMPTY_TIMELINE_BASELINE,
    publicationPort: apiClient,
    initializeDraftValues,
    loadWindowBaseline,
  })
  const controllerRef = useRef<TimelineDraftController>(timelineController)
  controllerRef.current = timelineController
  const { snapshot, registry, mcpConnected, loading: snapshotLoading, registryVersion } = useControlSnapshot()
  const monitoringApi = useMemo(
    () => new MonitoringApi(monitoringRequestContextFromSearchParams(new URLSearchParams(window.location.search))),
    [roomKey]
  )
  const projection = useActiveClimateProjection({
    location: section === 'control' ? location ?? '' : '',
    cluster,
    registryVersion,
    api: monitoringApi,
  })
  const projectionRefreshRef = useRef(projection.refresh)
  projectionRefreshRef.current = projection.refresh
  const [nowMs, setNowMs] = useState(Date.now())
  useEffect(() => {
    const interval = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])
  const activeKey = currentActiveProfileKey(registryVersion, projection.current, location ?? '', cluster)
  const runningActiveProfile = useMemo<ModeProfileIdentity | null>(() => {
    if (!activeKey) return null
    const mode = catalogue.modes.find(candidate => candidate.id === activeKey.modeId)
    const submode = catalogue.submodes.find(candidate => candidate.id === activeKey.submodeId)
    return {
      ...activeKey,
      modeName: mode?.name ?? `Mode ${activeKey.modeId}`,
      submodeName: activeKey.submodeId === null ? null :
        submode?.name ?? `Submode ${activeKey.submodeId}`,
    }
  }, [activeKey?.modeId, activeKey?.submodeId, catalogue])
  const liveRef = useRef({ runningActiveProfile, configuredProfile, configuredReadable })
  liveRef.current = { runningActiveProfile, configuredProfile, configuredReadable }
  const activationPending = activationPhase !== null
  const busy = saving || activationPending
  const saved = timelineController.state.saved
  const timelineReady = selectedProfile !== null &&
    saved.room.location === location && saved.room.cluster === cluster &&
    saved.modeId === selectedProfile.modeId && (saved.submodeId ?? null) === selectedProfile.submodeId &&
    saved.baseConfigRevision !== ''
  const selectedIsConstant = isCanonicalConstantMode(selectedProfile?.modeName ?? '')
  const activeModeName = catalogue.modes.find(mode => mode.id === activeKey?.modeId)?.name ??
    (sameProfile(runningActiveProfile, configuredProfile) ? configuredProfile?.modeName : null)
  const activeIsConstant = isCanonicalConstantMode(activeModeName ?? '')
  const liveIdentityConfirmed = configuredReadable && sameProfile(runningActiveProfile, configuredProfile)
  const waitForLiveIdentity = useCallback((target: ModeProfileIdentity): Promise<boolean> => {
    const live = liveRef.current
    if (live.configuredReadable && sameProfile(target, live.runningActiveProfile) &&
      sameProfile(target, live.configuredProfile)) return Promise.resolve(true)
    return new Promise(resolve => {
      const generation = roomGeneration.current
      const finish = (confirmed: boolean) => {
        window.clearTimeout(deadline)
        if (lightAuthorityWait.current?.settle === finish) lightAuthorityWait.current = null
        resolve(confirmed)
      }
      const deadline = window.setTimeout(() => finish(false), 5000)
      lightAuthorityWait.current = { target, generation, settle: finish }
      void projectionRefreshRef.current()
    })
  }, [])
  useEffect(() => {
    const waiting = lightAuthorityWait.current
    if (!waiting) return
    if (waiting.generation !== roomGeneration.current ||
      (configuredReadable && !sameProfile(waiting.target, configuredProfile))) {
      waiting.settle(false)
    } else if (liveIdentityConfirmed && sameProfile(waiting.target, runningActiveProfile)) {
      waiting.settle(true)
    }
  }, [configuredProfile, configuredReadable, liveIdentityConfirmed, runningActiveProfile])
  const crossModePending = lightPending.devices.length > 0 && selectedProfile !== null &&
    (selectedProfile.modeId !== configuredProfile?.modeId || lightPending.modeId !== configuredProfile?.modeId)
  const selectedIsActive = sameProfile(selectedProfile, runningActiveProfile)
  const selectedIsConfigured = sameProfile(selectedProfile, configuredProfile)
  const canSave = timelineReady && metadataReadable && !selectionLoading && !busy &&
    timelineController.state.status.kind !== 'conflict'
  const selectedCatalogueKnown = selectedProfile !== null &&
    catalogue.modes.some(mode => mode.id === selectedProfile.modeId) &&
    (selectedProfile.submodeId === null || catalogue.submodes.some(mode => mode.id === selectedProfile.submodeId))
  const canActivate = canSave && configuredReadable && !catalogueLoading &&
    !catalogue.modesFailed && !catalogue.submodesFailed && selectedCatalogueKnown && !crossModePending &&
    !selectedIsActive && !selectedIsConfigured
  const activationLabel = activationPhase === 'saving' ? 'Saving profile…' :
    activationPhase === 'activating' ? 'Activating…' :
      activationPhase === 'confirming' ? 'Checking running status…' :
        selectedIsActive ? 'Active' :
          selectedIsConfigured ? 'Active (unconfirmed)' :
            !isTimelineDraftPersisted(timelineController.state) ? 'Save & Activate' :
              `Activate ${profileLabel(selectedProfile)}`
  const activationConfirmed = activationConfirmation !== null && projection.current !== null &&
    liveIdentityConfirmed && sameProfile(runningActiveProfile, activationConfirmation.target) &&
    projection.current.version.config_version >= activationConfirmation.configVersion
  const chartProjection = activationConfirmation && !activationConfirmed ? {
    ...projection, current: null, future: [], trajectory: null,
    currentError: 'Running projection updating', projectionError: 'Running projection updating',
  } : projection
  useEffect(() => {
    if (!activationConfirmation || !activationConfirmed) return
    if (sameProfile(selectedRef.current, activationConfirmation.target)) {
      setSuccess('Activation confirmed')
      setSaveWarning(activationConfirmation.warning)
    }
    setActivationConfirmation(null)
  }, [activationConfirmation, activationConfirmed])
  const modeOptions = useMemo(
    () => modeOptionsFor(catalogue, location === 'Veg Room'),
    [catalogue, location]
  )
  const showFlowerSubmodes = location !== 'Veg Room' &&
    (selectedProfile?.modeName.toLowerCase() === 'flower' || runningActiveProfile?.modeName.toLowerCase() === 'flower')
  const submodeOptions = useMemo(
    () => showFlowerSubmodes ? submodeOptionsFor(catalogue, 'flower') : [],
    [catalogue, showFlowerSubmodes]
  )

  const loadSelectedProfile = useCallback((identity: ModeProfileIdentity, replace: boolean): Promise<void> => {
    if (!location || section !== 'control') return Promise.resolve()
    const generation = roomGeneration.current
    const epoch = selectionEpoch.current
    const controller = controllerRef.current
    const window = controller.state.saved.room.location === location &&
      controller.state.saved.room.cluster === cluster
      ? controller.state.saved.window ?? dailyWindow() : dailyWindow()
    const key = [generation, epoch, identity.modeId, identity.submodeId, window.start, window.end].join('|')
    if (profileFlight.current?.key === key) return profileFlight.current.promise
    const applicable = () => generation === roomGeneration.current && scopeRef.current === roomKey &&
      selectionEpoch.current === epoch && sameProfile(selectedRef.current, identity)
    const request: TimelineSavedRequest = {
      location, cluster, modeId: identity.modeId, submodeId: identity.submodeId, window,
    }
    if (replace) {
      setMetadataReadable(false)
      setFallbackPeriods([])
      controller.confirmRoomSwitch({
        ...EMPTY_TIMELINE_BASELINE, room: { location, cluster },
        modeId: identity.modeId, submodeId: identity.submodeId, window,
      })
    }
    setSelectionLoading(true)
    setProfileError(null)
    const promise = (async () => {
      let baseline: TimelineSavedBaseline | null = null
      try { baseline = await apiClient.getSaved(request) }
      catch (readError) {
        logger.warn('Selected trajectory unavailable; reading configuration aggregate', readError)
        if (!applicable()) return
        try { baseline = await apiClient.getConfiguration(request) }
        catch (configurationError) { logger.error('Selected profile metadata unavailable', configurationError) }
      }
      if (!applicable()) return
      if (baseline) {
        if (replace) controllerRef.current.confirmRoomSwitch(baseline)
        else controllerRef.current.reconcileSaved(baseline)
        setMetadataReadable(true)
        setFallbackPeriods([])
        return
      }
      setMetadataReadable(false)
      setProfileError('Profile metadata unavailable — Save and Activate disabled. Live controls remain available.')
      if (controllerRef.current.state.saved.baseConfigRevision !== '') return
      try {
        const periods = await apiClient.getClimatePeriods(location, cluster, identity.modeId, identity.submodeId ?? undefined)
        if (!applicable()) return
        const mapped = mapPeriodsFromApi(periods as unknown as RawClimatePeriod[])
        setFallbackPeriods(mapped.length === 0 && isCanonicalConstantMode(identity.modeName)
          ? [createConstantPeriod(identity.modeName)] : mapped)
      } catch (legacyError) { logger.error('Legacy period fallback unavailable', legacyError) }
    })().finally(() => {
      if (profileFlight.current?.key === key) profileFlight.current = null
      if (applicable()) setSelectionLoading(false)
    })
    profileFlight.current = { key, promise }
    return promise
  }, [cluster, location, roomKey, section])

  const refreshAuthority = useCallback((refreshSelected = false): Promise<void> => {
    if (!location || section !== 'control') return Promise.resolve()
    const generation = roomGeneration.current
    if (refreshSelected) refreshQueued.current = true
    if (authorityFlight.current?.generation === generation) return authorityFlight.current.promise
    const promise = (async () => {
      do {
        const refreshProfile = refreshQueued.current
        refreshQueued.current = false
        try {
          const read = await apiClient.getActiveRoomMode(location, cluster)
          if (generation !== roomGeneration.current || scopeRef.current !== roomKey) return
          const configured = profileFromRead(read)
          const changed = !sameProfile(configuredRef.current, configured)
          configuredRef.current = configured
          setConfiguredProfile(configured)
          setConfiguredReadable(configured !== null)
          setAuthorityError(configured ? null : 'Configured mode is unavailable')
          if ((changed || refreshProfile) && selectedRef.current) {
            if (operationRef.current !== null) profileRefreshPending.current = true
            else await loadSelectedProfile(selectedRef.current, false)
          }
        } catch (readError) {
          if (generation !== roomGeneration.current || scopeRef.current !== roomKey) return
          setConfiguredReadable(false)
          setAuthorityError('Configured mode could not be confirmed; activation and target editing are disabled.')
          logger.error('Configured mode read failed', readError)
        }
      } while (refreshQueued.current && generation === roomGeneration.current && scopeRef.current === roomKey)
    })().finally(() => {
      if (authorityFlight.current?.promise === promise) authorityFlight.current = null
    })
    authorityFlight.current = { generation, promise }
    return promise
  }, [cluster, loadSelectedProfile, location, roomKey, section])

  useEffect(() => {
    const generation = ++roomGeneration.current
    selectionEpoch.current += 1
    lastRegistryVersion.current = null
    selectedRef.current = null
    configuredRef.current = null
    operationRef.current = null
    profileFlight.current = null
    refreshQueued.current = false
    profileRefreshPending.current = false
    setSelectedProfile(null)
    setConfiguredProfile(null)
    lightAuthorityWait.current?.settle(false)
    setConfiguredReadable(false)
    setMetadataReadable(false)
    setFallbackPeriods([])
    setPendingSelection(null)
    setLightPending({ modeId: null, devices: [] })
    setSaving(false)
    setActivationPhase(null)
    setActivationConfirmation(null)
    setError(null)
    setSuccess(null)
    setSaveWarning(null)
    setAuthorityError(null)
    setProfileError(null)
    setCatalogue(EMPTY_MODE_CATALOGUE)
    controllerRef.current.confirmRoomSwitch(EMPTY_TIMELINE_BASELINE)
    if (!location || section !== 'control') {
      setCatalogueLoading(false)
      return
    }
    setCatalogueLoading(true)
    const applicable = () => generation === roomGeneration.current && scopeRef.current === roomKey
    const promise = (async () => {
      const [modes, submodes, active] = await Promise.allSettled([
        apiClient.getRoomModes(), apiClient.getFlowerSubmodes(), apiClient.getActiveRoomMode(location, cluster),
      ])
      if (!applicable()) return
      setCatalogue({
        modes: modes.status === 'fulfilled' ? modes.value : [],
        submodes: submodes.status === 'fulfilled' ? submodes.value : [],
        modesFailed: modes.status === 'rejected',
        submodesFailed: submodes.status === 'rejected',
      })
      setCatalogueLoading(false)
      if (active.status === 'fulfilled') {
        const configured = profileFromRead(active.value)
        configuredRef.current = configured
        setConfiguredProfile(configured)
        setConfiguredReadable(configured !== null)
        if (configured) {
          selectedRef.current = configured
          setSelectedProfile(configured)
          await loadSelectedProfile(configured, true)
        } else setAuthorityError('Configured mode is unavailable; choose a readable profile to inspect.')
      } else {
        setAuthorityError('Configured mode could not be confirmed; live safety controls remain available.')
        logger.error('Initial configured mode read failed', active.reason)
      }
    })().finally(() => {
      if (authorityFlight.current?.promise === promise) authorityFlight.current = null
      if (applicable() && refreshQueued.current) void refreshAuthority(true)
    })
    authorityFlight.current = { generation, promise }
    return () => {
      if (roomGeneration.current === generation) roomGeneration.current += 1
      lightAuthorityWait.current?.settle(false)
    }
  }, [cluster, loadSelectedProfile, location, refreshAuthority, roomKey, section])

  useEffect(() => {
    const previous = lastRegistryVersion.current
    lastRegistryVersion.current = registryVersion
    if (section !== 'control' || registryVersion == null || previous === registryVersion) return
    if (controllerRef.current.state.saved.baseConfigRevision) void refreshAuthority(true)
  }, [registryVersion, refreshAuthority, roomKey, section])
  useEffect(() => {
    if (section !== 'control') return
    const interval = setInterval(() => void refreshAuthority(), 5000)
    return () => clearInterval(interval)
  }, [refreshAuthority, section])
  useEffect(() => {
    if (section !== 'control') return
    wsClient.acquire()
    const refresh = () => {
      void refreshAuthority(true)
      void projectionRefreshRef.current()
    }
    const offs = [
      wsClient.on('mode_update', refresh),
      wsClient.on('room_schedule_update', refresh),
      wsClient.on('climate_schedule_update', refresh),
    ]
    return () => { offs.forEach(off => off()); wsClient.release() }
  }, [refreshAuthority, section])

  const inspectProfile = useCallback((identity: ModeProfileIdentity) => {
    selectionEpoch.current += 1
    selectedRef.current = identity
    setSelectedProfile(identity)
    setPendingSelection(null)
    setError(null)
    setSuccess(null)
    setSaveWarning(null)
    void loadSelectedProfile(identity, true)
  }, [loadSelectedProfile])
  const handleSelectProfile = useCallback((modeName: string, submodeName?: string) => {
    if (operationRef.current !== null || catalogueLoading) return
    const identity = resolveSelectedProfileIdentity(modeName, submodeName ?? null, catalogue)
    if (!identity || sameProfile(identity, selectedRef.current)) return
    if (isTimelineDraftEdited(controllerRef.current.state)) setPendingSelection(identity)
    else inspectProfile(identity)
  }, [catalogue, catalogueLoading, inspectProfile])

  const explainSaveFailure = useCallback((result: TimelineSaveResult) => {
    if (result.kind === 'conflict') setError('Configuration changed; your climate draft is preserved. Discard and reload before saving.')
    else if (result.kind === 'failed') setError(extractErrorMessage(result.error, 'Profile save failed'))
    else if (result.kind === 'superseded') setSaveWarning(result.committedBaseline
      ? 'Reviewed climate profile saved; newer edits remain unsaved. No activation was sent.'
      : 'Save superseded by newer edits or selection; no activation was sent.')
    else if (result.kind === 'busy') setSaveWarning('A climate save is already in progress; no activation was sent.')
  }, [])
  const handleSave = useCallback(async () => {
    if (!canSave || operationRef.current !== null || !selectedRef.current) return
    const owner = {}
    operationRef.current = owner
    const generation = roomGeneration.current
    const selected = selectedRef.current
    const before = liveRef.current
    const saveLiveTargets = lightPendingRef.current.devices.length > 0 &&
      before.configuredReadable && sameProfile(selected, before.configuredProfile) &&
      lightPendingRef.current.modeId === selected.modeId
    const applicable = () => generation === roomGeneration.current && scopeRef.current === roomKey &&
      operationRef.current === owner && sameProfile(selectedRef.current, selected)
    setSaving(true)
    setError(null)
    setSuccess(null)
    setSaveWarning(null)
    try {
      const result = await controllerRef.current.save()
      if (!applicable()) return
      if (result.kind !== 'saved' && result.kind !== 'unchanged') { explainSaveFailure(result); return }
      let warning = result.kind === 'saved' ? result.warning : null
      if (saveLiveTargets) {
        const confirmed = await waitForLiveIdentity(selected)
        if (!applicable()) return
        const lights = confirmed ? await lightIntensityRef.current?.savePendingChanges() : null
        if (!applicable()) return
        const failed = lights?.failedDevices ?? lightPendingRef.current.devices
        if (failed.length) warning = [
          warning, `Climate profile saved; light targets failed: ${failed.join(', ')}. ` +
            (confirmed ? 'Failed edits remain pending.' : 'Running identity could not be confirmed; edits remain pending without target writes.'),
        ].filter(Boolean).join('; ')
      }
      setSaveWarning(warning)
      setSuccess(warning ? null : 'Profile saved')
      if (result.kind === 'saved') void projectionRefreshRef.current()
    } catch (saveError) {
      if (applicable()) setError(extractErrorMessage(saveError, 'Profile save failed'))
    } finally {
      if (operationRef.current === owner) operationRef.current = null
      if (generation === roomGeneration.current && scopeRef.current === roomKey) {
        setSaving(false)
        if (refreshQueued.current || profileRefreshPending.current) {
          profileRefreshPending.current = false
          void refreshAuthority(true)
        }
      }
    }
  }, [canSave, explainSaveFailure, refreshAuthority, roomKey, waitForLiveIdentity])

  const handleActivateSelected = useCallback(async () => {
    if (!canActivate || operationRef.current !== null || !location || !selectedRef.current) return
    const owner = {}
    operationRef.current = owner
    const generation = roomGeneration.current
    const selected = selectedRef.current
    const applicable = () => generation === roomGeneration.current && scopeRef.current === roomKey &&
      operationRef.current === owner && sameProfile(selectedRef.current, selected)
    let savedBeforeActivation = false
    let warning: string | null = null
    setError(null)
    setSuccess(null)
    setSaveWarning(null)
    setActivationPhase(isTimelineDraftPersisted(controllerRef.current.state) ? 'activating' : 'saving')
    try {
      const result = await controllerRef.current.save()
      if (!applicable()) return
      if (result.kind !== 'saved' && result.kind !== 'unchanged') { explainSaveFailure(result); return }
      savedBeforeActivation = result.kind === 'saved'
      warning = result.kind === 'saved' ? result.warning : null
      setSaveWarning(warning)
      setActivationPhase('activating')
      const activated = await apiClient.setRoomMode(location, cluster, {
        mode_name: selected.modeName,
        submode_name: selected.submodeName ?? undefined,
        expected_config_revision: result.baseline.baseConfigRevision,
      })
      if (!applicable()) return
      const configured = profileFromRead(activated)
      configuredRef.current = configured
      setConfiguredProfile(configured)
      setConfiguredReadable(configured !== null)
      const confirmedWarning = [warning, activated.warning].filter(Boolean).join('; ') || null
      setActivationConfirmation({
        target: selected,
        configVersion: Number.parseInt(activated.config_revision, 16),
        warning: confirmedWarning,
      })
      warning = [confirmedWarning, activated.runtime_ready === false
        ? 'Profile configured, but runtime refresh failed; running status is unconfirmed.' : null,
      ].filter(Boolean).join('; ') || null
      setSaveWarning(warning)
      setSuccess(warning ? null : 'Profile configured; checking completed control tick')
      setActivationPhase('confirming')
      await Promise.allSettled([refreshAuthority(), projectionRefreshRef.current()])
    } catch (activationError) {
      if (!applicable()) return
      // A timeout may be post-commit. Read authority once; never retry a mutation.
      await Promise.allSettled([refreshAuthority(), projectionRefreshRef.current()])
      if (!applicable()) return
      const message = extractErrorMessage(activationError, 'Activation failed or could not be confirmed')
      setError(savedBeforeActivation ? `Climate profile saved; activation failed or is unconfirmed: ${message}` : message)
      setSaveWarning(warning)
    } finally {
      if (operationRef.current === owner) operationRef.current = null
      if (generation === roomGeneration.current && scopeRef.current === roomKey) {
        setActivationPhase(null)
        void refreshAuthority(true)
      }
    }
  }, [canActivate, cluster, explainSaveFailure, location, refreshAuthority, roomKey])

  const relayChannels: RelayChannelViewModel[] = useMemo(() => buildRelayChannelViewModels(snapshot), [snapshot])
  const [menuOpenChannel, setMenuOpenChannel] = useState<number | null>(null)
  const handleRelayMenuAction = useCallback(async (
    channel: number, action: 'auto' | 'timer-5m' | 'timer-10m' | 'timer-30m' | 'timer-1h' | 'off'
  ) => {
    const vm = relayChannels.find(candidate => candidate.channel === channel)
    if (!vm?.isAssigned || !vm.assignedDeviceName || !vm.location || !vm.cluster) return
    setMenuOpenChannel(null)
    try {
      if (action === 'auto' || action === 'off') {
        await apiClient.commandDevice(vm.location, vm.cluster, vm.assignedDeviceName, {
          action: action === 'auto' ? 'AUTO' : 'MANUAL_OFF',
          reason: action === 'auto' ? 'Room menu AUTO' : 'Room menu OFF',
        })
      } else {
        const durationSeconds = action === 'timer-5m' ? 300 : action === 'timer-10m' ? 600 :
          action === 'timer-30m' ? 1800 : 3600
        await apiClient.commandDevice(vm.location, vm.cluster, vm.assignedDeviceName, {
          action: 'TIMED_ON', duration_seconds: durationSeconds,
          reason: `Room menu ON ${durationSeconds / 60}m`,
        })
      }
    } catch (relayError) { logger.error(`Relay action failed for channel ${channel}`, relayError) }
  }, [relayChannels])

  useEffect(() => {
    const roomName = cluster === 'main' ? getLocationDisplayName(location ?? '') :
      `${getLocationDisplayName(location ?? '')} - ${getClusterDisplayName(location ?? '', cluster)}`
    setActions(section === 'control' ? {
      roomName, showActions: true, saving, saveSuccess: success, saveError: error ?? profileError ?? authorityError, saveWarning,
      activeProfile: runningActiveProfile, configuredProfile, selectedProfile, modeOptions, submodeOptions,
      onSelectProfile: handleSelectProfile, onActivateSelected: handleActivateSelected,
      activationPending, selectionLoading: selectionLoading || catalogueLoading,
      canActivate, canSave, activationLabel, onSave: handleSave,
    } : { roomName, showActions: false })
    return () => setActions({})
  }, [
    activationLabel, activationPending, canActivate, canSave, catalogueLoading, cluster, configuredProfile,
    authorityError, profileError,
    error, handleActivateSelected, handleSave, handleSelectProfile, location, modeOptions, runningActiveProfile,
    saveWarning, saving, section, selectedProfile, selectionLoading, setActions, submodeOptions, success,
  ])

  if (!location) return <div className="text-text-default">Invalid zone</div>
  if (section === 'automation') return (
    <div className="min-h-screen bg-surface-base p-1">
      <div className="max-w-full mx-auto flex min-h-0 flex-col gap-2">
        <RelayPidTimeline location={location} cluster={cluster} registry={registry}
          snapshot={snapshot} snapshotLoading={snapshotLoading} />
        <PIDTuningPanel location={location} cluster={cluster} devices={registry} />
      </div>
    </div>
  )
  const lockedPhotoperiod = selectedProfile?.modeName.toLowerCase() === 'flower' ? 12 :
    selectedProfile?.modeName.toLowerCase() === 'veg' ? 18 : null
  const profileDetails = (
    <div className="space-y-2">
      <p className="text-xs text-text-secondary" aria-live="polite">
        Running: {profileLabel(runningActiveProfile)} · Configured: {profileLabel(configuredProfile)}
        {!liveIdentityConfirmed ? ' — running unconfirmed' : ''} · Editing: {profileLabel(selectedProfile)}
      </p>
      <p className="text-xs text-text-secondary">Activation: {activationLabel}</p>
      {(authorityError || profileError || catalogue.modesFailed || catalogue.submodesFailed || error || saveWarning) && (
        <div className="text-xs text-status-warning-text" role="status">
          {authorityError && <p>{authorityError}</p>}
          {profileError && <p>{profileError}</p>}
          {(catalogue.modesFailed || catalogue.submodesFailed) && <p>Mode catalogue incomplete; unreadable choices and activation are disabled.</p>}
          {error && <p>{error}</p>}
          {saveWarning && <p>{saveWarning}</p>}
        </div>
      )}
      {timelineReady && metadataReadable && !saved.parametersConfigured && (
        <p className="text-xs text-status-warning-text" role="status">
          Unsaved initial parameters — Save profile creates this profile's parameter row.
        </p>
      )}
      {selectionLoading && <p className="text-xs text-text-muted">Loading selected profile…</p>}
      {crossModePending && <p className="text-xs text-status-warning-text" role="status">
        Unsaved live light edits belong to another mode. Save or discard them before activating this mode.
      </p>}
      {activeIsConstant && lightPending.devices.length > 0 && (
        <div className="space-y-1 text-xs text-status-warning-text">
          <p>Pending live light edits — mode {lightPending.modeId ?? 'unknown'}: {lightPending.devices.join(', ')}.</p>
          <button type="button" disabled={busy || !liveIdentityConfirmed || lightPending.modeId !== configuredProfile?.modeId}
            className="border border-border-default px-2 py-1 disabled:opacity-40"
            onClick={() => void lightIntensityRef.current?.savePendingChanges()}>Save light targets</button>{' '}
          <button type="button" disabled={busy} className="border border-border-default px-2 py-1 disabled:opacity-40"
            onClick={() => lightIntensityRef.current?.discardPendingChanges()}>Discard light edits</button>
        </div>
      )}
      {timelineController.state.status.kind === 'conflict' && selectedProfile && (
        <div className="text-xs text-status-warning-text" role="status">
          Configuration changed; your draft is preserved.{' '}
          <button type="button" disabled={busy || selectionLoading}
            className="underline" onClick={() => void loadSelectedProfile(selectedProfile, true)}>
            Discard edits &amp; reload saved profile
          </button>
        </div>
      )}
    </div>
  )
  return (
    <div className="min-h-screen bg-surface-base p-1">
      <div className="max-w-full mx-auto h-[calc(100vh-1rem)] min-w-0 flex flex-col gap-1 min-h-0">
        {selectedProfile && (
          <div className="w-full min-w-0 shrink-0">
            <TimelineEditor controller={timelineController} lockedPhotoperiodHours={lockedPhotoperiod}
              forcedMoonPhase={selectedIsConstant} constantMode={selectedIsConstant}
              editingEnabled={timelineReady && !busy}
              selectedLabel={profileLabel(selectedProfile)} activeLabel={sameProfile(runningActiveProfile, selectedProfile)
                ? profileLabel(selectedProfile) : runningActiveProfile ? profileLabel(runningActiveProfile) : null}
              operationalProjection={chartProjection} onWindowChange={timelineController.setWindow}
              profileDetails={profileDetails}
              profileWarning={Boolean(authorityError || profileError || error || saveWarning || crossModePending ||
                catalogue.modesFailed || catalogue.submodesFailed || !saved.parametersConfigured ||
                timelineController.state.status.kind === 'conflict')} />
          </div>
        )}
        <div className="flex gap-1">
          <div className="flex-1 min-w-0 flex flex-col gap-1 h-full">
            <fieldset disabled={busy} className="bg-surface-primary rounded-lg border border-border-subtle p-1 flex-[56] min-h-[112px]">
              {timelineReady
                ? <ClimatePeriodsTable {...createClimatePeriodsTableAdapter(timelineController.state, timelineController.editPeriods)} constantMode={selectedIsConstant} />
                : <ClimatePeriodsTable periods={fallbackPeriods} onChange={setFallbackPeriods} constantMode={selectedIsConstant} />}
            </fieldset>
            <div className="flex-[44] flex min-h-0 flex-col" aria-label="Live light controls">
              <div className={activeIsConstant ? 'hidden' : 'contents'}>
                <LightIntensity ref={lightIntensityRef} location={location} cluster={cluster} compact
                  activeModeId={configuredProfile?.modeId ?? null} onPendingChange={setLightPending}
                  hiddenNormalControls={activeIsConstant} targetEditingEnabled={liveIdentityConfirmed && !activationPending} />
              </div>
              {activeIsConstant && (
                <ManualLightControl location={location} cluster={cluster} compact />
              )}
            </div>
          </div>
          <div className="shrink-0 min-w-0">
            {!mcpConnected && <div className="mb-1 rounded-sm border border-status-danger-border/80 bg-status-danger-bg/30 px-2 py-1 text-10 font-semibold text-status-danger-text">MCP23017 disconnected</div>}
            <RelayChannelMatrix channels={relayChannels} nowMs={nowMs} variant="compact" location={location}
              menuOpenChannel={menuOpenChannel} onToggleMenu={channel => setMenuOpenChannel(previous => previous === channel ? null : channel)}
              onMenuAction={handleRelayMenuAction} />
          </div>
        </div>
        <div className="flex-1 min-h-[160px]">
          <VerticalNotesBlock location={location} cluster={cluster} currentMode={runningActiveProfile?.modeName ?? configuredProfile?.modeName} />
        </div>
      </div>
      <Dialog open={pendingSelection !== null} onOpenChange={open => { if (!open) setPendingSelection(null) }}>
        <DialogContent>
          <DialogTitle>Discard unsaved climate edits?</DialogTitle>
          <DialogDescription>Inspecting another profile discards these unsaved edits. The running mode does not change.</DialogDescription>
          <DialogFooter>
            <button type="button" data-testid="dialog-cancel" className="rounded border border-border-default px-3 py-1.5 text-sm"
              onClick={() => setPendingSelection(null)}>Cancel</button>
            <button type="button" data-testid="dialog-discard"
              className="rounded border border-status-danger-border bg-status-danger-bg px-3 py-1.5 text-sm text-status-danger-text"
              onClick={() => { if (pendingSelection) inspectProfile(pendingSelection) }}>
              Discard edits &amp; inspect
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
