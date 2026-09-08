import { useCallback, type ChangeEvent } from 'react'
import type { SeverityLevel } from '../presentation/severity'

export type FilterDimension = 'room' | 'category' | 'type' | 'severity'

export interface FilterState {
  severity: SeverityLevel | 'all'
  search: string
  rooms: readonly string[]
  categories: readonly string[]
  types: readonly string[]
}

interface EventFiltersProps {
  filters: FilterState
  onChange: (next: FilterState) => void
  rooms: readonly string[]
  categories: readonly string[]
  types: readonly string[]
}

const SEVERITY_OPTIONS: ReadonlyArray<{ value: SeverityLevel | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'warning', label: 'Warning' },
  { value: 'info', label: 'Info' },
]

function MultiSelectChip({
  label,
  selected,
  onClick,
}: {
  label: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`px-2 py-1 text-xs font-semibold border transition-colors ${
        selected
          ? 'bg-surface-tertiary text-text-default border-border-emphasis'
          : 'bg-surface-secondary text-text-default border-border-subtle hover:border-border-default'
      }`}
    >
      {label}
    </button>
  )
}

export function EventFilters({ filters, onChange, rooms, categories, types }: EventFiltersProps) {
  const handleSearchChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      onChange({ ...filters, search: event.target.value })
    },
    [filters, onChange],
  )

  const toggle = useCallback(
    (key: 'rooms' | 'categories' | 'types', value: string) => {
      const current = filters[key] as readonly string[]
      const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
      onChange({ ...filters, [key]: next })
    },
    [filters, onChange],
  )

  return (
    <div className="flex flex-col gap-2" role="toolbar" aria-label="Event filters">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1" role="group" aria-label="Severity filter">
          {SEVERITY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={filters.severity === option.value}
              onClick={() => onChange({ ...filters, severity: option.value })}
              className={`px-2 py-1 text-xs font-semibold border transition-colors ${
                filters.severity === option.value
                  ? 'bg-surface-tertiary text-text-default border-border-emphasis'
                  : 'bg-surface-secondary text-text-default border-border-subtle hover:text-text-default hover:border-border-default'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <input
          type="search"
          role="searchbox"
          aria-label="Filter events"
          placeholder="Filter events..."
          value={filters.search}
          onChange={handleSearchChange}
          className="flex-1 min-w-[120px] px-2 py-1 text-xs bg-surface-secondary border border-border-subtle text-text-default placeholder-text-secondary focus:outline-none focus:border-border-emphasis"
        />
      </div>

      {rooms.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Room filter">
          <span className="text-xs text-text-default font-semibold">Rooms:</span>
          {rooms.map((room) => (
            <MultiSelectChip
              key={room}
              label={room}
              selected={filters.rooms.includes(room)}
              onClick={() => toggle('rooms', room)}
            />
          ))}
        </div>
      )}

      {categories.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Category filter">
          <span className="text-xs text-text-default font-semibold">Categories:</span>
          {categories.map((category) => (
            <MultiSelectChip
              key={category}
              label={category}
              selected={filters.categories.includes(category)}
              onClick={() => toggle('categories', category)}
            />
          ))}
        </div>
      )}

      {types.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Event type filter">
          <span className="text-xs text-text-default font-semibold">Types:</span>
          {types.map((type) => (
            <MultiSelectChip
              key={type}
              label={type}
              selected={filters.types.includes(type)}
              onClick={() => toggle('types', type)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
