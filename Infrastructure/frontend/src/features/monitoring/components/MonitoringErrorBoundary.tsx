/**
 * Lightweight error boundary for the monitoring pages.
 *
 * Catches an unexpected render error inside a page so it does not crash the
 * whole app, and renders a fallback with a Reload link. This is page-scoped and
 * intentionally separate from the app-level `ErrorBoundary`.
 */
import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

export class MonitoringErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="mon-page">
          <div className="mon-banner mon-banner--error" role="alert">
            <p>Something went wrong rendering this dashboard.</p>
            <a href={window.location.pathname} onClick={() => window.location.reload()}>
              Reload
            </a>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
