import { describe, expect, it } from 'vitest'
import { authorityCurrent, authorityEnvelope, authorityFuture, authorityNow, authorityPeriod, authorityWindow } from '../../__tests__/timelineAuthorityFixtures'
import { buildTimelineSources, currentSetpointSeriesId, type TimelineSources, type TimelineSourcesOptions } from '../timelineSources'
import { buildTimelineOptions } from '../timelineOptions'

function options(): TimelineSourcesOptions {
  return { location: 'Flower Room', cluster: 'main', window: authorityWindow, now: authorityNow,
    current: authorityCurrent(), future: [authorityFuture()], saved: authorityEnvelope(), draft: null,
    localDraft: [authorityPeriod], selectedLabel: 'Drying', activeLabel: 'Flower / Bulk' }
}
function sample(chart: TimelineSources, role: string, time: number): number | null | undefined {
  const series = chart.meta.findIndex(entry => entry.role === role && entry.metric === 'heating_setpoint') + 1
  return chart.data[series]?.[chart.sampleTimes.indexOf(time)]
}

describe('timeline source authority', () => {
  it('retains 22 current, 24 canonical future, 18 saved and 19 local draft under unique roles', () => {
    const chart = buildTimelineSources(options())
    expect(sample(chart, 'active-current', authorityNow - 100)).toBe(22)
    expect(sample(chart, 'active-current', authorityNow)).toBeNull()
    expect(sample(chart, 'active-future', authorityNow)).toBe(24)
    expect(sample(chart, 'selected-saved', authorityNow)).toBe(18)
    expect(sample(chart, 'selected-draft', authorityNow)).toBe(19)
    expect(chart.meta.some(entry => entry.role === 'selected-saved' && entry.kind === 'effective')).toBe(false)
    expect(chart.data.slice(1).flat().includes(99)).toBe(false)
    expect(new Set(chart.meta.map(entry => entry.key)).size).toBe(chart.meta.length)
    expect(chart.qualities.get('active-current:heating_setpoint:effective')?.[chart.sampleTimes.indexOf(authorityNow - 100)]).toBe('exact')
    expect(chart.qualities.get('selected-draft:heating_setpoint:scheduled')?.[chart.sampleTimes.indexOf(authorityNow)]).toBe('estimated')
    const plot = buildTimelineOptions(chart.meta, 800, 400, authorityWindow, {}, [])
    const roleStyle = (role: string) => plot.series[chart.meta.findIndex(entry => entry.role === role) + 1]
    expect(roleStyle('active-current')?.points).toMatchObject({ show: true })
    expect(roleStyle('active-future')?.dash).toEqual([6, 4])
    expect(roleStyle('selected-saved')?.dash).toEqual([])
    expect(roleStyle('selected-draft')?.dash).toEqual([2, 3])
  })

  it('samples subminute step boundaries and coverage holes without window-edge carry', () => {
    const first = authorityFuture()
    const boundary = authorityNow + 22_222
    first.valid_until = new Date(boundary)
    first.series[0]!.valid_until = new Date(boundary)
    const second = authorityFuture()
    second.valid_from = new Date(boundary + 10_000)
    second.series[0]!.valid_from = second.valid_from
    second.series[0]!.value = 26
    const chart = buildTimelineSources({ ...options(), future: [first, second] })
    expect(chart.sampleTimes).toContain(boundary - 1)
    expect(sample(chart, 'active-future', boundary - 1)).toBe(24)
    expect(sample(chart, 'active-future', boundary)).toBeNull()
    expect(sample(chart, 'active-future', boundary + 10_000)).toBe(26)
    for (let index = 1; index < chart.data.length; index++) expect(chart.data[index]?.at(-1)).toBeNull()
  })

  it('clears expired or mismatched actual publications without suppressing profile preparation', () => {
    for (const change of [
      { now: authorityNow + 5_000 },
      ...['.aux.', 'veg_room.'].map(scope => ({
        current: { ...authorityCurrent(), series: authorityCurrent().series.map(point => ({
          ...point, series_id: { value: scope === '.aux.'
            ? point.series_id.value.replace('.main.', scope)
            : point.series_id.value.replace('flower_room.', scope) },
        })) },
      })),
      { future: [{ ...authorityFuture(), version: { ...authorityFuture().version, config_version: 38 } }] },
      { future: [] },
    ]) {
      const chart = buildTimelineSources({ ...options(), ...change })
      const futureValues = chart.data[chart.meta.findIndex(entry => entry.role === 'active-future') + 1] ?? []
      for (const value of futureValues) expect(value).toBeNull()
      expect(sample(chart, 'selected-saved', authorityNow - 100)).toBe(18)
      expect(sample(chart, 'selected-draft', authorityNow - 100)).toBe(19)
    }
  })

  it('retains rich scheduled segment quality while current ID normalization fences rooms and clusters', () => {
    expect(currentSetpointSeriesId(' Flower--Room ', '1 Main!', 'heating_setpoint')).toBe('flower_room.v_1_main.setpoint.effective_heating_setpoint')
    expect(currentSetpointSeriesId('!!!', 'main', 'heating_setpoint')).toBeNull()
    const draft = authorityEnvelope(19, 1)
    draft.segments[0]!.quality = 'estimated'
    const chart = buildTimelineSources({ ...options(), draft, localDraft: null })
    expect(sample(chart, 'selected-draft', authorityNow)).toBe(19)
    expect(chart.qualities.get('selected-draft:heating_setpoint:scheduled')?.[chart.sampleTimes.indexOf(authorityNow)]).toBe('estimated')
  })
})
