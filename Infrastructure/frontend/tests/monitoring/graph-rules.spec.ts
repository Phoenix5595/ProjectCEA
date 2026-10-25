/**
 * Owner graph rules R1–R4 on the guarded fixture preview: live tail at the
 * right edge, visible buckets, in-place zoom near now, Monitoring vs
 * Historical separation, effective setpoints, and the Toronto climate timeline.
 */
import { expect, test, type Page } from '@playwright/test'

import type { MonitoringPerfDebug } from '../../src/features/monitoring/perfMarks'

import { fixtureUrl } from './fixtureUrl'

declare global {
  interface Window {
    readonly __monitoringPerf?: MonitoringPerfDebug
  }
}

const CLIMATE = 'Flower climate conditions'
const MINUTE = 60_000

interface ChartScale {
  readonly min: number
  readonly max: number
  readonly box: { x: number; y: number; width: number; height: number }
}

async function chartScale(page: Page, title: string): Promise<ChartScale> {
  await expect
    .poll(async () =>
      page.evaluate(
        name => window.__monitoringPerf?.charts.find(chart => chart.title === name)?.xScaleMax ?? null,
        title
      )
    )
    .not.toBeNull()
  const scale = await page.evaluate(name => {
    const chart = window.__monitoringPerf?.charts.find(candidate => candidate.title === name)
    return { min: chart?.xScaleMin ?? 0, max: chart?.xScaleMax ?? 0 }
  }, title)
  const box = await page.getByRole('region', { name: title }).locator('.u-over').first().boundingBox()
  if (box === null) throw new Error(`no plot overlay for ${title}`)
  return { ...scale, box }
}

async function dragChart(page: Page, title: string, fromMs: number, toMs: number): Promise<void> {
  const { min, max, box } = await chartScale(page, title)
  const xFor = (t: number) => box.x + ((t - min) / (max - min)) * box.width
  const y = box.y + box.height / 2
  await page.mouse.move(xFor(fromMs), y)
  await page.mouse.down()
  await page.mouse.move(xFor(toMs), y, { steps: 12 })
  await page.mouse.up()
}

async function tableRows(page: Page, title: string): Promise<{ heads: string[]; rows: string[][] }> {
  const region = page.getByRole('region', { name: title })
  const toggle = region.getByRole('button', { name: 'View data as table' })
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click()
  return region.locator(`table[aria-label="${title} data"]`).evaluate(table => ({
    heads: [...table.querySelectorAll('thead th')].map(cell => cell.textContent ?? ''),
    rows: [...table.querySelectorAll('tbody tr')].map(row =>
      [...row.querySelectorAll('td')].map(cell => cell.textContent ?? '')
    ),
  }))
}

function parseTableTime(text: string): number {
  // `formatTimestamp` renders the browser-local wall clock `YYYY/MM/DD HH:MM:SS`.
  const [date, time] = text.split(' ')
  const [y, mo, d] = date.split('/').map(Number)
  const [h, mi, s] = time.split(':').map(Number)
  return new Date(y, mo - 1, d, h, mi, s).getTime()
}

const PRESET_MS: Record<string, number> = {
  '1h': 60 * MINUTE,
  '3h': 180 * MINUTE,
  '6h': 360 * MINUTE,
  '12h': 720 * MINUTE,
  '24h': 1440 * MINUTE,
  '7d': 7 * 1440 * MINUTE,
}

function liveDuration(url: string): number | null {
  const label = /[?&]range=live-([^&]+)/.exec(url)?.[1]
  if (label === undefined) return null
  return PRESET_MS[label] ?? (/^\d+$/.test(label) ? Number(label) : null)
}

test('a: live values reach the right edge and buckets are visible', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.goto(fixtureUrl('/flower/monitoring?range=live-1h', testInfo, 'tail', 'live-now'))
  const badge = page.getByRole('region', { name: CLIMATE }).getByTestId('chart-bucket-badge')
  await expect(badge).toHaveText(/^Buckets: (raw|\d+ s)$/, { timeout: 20_000 })
  await page.waitForTimeout(6_000)
  // Freeze the view so the data table snapshot and the live table agree.
  const pausedAt = await page.evaluate(() => Date.now())
  await page.getByRole('button', { name: 'Pause' }).click()

  const backTable = page.getByRole('table', { name: 'Back Cluster' })
  const { heads, rows } = await tableRows(page, CLIMATE)
  const column = heads.indexOf('Dry Bulb (°C) - Back')
  expect(column).toBeGreaterThan(0)
  const plotted = rows.filter(row => row[column] !== '—')
  const last = plotted[plotted.length - 1]
  expect(parseTableTime(last[0])).toBeGreaterThanOrEqual(pausedAt - 3_000)
  const liveText = (await backTable.getByRole('row', { name: /Dry Bulb/ }).innerText()).match(
    /(\d+\.\d)°C/
  )?.[1]
  expect(last[column].startsWith(`${liveText}°C`)).toBe(true)

  const { box } = await chartScale(page, CLIMATE)
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5)
  const tooltip = page.locator('.mon-tooltip__bucket').first()
  await expect(tooltip).toHaveText(/\d{2}:\d{2}:\d{2}–\d{2}:\d{2}:\d{2} · \d+ samples · /)
})

test('b–d: zoom near now stays live, past zoom is fixed, Now returns to live', async ({
  page,
}, testInfo) => {
  await page.goto(fixtureUrl('/flower/monitoring?range=live-1h', testInfo, 'zoom', 'live-now'))
  await expect(page.getByRole('region', { name: CLIMATE }).getByTestId('chart-bucket-badge')).toBeVisible({
    timeout: 20_000,
  })
  const now = await page.evaluate(() => Date.now())
  await dragChart(page, CLIMATE, now - 20 * MINUTE, now + 5 * MINUTE)
  await expect.poll(() => liveDuration(page.url())).not.toBe(60 * MINUTE)
  const duration = liveDuration(page.url()) ?? 0
  expect(Math.abs(duration - 20 * MINUTE)).toBeLessThan(MINUTE)
  await expect
    .poll(async () => {
      const { max } = await chartScale(page, CLIMATE)
      const current = await page.evaluate(() => Date.now())
      return Math.abs(max - current - duration / 9) < 15_000
    })
    .toBe(true)

  // c–d start from the default live hour so Now has a known duration to restore.
  await page.goto(fixtureUrl('/flower/monitoring?range=live-1h', testInfo, 'past', 'live-now'))
  await expect(page.getByRole('region', { name: CLIMATE }).getByTestId('chart-bucket-badge')).toBeVisible({
    timeout: 20_000,
  })
  const later = await page.evaluate(() => Date.now())
  await dragChart(page, CLIMATE, later - 40 * MINUTE, later - 20 * MINUTE)
  await expect(page).toHaveURL(/start=.*&end=/)
  expect(page.url()).toContain('/flower/monitoring')

  await page.getByRole('button', { name: 'Now', exact: true }).click()
  await expect(page).toHaveURL(/range=live-1h/)
})

test('e: Monitoring climate panel shows recorded and projected setpoints', async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000)
  const requests: string[] = []
  page.on('request', request => requests.push(request.url()))
  await page.goto(fixtureUrl('/flower/monitoring?range=live-1h', testInfo, 'setpoints'))
  await expect(page.getByRole('region', { name: CLIMATE }).getByTestId('chart-bucket-badge')).toBeVisible({
    timeout: 20_000,
  })
  const { heads, rows } = await tableRows(page, CLIMATE)
  for (const target of ['heating', 'cooling', 'vpd']) expect(heads).toContain(target)
  const heating = heads.indexOf('heating')
  const now = await page.evaluate(() => Date.now())
  const recorded = rows.filter(row => parseTableTime(row[0]) <= now && row[heating] !== '—')
  const projected = rows.filter(row => parseTableTime(row[0]) > now + MINUTE && row[heating] !== '—')
  expect(recorded.length).toBeGreaterThan(0)
  expect(projected.length).toBeGreaterThan(0)
  expect(requests.some(url => url.includes('/history?') && url.includes('scope=room'))).toBe(true)
  expect(requests.some(url => url.includes('/projection'))).toBe(true)
})

test('f: Historical 3-day range shows recorded setpoints only and never projects', async ({
  page,
}, testInfo) => {
  test.setTimeout(90_000)
  const requests: string[] = []
  page.on('request', request => requests.push(request.url()))
  const end = new Date(Date.now() - 60 * MINUTE)
  const start = new Date(end.getTime() - 3 * 24 * 60 * MINUTE)
  await page.goto(
    fixtureUrl(
      `/flower/historical?start=${start.toISOString()}&end=${end.toISOString()}`,
      testInfo,
      'hist3d'
    )
  )
  await expect(page.getByRole('region', { name: CLIMATE }).getByTestId('chart-bucket-badge')).toBeVisible({
    timeout: 20_000,
  })
  expect(page.url()).toContain('/flower/historical')
  const { heads, rows } = await tableRows(page, CLIMATE)
  const heating = heads.indexOf('heating')
  expect(heating).toBeGreaterThan(0)
  expect(rows.some(row => row[heating] !== '—')).toBe(true)
  expect(rows.every(row => parseTableTime(row[0]) <= end.getTime() + 1_000)).toBe(true)
  await page.waitForTimeout(3_000)
  expect(requests.filter(url => url.includes('/projection'))).toEqual([])
  expect(requests.filter(url => url.includes('/sensors/monitoring/live/'))).toEqual([])
  await expect(page.getByRole('button', { name: 'Now', exact: true })).toHaveCount(0)
})

test('g–h: 31-day Historical reads climate setpoints and zooms in place', async ({
  page,
}, testInfo) => {
  const requests: string[] = []
  page.on('request', request => requests.push(request.url()))
  await page.goto(fixtureUrl('/flower/historical', testInfo, 'hist31'))
  await expect
    .poll(() => requests.some(url => url.includes('/history?') && url.includes('scope=climate')), {
      timeout: 20_000,
    })
    .toBe(true)
  const overviewPlot = page.locator('.u-over').first()
  await expect(overviewPlot).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(1_000)
  const scale = await page.evaluate(() => {
    const chart = window.__monitoringPerf?.charts.find(candidate => candidate.xScaleMax !== null)
    return { min: chart?.xScaleMin ?? 0, max: chart?.xScaleMax ?? 0 }
  })
  const { min, max } = scale
  const box = await overviewPlot.boundingBox()
  if (box === null) throw new Error('no overview plot')
  const initialStart = new URL(page.url()).searchParams.get('start')
  const from = min + (max - min) * 0.4
  const to = from + 3 * 24 * 60 * MINUTE
  const xFor = (t: number) => box.x + ((t - min) / (max - min)) * box.width
  await page.mouse.move(xFor(from), box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(xFor(to), box.y + box.height / 2, { steps: 12 })
  await page.mouse.up()
  await expect
    .poll(() => new URL(page.url()).searchParams.get('start'))
    .not.toBe(initialStart)
  expect(page.url()).toContain('/flower/historical')
  const url = new URL(page.url())
  const span = new Date(url.searchParams.get('end') ?? '').getTime() - new Date(url.searchParams.get('start') ?? '').getTime()
  expect(Math.abs(span - 3 * 24 * 60 * MINUTE)).toBeLessThan(6 * 60 * MINUTE)
  await expect(page.getByRole('button', { name: 'Reset Zoom' })).toBeEnabled()
})

test('i: a Monitoring custom range over 7 days opens Historical', async ({ page }, testInfo) => {
  await page.goto(fixtureUrl('/flower/monitoring?range=live-1h', testInfo, 'custom10d'))
  const pad = (n: number) => String(n).padStart(2, '0')
  const local = (d: Date) =>
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  const end = new Date(Date.now() - 60 * MINUTE)
  const start = new Date(end.getTime() - 10 * 24 * 60 * MINUTE)
  await page.getByRole('textbox', { name: 'Range start', exact: true }).fill(local(start))
  await page.getByRole('textbox', { name: 'Range end', exact: true }).fill(local(end))
  await page.getByRole('button', { name: 'Apply fixed range' }).click()
  await expect(page).toHaveURL(/\/flower\/historical\?.*start=/)
})

test('j: climate timeline uses Toronto time and hides Draft for a clean editor', async ({
  page,
}, testInfo) => {
  await page.goto(fixtureUrl('/flower/control', testInfo, 'timeline'))
  await expect(page.getByText('Times America/Toronto')).toBeVisible({ timeout: 20_000 })
  const overlay = page.getByTestId('control-timeline-uplot').locator('.u-over')
  await expect(overlay).toBeVisible()
  await page.waitForTimeout(1_000)
  const box = await overlay.boundingBox()
  if (box === null) throw new Error('no timeline plot')
  // The daily window is the Toronto calendar day, so the plot midpoint is ~12:00 Toronto.
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  const tooltip = page
    .locator('[class*="tooltip"]')
    .filter({ hasText: /\d{2}:\d{2}:\d{2}\.\d{3}/ })
    .first()
  await expect(tooltip).toContainText(/1[12]:\d{2}:\d{2}/)
  await expect(tooltip).not.toContainText('UTC')
  await expect(tooltip).not.toContainText('Draft')
})
