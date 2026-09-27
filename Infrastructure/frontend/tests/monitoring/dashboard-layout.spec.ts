import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { eventHistoryFixture } from '../../src/features/event-log/config/fixtures'
import { FIXTURE_ORIGIN, FIXTURE_WS_ORIGIN } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const FIXED_NOW = new Date('2026-08-15T12:00:00.000Z')
const LONG_NOTE = Array.from(
  { length: 10 },
  (_, index) =>
    `Calibration pass ${index + 1}: confirm the intake screen is clear, compare the probe with the reference meter, and record the observed room conditions before adjusting any equipment.`
).join('\n')

const CALENDAR_ITEMS = [
  {
    id: 901,
    source: 'manual',
    event_type: 'planned_task',
    title: 'August room inspection',
    start_date: '2026-08-15',
    end_date: null,
    location: 'Lab',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
  {
    id: 902,
    source: 'manual',
    event_type: 'planned_task',
    title: 'August filter check',
    start_date: '2026-08-15',
    end_date: null,
    location: 'Flower Room',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
  {
    id: 903,
    source: 'manual',
    event_type: 'planned_task',
    title: 'Long note event',
    start_date: '2026-09-10',
    end_date: null,
    location: 'Veg Room',
    cluster: 'main',
    editable: true,
    notes: LONG_NOTE,
    deleted_at: null,
  },
  {
    id: 904,
    source: 'manual',
    event_type: 'planned_task',
    title: 'September follow-up task',
    start_date: '2026-09-10',
    end_date: null,
    location: 'Lab',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
  {
    id: 905,
    source: 'manual',
    event_type: 'planned_task',
    title: 'September follow-up date',
    start_date: '2026-09-11',
    end_date: null,
    location: 'Lab',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
  {
    id: 906,
    source: 'manual',
    event_type: 'planned_task',
    title: 'September nutrient check',
    start_date: '2026-09-10',
    end_date: null,
    location: 'Flower Room',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
  {
    id: 907,
    source: 'manual',
    event_type: 'planned_task',
    title: 'September sensor inspection',
    start_date: '2026-09-10',
    end_date: null,
    location: 'Veg Room',
    cluster: 'main',
    editable: true,
    notes: null,
    deleted_at: null,
  },
]

function eventHistoryWithFiftyRows() {
  const template = eventHistoryFixture('grouped-console').items[0]!
  const nowMs = FIXED_NOW.getTime()
  const items = Array.from({ length: 50 }, (_, index) => {
    const isLast = index === 49
    return {
      redis_id: `${nowMs - index * 1_000}-${index}`,
      event: {
        ...template.event,
        event_id: `dashboard-layout-${index}`,
        occurred_at: new Date(nowMs - index * 60_000).toISOString(),
        category: 'system',
        event_type: 'system.dashboard_layout_fixture',
        reason_text: isLast ? 'DASHBOARD_LAYOUT_LAST_ROW' : `Dashboard fixture event ${index + 1}`,
        entity: {
          entity_type: 'service',
          entity_id: `dashboard-layout-${index}`,
          location: 'Lab',
          cluster: 'main',
        },
        payload: { room: 'Lab', cluster: 'main' },
      },
    }
  })

  const ninthCategory = {
    redis_id: `${nowMs - 60_000_000}-dashboard-ninth`,
    event: {
      ...template.event,
      event_id: 'dashboard-layout-ninth-category',
      occurred_at: new Date(nowMs - 60_000_000).toISOString(),
      category: 'dashboard_ninth_category',
      event_type: 'dashboard.ninth_category_fixture',
      reason_text: 'DASHBOARD_NINTH_CATEGORY',
      entity: {
        entity_type: 'service',
        entity_id: 'dashboard-ninth-category',
        location: 'Lab',
        cluster: 'main',
      },
      payload: { room: 'Lab', cluster: 'main' },
    },
  }

  return {
    items: [...items, ninthCategory],
    newest_cursor: items[0]!.redis_id,
    oldest_cursor: ninthCategory.redis_id,
    earliest_cursor: ninthCategory.redis_id,
    has_more: false,
    scan: { scanned: items.length + 1, limit: 500 },
  }
}
async function calendarGeometry(page: Page) {
  return page.evaluate(() => {
    const panel = document.querySelector('.grow-calendar--dashboard .grow-cal-panel')
    const table = document.querySelector('.grow-calendar--dashboard .rdp-month_grid')
    if (!panel || !table) throw new Error('Dashboard calendar panel or month table is missing')
    const panelRect = panel.getBoundingClientRect()
    const tableRect = table.getBoundingClientRect()
    const weekHeights = Array.from(table.querySelectorAll('.rdp-week')).map(
      row => row.getBoundingClientRect().height
    )
    return {
      panelHeight: panelRect.height,
      tableBottomGap: panelRect.bottom - tableRect.bottom,
      weekHeights,
    }
  })
}

test('dashboard fits both supported desktop viewports without overflow', async ({
  page,
  context,
}, testInfo) => {
  test.skip(
    Boolean(process.env.BASE_URL),
    'This layout regression requires the isolated fixture preview'
  )

  const viewport = page.viewportSize()
  expect(viewport).not.toBeNull()
  const expectedViewports = {
    'chromium-functional-1920x1080': { width: 1920, height: 1080 },
    'chromium-functional-1280x1440': { width: 1280, height: 1440 },
  }
  expect(expectedViewports[testInfo.project.name as keyof typeof expectedViewports]).toBeDefined()
  expect(viewport).toEqual(
    expectedViewports[testInfo.project.name as keyof typeof expectedViewports]
  )

  const blockedHttp: string[] = []
  const blockedWebSockets: string[] = []
  const eventHistory = eventHistoryWithFiftyRows()

  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method().toUpperCase()
    if (url.origin !== FIXTURE_ORIGIN) {
      blockedHttp.push(`${method} ${url.toString()}`)
      await route.abort()
      return
    }

    const readOnly = method === 'GET' || method === 'HEAD' || method === 'OPTIONS'
    const fixtureBulkRead = method === 'POST' && url.pathname === '/api/sensor-data'
    if (!readOnly && !fixtureBulkRead) {
      blockedHttp.push(`${method} ${url.pathname}`)
      await route.abort()
      return
    }

    if (method === 'GET' && url.pathname === '/api/calendar/events') {
      await route.fulfill({ json: { items: CALENDAR_ITEMS, next_cursor: null } })
      return
    }
    if (method === 'GET' && url.pathname === '/api/events/history') {
      await route.fulfill({ json: eventHistory })
      return
    }
    if (method === 'GET' && url.pathname === '/api/alarms') {
      await route.fulfill({ json: { alarms: [] } })
      return
    }
    if (method === 'GET' && url.pathname === '/api/schedules') {
      await route.fulfill({ json: [] })
      return
    }
    if (method === 'GET' && url.pathname === '/api/status') {
      const health = url.searchParams.get('health') === 'true'
      await route.fulfill({
        json: health
          ? {
              service_health: Array.from({ length: 14 }, (_, index) => ({
                name: `fixture-service-${index + 1}`,
                status: index === 12 ? 'stopped' : 'running',
                latency_ms: index === 12 ? undefined : 8 + index,
              })),
            }
          : {
              system: {
                cpu_percent: 15,
                memory_percent: 42,
                disk_percent: 28,
                uptime_seconds: 86400,
                cpu_temp_c: 45,
                throttle_status: '0x0',
              },
              devices: {},
              degraded: {
                active: true,
                reason: 'Dashboard layout fixture',
                failure_count: 1,
                success_count: 2,
              },
            },
      })
      return
    }

    await route.continue()
  })

  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url())
    const origin = `${url.protocol}//${url.host}`
    if (origin !== FIXTURE_WS_ORIGIN) {
      blockedWebSockets.push(url.toString())
      socket.close()
      return
    }
    socket.connectToServer()
  })

  await page.clock.setFixedTime(FIXED_NOW)
  const startUrl = new URL(
    fixtureUrl('/', testInfo, 'dashboard-layout', 'disconnect'),
    FIXTURE_ORIGIN
  )
  await page.goto(startUrl.toString())
  await expect(page.getByText('Siberian Jungle')).toBeVisible()
  await expect(page.getByText('Control loop degraded: Dashboard layout fixture')).toBeVisible()
  await expect(page.getByText('fixture-service-14')).toBeVisible()
  await expect(page.locator('.grow-calendar--dashboard .rdp-month_caption')).toContainText(
    /ao[uû]t 2026/i
  )
  await expect(page.locator('.grow-calendar--dashboard .rdp-week')).toHaveCount(6)
  await expect(page.locator('.grow-calendar--dashboard .rdp-day_button')).toHaveCount(42)

  await page.screenshot({ path: testInfo.outputPath('dashboard-initial.png') })
  const initialCalendar = await calendarGeometry(page)
  expect(initialCalendar.tableBottomGap).toBeGreaterThanOrEqual(-1)
  expect(initialCalendar.tableBottomGap).toBeLessThan(12)
  expect(
    Math.max(...initialCalendar.weekHeights) - Math.min(...initialCalendar.weekHeights)
  ).toBeLessThanOrEqual(2.2)
  await expect(page.locator('.grow-calendar--dashboard .rdp-day_button').first()).toHaveCSS(
    'min-height',
    '44px'
  )

  const shell = await page.evaluate(() => {
    const navigation = document.querySelector('#primary-navigation')
    const sidebar = navigation?.closest('aside')
    const dashboard = document.querySelector('.main-dashboard')
    const ribbon = document.querySelector('.dashboard-ribbon')
    if (!sidebar || !dashboard || !ribbon) return null
    const sidebarRect = sidebar.getBoundingClientRect()
    const dashboardRect = dashboard.getBoundingClientRect()
    return {
      sidebar: { x: sidebarRect.x, width: sidebarRect.width },
      dashboard: { x: dashboardRect.x, width: dashboardRect.width },
      ribbonPosition: getComputedStyle(ribbon).position,
    }
  })
  expect(shell).not.toBeNull()
  expect(shell!.sidebar).toEqual({ x: 0, width: 30 })
  expect(shell!.dashboard.x).toBe(30)
  expect(shell!.ribbonPosition).toBe('static')
  await expect(page.getByRole('button', { name: /sidebar/i })).toHaveCount(0)

  const flowerRow = page.locator('a[href="/zone/Flower%20Room/main"]')
  const vegRow = page.locator('a[href="/zone/Veg%20Room/main"]')
  const labRow = page.locator('a[href="/zone/Lab/main"]')
  await expect(flowerRow).toContainText('Mode: Sleep')
  await expect(vegRow).toContainText('Mode: Veg')
  await expect(labRow).not.toContainText(
    /Mode:|No scheduled transition|TEMP IN BAND|AUTO|Setpoints/
  )
  await expect(labRow).toContainText('heater-1')
  await expect(flowerRow).toBeInViewport()
  await expect(vegRow).toBeInViewport()
  await expect(labRow).toBeInViewport()
  await expect(page.locator('.dashboard-water-panel [role="img"]')).toBeInViewport()
  await expect(page.locator('button[aria-label="Open mothernode status"]')).toBeInViewport()

  const metrics = await page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector)
      if (!element) return null
      const { x, y, width, height, top, right, bottom, left } = element.getBoundingClientRect()
      return { x, y, width, height, top, right, bottom, left }
    }
    const root = box('.main-dashboard')
    const names = {
      calendar: '.grow-calendar--dashboard',
      calendarTrack: '.dashboard-calendar-slot',
      inspector: '.dashboard-inspector',
      inspectorTrack: '.dashboard-inspector-slot',
      upper: '.dashboard-upper',
      rooms: '.dashboard-room-rows',
      center: '.dashboard-center',
      rail: '.dashboard-rail',
      events: '.dashboard-event-log-panel',
      water: '.dashboard-water-panel',
      waterGraphic: '.dashboard-water-panel [role="img"]',
      footer: 'button[aria-label="Open mothernode status"]',
    }
    const boxes = Object.fromEntries(
      Object.entries(names).map(([name, selector]) => [name, box(selector)])
    )
    const documentElement = document.documentElement
    const rail = document.querySelector<HTMLElement>('.dashboard-rail')
    const railOverflow = rail
      ? {
          scrollHeight: rail.scrollHeight,
          clientHeight: rail.clientHeight,
          scrollWidth: rail.scrollWidth,
          clientWidth: rail.clientWidth,
        }
      : null
    const scrollContainers = Array.from(document.querySelectorAll<HTMLElement>('.main-dashboard *'))
      .filter(element => {
        const style = getComputedStyle(element)
        return (
          ['auto', 'scroll'].includes(style.overflowX) ||
          ['auto', 'scroll'].includes(style.overflowY)
        )
      })
      .map(element => ({
        tag: element.tagName,
        className: typeof element.className === 'string' ? element.className : '',
        overflowX: getComputedStyle(element).overflowX,
        overflowY: getComputedStyle(element).overflowY,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }))
    const outsideControls = Array.from(
      document.querySelectorAll<HTMLElement>(
        '.main-dashboard button, .main-dashboard a, .main-dashboard input, .main-dashboard select'
      )
    )
      .filter(element => element.getClientRects().length > 0)
      .map(element => {
        const rect = element.getBoundingClientRect()
        return {
          label:
            element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName,
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        }
      })
      .filter(
        item =>
          !root ||
          item.left < root.left - 1 ||
          item.right > root.right + 1 ||
          item.top < root.top - 1 ||
          item.bottom > root.bottom + 1
      )
    return {
      viewport: { width: window.innerWidth, height: documentElement.clientHeight },
      document: {
        width: documentElement.scrollWidth,
        height: documentElement.scrollHeight,
        clientWidth: documentElement.clientWidth,
        clientHeight: documentElement.clientHeight,
        scrollY: window.scrollY,
      },
      root,
      boxes,
      railOverflow,
      scrollContainers,
      outsideControls,
    }
  })

  expect(metrics.boxes.calendar).not.toBeNull()
  expect(metrics.boxes.calendarTrack).not.toBeNull()
  expect(metrics.boxes.inspector).not.toBeNull()
  expect(metrics.boxes.inspectorTrack).not.toBeNull()
  expect(metrics.boxes.upper).not.toBeNull()
  expect(metrics.boxes.rooms).not.toBeNull()
  expect(metrics.boxes.center).not.toBeNull()
  expect(metrics.boxes.rail).not.toBeNull()
  expect(metrics.boxes.events).not.toBeNull()
  expect(metrics.boxes.water).not.toBeNull()
  expect(metrics.boxes.waterGraphic).not.toBeNull()
  expect(metrics.boxes.footer).not.toBeNull()
  expect(metrics.railOverflow).not.toBeNull()
  expect(metrics.scrollContainers).toEqual([])
  expect(metrics.outsideControls).toEqual([])
  expect(metrics.document.width).toBeLessThanOrEqual(metrics.document.clientWidth + 1)
  expect(metrics.document.height).toBeLessThanOrEqual(metrics.document.clientHeight + 1)
  expect(metrics.document.scrollY).toBe(0)
  expect(metrics.root!.x).toBe(30)
  expect(metrics.root!.right).toBeLessThanOrEqual(metrics.viewport.width + 1)
  expect(metrics.boxes.center!.right).toBeLessThanOrEqual(metrics.boxes.rail!.left + 1)
  expect(metrics.boxes.calendar!.top).toBeCloseTo(metrics.boxes.inspector!.top, 0)
  expect(metrics.boxes.calendar!.right).toBeLessThanOrEqual(metrics.boxes.inspector!.left + 1)
  expect(
    metrics.boxes.inspectorTrack!.left - metrics.boxes.calendarTrack!.right
  ).toBeCloseTo(8, 0)
  const upperTrackRatio =
    metrics.boxes.calendarTrack!.width / metrics.boxes.inspectorTrack!.width
  expect(upperTrackRatio).toBeGreaterThan(3.8)
  expect(upperTrackRatio).toBeLessThan(4.2)
  expect(metrics.boxes.inspectorTrack!.width).toBeGreaterThanOrEqual(
    viewport!.width >= 1600 ? 16 * 16 : 12 * 16
  )
  const centerTrackRatio = metrics.boxes.upper!.height / metrics.boxes.rooms!.height
  expect(centerTrackRatio).toBeCloseTo(viewport!.width >= 1600 ? 55 / 45 : 52 / 48, 1)
  expect(metrics.boxes.rooms!.top).toBeGreaterThanOrEqual(metrics.boxes.upper!.bottom - 1)
  expect(metrics.boxes.rooms!.left).toBeGreaterThanOrEqual(metrics.boxes.center!.left - 1)
  expect(metrics.boxes.rooms!.right).toBeLessThanOrEqual(metrics.boxes.center!.right + 1)
  expect(metrics.boxes.rooms!.width).toBeGreaterThanOrEqual(metrics.boxes.center!.width - 1)
  expect(metrics.boxes.rooms!.height).toBeGreaterThanOrEqual(metrics.boxes.center!.height * 0.38)
  expect(metrics.boxes.events!.bottom).toBeLessThanOrEqual(metrics.boxes.water!.top + 1)
  expect(metrics.boxes.events!.left).toBeGreaterThanOrEqual(metrics.boxes.rail!.left - 1)
  expect(metrics.boxes.events!.right).toBeLessThanOrEqual(metrics.boxes.rail!.right + 1)
  expect(metrics.boxes.water!.bottom).toBeLessThanOrEqual(metrics.boxes.footer!.top + 1)
  expect(metrics.boxes.water!.left).toBeGreaterThanOrEqual(metrics.boxes.rail!.left - 1)
  expect(metrics.boxes.water!.right).toBeLessThanOrEqual(metrics.boxes.rail!.right + 1)
  expect(
    metrics.boxes.waterGraphic!.left
  ).toBeGreaterThanOrEqual(metrics.boxes.water!.left - 1)
  expect(metrics.boxes.waterGraphic!.right).toBeLessThanOrEqual(metrics.boxes.water!.right + 1)
  expect(metrics.boxes.waterGraphic!.top).toBeGreaterThanOrEqual(metrics.boxes.water!.top - 1)
  expect(metrics.boxes.waterGraphic!.bottom).toBeLessThanOrEqual(metrics.boxes.water!.bottom + 1)
  expect(
    Math.abs(metrics.boxes.waterGraphic!.width - metrics.boxes.waterGraphic!.height)
  ).toBeLessThanOrEqual(1)
  expect(metrics.railOverflow!.scrollHeight).toBeLessThanOrEqual(
    metrics.railOverflow!.clientHeight + 1
  )
  expect(metrics.railOverflow!.scrollWidth).toBeLessThanOrEqual(metrics.railOverflow!.clientWidth + 1)

  const roomBoxes = await Promise.all([flowerRow, vegRow, labRow].map(row => row.boundingBox()))
  expect(roomBoxes.every(box => box !== null)).toBe(true)
  expect(roomBoxes[0]!.height / roomBoxes[1]!.height).toBeCloseTo(1.5, 1)
  expect(roomBoxes[2]!.height / roomBoxes[1]!.height).toBeCloseTo(0.55, 1)
  for (let first = 0; first < roomBoxes.length; first += 1) {
    const current = roomBoxes[first]!
    expect(current.x).toBeGreaterThanOrEqual(metrics.boxes.center!.left - 1)
    expect(current.x + current.width).toBeLessThanOrEqual(metrics.boxes.center!.right + 1)
    expect(current.x + current.width).toBeGreaterThanOrEqual(metrics.boxes.center!.right - 1)
    if (first === 0) {
      expect(current.y).toBeGreaterThanOrEqual(metrics.boxes.rooms!.top - 1)
    } else {
      const previous = roomBoxes[first - 1]!
      expect(previous.y + previous.height).toBeLessThanOrEqual(current.y + 1)
    }
  }
  expect(roomBoxes[2]!.y + roomBoxes[2]!.height).toBeLessThanOrEqual(
    metrics.boxes.rooms!.bottom + 1
  )
  const initialPanels = [
    metrics.boxes.calendar!,
    metrics.boxes.inspector!,
    metrics.boxes.rooms!,
    metrics.boxes.events!,
    metrics.boxes.water!,
    metrics.boxes.footer!,
  ]
  for (let first = 0; first < initialPanels.length; first += 1) {
    for (let second = first + 1; second < initialPanels.length; second += 1) {
      const a = initialPanels[first]!
      const b = initialPanels[second]!
      const overlaps =
        a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1
      expect(overlaps).toBe(false)
    }
  }

  await page.locator('.rdp-button_next').click()
  await expect(page.locator('.grow-calendar--dashboard .rdp-month_caption')).toContainText(
    /septembre 2026/i
  )
  await expect(page.locator('.grow-calendar--dashboard .rdp-week')).toHaveCount(6)
  await expect(page.locator('.grow-calendar--dashboard .rdp-day_button')).toHaveCount(42)
  const september = await calendarGeometry(page)
  expect(september.tableBottomGap).toBeGreaterThanOrEqual(-1)
  expect(september.tableBottomGap).toBeLessThan(12)
  expect(
    Math.max(...september.weekHeights) - Math.min(...september.weekHeights)
  ).toBeLessThanOrEqual(2.2)
  await expect(page.locator('.rdp-day_button[aria-label*="4 tasks"]')).toHaveCount(1)

  const septemberTenth = page.locator('.rdp-day_button[aria-label*="10 septembre 2026"]')
  await expect(septemberTenth).toHaveCount(1)
  await septemberTenth.click()
  const allDayEvents = page.getByRole('button', { name: 'All 4 events' })
  await expect(allDayEvents).toBeVisible()
  await allDayEvents.click()
  const inspectorDialog = page.getByRole('dialog')
  await expect(inspectorDialog.getByRole('button', { name: /Long note event/ })).toBeVisible()
  await inspectorDialog.getByRole('button', { name: /Long note event/ }).click()
  await expect(inspectorDialog.getByText(/Calibration pass 10:/)).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('dashboard-selected-detail.png'),
    fullPage: true,
  })
  await inspectorDialog.getByRole('button', { name: 'Close' }).click()
  await expect(inspectorDialog.getByRole('button', { name: /Long note event/ })).toBeFocused()
  await inspectorDialog.getByRole('button', { name: 'Close' }).click()
  await expect(allDayEvents).toBeFocused()

  const septemberEleventh = page.locator('.rdp-day_button[aria-label*="11 septembre 2026"]')
  await septemberEleventh.click()
  const newEventButton = page.getByRole('button', { name: 'New event on this date' })
  await newEventButton.click()
  await expect(page.getByRole('dialog', { name: 'New event' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Title' })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(newEventButton).toBeFocused()

  const groupedStatus = page.getByTestId('event-groups-page-status')
  await expect(groupedStatus).toHaveText('Groups 1 of 2 · 9 categories')
  await page.getByTestId('event-group-system').click()
  const eventStatus = page.getByTestId('event-events-page-status')
  await expect(eventStatus).toHaveText('Page 1 of 10 · 50 events')
  for (let pageNumber = 2; pageNumber <= 10; pageNumber += 1) {
    await page.getByRole('button', { name: 'Next events page' }).click()
  }
  await expect(eventStatus).toHaveText('Page 10 of 10 · 50 events')
  const eventList = page.getByRole('list', { name: 'Event list' })
  const sentinelRow = eventList
    .getByRole('listitem')
    .filter({ hasText: 'DASHBOARD_LAYOUT_LAST_ROW' })
  await expect(sentinelRow).toBeVisible()
  await sentinelRow.getByTestId('event-detail-opener').click()
  const eventDialog = page.getByRole('dialog')
  await expect(eventDialog).toContainText('DASHBOARD_LAYOUT_LAST_ROW')
  await expect(eventDialog).toContainText('Severity:')
  await expect(eventDialog).toContainText('service dashboard-layout-49')
  await expect(eventDialog).toContainText('07:11:00')
  await page.screenshot({
    path: testInfo.outputPath('dashboard-event-detail.png'),
    fullPage: true,
  })
  await eventDialog.getByRole('button', { name: 'Close' }).click()
  await expect(sentinelRow.getByTestId('event-detail-opener')).toBeFocused()

  await page.getByTestId('event-group-collapse').click()
  await expect(groupedStatus).toHaveText('Groups 1 of 2 · 9 categories')
  await page.getByRole('button', { name: 'Next category page' }).click()
  await expect(groupedStatus).toHaveText('Groups 2 of 2 · 9 categories')
  await expect(page.getByTestId('event-group-dashboard_ninth_category')).toBeVisible()
  await page.getByRole('button', { name: 'Previous category page' }).click()

  if (!(await page.getByRole('button', { name: 'All events' }).isVisible())) {
    await page.getByRole('button', { name: /^Filters/ }).click()
  }
  await page.getByRole('button', { name: 'All events' }).click()
  await expect(eventStatus).toHaveText('Page 1 of 11 · 51 events')

  const filterSearch = page.getByRole('searchbox', { name: 'Filter events' })
  if (!(await filterSearch.isVisible())) {
    await page.getByRole('button', { name: /^Filters/ }).click()
  }
  await filterSearch.fill('dashboard-layout-49')
  await expect(eventStatus).toHaveText('Page 1 of 1 · 1 event')
  await filterSearch.fill('')
  await expect(eventStatus).toHaveText('Page 1 of 11 · 51 events')
  await page.keyboard.press('Escape')

  const finalMetrics = await page.evaluate(() => ({
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: document.documentElement.clientHeight,
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    scrollY: window.scrollY,
  }))
  expect(finalMetrics.documentWidth).toBeLessThanOrEqual(finalMetrics.viewportWidth + 1)
  expect(finalMetrics.documentHeight).toBeLessThanOrEqual(finalMetrics.viewportHeight + 1)
  expect(finalMetrics.scrollY).toBe(0)
  const finalRailOverflow = await page.locator('.dashboard-rail').evaluate(rail => ({
    scrollHeight: rail.scrollHeight,
    clientHeight: rail.clientHeight,
    scrollWidth: rail.scrollWidth,
    clientWidth: rail.clientWidth,
  }))
  expect(finalRailOverflow.scrollHeight).toBeLessThanOrEqual(finalRailOverflow.clientHeight + 1)
  expect(finalRailOverflow.scrollWidth).toBeLessThanOrEqual(finalRailOverflow.clientWidth + 1)

  await page.screenshot({
    path: testInfo.outputPath('dashboard-full-page.png'),
    fullPage: true,
  })
  expect(blockedHttp).toEqual([])
  expect(blockedWebSockets).toEqual([])
})
