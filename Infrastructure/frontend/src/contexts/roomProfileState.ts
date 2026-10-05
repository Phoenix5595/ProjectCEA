import type { components } from '../generated/api'
import type { FlowerSubmode, ModeProfileIdentity, RoomMode } from '../types/modes'

/** Active-mode read response shape from the generated API contract. */
export type ActiveModeResponse = components['schemas']['ActiveModeResponse']

/** Catalogue of selectable room profiles loaded once per room page mount. */
export type ModeCatalogue = {
  readonly modes: readonly RoomMode[]
  readonly submodes: readonly FlowerSubmode[]
  /** Mode catalogue failed; valid reads stay but choices are disabled. */
  readonly modesFailed: boolean
  /** Submode catalogue failed; valid reads stay but choices are disabled. */
  readonly submodesFailed: boolean
}

export const EMPTY_MODE_CATALOGUE: ModeCatalogue = {
  modes: [],
  submodes: [],
  modesFailed: false,
  submodesFailed: false,
}

const CANONICAL_MODE_ORDER = ['veg', 'flower', 'drying', 'sleep'] as const
const CANONICAL_SUBMODE_ORDER = ['stretch', 'bulk', 'ripen'] as const


/**
 * Resolve the selected profile identity from raw configured names plus the
 * catalogue. NULL submode resolves to the flower NULL/base profile; NULL IDs
 * never become an active identity through synthetic names.
 */
export function resolveSelectedProfileIdentity(
  modeName: string | null,
  submodeName: string | null | undefined,
  catalogue: ModeCatalogue
): ModeProfileIdentity | null {
  if (modeName == null || modeName.trim() === '') return null
  const mode = catalogue.modes.find(
    candidate => candidate.name.toLowerCase() === modeName.toLowerCase()
  )
  if (mode == null) return null
  if (submodeName && mode.name.toLowerCase() !== 'flower') return null
  if (submodeName == null || submodeName.trim() === '') {
    return {
      modeId: mode.id,
      submodeId: null,
      modeName: mode.name,
      submodeName: null,
    }
  }
  const submode = catalogue.submodes.find(
    candidate => candidate.name.toLowerCase() === submodeName.toLowerCase()
  )
  if (submode == null) return null
  return {
    modeId: mode.id,
    submodeId: submode.id,
    modeName: mode.name,
    submodeName: submode.name,
  }
}


/**
 * Chip options for the ribbon: canonical grow-mode order restricted to the
 * sector (Veg Room only ever shows its valid Veg option), with catalogue-
 * confirmed names taking precedence when the catalogue has loaded.
 */
export function modeOptionsFor(catalogue: ModeCatalogue, vegetationOnly: boolean): readonly ModeProfileIdentity[] {
  const canonical = CANONICAL_MODE_ORDER.flatMap(name =>
    catalogue.modes.filter(mode => mode.name.toLowerCase() === name)
  )
  const extras = catalogue.modes.filter(
    mode => !CANONICAL_MODE_ORDER.some(name => mode.name.toLowerCase() === name)
  )
  return [...canonical, ...extras]
    .filter(mode => !vegetationOnly || mode.name.toLowerCase() === 'veg')
    .map(mode => ({ modeId: mode.id, submodeId: null, modeName: mode.name, submodeName: null }))
}

/** Submode chip options for the currently relevant grow mode. */
export function submodeOptionsFor(
  catalogue: ModeCatalogue,
  modeName: string | null | undefined
): readonly ModeProfileIdentity[] {
  const flower = catalogue.modes.find(mode => mode.name.toLowerCase() === 'flower')
  if (modeName?.toLowerCase() !== 'flower' || !flower) return []
  const ordered = CANONICAL_SUBMODE_ORDER.flatMap(name =>
    catalogue.submodes.filter(mode => mode.name.toLowerCase() === name)
  )
  const extras = catalogue.submodes.filter(
    mode => !CANONICAL_SUBMODE_ORDER.some(name => mode.name.toLowerCase() === name)
  )
  return [
    { modeId: flower.id, submodeId: null, modeName: flower.name, submodeName: null },
    ...[...ordered, ...extras].map(submode => ({
      modeId: flower.id, submodeId: submode.id, modeName: flower.name, submodeName: submode.name,
    })),
  ]
}
