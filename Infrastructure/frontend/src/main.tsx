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

function isDynamicModuleLoadError(message: string | null | undefined): boolean {
  if (typeof message !== 'string') return false
  return (
    message.includes('Failed to fetch dynamically imported module') ||
    message.includes('error loading dynamically imported module') ||
    message.includes('Loading chunk') ||
    (message.includes('dynamically imported module') && message.toLowerCase().includes('error')) ||
    message.includes('Importing a module script failed')
  )
}
export { isDynamicModuleLoadError }

const RELOAD_GUARD_KEY = 'spa_bundle_reload'
const RELOAD_GUARD_WINDOW_MS = 5000

function reloadOnce(): void {
  const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) ?? 0)
  if (Number.isFinite(last) && Date.now() - last < RELOAD_GUARD_WINDOW_MS) return
  sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()))
  window.location.reload()
}

export function registerBundleReloadHandler(): void {
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
