/* Capture the reworked Flower soil layout at both approved viewports. */
const { chromium } = require('@playwright/test')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:4174'
const OUT = path.resolve(__dirname, '../../.omo/evidence/flower-soil-scada')
const VIEWPORTS = [
  { width: 1920, height: 1080, slug: '1920x1080' },
  { width: 1280, height: 1440, slug: '1280x1440' },
]

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
    const page = await context.newPage()
    const consoleErrors = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    const url = `${BASE}/flower/soil?fixtureSession=layout-${viewport.slug}&scenario=soil-probes-2`
    await page.goto(url, { waitUntil: 'networkidle' })
    await page.waitForTimeout(3000)
    await page.screenshot({ path: path.join(OUT, `soil-layout-${viewport.slug}.png`), fullPage: false })
    const overflow = await page.evaluate(
      () => document.documentElement.scrollHeight - document.documentElement.clientHeight,
    )
    const probeCards = await page.getByTestId('soil-probe-card').count()
    const firstCard = await page.getByTestId('soil-probe-card').first().textContent()
    console.log(`viewport ${viewport.slug}: overflow=${overflow}px probeCards=${probeCards} consoleErrors=${consoleErrors.length}`)
    console.log(`  first card: ${(firstCard ?? '').slice(0, 60)}`)
    for (const error of consoleErrors.slice(0, 3)) console.log(`  console error: ${error.slice(0, 200)}`)
    await context.close()
  }
  await browser.close()
})()
