import { expect, test } from '@playwright/test'
import type { Locator, Page, TestInfo, WebSocketRoute } from '@playwright/test'

import { FIXTURE_ORIGIN, FIXTURE_WS_ORIGIN } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'
import { DASHBOARD_DEVICES, DASHBOARD_LIGHT_INTENSITIES } from './dashboardDeviceFixtures'

const NOW = new Date('2026-08-15T12:00:00.000Z')

function sensorMessage(value: number | null): string {
  return JSON.stringify({
    type: 'sensor_update', location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb_v',
    value, time: NOW.toISOString(),
  })
}

function visibleText(row: Locator, text: string | RegExp): Locator {
  return row.getByText(text, { exact: typeof text === 'string' }).filter({ visible: true })
}

test.describe('guarded dashboard refactor parity', () => {
  const sockets = new Set<WebSocketRoute>()
  let blockedHttp: string[]
  let blockedWebSockets: string[]
  let errors: string[]

  test.beforeEach(async ({ context, page }) => {
    if (process.env.BASE_URL) throw new Error('Only an owned guarded loopback fixture is allowed')
    sockets.clear()
    blockedHttp = []
    blockedWebSockets = []
    errors = []
    page.on('pageerror', error => errors.push(error.message))
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      const method = request.method()
      if (url.origin !== FIXTURE_ORIGIN || !(
        method === 'GET' || method === 'HEAD' ||
        (method === 'POST' && url.pathname === '/api/sensor-data')
      )) {
        blockedHttp.push(`${method} ${url}`)
        await route.abort()
        return
      }
      const live = /^\/api\/sensors\/([^/]+)\/([^/]+)\/live$/.exec(url.pathname)
      if (live) {
        const location = decodeURIComponent(live[1]!)
        const cluster = decodeURIComponent(live[2]!)
        const suffix = location === 'Flower Room' ? (cluster === 'front' ? '_f' : '_b') : '_v'
        const sensor = location === 'Lab' ? 'lab_temp' : `dry_bulb${suffix}`
        await route.fulfill({ json: {
          [sensor]: { sensor_type: sensor, location, cluster, unit: '°C',
            data: [{ value: 21, timestamp: NOW.toISOString() }] },
        } })
        return
      }
      await route.continue()
    })
    await context.routeWebSocket('**/*', socket => {
      if (new URL(socket.url()).origin !== FIXTURE_WS_ORIGIN) {
        blockedWebSockets.push(socket.url())
        socket.close()
        return
      }
      // No connectToServer: every sensor frame is an in-process fixture.
      sockets.add(socket)
      socket.onClose(() => sockets.delete(socket))
    })
    await page.clock.install({ time: new Date(NOW.getTime() - 60_000) })
  })

  test.afterEach(async ({ page }, testInfo) => {
    await testInfo.attach('network-and-page-errors', {
      body: JSON.stringify({ blockedHttp, blockedWebSockets, errors }),
      contentType: 'application/json',
    })
    await page.screenshot({ path: testInfo.outputPath('surface.png') })
    expect(blockedHttp).toEqual([])
    expect(blockedWebSockets).toEqual([])
    expect(errors).toEqual([])
    sockets.clear()
  })

  async function start(page: Page, testInfo: TestInfo) {
    await page.goto(fixtureUrl('/', testInfo, 'sensor-refactor'))
    await expect.poll(() => sockets.size).toBeGreaterThan(0)
    const veg = page.locator('.dashboard-zone-row').filter({ hasText: 'Vegetation Room' })
    await expect(visibleText(veg, '21.00°C')).toBeVisible()
    await page.clock.pauseAt(NOW)
    const viewports: Record<string, { width: number; height: number }> = {
      'chromium-functional-1920x1080': { width: 1920, height: 1080 },
      'chromium-functional-1280x1440': { width: 1280, height: 1440 },
    }
    expect(page.viewportSize()).toEqual(viewports[testInfo.project.name])
    for (const href of ['/zone/Flower%20Room/main', '/zone/Veg%20Room/main', '/zone/Lab/main']) {
      await expect(page.locator(`.dashboard-zone-row[href="${href}"]`)).toBeInViewport()
    }
    const rail = page.locator('aside').filter({ has: page.locator('nav[aria-label="Primary navigation"]') })
    await expect(rail).toBeVisible()
    const width = await rail.evaluate(element => element.getBoundingClientRect().width)
    expect(Math.abs(width - 30)).toBeLessThanOrEqual(0.5)
    await expect(page.locator('.dashboard-zone-row')).toHaveCount(3)
    for (const device of DASHBOARD_DEVICES) {
      const room = page.locator('.dashboard-zone-row')
        .filter({ hasText: device.location === 'Veg Room' ? 'Vegetation Room' : device.location })
      const name = visibleText(room, device.display_name)
      await expect(name).toBeVisible()
      const intensity = DASHBOARD_LIGHT_INTENSITIES[
        `${device.location}_${device.cluster}_${device.device_name}_intensity`
      ]
      await expect(name.locator('..')).toContainText(`${intensity}%`)
      expect(await name.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    }
    const flower = page.locator('.dashboard-zone-row').filter({ hasText: 'Flower Room' })
    const lab = page.locator('.dashboard-zone-row[href="/zone/Lab/main"]')
    const labBox = await lab.boundingBox()
    const flowerBox = await flower.boundingBox()
    const vegBox = await veg.boundingBox()
    expect(labBox!.height).toBeLessThanOrEqual(Math.min(flowerBox!.height, vegBox!.height) / 2)
    await expect(visibleText(lab, /AUTO|Decision|Setpoints|heater|CO₂|VPD|RH|Δ10m/)).toHaveCount(0)
    const inspector = page.getByRole('complementary', { name: 'Calendar inspector' })
    for (const label of [/^New event$/i, /^Create flower grow plan$/i]) {
      const button = inspector.getByRole('button', { name: label })
      await expect(button).toBeVisible()
      const box = await button.boundingBox()
      expect(box!.width).toBeGreaterThanOrEqual(100)
      expect(box!.height).toBeLessThanOrEqual(80)
    }
    const shell = await page.evaluate(() => {
      return {
        calendar: document.querySelector('.dashboard-calendar-slot')!.getBoundingClientRect().toJSON(),
        inspector: document.querySelector('.dashboard-inspector-slot')!.getBoundingClientRect().toJSON(),
        footer: document.querySelector('button[aria-label="Open mothernode status"]')!
          .parentElement!.getBoundingClientRect().toJSON(),
        rows: [...document.querySelectorAll('.dashboard-zone-row')].map(row => row.getBoundingClientRect().toJSON()),
        overflow: document.documentElement.scrollWidth > innerWidth,
      }
    })
    expect(shell.overflow).toBe(false)
    for (const row of shell.rows) expect(row.bottom).toBeLessThanOrEqual(shell.footer.top + 1)
    if (page.viewportSize()!.width === 1280) {
      expect(shell.inspector.top).toBeGreaterThanOrEqual(shell.calendar.bottom - 1)
    } else {
      expect(shell.inspector.left).toBeGreaterThanOrEqual(shell.calendar.right - 1)
    }
    return veg
  }

  test('zero is valid and later null selects a valid polling fallback', async ({ page }, testInfo) => {
    const veg = await start(page, testInfo)
    for (const socket of sockets) socket.send(sensorMessage(0))
    await expect(visibleText(veg, '0.00°C')).toBeVisible()
    await expect(visibleText(veg, /LIVE · \d+s · WS/)).toBeVisible()
    await expect(visibleText(veg, /BAD VALUE|STALE/)).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('valid-zero.png') })
    for (const socket of sockets) socket.send(sensorMessage(null))
    await expect(visibleText(veg, '21.00°C')).toBeVisible()
    await expect(visibleText(veg, /LIVE · \d+s · POLL/)).toBeVisible()
    await expect(visibleText(veg, '0.00°C')).toHaveCount(0)
    await expect(visibleText(veg, /BAD VALUE|STALE/)).toHaveCount(0)
  })

  test('old observation timestamps become stale on the existing clock and fall back to polling', async ({ page }, testInfo) => {
    const veg = await start(page, testInfo)
    for (const socket of sockets) socket.send(sensorMessage(5))
    await expect(visibleText(veg, '5.00°C')).toBeVisible()
    await page.clock.runFor(45_000)
    await expect(visibleText(veg, '5.00°C')).toBeVisible()
    await expect(visibleText(veg, /BAD VALUE|STALE/)).toHaveCount(0)
    await page.clock.runFor(1_000)
    await expect(visibleText(veg, '21.00°C')).toBeVisible()
    await expect(visibleText(veg, /STALE · \d+s · POLL/)).toBeVisible()
    for (const socket of sockets) socket.send(sensorMessage(null))
    await expect(visibleText(veg, '21.00°C')).toBeVisible()
    await expect(visibleText(veg, /STALE · \d+s · POLL/)).toBeVisible()
    await expect(visibleText(veg, /BAD VALUE/)).toHaveCount(0)
  })
})
