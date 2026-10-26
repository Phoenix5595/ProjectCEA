import { addDays, format } from 'date-fns'
import { toZonedTime } from 'date-fns-tz'
import { useId, useState } from 'react'
import { toast } from 'sonner'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { apiClient } from '../../services/api'
import { extractErrorMessage } from '../../utils/errors'
import {
  buildFlowerGrowPlanPreview,
  CALENDAR_TZ,
  defaultBedVegStart,
  flowerStartFor,
  shiftIsoDate,
  type FlowerGrowPlanAnchor,
  type FlowerGrowPlanInput,
} from '../../utils/flowerGrowPlan'

interface FlowerGrowWizardProps {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

const ANCHOR_CHOICES: Array<{ value: FlowerGrowPlanAnchor; label: string }> = [
  { value: 'bed_veg_start', label: 'Start of veg beds' },
  { value: 'flower_start', label: 'Start of flower' },
  { value: 'ripen_end', label: 'End of ripening' },
]

export default function FlowerGrowWizard({ open, onClose, onCreated }: FlowerGrowWizardProps) {
  const idPrefix = useId()
  const [cropName, setCropName] = useState('Flower cycle')
  const [environment, setEnvironment] = useState<'indoor' | 'outdoor'>('indoor')
  const [anchor, setAnchor] = useState<FlowerGrowPlanAnchor>('flower_start')
  const [anchorDate, setAnchorDate] = useState(() =>
    format(addDays(toZonedTime(new Date(), CALENDAR_TZ), 14), 'yyyy-MM-dd')
  )
  const [bedVegStartOverride, setBedVegStartOverride] = useState<string | null>(null)
  const [bedVegDays, setBedVegDays] = useState(14)
  const [stretchDays, setStretchDays] = useState(21)
  const [bulkDays, setBulkDays] = useState(21)
  const [ripenDays, setRipenDays] = useState(21)
  const [dryingDays, setDryingDays] = useState(7)
  const [potEnabled, setPotEnabled] = useState(false)
  const [potStart, setPotStart] = useState('')
  const [cloneEnabled, setCloneEnabled] = useState(false)
  const [cloneStart, setCloneStart] = useState('')
  const [autoSubmode, setAutoSubmode] = useState(true)
  const [saving, setSaving] = useState(false)

  const flowerStart = flowerStartFor({
    anchor,
    anchorDate,
    bedVegDays,
    stretchDays,
    bulkDays,
    ripenDays,
  })
  const bedVegStart =
    anchor === 'bed_veg_start'
      ? anchorDate
      : (bedVegStartOverride ??
        defaultBedVegStart({ anchor, anchorDate, bedVegDays, stretchDays, bulkDays, ripenDays }))

  const planInput: FlowerGrowPlanInput = {
    anchor,
    anchorDate,
    bedVegStart,
    bedVegDays,
    stretchDays,
    bulkDays,
    ripenDays,
    dryingDays,
    potStart: potEnabled && potStart ? potStart : null,
    cloneStart: cloneEnabled && cloneStart ? cloneStart : null,
  }

  let preview = buildFlowerGrowPlanPreview(planInput)
  if (potEnabled && !potStart) preview = { phases: [], error: 'Pot start is required' }
  else if (cloneEnabled && !cloneStart) preview = { phases: [], error: 'Clone start is required' }
  const flowerStartInPast = flowerStart < format(toZonedTime(new Date(), CALENDAR_TZ), 'yyyy-MM-dd')

  const handleSubmit = async () => {
    if (preview.error) {
      toast.error(preview.error)
      return
    }
    setSaving(true)
    try {
      await apiClient.createFlowerGrowPlan({
        idempotency_key: crypto.randomUUID(),
        crop_name: cropName,
        environment,
        anchor,
        anchor_date: anchorDate,
        bed_veg_start: bedVegStart,
        bed_veg_days: bedVegDays,
        stretch_days: stretchDays,
        bulk_days: bulkDays,
        ripen_days: ripenDays,
        drying_days: dryingDays,
        pot_start: planInput.potStart,
        clone_start: planInput.cloneStart,
        auto_mode_transition: autoSubmode,
      })
      toast.success('Grow plan created')
      onCreated()
      onClose()
    } catch (e: unknown) {
      toast.error(extractErrorMessage(e, 'Failed to create plan'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={o => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto bg-surface-base rounded-lg shadow-xl">
        <DialogTitle className="normal-case tracking-normal text-lg font-bold text-text-default mb-4">
          Flower grow plan
        </DialogTitle>

        <label htmlFor={`${idPrefix}-crop-name`} className="block text-sm text-text-secondary mb-1">
          Crop name
        </label>
        <Input
          id={`${idPrefix}-crop-name`}
          className="mb-3"
          value={cropName}
          onChange={e => setCropName(e.target.value)}
        />

        <label
          htmlFor={`${idPrefix}-environment`}
          className="block text-sm text-text-secondary mb-1"
        >
          Environment
        </label>
        <Select value={environment} onValueChange={v => setEnvironment(v as 'indoor' | 'outdoor')}>
          <SelectTrigger id={`${idPrefix}-environment`} className="w-full mb-3">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="indoor">Indoor</SelectItem>
            <SelectItem value="outdoor">Outdoor</SelectItem>
          </SelectContent>
        </Select>

        <fieldset className="mb-3">
          <legend className="block text-sm text-text-secondary mb-1">Anchor</legend>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-text-default">
            {ANCHOR_CHOICES.map(choice => (
              <label key={choice.value} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name={`${idPrefix}-anchor`}
                  value={choice.value}
                  checked={anchor === choice.value}
                  onChange={() => setAnchor(choice.value)}
                />
                {choice.label}
              </label>
            ))}
          </div>
        </fieldset>

        <label
          htmlFor={`${idPrefix}-anchor-date`}
          className="block text-sm text-text-secondary mb-1"
        >
          Anchor date
        </label>
        <input
          id={`${idPrefix}-anchor-date`}
          type="date"
          required
          className="w-full mb-1 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={anchorDate}
          onChange={e => setAnchorDate(e.target.value)}
        />
        {flowerStartInPast && (
          <p className="text-amber-500 text-xs mb-3">Flower start is in the past.</p>
        )}

        <label
          htmlFor={`${idPrefix}-bed-veg-start`}
          className="block text-sm text-text-secondary mb-1"
        >
          Bed veg start
        </label>
        <input
          id={`${idPrefix}-bed-veg-start`}
          type="date"
          required
          readOnly={anchor === 'bed_veg_start'}
          className="w-full mb-3 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default read-only:opacity-70"
          value={bedVegStart}
          onChange={e => setBedVegStartOverride(e.target.value)}
        />

        <div className="grid grid-cols-2 gap-2 mb-3 text-sm">
          <label>
            Bed veg (d)
            <input
              type="number"
              min={1}
              max={84}
              className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary"
              value={bedVegDays}
              onChange={e => setBedVegDays(Number(e.target.value))}
            />
          </label>
          <label>
            Stretch (d)
            <input
              type="number"
              min={1}
              max={60}
              className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary"
              value={stretchDays}
              onChange={e => setStretchDays(Number(e.target.value))}
            />
          </label>
          <label>
            Bulk (d)
            <input
              type="number"
              min={1}
              max={60}
              className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary"
              value={bulkDays}
              onChange={e => setBulkDays(Number(e.target.value))}
            />
          </label>
          <label>
            Ripen (d)
            <input
              type="number"
              min={1}
              max={60}
              className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary"
              value={ripenDays}
              onChange={e => setRipenDays(Number(e.target.value))}
            />
          </label>
          <label>
            Drying (d)
            <input
              type="number"
              min={1}
              max={14}
              className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary"
              value={dryingDays}
              onChange={e => setDryingDays(Number(e.target.value))}
            />
          </label>
        </div>

        <label className="flex items-center gap-2 mb-2 text-sm text-text-default">
          <input
            type="checkbox"
            checked={potEnabled}
            onChange={e => {
              setPotEnabled(e.target.checked)
              if (e.target.checked && !potStart) setPotStart(shiftIsoDate(bedVegStart, -14))
            }}
          />
          Veg pot
        </label>
        {potEnabled && (
          <label className="mb-3 block text-sm text-text-secondary">
            Pot start
            <input
              type="date"
              required
              className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
              value={potStart}
              onChange={e => setPotStart(e.target.value)}
            />
          </label>
        )}

        <label className="flex items-center gap-2 mb-2 text-sm text-text-default">
          <input
            type="checkbox"
            checked={cloneEnabled}
            onChange={e => {
              setCloneEnabled(e.target.checked)
              if (e.target.checked && !cloneStart) {
                setCloneStart(shiftIsoDate(potEnabled && potStart ? potStart : bedVegStart, -21))
              }
            }}
          />
          Cloning
        </label>
        {cloneEnabled && (
          <label className="mb-3 block text-sm text-text-secondary">
            Clone start
            <input
              type="date"
              required
              className="w-full px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
              value={cloneStart}
              onChange={e => setCloneStart(e.target.value)}
            />
          </label>
        )}

        <label className="flex items-center gap-2 mb-3 text-sm text-text-default">
          <input
            type="checkbox"
            checked={autoSubmode}
            onChange={e => setAutoSubmode(e.target.checked)}
          />
          Automatic submode changes
        </label>

        {preview.error && <p className="text-red-500 text-sm mb-2">{preview.error}</p>}

        <div className="mb-4 max-h-32 overflow-y-auto text-xs text-text-secondary border border-border-default rounded p-2">
          {preview.phases.map(p => (
            <div key={p.phaseOrder}>
              {p.start} → {p.end}: {p.title} ({p.location})
            </div>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 rounded bg-surface-secondary text-text-secondary"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving || !!preview.error}
            onClick={() => void handleSubmit()}
            className="px-3 py-1 rounded bg-accent-vivid text-accent-vivid-foreground disabled:opacity-50"
          >
            {saving ? 'Creating…' : 'Create plan'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
