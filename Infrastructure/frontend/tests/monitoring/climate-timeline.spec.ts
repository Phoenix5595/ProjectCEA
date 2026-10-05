import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const expect = baseExpect.configure({ timeout: 15_000 })

// Cold route imports and real canvas drags run on the ARM desktop QA host.
test.setTimeout(60_000)
test.use({ actionTimeout: 15_000 })
test.beforeEach(async ({ page }) => { page.setDefaultTimeout(15_000) })

function trackViolations(page: import('@playwright/test').Page): string[] {
  const violations: string[] = []
  page.on('request', request => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  return violations
}

test('climate timeline remains usable', async ({ page }, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Climate Periods' })).toBeVisible()
  await expect(page.getByTestId('control-timeline-handle-0-start')).toHaveCount(0)
  await expect(page.getByTestId('calendar-transition-skipped-overlay')).toHaveCount(0)

  const firstStart = page.locator('input[placeholder="HH:MM"]').first()
  await expect(firstStart).toHaveValue('06:00')
  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByTestId('control-timeline-handle-0-start')).toBeVisible()

  await page.getByTestId('control-timeline-handle-0-start').focus()
  await page.keyboard.press('ArrowRight')
  await expect(firstStart).toHaveValue('06:05')
  await page.getByRole('button', { name: 'Review' }).click()
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled()
  expect(violations).toEqual([])
})

test('renders a skipped calendar transition over the saved reality in compact and expanded modes', async ({
  page,
}, testInfo) => {
  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'calendar-transition-skipped'))

  const overlay = page.getByTestId('calendar-transition-skipped-overlay')
  await expect(overlay).toBeVisible()
  await expect(overlay).toHaveAttribute('aria-label', 'Calendar transition skipped: unknown_mode')
  await expect(page.getByText('Day cycle')).toBeVisible()
  await expect(page.getByText('Calendar transition skipped: unknown_mode')).toBeVisible()

  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByText('EDITABLE')).toBeVisible()
  await expect(overlay).toBeVisible()
  await expect(page.getByTestId('control-timeline-handle-0-start')).toBeVisible()
})

for (const scenario of [
  'timeline-preview-failed',
  'timeline-preview-stale',
  'timeline-wrong-room',
] as const) {
  test(`rejects ${scenario} without an unsafe Apply call`, async ({ page }, testInfo) => {
    const violations = trackViolations(page)
    const applyRequests: string[] = []
    page.on('request', request => {
      if (new URL(request.url()).pathname.endsWith('/apply')) applyRequests.push(request.url())
    })

    await page.goto(fixtureUrl('/flower/control', testInfo, undefined, scenario))
    await page.getByRole('button', { name: 'Expand editor' }).click()
    await page.getByTestId('control-timeline-handle-0-start').focus()
    await page.keyboard.press('ArrowRight')
    await page.getByRole('button', { name: 'Review' }).click()
    await expect(page.getByRole('button', { name: 'Apply' })).toBeDisabled()
    expect(applyRequests).toEqual([])
    expect(violations).toEqual([])
  })
}

test('keeps the primary climate periods table functional when the saved timeline API fails', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-api-failure'))

  const table = page.getByRole('table')
  await expect(table).toBeVisible()
  const firstStart = table.locator('input[placeholder="HH:MM"]').first()
  await firstStart.fill('06:15')
  await expect(firstStart).toHaveValue('06:15')
  await expect(page.getByTestId('save-profile')).toBeDisabled()
  expect(violations).toEqual([])
})

test('surfaces timeline_unavailable while retaining the fallback periods UI', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-unavailable-409'))

  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByTestId('save-profile')).toBeDisabled()
  expect(violations).toEqual([])
})

test('uses the canonical mode instead of contradictory API is_constant flags', async ({
  page,
}, testInfo) => {
  await page.goto(fixtureUrl('/vegetation/control', testInfo, undefined, 'veg-constant-flag'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.locator('input[placeholder="HH:MM"]').first()).toBeEnabled()

  await page.goto(fixtureUrl('/flower/control', testInfo, 'sleep', 'sleep-scheduled-flag'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  await expect(page.locator('input[placeholder="HH:MM"]').first()).toBeDisabled()
})

async function canvasPixelStats(
  page: import('@playwright/test').Page,
  selector: string
): Promise<{
  orangeRows: number
  grayStrip: number
}> {
  return page.evaluate(sel => {
    const host = document.querySelector(sel)
    const canvas = host?.querySelector('canvas')
    if (!(canvas instanceof HTMLCanvasElement)) return { orangeRows: -1, grayStrip: -1 }
    const ctx = canvas.getContext('2d')
    if (!ctx) return { orangeRows: -1, grayStrip: -1 }
    const { width, height } = canvas
    const data = ctx.getImageData(0, 0, width, height).data
    const orangeRows = new Set<number>()
    let grayStrip = 0
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = (y * width + x) * 4
        const r = data[index],
          g = data[index + 1],
          b = data[index + 2]
        if (Math.abs(r - 234) < 45 && Math.abs(g - 88) < 45 && b < 60) orangeRows.add(y)
        if (y > height * 0.9 && Math.abs(r - g) < 8 && Math.abs(g - b) < 8 && r > 30 && r < 200)
          grayStrip += 1
      }
    }
    return { orangeRows: orangeRows.size, grayStrip }
  }, selector)
}

test('expanded daily editor supports mouse drags and two-way table sync', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await page.getByRole('button', { name: 'Expand editor' }).click()
  await expect(page.getByText('EDITABLE')).toBeVisible()
  await page.getByRole('button', { name: 'daily' }).click()
  await expect(page.getByTestId('control-timeline-value-grip-0-heating')).toBeVisible()

  const stats = await canvasPixelStats(page, '[data-testid="control-timeline-uplot"]')
  expect(stats.orangeRows).toBeGreaterThanOrEqual(4)
  expect(stats.grayStrip).toBeGreaterThan(0)

  const grip = page.getByTestId('control-timeline-value-grip-0-heating')
  const gripBox = await grip.boundingBox()
  if (!gripBox) throw new Error('value grip not positioned')
  await page.mouse.move(gripBox.x + 5, gripBox.y + 5)
  await page.mouse.down()
  await page.mouse.move(gripBox.x + 5, Math.max(gripBox.y - 80, 0), { steps: 10 })
  await page.mouse.up()

  const heatInput = page.locator('input[placeholder="°C"]').first()
  await expect(heatInput).not.toHaveValue(/^22$/)

  const startGrip = page.getByTestId('control-timeline-boundary-grip-1-start')
  const startBox = await startGrip.boundingBox()
  if (!startBox) throw new Error('boundary grip not positioned')
  await page.mouse.move(startBox.x + 4, startBox.y + 20)
  await page.mouse.down()
  await page.mouse.move(startBox.x + 84, startBox.y + 20, { steps: 10 })
  await page.mouse.up()

  const nightStart = page.locator('input[placeholder="HH:MM"]').nth(2)
  const boundaryValue = await nightStart.inputValue()
  expect(boundaryValue).not.toBe('18:00')
  const minutes = Number(boundaryValue.slice(0, 2)) * 60 + Number(boundaryValue.slice(3, 5))
  expect(minutes % 5).toBe(0)

  const heatBefore = page.locator('input[placeholder="°C"]').first()
  const reviewStatus = page.getByText(/^Reviewed draft \d+$/)
  await expect(reviewStatus).toBeVisible()
  const previousReviewStatus = await reviewStatus.textContent()
  if (previousReviewStatus === null) throw new Error('timeline preview status was missing')
  const currentHeatValue = Number(await heatBefore.inputValue())
  const nextHeatValue = currentHeatValue === 24 ? '23' : '24'
  await heatBefore.fill(nextHeatValue)
  await expect(heatBefore).toHaveValue(nextHeatValue)
  await expect
    .poll(async () => {
      const currentStatus = await reviewStatus.textContent()
      return currentStatus !== previousReviewStatus ? currentStatus : null
    })
    .not.toBeNull()

  await page.getByRole('button', { name: 'Collapse editor' }).click()
  await expect(page.getByTestId('control-timeline-value-grip-0-heating')).toHaveCount(0)
  expect(violations).toEqual([])
})

test('renders faint period labels and archives viewport screenshots', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo))
  await page.screenshot({
    path: testInfo.outputPath('climate-timeline-compact.png'),
    fullPage: false,
  })

  await page.getByRole('button', { name: 'Expand editor' }).click()
  await page.screenshot({
    path: testInfo.outputPath('climate-timeline-expanded.png'),
    fullPage: false,
  })

  await expect(page.getByTestId('control-timeline-period-legend')).toContainText('Day cycle')
  const stats = await canvasPixelStats(page, '[data-testid="control-timeline-uplot"]')
  expect(stats.grayStrip).toBeGreaterThan(0)
  expect(violations).toEqual([])
})


test('surfaces a 409 conflict from the header Save and preserves the draft', async ({
  page,
}, testInfo) => {
  const violations = trackViolations(page)

  await page.goto(fixtureUrl('/flower/control', testInfo, undefined, 'timeline-apply-conflict'))
  await expect(page.getByRole('region', { name: 'Climate control timeline' })).toBeVisible()
  const heat = page.locator('input[placeholder="°C"]').first()
  await heat.fill('23.5')
  const rejection = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/apply') && response.status() === 409)
  await page.getByTestId('save-profile').click()
  await rejection
  await expect(page.locator('input[placeholder="°C"]').first()).toHaveValue('23.5')
  expect(violations).toEqual([])
})

interface ProfileFixtureState {
  active: { mode_id: number; submode_id: number | null }
  running: { mode_id: number; submode_id: number | null }
  global_revision: number
  registry_version: number
  profiles: Array<{
    location: string; cluster: string; mode_id: number; submode_id: number | null
    parameters_configured: boolean
    periods: Array<{ heating_setpoint: number | null; start_time: string; end_time: string }>
  }>
  light_targets: Record<string, number>
  mutations: Array<{ method: string; path: string; success: boolean; mode_id: number
    config_revision?: string; expected_config_revision?: string; target_intensity?: number }>
}

function fixtureQuery(page: Page): string { return new URL(page.url()).search }
async function fixtureState(page: Page): Promise<ProfileFixtureState> {
  const response = await page.request.get(`/__fixture/mode-profile-state${fixtureQuery(page)}`)
  expect(response.status()).toBe(200)
  return response.json()
}
function profile(state: ProfileFixtureState, modeId: number, submodeId: number | null = null) {
  return state.profiles.find(row => row.location === 'Flower Room' && row.cluster === 'main' &&
    row.mode_id === modeId && row.submode_id === submodeId)!
}
function heat(page: Page) { return page.getByRole('table').locator('input[placeholder="°C"]').first() }
async function openPreparation(page: Page, info: TestInfo, scenario = 'mode-profile-preparation') {
  const violations = trackViolations(page)
  await page.goto(fixtureUrl('/flower/control', info, info.project.name, scenario))
  await expect(page.getByRole('button', { name: 'Select Flower Bulk profile', exact: true }))
    .toHaveAttribute('data-profile-state', 'selected-active')
  await expect(heat(page)).toHaveValue('22')
  return violations
}
async function selectDrying(page: Page, savedValue = '18') {
  await page.getByRole('button', { name: 'Select Drying profile', exact: true }).click()
  await expect(heat(page)).toHaveValue(savedValue)
  await expect(page.getByTestId('save-profile')).toBeEnabled()
}

test('browses exact inactive mode and submode without mutating running authority', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  await selectDrying(page)
  await expect(page.getByRole('button', { name: 'Select Flower profile', exact: true })).toHaveAttribute('data-profile-state', 'active')
  await expect(page.getByRole('button', { name: 'Select Flower Bulk profile', exact: true })).toHaveAttribute('data-profile-state', 'active')
  await expect(page.getByRole('button', { name: 'Select Drying profile', exact: true })).toHaveAttribute('data-profile-state', 'selected')
  await expect(page.getByRole('region', { name: 'Climate control timeline' }).getByLabel('Timeline sources')).toBeVisible()
  const box = await page.getByTestId('activate-selected').boundingBox()
  expect(box).not.toBeNull()
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height).toBeLessThan(info.project.use.viewport!.height)
  await page.screenshot({ path: info.outputPath('inactive-selection.png') })
  await page.getByRole('button', { name: 'Select Flower Stretch profile', exact: true }).click()
  await expect(heat(page)).toHaveValue('24')
  await expect(page.getByRole('button', { name: 'Select Flower Bulk profile', exact: true })).toHaveAttribute('data-profile-state', 'active')
  await expect(page.getByRole('button', { name: 'Select Flower Stretch profile', exact: true })).toHaveAttribute('data-profile-state', 'selected')
  const state = await fixtureState(page)
  expect(state.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(state.mutations).toEqual([])
  expect(violations).toEqual([])
})

test('running mode selects one light panel and manual modes discover the room inventory', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  const controls = page.getByLabel('Live light controls', { exact: true })
  await expect(controls.getByRole('slider')).toHaveCount(3)
  await expect(controls.getByText('Manual Override', { exact: true })).toHaveCount(0)
  for (const mode of ['Drying', 'Sleep', 'Veg', 'Flower']) {
    await page.getByRole('button', { name: `Select ${mode} profile`, exact: true }).click()
    await expect(page.getByTestId('activate-selected')).toBeEnabled()
    await page.getByTestId('activate-selected').click()
    await expect(page.getByRole('button', { name: `Select ${mode} profile`, exact: true }))
      .toHaveAttribute('data-profile-state', 'selected-active')
    if (mode === 'Drying' || mode === 'Sleep') {
      await expect(controls.getByRole('slider')).toHaveCount(0)
      await expect(controls.getByRole('spinbutton')).toHaveCount(0)
      await expect(controls.getByText('Manual Override', { exact: true })).toBeVisible()
      await expect(controls.getByText(/Found 3 light.*Chilled Front, Apache, Chilled Back/)).toBeVisible()
      await expect(controls.getByRole('button', { name: 'Off', exact: true })).toBeEnabled()
      await expect(controls.getByRole('button', { name: '5m', exact: true })).toBeEnabled()
    } else {
      await expect(controls.getByRole('slider')).toHaveCount(3)
      await expect(controls.getByText('Manual Override', { exact: true })).toHaveCount(0)
    }
  }
  expect(violations).toEqual([])
})

test('persists inactive 19 while current 22 and running future 24 remain independent', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  await selectDrying(page)
  await heat(page).fill('19')
  await page.getByTestId('save-profile').click()
  await expect.poll(async () => profile(await fixtureState(page), 3).periods[0]?.heating_setpoint).toBe(19)
  const state = await fixtureState(page)
  expect(profile(state, 2, 11).periods[0]?.heating_setpoint).toBe(22)
  expect(state.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(state.mutations.filter(row => row.path.endsWith('/mode') || row.path.endsWith('/target'))).toEqual([])
  const current = await (await page.request.get(`/api/monitoring/control/Flower%20Room/current${fixtureQuery(page)}`)).json()
  const future = await (await page.request.get(`/api/monitoring/control/Flower%20Room/projection${fixtureQuery(page)}`)).json()
  expect(current.value.series.find((row: { series_id: { value: string } }) => row.series_id.value === 'flower_room.main.setpoint.effective_heating_setpoint').value).toBe(22)
  expect(future.value[0].series.find((row: { series_id: { value: string } }) => row.series_id.value === 'climate.heating_setpoint_target').value).toBe(24)
  await page.reload()
  await selectDrying(page, '19')
  await expect(heat(page)).toHaveValue('19')
  expect(violations).toEqual([])
})

test('keyboard Cancel retains every edit and explicit discard changes only inspection', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  await selectDrying(page)
  await heat(page).fill('19')
  await page.getByRole('button', { name: 'Select Sleep profile', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  await expect(heat(page)).toHaveValue('19')
  await page.getByRole('button', { name: 'Select Sleep profile', exact: true }).click()
  await dialog.getByTestId('dialog-discard').click()
  await expect(heat(page)).toHaveValue('')
  const state = await fixtureState(page)
  expect(state.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(profile(state, 3).periods[0]?.heating_setpoint).toBe(18)
  expect(profile(state, 4).parameters_configured).toBe(false)
  expect(state.mutations).toEqual([])
  expect(violations).toEqual([])
})

test('Save & Activate commits preparation before guarded activation and confirms the new tick', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  await selectDrying(page)
  await heat(page).fill('19')
  await page.getByTestId('activate-selected').click()
  await expect(page.getByRole('button', { name: 'Select Drying profile', exact: true }))
    .toHaveAttribute('data-profile-state', 'selected-active')
  const state = await fixtureState(page)
  const saved = state.mutations.find(row => row.path.endsWith('/apply') && row.success)!
  const activation = state.mutations.find(row => row.path.endsWith('/mode') && row.success)!
  expect(state.mutations.indexOf(saved)).toBeLessThan(state.mutations.indexOf(activation))
  expect(activation.expected_config_revision).toBe(saved.config_revision)
  expect(state.active).toEqual({ mode_id: 3, submode_id: null })
  expect(state.running).toEqual(state.active)
  expect(state.registry_version).toBeGreaterThan(9)
  expect(profile(state, 3).periods[0]?.heating_setpoint).toBe(19)
  await expect(page.getByTestId('activate-selected')).toBeDisabled()
  await page.screenshot({ path: info.outputPath('activation-confirmed.png') })
  expect(violations).toEqual([])
})

test('a revision conflict preserves preparation and never sends activation', async ({ page }, info) => {
  const violations = await openPreparation(page, info, 'mode-profile-conflict')
  await selectDrying(page)
  await heat(page).fill('19')
  const rejection = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/preview') && response.status() === 409)
  await page.getByTestId('activate-selected').click()
  await rejection
  await expect(page.getByTestId('activate-selected')).toBeDisabled()
  await expect(heat(page)).toHaveValue('19')
  const state = await fixtureState(page)
  expect(state.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(profile(state, 3).periods[0]?.heating_setpoint).toBe(18)
  expect(state.mutations.filter(row => row.path.endsWith('/mode'))).toEqual([])
  expect(violations).toEqual([])
})

test('failed activation retains the committed preparation and does not retry the write', async ({ page }, info) => {
  const violations = await openPreparation(page, info, 'mode-profile-activation-failed')
  await selectDrying(page)
  await heat(page).fill('19')
  const failed = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/mode') && response.status() === 503)
  await page.getByTestId('activate-selected').click()
  await failed
  await page.getByTestId('control-timeline-details').click()
  await expect(page.getByLabel('Timeline source and profile details').getByText(/Climate profile saved; activation failed or is unconfirmed:/)).toBeVisible()
  const state = await fixtureState(page)
  expect(profile(state, 3).periods[0]?.heating_setpoint).toBe(19)
  expect(state.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(state.mutations.filter(row => row.path.endsWith('/mode'))).toHaveLength(1)
  await expect(heat(page)).toHaveValue('19')
  expect(violations).toEqual([])
})

test('partial live light save commits climate and successful fixture while retaining failed edits', async ({ page }, info) => {
  const violations = await openPreparation(page, info, 'mode-profile-partial-light-save')
  const front = page.getByRole('spinbutton', { name: 'Chilled Front light target', exact: true })
  const apache = page.getByRole('spinbutton', { name: 'Apache light target', exact: true })
  await front.fill('55')
  await apache.fill('65')
  await heat(page).fill('23')
  await page.getByTestId('save-profile').click()
  await page.getByTestId('control-timeline-details').click()
  await expect(page.getByLabel('Timeline source and profile details').getByText(/Climate profile saved; light targets failed: light_f_2/)).toBeVisible()
  await expect(apache).toHaveValue('65')
  const state = await fixtureState(page)
  expect(profile(state, 2, 11).periods[0]?.heating_setpoint).toBe(23)
  expect(state.light_targets[JSON.stringify(['Flower Room', 'main', 'light_f_1', 2])]).toBe(55)
  expect(state.light_targets[JSON.stringify(['Flower Room', 'main', 'light_f_2', 2])]).not.toBe(65)
  expect(state.mutations.filter(row => row.path.endsWith('/light_f_1/target') && row.success)).toHaveLength(1)
  expect(state.mutations.filter(row => row.path.endsWith('/light_f_2/target') && !row.success)).toHaveLength(1)
  expect(violations).toEqual([])
})

test('inactive saving leaves staged live targets alone and cross-mode activation needs discard', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  const front = page.getByRole('spinbutton', { name: 'Chilled Front light target', exact: true })
  await front.fill('55')
  await selectDrying(page)
  await heat(page).fill('19')
  await page.getByTestId('save-profile').click()
  await expect.poll(async () => profile(await fixtureState(page), 3).periods[0]?.heating_setpoint).toBe(19)
  await expect(front).toHaveValue('55')
  await expect(page.getByTestId('activate-selected')).toBeDisabled()
  expect((await fixtureState(page)).mutations.filter(row => row.path.endsWith('/target'))).toEqual([])
  await page.getByRole('button', { name: 'Discard light edits', exact: true }).click()
  await expect(page.getByTestId('activate-selected')).toBeEnabled()
  await page.getByTestId('activate-selected').click()
  await expect(page.getByRole('button', { name: 'Select Drying profile', exact: true })).toHaveAttribute('data-profile-state', 'selected-active')
  const stale = await page.request.post(`/api/lights/Flower%20Room/main/light_f_1/target${fixtureQuery(page)}`, {
    data: { target_intensity: 88, expected_mode_id: 2 },
  })
  expect(stale.status()).toBe(409)
  expect((await fixtureState(page)).light_targets[JSON.stringify(['Flower Room', 'main', 'light_f_1', 2])]).not.toBe(88)
  expect(violations).toEqual([])
})

test('same-mode submode activation retains shared pending light targets', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  const front = page.getByRole('spinbutton', { name: 'Chilled Front light target', exact: true })
  await front.fill('55')
  await page.getByRole('button', { name: 'Select Flower Stretch profile', exact: true }).click()
  await expect(heat(page)).toHaveValue('24')
  await expect(page.getByTestId('activate-selected')).toBeEnabled()
  await page.getByTestId('activate-selected').click()
  await expect(page.getByRole('button', { name: 'Select Flower Stretch profile', exact: true })).toHaveAttribute('data-profile-state', 'selected-active')
  await expect(front).toHaveValue('55')
  await expect(page.getByRole('button', { name: 'Save light targets', exact: true })).toBeEnabled()
  expect((await fixtureState(page)).mutations.filter(row => row.path.endsWith('/target'))).toEqual([])
  expect(violations).toEqual([])
})

test('unconfigured all-NULL Sleep becomes persisted only on Save without a numeric forecast', async ({ page }, info) => {
  const violations = await openPreparation(page, info)
  await page.getByRole('button', { name: 'Select Sleep profile', exact: true }).click()
  await expect(heat(page)).toHaveValue('')
  const before = await fixtureState(page)
  expect(profile(before, 4).parameters_configured).toBe(false)
  expect(profile(before, 4).periods).toEqual([])
  await page.getByTestId('save-profile').click()
  await expect.poll(async () => profile(await fixtureState(page), 4).parameters_configured).toBe(true)
  const after = await fixtureState(page)
  expect(profile(after, 4).periods.map(row => [row.start_time, row.end_time, row.heating_setpoint])).toEqual([['00:00', '00:00', null]])
  expect(after.active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(violations).toEqual([])
})

test('mismatched running forecast leaves actual identity and inactive preparation usable', async ({ page }, info) => {
  const violations = await openPreparation(page, info, 'mode-profile-projection-stale')
  await selectDrying(page)
  await heat(page).fill('19')
  await page.getByTestId('save-profile').click()
  await expect.poll(async () => profile(await fixtureState(page), 3).periods[0]?.heating_setpoint).toBe(19)
  await expect(page.getByRole('button', { name: 'Select Flower Bulk profile', exact: true })).toHaveAttribute('data-profile-state', 'active')
  expect((await fixtureState(page)).active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(violations).toEqual([])
})

test('late preview and old-window reads cannot replace newer preparation', async ({ page }, info) => {
  const violations = await openPreparation(page, info, 'mode-profile-stale-response')
  await selectDrying(page)
  await page.getByRole('button', { name: 'Expand editor', exact: true }).click()
  const rolling = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/profile') &&
    new URL(request.url()).searchParams.get('start') !== new Date(new Date().setUTCHours(0, 0, 0, 0)).toISOString())
  await page.getByRole('button', { name: 'rolling', exact: true }).click()
  await rolling
  await page.getByRole('button', { name: 'daily', exact: true }).click()
  const oldPreview = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/preview') &&
    response.request().postDataJSON()?.periods?.[0]?.heating_setpoint === 19)
  const oldRequest = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/preview') &&
    request.postDataJSON()?.periods?.[0]?.heating_setpoint === 19)
  await heat(page).fill('19')
  await page.getByRole('button', { name: 'Review', exact: true }).click()
  await oldRequest
  await heat(page).fill('20')
  await oldPreview
  await expect(heat(page)).toHaveValue('20')
  await expect(page.getByRole('button', { name: 'Apply', exact: true })).toBeEnabled()
  await page.getByTestId('save-profile').click()
  await expect.poll(async () => profile(await fixtureState(page), 3).periods[0]?.heating_setpoint).toBe(20)
  expect((await fixtureState(page)).active).toEqual({ mode_id: 2, submode_id: 11 })
  expect(violations).toEqual([])
})
