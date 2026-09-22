import { useCallback, type ChangeEvent } from 'react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import type { EventLogView } from './EventGroupedView'

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
  view: EventLogView
  onViewChange: (view: EventLogView) => void
  /**
   * Sidebar mode: only the primary rooms render as buttons; every other
   * control (view, severity, search, extra rooms, categories, types) folds
   * into a single Filters submenu.
   */
  compact?: boolean
  /** Rooms always shown as chips in compact mode (e.g. Flower Room, Veg Room). */
  primaryRooms?: readonly string[]
  /** Move the compact Filters trigger into the Event Log heading. */
  hideCompactTrigger?: boolean
}

const SEVERITY_OPTIONS: ReadonlyArray<{ value: SeverityLevel | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  ...(
    Object.entries(SEVERITY_LABELS) as ReadonlyArray<[SeverityLevel, string]>
  ).map(([value, label]) => ({ value, label })),
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

const VIEW_OPTIONS: ReadonlyArray<{ value: EventLogView; label: string }> = [
  { value: 'grouped', label: 'Alerts' },
  { value: 'flat', label: 'All events' },
]

function toggleDimension(
  filters: FilterState,
  onChange: (next: FilterState) => void,
  key: 'rooms' | 'categories' | 'types',
  value: string,
) {
  const current = filters[key]
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
  onChange({ ...filters, [key]: next })
}

interface FilterCheckboxListProps {
  label: string
  heading: string
  options: readonly string[]
  selected: readonly string[]
  onToggle: (value: string) => void
}

function FilterCheckboxList({
  label,
  heading,
  options,
  selected,
  onToggle,
}: FilterCheckboxListProps) {
  if (options.length === 0) return null
  return (
    <div className="flex flex-col gap-1" role="group" aria-label={label}>
      <p className="text-11 font-bold uppercase tracking-wide text-text-secondary">{heading}</p>
      {options.map((option) => (
        <label
          key={option}
          className="flex items-center gap-2 px-1.5 py-0.5 text-xs text-text-default rounded-sm hover:bg-surface-secondary cursor-pointer"
        >
          <input
            type="checkbox"
            checked={selected.includes(option)}
            onChange={() => onToggle(option)}
            className="size-3.5 accent-[var(--accent)]"
          />
          <span className="break-all">{option}</span>
        </label>
      ))}
    </div>
  )
}

interface FiltersMenuProps {
  filters: FilterState
  onChange: (next: FilterState) => void
  rooms: readonly string[]
  categories: readonly string[]
  types: readonly string[]
  view: EventLogView
  onViewChange: (view: EventLogView) => void
  /** Label of the trigger button; count reflects every active dimension. */
  triggerLabel: string
}

/** One submenu holding every non-room filter control. */
function FiltersMenu({
  filters,
  rooms,
  categories,
  types,
  view,
  onViewChange,
  onChange,
  triggerLabel,
}: FiltersMenuProps) {
  const handleSearchChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      onChange({ ...filters, search: event.target.value })
    },
    [filters, onChange],
  )

  const activeCount =
    filters.categories.length +
    filters.types.length +
    filters.rooms.length +
    (filters.severity !== 'all' ? 1 : 0) +
    (filters.search.trim() ? 1 : 0)

  return (
    <Popover>
      <PopoverTrigger
        type="button"
        className={`px-2 py-1 text-xs font-semibold border transition-colors whitespace-nowrap ${
          activeCount > 0
            ? 'bg-surface-tertiary text-text-default border-border-emphasis'
            : 'bg-surface-secondary text-text-default border-border-subtle hover:text-text-default hover:border-border-default'
        }`}
      >
        {triggerLabel}
        {activeCount > 0 ? ` (${activeCount})` : ''}
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-56 p-2"
        aria-label="Event log filters"
      >
        <div className="flex flex-col gap-2 max-h-80 overflow-y-auto">
          <label className="text-11 font-bold uppercase tracking-wide text-text-secondary">
            Filter text
            <input
              type="search"
              role="searchbox"
              aria-label="Filter events"
              placeholder="Filter events..."
              value={filters.search}
              onChange={handleSearchChange}
              className="mt-0.5 w-full px-2 py-1 text-xs bg-surface-secondary border border-border-subtle text-text-default placeholder-text-secondary focus-visible:outline-none focus-visible:border-border-emphasis font-normal"
            />
          </label>
          <div className="flex gap-1" role="group" aria-label="Event log view">
            {VIEW_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={view === option.value}
                onClick={() => onViewChange(option.value)}
                className={`px-2 py-1 text-xs font-semibold border transition-colors ${
                  view === option.value
                    ? 'bg-surface-tertiary text-text-default border-border-emphasis'
                    : 'bg-surface-secondary text-text-default border-border-subtle hover:text-text-default hover:border-border-default'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-1" role="group" aria-label="Severity filter">
            <p className="text-11 font-bold uppercase tracking-wide text-text-secondary">Severity</p>
            <div className="flex flex-wrap gap-1">
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
          </div>
          <FilterCheckboxList
            label="Room filter"
            heading="Rooms"
            options={rooms}
            selected={filters.rooms}
            onToggle={(value) => toggleDimension(filters, onChange, 'rooms', value)}
          />
          <FilterCheckboxList
            label="Category filter"
            heading="Categories"
            options={categories}
            selected={filters.categories}
            onToggle={(value) => toggleDimension(filters, onChange, 'categories', value)}
          />
          <FilterCheckboxList
            label="Event type filter"
            heading="Types"
            options={types}
            selected={filters.types}
            onToggle={(value) => toggleDimension(filters, onChange, 'types', value)}
          />
        </div>
      </PopoverContent>

    </Popover>
  )
}
export function CompactFiltersTrigger({
  filters,
  onChange,
  rooms,
  categories,
  types,
  view,
  onViewChange,
  primaryRooms = [],
  triggerLabel = 'Filters',
}: Omit<EventFiltersProps, 'compact' | 'hideCompactTrigger'> & {
  triggerLabel?: string
}) {
  return (
    <FiltersMenu
      filters={filters}
      rooms={rooms.filter((room) => !primaryRooms.includes(room))}
      categories={categories}
      types={types}
      view={view}
      onViewChange={onViewChange}
      onChange={onChange}
      triggerLabel={triggerLabel}
    />
  )
}

export function EventFilters({
  filters,
  onChange,
  rooms,
  categories,
  types,
  view,
  onViewChange,
  compact = false,
  primaryRooms = [],
  hideCompactTrigger = false,
}: EventFiltersProps) {
  const extraRooms = rooms.filter((room) => !primaryRooms.includes(room))

  if (compact) {
    const activeCount =
      filters.categories.length +
      filters.types.length +
      filters.rooms.length +
      (filters.severity !== 'all' ? 1 : 0) +
      (filters.search.trim() ? 1 : 0)
    return (
      <div className="flex flex-col gap-2" role="toolbar" aria-label="Event filters">
        <div className="flex flex-wrap items-center gap-1">
          {primaryRooms.map((room) => (
            <MultiSelectChip
              key={room}
              label={room}
              selected={filters.rooms.includes(room)}
              onClick={() => toggleDimension(filters, onChange, 'rooms', room)}
            />
          ))}
          {!hideCompactTrigger && (
            <FiltersMenu
              filters={filters}
              rooms={extraRooms}
              categories={categories}
              types={types}
              view={view}
              onViewChange={onViewChange}
              onChange={onChange}
              triggerLabel="Filters"
            />
          )}
        </div>
        {activeCount > 0 && (
          <button
            type="button"
            onClick={() =>
              onChange({ severity: 'all', search: '', rooms: [], categories: [], types: [] })
            }
            className="self-start px-2 py-0.5 text-xs font-semibold border bg-surface-secondary border-border-subtle text-text-default hover:border-border-default"
          >
            Clear all filters ({activeCount})
          </button>
        )}
      </div>
    )
  }

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onChange({ ...filters, search: event.target.value })
  }

  return (
    <div className="flex flex-col gap-2" role="toolbar" aria-label="Event filters">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Event log view">
          {VIEW_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={view === option.value}
              onClick={() => onViewChange(option.value)}
              className={`px-2 py-1 text-xs font-semibold border transition-colors ${
                view === option.value
                  ? 'bg-surface-tertiary text-text-default border-border-emphasis'
                  : 'bg-surface-secondary text-text-default border-border-subtle hover:text-text-default hover:border-border-default'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Severity filter">
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
        <FilterDropdown
          filters={filters}
          categories={categories}
          types={types}
          onChange={onChange}
        />
        <input
          type="search"
          role="searchbox"
          aria-label="Filter events"
          placeholder="Filter events..."
          value={filters.search}
          onChange={handleSearchChange}
          className="flex-1 min-w-[120px] px-2 py-1 text-xs bg-surface-secondary border border-border-subtle text-text-default placeholder-text-secondary focus-visible:outline-none focus-visible:border-border-emphasis"
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
              onClick={() => toggleDimension(filters, onChange, 'rooms', room)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface FilterDropdownProps {
  filters: FilterState
  categories: readonly string[]
  types: readonly string[]
  onChange: (next: FilterState) => void
}

function FilterDropdown({ filters, categories, types, onChange }: FilterDropdownProps) {
  const activeCount = filters.categories.length + filters.types.length

  const clear = useCallback(() => {
    onChange({ ...filters, categories: [], types: [] })
  }, [filters, onChange])

  return (
    <Popover>
      <PopoverTrigger
        type="button"
        className={`px-2 py-1 text-xs font-semibold border transition-colors whitespace-nowrap ${
          activeCount > 0
            ? 'bg-surface-tertiary text-text-default border-border-emphasis'
            : 'bg-surface-secondary text-text-default border-border-subtle hover:text-text-default hover:border-border-default'
        }`}
      >
        Categories &amp; types{activeCount > 0 ? ` (${activeCount})` : ''}
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-56 p-2"
        aria-label="Category and type filters"
      >
        <div className="flex flex-col gap-2 max-h-64 overflow-y-auto">
          <FilterCheckboxList
            label="Category filter"
            heading="Categories"
            options={categories}
            selected={filters.categories}
            onToggle={(value) => toggleDimension(filters, onChange, 'categories', value)}
          />
          <FilterCheckboxList
            label="Event type filter"
            heading="Types"
            options={types}
            selected={filters.types}
            onToggle={(value) => toggleDimension(filters, onChange, 'types', value)}
          />
        </div>
        {activeCount > 0 && (
          <button
            type="button"
            onClick={clear}
            className="mt-2 px-2 py-1 text-xs font-semibold border bg-surface-secondary border-border-subtle text-text-default hover:border-border-default"
          >
            Clear
          </button>
        )}
      </PopoverContent>
    </Popover>
  )
}
