import { Component, type ErrorInfo, type ReactNode } from 'react'

import { logger } from '../utils/logger'

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    logger.error('[ErrorBoundary] rendering error', error, errorInfo)
  }

  handleReload = (): void => {
    window.location.reload()
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      const message = this.state.error?.message ?? 'Unknown rendering error'
      return (
        <div
          role="alert"
          className="flex flex-col items-center justify-center min-h-screen p-8 gap-4 text-center bg-background text-foreground"
        >
          <h1 className="text-2xl font-semibold">Something went wrong</h1>
          <p className="text-muted max-w-lg break-words">{message}</p>
          <button
            type="button"
            onClick={this.handleReload}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition"
          >
            Reload application
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
