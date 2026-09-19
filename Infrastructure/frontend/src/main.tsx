import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './styles/index.css'

/**
 * A deploy replaces the content-hashed lazy chunks. A tab already running the
 * previous bundle then fails its lazy import on the next navigation to an
 * unvisited route ("Error loading dynamically imported module"). One guarded
 * reload picks up the fresh index.html without risking a reload loop.
 */

export function isDynamicModuleLoadError(message: string | null | undefined): boolean {
  if (typeof message !== 'string') return false
  const lowered = message.toLowerCase()
  return (
    lowered.includes('failed to fetch dynamically imported module') ||
    lowered.includes('error loading dynamically imported module') ||
    lowered.includes('loading chunk') ||
    lowered.includes('dynamically imported module') ||
    lowered.includes('importing a module script failed')
  )
}

export function shouldReload(lastReloadAtMs: number | null, nowMs: number, windowMs: number): boolean {
  if (lastReloadAtMs === null) return true
  return nowMs - lastReloadAtMs >= windowMs
}

const RELOAD_GUARD_KEY = 'spa_bundle_reload'
const RELOAD_GUARD_WINDOW_MS = 5000

// Fallback stamp when storage access is denied: one recovery per document
// session is still enforced even though it cannot survive a reload.
let lastReloadInMemory: number | null = null

function readLastReload(): number | null {
  try {
    const raw = sessionStorage.getItem(RELOAD_GUARD_KEY)
    if (raw === null) return null
    return Number.isFinite(Number(raw)) ? Number(raw) : null
  } catch {
    return lastReloadInMemory
  }
}

function stampReload(): void {
  lastReloadInMemory = Date.now()
  try {
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()))
  } catch {
    // A denied storage write must not prevent last-resort recovery.
  }
}

function reloadOnce(): void {
  const last = readLastReload()
  const stamp = Number.isFinite(last) ? last : null
  if (!shouldReload(stamp, Date.now(), RELOAD_GUARD_WINDOW_MS)) return
  stampReload()
  window.location.reload()
}

export function registerBundleReloadHandler(): void {
  // Vite emits this event for failed dynamic imports/modulepreloads; the
  // generic channels catch the same failures outside the Vite build path.
  window.addEventListener('vite:preloadError', reloadOnce)
  window.addEventListener('error', (event) => {
    if (isDynamicModuleLoadError(event.message)) reloadOnce()
  })
  window.addEventListener('unhandledrejection', (event) => {
    if (isDynamicModuleLoadError(String(event.reason))) reloadOnce()
  })
}

registerBundleReloadHandler()

const container = document.getElementById('root')
if (container) {
  ReactDOM.createRoot(container).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
