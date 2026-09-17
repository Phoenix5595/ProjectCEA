/* Capture monitoring page screenshots + network/console logs for visual QA. */
const { chromium } = require('@playwright/test')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:4173'
const OUT = path.resolve(__dirname, '../../../../.omo/evidence/grafana-replacement-veg-flower/29-visual-a11y')
const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1280, height: 1440 },
]
const PAGES = [
  { slug: 'flower', path: '/flower/monitoring' },
  { slug: 'veg', path: '/vegetation/monitoring' },
]

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const logs = { requests: [], console: [] }

  for (const pageDef of PAGES) {
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({ viewport })
      const page = await context.newPage()
      page.on('request', (req) => {
        logs.requests.push({ page: pageDef.slug, viewport, url: req.url() })
      })
      page.on('console', (msg) => {
        if (msg.type() === 'error' || msg.type() === 'warning') {
          logs.console.push({ page: pageDef.slug, viewport, type: msg.type(), text: msg.text() })
        }
      })
      await page.goto(`${BASE}${pageDef.path}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      await page.screenshot({
        path: path.join(OUT, `${pageDef.slug}-${viewport.width}x${viewport.height}.png`),
        fullPage: true,
      })
      await context.close()
    }
  }

  fs.writeFileSync(path.join(OUT, 'network-console.json'), JSON.stringify(logs, null, 2))
  await browser.close()
  console.log('captures written to', OUT)
  console.log('requests:', logs.requests.length, 'console:', logs.console.length)
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
