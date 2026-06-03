import { useMemo, useState } from 'react';
import { format } from 'date-fns';
import { toast } from 'sonner';

import { apiClient } from '../../services/api';
import {
  buildFlowerGrowPlanPreview,
  isFlowerEndInPast,
  type FlowerGrowPlanInput,
} from '../../utils/flowerGrowPlan';

interface FlowerGrowWizardProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export default function FlowerGrowWizard({ open, onClose, onCreated }: FlowerGrowWizardProps) {
  const [cropName, setCropName] = useState('Flower cycle');
  const [environment, setEnvironment] = useState<'indoor' | 'outdoor'>('indoor');
  const [flowerEnd, setFlowerEnd] = useState(format(new Date(), 'yyyy-MM-dd'));
  const [flowerWeeks, setFlowerWeeks] = useState(9);
  const [includePot, setIncludePot] = useState(true);
  const [cloneWeeks, setCloneWeeks] = useState(3);
  const [potWeeks, setPotWeeks] = useState(2);
  const [bedWeeks, setBedWeeks] = useState(2);
  const [stretchDays, setStretchDays] = useState(21);
  const [ripenDays, setRipenDays] = useState(21);
  const [dryingDays, setDryingDays] = useState(7);
  const [autoMode, setAutoMode] = useState(true);
  const [saving, setSaving] = useState(false);

  const planInput: FlowerGrowPlanInput = useMemo(
    () => ({
      flowerEnd,
      flowerWeeks,
      includePotPhases: includePot,
      cloneWeeks,
      potWeeks,
      bedWeeks,
      stretchDays,
      ripenDays,
      dryingDays,
    }),
    [flowerEnd, flowerWeeks, includePot, cloneWeeks, potWeeks, bedWeeks, stretchDays, ripenDays, dryingDays]
  );

  const preview = useMemo(() => buildFlowerGrowPlanPreview(planInput), [planInput]);
  const pastWarning = isFlowerEndInPast(flowerEnd);

  if (!open) return null;

  const handleSubmit = async () => {
    if (preview.error) {
      toast.error(preview.error);
      return;
    }
    setSaving(true);
    try {
      await apiClient.createFlowerGrowPlan({
        idempotency_key: crypto.randomUUID(),
        crop_name: cropName,
        environment,
        flower_end: flowerEnd,
        flower_weeks: flowerWeeks,
        include_pot_phases: includePot,
        clone_weeks: cloneWeeks,
        pot_weeks: potWeeks,
        bed_weeks: bedWeeks,
        stretch_days: stretchDays,
        ripen_days: ripenDays,
        drying_days: dryingDays,
        auto_mode_transition: autoMode,
      });
      toast.success('Grow plan created');
      onCreated();
      onClose();
    } catch (e: unknown) {
      const msg = e && typeof e === 'object' && 'response' in e
        ? String((e as { response?: { data?: { detail?: string } } }).response?.data?.detail)
        : 'Failed to create plan';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-surface-base border border-border-default rounded-lg max-w-lg w-full max-h-[90vh] overflow-y-auto p-4 shadow-xl">
        <h2 className="text-lg font-bold text-text-default mb-4">Flower grow plan</h2>

        <label className="block text-sm text-text-secondary mb-1">Crop name</label>
        <input
          className="w-full mb-3 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={cropName}
          onChange={(e) => setCropName(e.target.value)}
        />

        <label className="block text-sm text-text-secondary mb-1">Environment</label>
        <select
          className="w-full mb-3 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={environment}
          onChange={(e) => setEnvironment(e.target.value as 'indoor' | 'outdoor')}
        >
          <option value="indoor">Indoor</option>
          <option value="outdoor">Outdoor</option>
        </select>

        <label className="block text-sm text-text-secondary mb-1">Flower end (last day of ripen)</label>
        <input
          type="date"
          className="w-full mb-1 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={flowerEnd}
          onChange={(e) => setFlowerEnd(e.target.value)}
        />
        {pastWarning && (
          <p className="text-amber-500 text-xs mb-3">This date is in the past — OK for retroactive logging.</p>
        )}

        <label className="block text-sm text-text-secondary mb-1">Flower length (weeks)</label>
        <input
          type="number"
          min={1}
          max={52}
          className="w-full mb-3 px-2 py-1 rounded border border-border-default bg-surface-secondary text-text-default"
          value={flowerWeeks}
          onChange={(e) => setFlowerWeeks(Number(e.target.value))}
        />

        <label className="flex items-center gap-2 mb-3 text-sm text-text-default">
          <input type="checkbox" checked={includePot} onChange={(e) => setIncludePot(e.target.checked)} />
          Include pot phases (clone + pot veg)
        </label>

        <div className="grid grid-cols-3 gap-2 mb-3 text-sm">
          {includePot && (
            <>
              <label>
                Clone (w)
                <input type="number" min={1} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={cloneWeeks} onChange={(e) => setCloneWeeks(Number(e.target.value))} />
              </label>
              <label>
                Pot (w)
                <input type="number" min={1} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={potWeeks} onChange={(e) => setPotWeeks(Number(e.target.value))} />
              </label>
            </>
          )}
          <label>
            Bed (w)
            <input type="number" min={1} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={bedWeeks} onChange={(e) => setBedWeeks(Number(e.target.value))} />
          </label>
          <label>
            Stretch (d)
            <input type="number" min={1} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={stretchDays} onChange={(e) => setStretchDays(Number(e.target.value))} />
          </label>
          <label>
            Ripen (d)
            <input type="number" min={1} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={ripenDays} onChange={(e) => setRipenDays(Number(e.target.value))} />
          </label>
          <label>
            Drying (d)
            <input type="number" min={1} max={14} className="w-full px-1 py-0.5 rounded border border-border-default bg-surface-secondary" value={dryingDays} onChange={(e) => setDryingDays(Number(e.target.value))} />
          </label>
        </div>

        <label className="flex items-center gap-2 mb-3 text-sm text-text-default">
          <input type="checkbox" checked={autoMode} onChange={(e) => setAutoMode(e.target.checked)} />
          Apply mode changes automatically
        </label>

        {preview.error && <p className="text-red-500 text-sm mb-2">{preview.error}</p>}

        <div className="mb-4 max-h-32 overflow-y-auto text-xs text-text-secondary border border-border-default rounded p-2">
          {preview.phases.map((p) => (
            <div key={p.phaseOrder}>
              {p.start} → {p.end}: {p.title} ({p.location})
            </div>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1 rounded bg-surface-secondary text-text-secondary">
            Cancel
          </button>
          <button
            type="button"
            disabled={saving || !!preview.error}
            onClick={() => void handleSubmit()}
            className="px-3 py-1 rounded bg-accent text-surface-base disabled:opacity-50"
          >
            {saving ? 'Creating…' : 'Create plan'}
          </button>
        </div>
      </div>
    </div>
  );
}
