import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const OUT = '/home/antoine/ProjectCEA/.omo/evidence/grafana-replacement-veg-flower-2/07-visual-qa'
fs.mkdirSync(OUT, { recursive: true })

const base = 'http://127.0.0.1:4173'
const themes = ['precision-void', 'control-room', 'verdant-growth', 'spectrum', 'obsidian', 'botanical']
const widths = [375, 768, 1280]
const pages = [
  { name: 'flower', path: '/flower/monitoring' },
  { name: 'vegetation', path: '/vegetation/monitoring' },
]

function url(p, id) {
  const sep = p.path.includes('?') ? '&' : '?'
  return `${base}${p.path}${sep}scenario=force-error&fixtureSession=${encodeURIComponent(id)}`
}

const browser = await chromium.launch()

let idx = 0
for (const p of pages) {
  for (const w of widths) {
    const page = await browser.newPage({ viewport: { width: w, height: 900 } })
    const u = url(p, `t7-${p.name}-${w}-${idx++}`)
    await page.goto(u, { waitUntil: 'networkidle' })
    await page.waitForSelector('.mon-banner--error', { state: 'visible' })
    await page.screenshot({ path: path.join(OUT, `${p.name}-${w}.png`), fullPage: true })
    await page.close()
  }
}

for (const theme of themes) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const u = url({ path: '/flower/monitoring' }, `t7-theme-${theme}-${idx++}`)
  await page.goto(u, { waitUntil: 'networkidle' })
  await page.evaluate((t) => localStorage.setItem('cea-theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('.mon-banner--error', { state: 'visible' })
  await page.screenshot({ path: path.join(OUT, `flower-theme-${theme}.png`), fullPage: true })
  await page.close()
}

await browser.close()
console.log('captured to', OUT)
