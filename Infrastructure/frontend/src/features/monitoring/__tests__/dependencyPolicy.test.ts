import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// Vitest runs from the frontend root, so the working directory is the repo
// root for this package. `import.meta.url` is not a `file:` URL under jsdom.
const ROOT = process.cwd()
const PKG = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf-8'))

// Packages that must never appear as dependencies: uPlot React wrappers and
// global runtime diagnostics. The monitoring feature drives uPlot directly and
// must not pull in a wrapper or a global instrumentation dependency.
const PROHIBITED_PACKAGES = [
  'react-scan',
  'react-doctor',
  'react-uplot',
  'uplot-react',
  'recharts',
  'chart.js',
  'react-chartjs-2',
  '@nivo/core',
]

const PROHIBITED_IMPORTS = ['react-scan', 'react-doctor', 'react-uplot', 'uplot-react']

function allSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...allSourceFiles(full))
    } else if (/\.(ts|tsx)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

function allDeps(): Record<string, string> {
  return { ...PKG.dependencies, ...PKG.devDependencies }
}

describe('dependency policy', () => {
  it('pins exact monitoring chart and accessibility versions', () => {
    const deps = allDeps()
    expect(deps['uplot']).toBe('1.6.32')
    expect(deps['zod']).toBe('3.25.76')
    expect(deps['@playwright/test']).toBe('1.60.0')
    expect(deps['@axe-core/playwright']).toBe('4.11.1')
  })

  it('rejects wrappers and global diagnostics', () => {
    const deps = allDeps()
    for (const pkg of PROHIBITED_PACKAGES) {
      expect(deps[pkg], `prohibited package ${pkg} must not be a dependency`).toBeUndefined()
    }

    const srcDir = join(ROOT, 'src')
    for (const file of allSourceFiles(srcDir)) {
      const content = readFileSync(file, 'utf-8')
      for (const mod of PROHIBITED_IMPORTS) {
        expect(content, `${file} must not import ${mod}`).not.toMatch(
          new RegExp(`from ['"]${mod}['"]|require\\(['"]${mod}['"]\\)`),
        )
      }
    }
  })

  it('does not add a generic runtime diagnostic dependency', () => {
    const deps = allDeps()
    for (const pkg of ['react-scan', 'react-doctor']) {
      expect(deps[pkg]).toBeUndefined()
    }
  })
})
